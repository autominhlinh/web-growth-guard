"""Opt-in public HTTP checks, distinct from offline release evidence."""
import json
import re
from html.parser import HTMLParser
from datetime import datetime, timezone
from urllib.parse import urlsplit
from .core import (audit_snapshot, validate_manifest, canonical_json, sha, require,
                   MAX_FILE, MAX_TOTAL)
from .http import Collector, FetchError, public_url
from .robots import Robots, AGENTS

RULESET = 'wgg-live-v1'
MAX_LIVE_PAGES = 10


def policy(obj):
    require(isinstance(obj, dict) and set(obj) == {'origin', 'pages', 'sitemap'}, 'Invalid live policy')
    origin = public_url(obj['origin'])
    require(origin == 'https://' + urlsplit(origin).hostname, 'Invalid origin')
    require(isinstance(obj['pages'], list) and 1 <= len(obj['pages']) <= MAX_LIVE_PAGES,
            'Declare 1-10 public pages')
    rows = []
    for index, page in enumerate(obj['pages']):
        require(isinstance(page, dict) and set(page) == {'url', 'indexable', 'canonical', 'language', 'alternates'},
                'Invalid live page fields')
        public_url(page['url'], origin)
        public_url(page['canonical'], origin)
        require(isinstance(page['alternates'], dict), 'Invalid alternates')
        for value in page['alternates'].values():
            public_url(value, origin)
        rows.append(dict(page, file=f'pages/{index}.html'))
    sm = obj['sitemap']
    if sm is not None:
        public_url(sm, origin)
        require(urlsplit(sm).path.endswith('.xml'), 'Expected simple XML sitemap URL')
    static = validate_manifest({'origin': origin, 'pages': rows, 'sitemap': 'sitemap.xml' if sm else None})
    normalized = {'origin': origin, 'pages': sorted(obj['pages'], key=lambda p: p['url']), 'sitemap': sm}
    return normalized, static


class MemorySnapshot:
    def __init__(self):
        self.cache = {}

    def read(self, name):
        if name not in self.cache:
            raise ValueError('Observation unavailable')
        return self.cache[name]


def mime(response):
    types = response.values('Content-Type')
    return types[0].split(';', 1)[0].strip().lower() if len(types) == 1 else ''


def is_utf8(response):
    types = response.values('Content-Type')
    if len(types) != 1:
        return False
    chars = re.findall(r'charset\s*=\s*["\']?([^;"\'\s]+)', types[0], re.I)
    return not chars or (len(chars) == 1 and chars[0].lower() in ('utf-8', 'utf8'))


def header_directives(values):
    """Track scoped header directives; unknown prefixes are not attributed to other bots."""
    result = {'all': False, 'googlebot': False, 'oai-searchbot': False}
    argument_rules = {'max-snippet', 'max-image-preview', 'max-video-preview', 'unavailable_after'}
    for value in values:
        scope = 'all'
        for part in value.lower().split(','):
            part = part.strip()
            if ':' in part:
                prefix, rest = (x.strip() for x in part.split(':', 1))
                if prefix in argument_rules:
                    continue
                scope = prefix
                part = rest
            if part in ('noindex', 'none') and scope in result:
                result[scope] = True
    return result


