"""Offline campaign URLs, landing markup evidence and multilingual cluster checks."""
import json
import re
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin
from .core import (Snapshot, Head, audit_snapshot, validate_manifest, checked_url,
                   canonical_json, sha, require, robots_tokens)
from .http import public_url

FORMAT = 'wgg-growth/v1'
RULESET = 'wgg-growth-v1'
UTM_REQUIRED = ('source', 'medium', 'campaign')
UTM_OPTIONAL = ('id', 'source_platform', 'content', 'term')
SLUG = re.compile(r'[a-z0-9][a-z0-9_-]{0,79}\Z')


def campaign_url(destination, tags):
    """Build a deterministic URL; no network, no click IDs or personal identifiers."""
    public_url(destination)
    require("?" not in destination and "#" not in destination, "Use an untagged HTTPS destination")
    require(isinstance(tags, dict) and set(UTM_REQUIRED) <= tags.keys() <=
            set(UTM_REQUIRED + UTM_OPTIONAL), 'Invalid campaign tag fields')
    require(all(isinstance(v, str) and SLUG.fullmatch(v) for v in tags.values()),
            'Use lowercase ASCII campaign labels (1-80 characters); no personal data')
    ordered = [('utm_' + key, tags[key]) for key in UTM_REQUIRED + UTM_OPTIONAL if key in tags]
    return {'format': 'wgg-campaign/v1', 'destination': destination,
            'url': destination + '?' + urlencode(ordered), 'tags': dict(ordered),
            'scope': 'URL construction only; no visit, redirect, posting or analytics measurement'}


def campaign_batch(rows, allowed=None):
    require(isinstance(rows, list) and 1 <= len(rows) <= 50, 'Declare 1-50 campaigns')
    results, seen = [], set()
    for row in rows:
        require(isinstance(row, dict) and set(row) == {'destination', 'tags'}, 'Invalid campaign row')
        if allowed is not None:
            require(row['destination'] in allowed, 'Destination outside approved landing scope')
        item = campaign_url(row['destination'], row['tags'])
        require(item['url'] not in seen, 'Duplicate campaign URL')
        seen.add(item['url'])
        results.append(item)
    return {'format': 'wgg-campaign-batch/v1', 'campaigns': results}


def validate_growth(obj):
    require(isinstance(obj, dict) and set(obj) == {'site', 'landings', 'clusters'}, 'Invalid growth policy')
    site = validate_manifest(obj['site'])
    pages = {row['url']: row for row in site['pages']}
    landings, seen = obj['landings'], set()
    require(isinstance(landings, list) and 1 <= len(landings) <= min(50, len(pages)), 'Declare 1-50 landings')
    for row in landings:
        require(isinstance(row, dict) and set(row) == {'url', 'cta_targets', 'require_social'}, 'Invalid landing fields')
        require(isinstance(row['url'], str) and row['url'] in pages and row['url'] not in seen,
                'Landing must be a unique declared page')
        seen.add(row['url'])
        require(type(row['require_social']) is bool, 'require_social must be boolean')
        targets = row['cta_targets']
        require(isinstance(targets, list) and 1 <= len(targets) <= 10, 'Declare 1-10 CTA targets')
        for target in targets:
            checked_url(target, site['origin'])
        require(len(set(targets)) == len(targets), 'Duplicate CTA target')
    clusters, member_seen, ids = obj['clusters'], set(), set()
    require(isinstance(clusters, list) and len(clusters) <= 30, 'Invalid cluster list')
    for cluster in clusters:
        require(isinstance(cluster, dict) and set(cluster) == {'id', 'members', 'x_default'}, 'Invalid cluster fields')
        ident = cluster['id']
        require(isinstance(ident, str) and SLUG.fullmatch(ident) and ident not in ids, 'Invalid cluster ID')
        ids.add(ident)
        members = cluster['members']
        require(isinstance(members, dict) and 2 <= len(members) <= 20, 'Declare 2-20 cluster members')
        for lang, url in members.items():
            require(isinstance(lang, str) and lang == lang.lower() and lang != 'x-default' and
                    isinstance(url, str) and url in pages and url not in member_seen and
                    pages[url]['language'].lower() == lang, 'Cluster member must match a unique declared language/page')
            member_seen.add(url)
            require(pages[url]['indexable'] and pages[url]['canonical'] == url,
                    'Cluster members must be declared indexable and self-canonical')
        default = cluster['x_default']
        require(default is None or (isinstance(default, str) and default in members.values()), 'x-default must point to a declared cluster member')
        if default is not None:
            require(pages[default]['indexable'] and pages[default]['canonical'] == default,
                    'x-default must be declared indexable and self-canonical')
        expected = dict(members)
        if default is not None:
            expected['x-default'] = default
        for url in members.values():
            require(pages[url]['alternates'] == expected, 'Cluster and page alternate expectations must match exactly')
    return {'site': site, 'landings': sorted((dict(x, cta_targets=sorted(x['cta_targets'])) for x in landings), key=lambda x: x['url']),
            'clusters': sorted(clusters, key=lambda c: c['id'])}


class Body(HTMLParser):
    """Static markup counts, not rendered visibility or conversion behavior."""
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.body = False
        self.inert = []
        self.h1 = 0
        self.anchors = []
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag == 'body':
            self.body = True
        if not self.body:
            return
        if tag in ('script', 'style', 'template', 'noscript'):
            self.inert.append(tag)
        if self.inert:
            return
        a = dict(attrs)
        if tag == 'h1':
            self.h1 += 1
        if tag == 'a' and a.get('href'):
            self.anchors.append(a['href'])

    def handle_endtag(self, tag):
        if self.inert:
            if self.inert[-1] == tag:
                self.inert.pop()
            return
        if tag == 'body':
            self.body = False


def _one(head, key):
    values = head.meta.get(key, [])
    return values[0] if len(values) == 1 and values[0] else None


def _social(head, url):
    """Report metadata only, never fetch image or render third-party HTML."""
    result, issues = {}, []
    for key in ('og:title', 'og:description', 'og:image', 'og:url'):
        value = _one(head, key)
        if value is None or len(value) > (2048 if key in ('og:image', 'og:url') else 500) or any(ord(c) < 32 for c in value):
            issues.append('SOCIAL_' + key.upper().replace(':', '_') + '_INVALID')
            continue
        if key in ('og:image', 'og:url'):
            try:
                public_url(value)
                if key == 'og:url':
                    require(value == url, 'Social URL must match landing URL')
            except ValueError:
                issues.append('SOCIAL_' + key.upper().replace(':', '_') + '_INVALID')
                continue
        result[key] = value
    # X/Twitter can fall back to OG; this checks declared metadata only.
    card = _one(head, 'twitter:card')
    if card not in ('summary', 'summary_large_image'):
        issues.append('SOCIAL_TWITTER_CARD_UNVERIFIED')
    else:
        result['twitter:card'] = card
    return result, issues


def read_growth_policy(root, name='growth.json'):
    return validate_growth(json.loads(Snapshot(root).read(name)))