class StructuredData(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.buffer = None
        self.blocks = self.invalid = 0
        self.feed(html)
        self.close()
        if self.buffer is not None:
            self.invalid += 1

    def handle_starttag(self, tag, attrs):
        if tag == 'script' and (dict(attrs).get('type') or '').lower().split(';', 1)[0].strip() == 'application/ld+json':
            self.buffer = []
            self.blocks += 1

    def handle_data(self, data):
        if self.buffer is not None:
            self.buffer.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.buffer is not None:
            try:
                obj = json.loads(''.join(self.buffer), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                if not isinstance(obj, (dict, list)):
                    raise ValueError('Expected JSON object or array')
            except (ValueError, RecursionError):
                self.invalid += 1
            self.buffer = None


def observation(response):
    return {'status': response.status, 'final_url': response.url, 'redirects': list(response.redirects),
            'bytes': len(response.body), 'sha256': sha(response.body) if response.status == 200 else None,
            'media_type': mime(response) if mime(response) in ('text/html', 'text/plain', 'application/xml', 'text/xml') else 'other'}


def discover(obj, collector=None):
    config, manifest = policy(obj)
    origin = config['origin']
    collection_mode = "public_https" if collector is None else "injected_transport"
    if collector is None:
        collector = Collector(origin)
    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    snap = MemorySnapshot()
    findings, pages = [], {}
    robots_obs = {'status': 'UNKNOWN', 'evidence': None}
    sitemap_obs = None

    def add(url, code, severity, detail):
        findings.append({'url': url, 'code': code, 'severity': severity, 'detail': detail})

    robots = None
    try:
        response = collector.get(origin + '/robots.txt', limit=512 * 1024)
        robots_obs['evidence'] = observation(response)
        if response.status in (404, 410):
            robots = Robots(b'')
            robots_obs['status'] = 'NOT_FOUND'
        elif response.status == 200 and mime(response) == 'text/plain' and is_utf8(response):
            robots = Robots(response.body)
            robots_obs['status'] = 'PARSED'
        else:
            add(origin, 'ROBOTS_UNVERIFIABLE', 'error', 'Robots policy unavailable or unsupported; no page collection')
    except (ValueError, UnicodeError):
        add(origin, 'ROBOTS_UNVERIFIABLE', 'error', 'Robots retrieval or parsing failed; no page collection')
    if robots is not None and config['sitemap']:
        try:
            response = collector.get(config['sitemap'], allowed=lambda p: robots.allowed('WebGrowthGuard', p))
            sitemap_obs = observation(response)
            if response.status != 200 or mime(response) not in ('application/xml', 'text/xml') or not is_utf8(response):
                raise FetchError('SITEMAP_RESPONSE_UNSUPPORTED')
            snap.cache['sitemap.xml'] = response.body
        except (ValueError, UnicodeError):
            add(origin, 'SITEMAP_HTTP_UNVERIFIABLE', 'error', 'Declared sitemap unavailable or unsupported')
    for row in manifest['pages']:
        url = row['url']
        path = urlsplit(url).path or '/'
        item = {'robots': {agent: 'UNKNOWN' for agent in AGENTS}, 'response': None,
                'header_noindex': None, 'jsonld': None}
        pages[url] = item
        if robots is None:
            continue
        try:
            item['robots'] = {agent: 'ALLOWED' if robots.allowed(agent, path) else 'DISALLOWED' for agent in AGENTS}
            if row['indexable']:
                for agent, code in [('Googlebot', 'GOOGLEBOT_ROBOTS_DISALLOWED'), ('OAI-SearchBot', 'OAI_SEARCHBOT_ROBOTS_DISALLOWED')]:
                    if item['robots'][agent] == 'DISALLOWED':
                        add(url, code, 'error', 'Expected discoverable page is disallowed by declared robots policy')
            if item['robots']['WebGrowthGuard'] != 'ALLOWED':
                add(url, 'ROBOTS_TOOL_DISALLOWED', 'error', 'Collector respects its own robots rules; page not retrieved')
                continue
            response = collector.get(url, allowed=lambda p: robots.allowed('WebGrowthGuard', p))
            item['response'] = observation(response)
            if response.redirects:
                add(url, 'PAGE_REDIRECTED', 'error', 'Declared exact page redirected; review scope and destination')
                continue
            if response.status != 200 or mime(response) != 'text/html' or not is_utf8(response):
                add(url, 'PAGE_HTTP_UNVERIFIABLE', 'error', 'Expected HTTP 200 UTF-8 HTML response')
                continue
            html = response.body.decode('utf-8-sig')
            flags = header_directives(response.values('X-Robots-Tag'))
            item['header_noindex'] = flags
            if row['indexable']:
                for flag, code in [('all', 'HTTP_NOINDEX'), ('googlebot', 'HTTP_GOOGLEBOT_NOINDEX'), ('oai-searchbot', 'HTTP_OAI_SEARCHBOT_NOINDEX')]:
                    if flags[flag]:
                        add(url, code, 'error', 'Restrictive X-Robots-Tag observed')
            structured = StructuredData(html)
            item['jsonld'] = {'blocks': structured.blocks, 'invalid': structured.invalid}
            if structured.invalid:
                add(url, 'JSONLD_INVALID', 'warning', 'Malformed JSON-LD syntax; schema eligibility not assessed')
            snap.cache[row['file']] = response.body
        except (ValueError, UnicodeError, RecursionError):
            add(url, 'PAGE_FETCH_UNVERIFIABLE', 'error', 'Request failed, outside permitted scope, or unsupported content')
    static = audit_snapshot(snap, manifest)
    findings.extend(static['findings'])
    unique = {(f['url'], f['code']): f for f in findings}
    return {'format': 'wgg-live/v1', 'ruleset': RULESET, 'origin': origin,
            'scope_sha256': sha(canonical_json(config)), 'collection_mode': collection_mode,
            'observed_at': observed_at, 'pages_expected': len(pages),
            'pages_verified': static['pages_verified'], 'robots': robots_obs, 'sitemap': sitemap_obs,
            'pages': pages, 'findings': sorted(unique.values(), key=lambda f: (f['url'], f['code'])),
            'limitations': 'Point-in-time same-origin GET as WebGrowthGuard; REP simulation only, not actual Google/OpenAI crawler, indexing, rendering, ranking or schema eligibility. GPTBot policy is informational, not a search gate.'}


def validate_live_report(report):
    require(isinstance(report, dict) and report.get('format') == 'wgg-live/v1' and report.get('ruleset') == RULESET,
            'Unsupported live report')
    public_url(report['origin'])
    require(report.get('collection_mode') in ('public_https', 'injected_transport'), 'Missing or invalid collection mode')
    require(isinstance(report.get('observed_at'), str) and len(report['observed_at']) <= 40, 'Invalid observation timestamp')
    require(datetime.fromisoformat(report['observed_at']).tzinfo is not None, 'Observation timestamp must include timezone')
    n, verified = report['pages_expected'], report['pages_verified']
    require(type(n) is int and type(verified) is int and 1 <= n <= MAX_LIVE_PAGES and 0 <= verified <= n,
            'Invalid coverage')
    require(isinstance(report.get('scope_sha256'), str) and re.fullmatch(r'[0-9a-f]{64}', report['scope_sha256']), 'Invalid scope')
    require(isinstance(report.get('pages'), dict) and len(report['pages']) == n, 'Invalid page list')
    observed = 0
    for url, item in report['pages'].items():
        public_url(url, report['origin'])
        require(isinstance(item, dict) and isinstance(item.get('robots'), dict) and set(item['robots']) == set(AGENTS)
                and all(v in ('ALLOWED', 'DISALLOWED', 'UNKNOWN') for v in item['robots'].values()), 'Invalid robots observation')
        response = item.get('response')
        if isinstance(response, dict) and response.get('status') == 200 and response.get('final_url') == url and not response.get('redirects') and response.get('media_type') == 'text/html' and item.get('header_noindex') is not None and item.get('jsonld') is not None:
            require(isinstance(response.get('sha256'), str) and re.fullmatch(r'[0-9a-f]{64}', response['sha256']), 'Invalid response digest')
            observed += 1
    require(observed == verified, 'Inconsistent live report coverage')
    findings = report.get('findings')
    require(isinstance(findings, list) and len(findings) <= 1000, 'Invalid findings')
    for f in findings:
        require(isinstance(f, dict) and set(f) == {'url', 'code', 'severity', 'detail'} and
                all(isinstance(v, str) and len(v) <= 2048 for v in f.values()) and f['severity'] in ('error', 'warning'), 'Invalid finding')
    return report


def compare_live(baseline, current):
    for r in (baseline, current):
        validate_live_report(r)
    require(baseline['collection_mode'] == current['collection_mode'], 'Collection mode changed; synthetic reports cannot be live baselines')
    require(baseline['origin'] == current['origin'] and baseline['scope_sha256'] == current['scope_sha256'] and
            set(baseline['pages']) == set(current['pages']), 'Live scope changed; establish a reviewed baseline')
    old = {(f['url'], f['code']): f for f in baseline['findings']}
    new = {(f['url'], f['code']): f for f in current['findings']}
    added = [new[k] for k in sorted(new) if k not in old or old[k]['severity'] != new[k]['severity']]
    errors = [f for f in current['findings'] if f['severity'] == 'error']
    if current['pages_verified'] != current['pages_expected'] or current.get('robots', {}).get('status') not in ('PARSED', 'NOT_FOUND'):
        errors.append({'url': current['origin'], 'code': 'INCOMPLETE_LIVE_COVERAGE', 'severity': 'error', 'detail': 'Incomplete live evidence'})
    return {'format': 'wgg-live-regression/v1', 'decision': 'BLOCK' if errors else 'REVIEW' if added else 'CLEAR',
            'added': added, 'resolved': [old[k] for k in sorted(old.keys() - new.keys())], 'current_errors': errors,
            'baseline_digest': sha(canonical_json(baseline)), 'current_digest': sha(canonical_json(current)),
            'limitations': 'Operator-trusted reports; not proof of indexing or deployment authorization.'}