def growth_audit(root, obj):
    config = validate_growth(obj)
    snap = Snapshot(root)
    site = config['site']
    static = audit_snapshot(snap, site)
    findings = list(static['findings'])
    pages = {p['url']: p for p in site['pages']}
    observations = {}
    heads = {}

    def add(url, code, severity, detail):
        findings.append({'url': url, 'code': code, 'severity': severity, 'detail': detail})

    for row in config['landings']:
        url = row['url']
        if url not in static['pages']:
            add(url, 'LANDING_UNVERIFIED', 'error', 'Landing source unavailable')
            continue
        raw = snap.read(pages[url]['file'])
        text = raw.decode('utf-8-sig')
        head = heads[url] = Head(text)
        body = Body(text)
        viewport = _one(head, 'viewport')
        if not viewport or not re.search(r'(?:^|,)\s*width\s*=\s*device-width\s*(?:,|$)', viewport, re.I):
            add(url, 'LANDING_VIEWPORT_UNVERIFIED', 'warning', 'Expected explicit width=device-width viewport')
        if body.h1 != 1:
            add(url, 'LANDING_H1_COUNT', 'warning', 'Expected one H1 in ordinary body markup')
        resolved = set()
        if not head.base_seen:
            for href in body.anchors:
                try:
                    resolved.add(checked_url(urljoin(url, href), site['origin']))
                except ValueError:
                    continue
        missing = sorted(set(row['cta_targets']) - resolved)
        if missing:
            add(url, 'LANDING_CTA_MISSING', 'error', 'One or more approved CTA targets missing from static anchor markup')
        metadata, issues = _social(head, url)
        for code in issues:
            add(url, code, 'error' if row['require_social'] and code != 'SOCIAL_TWITTER_CARD_UNVERIFIED' else 'warning',
                'Missing, ambiguous or unsupported social metadata')
        observations[url] = {'sha256': sha(raw), 'h1_count': body.h1, 'cta_expected': len(row['cta_targets']),
                             'cta_matched': len(row['cta_targets']) - len(missing), 'cta_missing': missing,
                             'social_fields': sorted(metadata), 'viewport_declared': bool(viewport)}
    for cluster in config['clusters']:
        expected = dict(cluster['members'])
        if cluster['x_default'] is not None:
            expected['x-default'] = cluster['x_default']
        for url in cluster['members'].values():
            if url not in static['pages']:
                add(url, 'CLUSTER_MEMBER_UNVERIFIED', 'error', 'Declared cluster member unavailable')
                continue
            h = heads.get(url) or Head(snap.read(pages[url]['file']).decode('utf-8-sig'))
            actual = {}
            ambiguous = False
            for link in h.links:
                if 'alternate' not in link.get('rel', '').lower().split() or not link.get('hreflang'):
                    continue
                lang = link['hreflang'].lower()
                if lang in actual or not link.get('href') or h.base_seen:
                    ambiguous = True
                    continue
                try:
                    actual[lang] = checked_url(link['href'], site['origin'])
                except ValueError:
                    ambiguous = True
            if ambiguous or actual != expected:
                add(url, 'CLUSTER_MEMBERSHIP_DRIFT', 'error', 'Observed alternate set differs from complete declared cluster')
            if h.lang.lower() != pages[url]['language'].lower():
                add(url, 'CLUSTER_LANGUAGE_DRIFT', 'error', 'HTML language differs from declared cluster member')
        # Existing core checks every declared member's noindex and canonical, including x-default.
    unique = {(f['url'], f['code']): f for f in findings}
    findings = [unique[k] for k in sorted(unique)]
    return {'format': FORMAT, 'ruleset': RULESET, 'origin': site['origin'],
            'scope_sha256': sha(canonical_json(config)), 'pages_expected': static['pages_expected'],
            'pages_verified': static['pages_verified'], 'pages': static['pages'],
            'landings_expected': len(config['landings']), 'landings': observations,
            'sitemap_sha256': static['sitemap_sha256'], 'findings': findings,
            'scope': 'Offline static markup and declared policy; not rendering, traffic, conversion, ranking or posting'}


def preview(root, config, url):
    config = validate_growth(config)
    require(url in {row['url'] for row in config['landings']}, 'Landing outside approved scope')
    row = next(p for p in config['site']['pages'] if p['url'] == url)
    raw = Snapshot(root).read(row['file'])
    fields, issues = _social(Head(raw.decode('utf-8-sig')), url)
    return {'url': url, 'sha256': sha(raw), 'fields': fields, 'issues': issues,
            'text_trust': 'untrusted', 'scope': 'Raw metadata only; image and platform rendering not verified'}


def validate_growth_report(r):
    require(isinstance(r, dict) and r.get('format') == FORMAT and r.get('ruleset') == RULESET, 'Invalid growth report')
    checked_url(r['origin'])
    require(isinstance(r.get('scope_sha256'), str) and re.fullmatch('[0-9a-f]{64}', r['scope_sha256']), 'Invalid policy digest')
    require(type(r.get('pages_expected')) is int and type(r.get('pages_verified')) is int and
            1 <= r['pages_expected'] <= 100 and 0 <= r['pages_verified'] <= r['pages_expected'] and
            isinstance(r.get('pages'), dict) and len(r['pages']) == r['pages_verified'], 'Invalid page coverage')
    require(type(r.get('landings_expected')) is int and 1 <= r['landings_expected'] <= min(50, r['pages_expected']) and
            isinstance(r.get('landings'), dict) and len(r['landings']) <= r['landings_expected'] and
            set(r['landings']) <= set(r['pages']), 'Invalid landing coverage')
    require(isinstance(r.get('findings'), list) and len(r['findings']) <= 5000, 'Invalid findings')
    for url in r['pages']:
        checked_url(url, r['origin'])
        require(isinstance(r['pages'][url], dict) and isinstance(r['pages'][url].get('sha256'), str) and
                re.fullmatch('[0-9a-f]{64}', r['pages'][url]['sha256']), 'Invalid page evidence')
    for url, item in r['landings'].items():
        require(isinstance(item, dict) and set(item) == {'sha256', 'h1_count', 'cta_expected', 'cta_matched', 'cta_missing', 'social_fields', 'viewport_declared'}, 'Invalid landing observation')
        require(item['sha256'] == r['pages'][url].get('sha256') and type(item['h1_count']) is int and 0 <= item['h1_count'] <= 1024 * 1024, 'Invalid landing digest or H1 count')
        n, matched, missing = item['cta_expected'], item['cta_matched'], item['cta_missing']
        require(type(n) is int and type(matched) is int and 1 <= n <= 10 and 0 <= matched <= n and isinstance(missing, list) and len(missing) == n - matched, 'Invalid CTA coverage')
        for target in missing:
            checked_url(target, r['origin'])
        require(len(set(missing)) == len(missing) and type(item['viewport_declared']) is bool and isinstance(item['social_fields'], list) and all(isinstance(k, str) and k in {'og:title','og:description','og:image','og:url','twitter:card'} for k in item['social_fields']), 'Invalid landing metadata')

    for f in r['findings']:
        require(isinstance(f, dict) and set(f) == {'url', 'code', 'severity', 'detail'} and
                all(isinstance(v, str) and len(v) <= 2048 for v in f.values()) and
                f['severity'] in ('warning', 'error'), 'Invalid finding')
    return r


def growth_compare(baseline, current):
    validate_growth_report(baseline)
    validate_growth_report(current)
    require(all(baseline[k] == current[k] for k in ('origin', 'scope_sha256', 'pages_expected', 'landings_expected')),
            'Growth policy changed; establish a reviewed baseline')
    old = {(f['url'], f['code']): f for f in baseline['findings']}
    new = {(f['url'], f['code']): f for f in current['findings']}
    added = [new[k] for k in sorted(new) if k not in old or new[k]['severity'] != old[k]['severity']]
    blockers = [f for f in current['findings'] if f['severity'] == 'error']
    if current['pages_verified'] != current['pages_expected'] or len(current['landings']) != current['landings_expected']:
        blockers.append({'url': current['origin'], 'code': 'INCOMPLETE_GROWTH_COVERAGE', 'severity': 'error', 'detail': 'Incomplete declared evidence'})
    return {'format': 'wgg-growth-regression/v1', 'decision': 'BLOCK' if blockers else 'REVIEW' if added else 'CLEAR',
            'added': added, 'resolved': [old[k] for k in sorted(old.keys() - new.keys())], 'current_errors': blockers,
            'baseline_digest': sha(canonical_json(baseline)), 'current_digest': sha(canonical_json(current)),
            'scope': 'Static evidence only; no conversion/ranking guarantee or deployment authorization'}
