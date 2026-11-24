"""Bounded offline analytics of operator-selected, pre-aggregated CSV exports.

No APIs, credentials, tracking, network, or mutation. Missing rows are unknown.
"""
import csv
import io
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from .core import Snapshot, canonical_json, checked_url, path_parts, require, sha

FORMAT = 'wgg-analytics/v1'
MAX_ROWS = 5000
MAX_ACTIONS = 200
MAX_COUNT = 10**12
KINDS = ('gsc_pages', 'gsc_queries', 'ga4_landings')
DEFAULTS = {'min_impressions': 200, 'low_ctr_pct': 2.0, 'position_min': 4.0,
            'position_max': 20.0, 'min_click_loss': 20, 'min_session_loss': 20,
            'drop_pct': 20.0, 'min_sessions': 100, 'engagement_drop_pp': 10.0}


def label(value, maximum=200):
    require(isinstance(value, str) and 0 < len(value) <= maximum and value == value.strip()
            and all(ord(c) >= 32 and ord(c) != 127 for c in value), 'Invalid analytics label')
    return value


def num(value, minimum=0, maximum=MAX_COUNT):
    # Do not accept locale separators, scientific notation, NaN, or huge integers.
    require(isinstance(value, str) and len(value) <= 24 and re.fullmatch(r'[0-9]+(?:\.[0-9]{1,8})?', value),
            'Invalid decimal metric')
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValueError('Invalid decimal metric') from None
    require(minimum <= result <= maximum, 'Metric out of range')
    return result


def count(value):
    require(isinstance(value, str) and re.fullmatch(r'[0-9]{1,13}', value), 'Expected integer metric')
    n = int(value)
    require(n <= MAX_COUNT, 'Count out of range')
    return n


def ratio(n, d, scale=100):
    return round(float(Decimal(n) * scale / Decimal(d)), 4) if d else None


def round4(n):
    return round(float(n), 4)


def period(obj):
    require(isinstance(obj, dict) and set(obj) == {'start', 'end'}, 'Invalid period fields')
    require(all(isinstance(v, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', v) for v in obj.values()), 'Use ISO calendar dates')
    start, end = date.fromisoformat(obj['start']), date.fromisoformat(obj['end'])
    require(start <= end and (end-start).days < 366, 'Period must contain 1-366 days')
    return start, end


def validate_policy(obj):
    require(isinstance(obj, dict) and set(obj) == {'origin','label','data_mode','periods','reports','thresholds'},
            'Invalid analytics policy fields')
    origin = checked_url(obj['origin'])
    require(origin == 'https://' + origin.split('/')[2], 'Analytics origin must have no path')
    label(obj['label'], 80)
    require(obj['data_mode'] in ('synthetic','operator_csv'), 'Unknown data mode')
    dates = obj['periods']
    require(isinstance(dates, dict) and set(dates) == {'previous','current'}, 'Declare previous/current periods')
    p0, p1 = period(dates['previous']); c0, c1 = period(dates['current'])
    require(p1 < c0 and p1-p0 == c1-c0, 'Periods must be ordered, disjoint, and equal in length')
    reports = obj['reports']
    require(isinstance(reports, dict) and 1 <= len(reports) <= len(KINDS) and set(reports) <= set(KINDS), 'Invalid report types')
    paths = set()
    for kind, report in reports.items():
        require(isinstance(report, dict) and set(report) == {'previous','current','scope'}, 'Invalid report configuration')
        # Scope is an operator assertion of same property/filter/time zone/metric definition in both periods.
        label(report['scope'], 300)
        for k in ('previous','current'):
            path = report[k]; path_parts(path)
            require(path.endswith('.csv') and path not in paths, 'Use distinct relative CSV paths')
            paths.add(path)
    thresholds = obj['thresholds']
    require(isinstance(thresholds, dict) and set(thresholds) <= DEFAULTS.keys(), 'Invalid analytics thresholds')
    thresholds = dict(DEFAULTS, **thresholds)
    for key, value in thresholds.items():
        if key.startswith('min_'):
            require(type(value) is int and 1 <= value <= MAX_COUNT, 'Invalid minimum count threshold')
        else:
            require(type(value) in (int,float) and 0 < value <= 1000, 'Invalid ratio/position threshold')
    require(thresholds['low_ctr_pct'] <= 100 and thresholds['drop_pct'] <= 100 and
            thresholds['engagement_drop_pp'] <= 100 and 1 <= thresholds['position_min'] <= thresholds['position_max'],
            'Threshold outside metric range')
    return dict(obj, thresholds=thresholds)


def page_key(value, origin, ga4=False):
    # GA4 normalized landing path, not full URL or path+query string.
    if ga4 and value.startswith('/') and not value.startswith('//'):
        value = origin + value
    checked_url(value, origin)
    require(len(value) <= 512 and '?' not in value and '#' not in value and
            all(ord(c) != 127 for c in value), 'Landing must be a query-free URL')
    return value


def csv_rows(raw, kind, origin):
    """Accept normalized schemas and the English standalone GSC Pages/Queries tables.

    Reject duplicate dimensions, totals, unsupported GA4 exports, and mixed query/page tables.
    """
    require(b'\x00' not in raw, 'NUL in CSV')
    text = raw.decode('utf-8-sig')
    reader = csv.reader(io.StringIO(text, newline=''), strict=True)
    try:
        header = next(reader, [])
        require(len(header) == len(set(header)), 'Duplicate CSV column')
        dimension = 'page' if kind == 'gsc_pages' else 'query'
        if kind.startswith('gsc_'):
            normalized = {dimension,'clicks','impressions','position'}
            native_dimension = 'Top pages' if dimension == 'page' else 'Top queries'
            native = {native_dimension,'Clicks','Impressions','CTR','Position'}
            if set(header) == normalized:
                names = {k:k for k in normalized}
            elif set(header) == native:
                names = {dimension:native_dimension,'clicks':'Clicks','impressions':'Impressions','position':'Position','ctr':'CTR'}
            else:
                raise ValueError('Unsupported Search Console CSV header; use separate normalized or English table')
        else:
            require(set(header) == {'landing_page','sessions','engaged_sessions','key_events'}, 'Use normalized GA4 landing CSV header')
            names = {k:k for k in header}
        rows = {}
        for i, cells in enumerate(reader, 1):
            require(i <= MAX_ROWS and len(cells) == len(header), 'CSV row limit or invalid row width')
            row = dict(zip(header, cells))
            fields = {k:row[v] for k,v in names.items()}
            if kind.startswith('gsc_'):
                key = (page_key(fields['page'], origin) if dimension == 'page' else label(fields['query'], 200))
                if dimension == 'query':
                    require(key.lower() not in ('(not set)','(other)','(total)','total','(not provided)'), 'Aggregate/placeholder query row unsupported')
                clicks, impressions = count(fields['clicks']), count(fields['impressions'])
                # For this schema, zero impressions implies no observed position; exports may use zero.
                pos = num(fields['position'], 0, 1000) if fields['position'] else None
                require(clicks <= impressions and ((impressions > 0 and pos is not None and pos >= 1) or
                        (impressions == 0 and pos in (None,0))), 'Inconsistent Search Console metrics')
                if 'ctr' in fields:
                    value = fields['ctr']; require(value.endswith('%'), 'Expected CTR percentage in English export')
                    num(value[:-1], 0, 100)  # Never use rounded export CTR for calculations.
                metrics = {'clicks':clicks,'impressions':impressions,'position':round4(pos) if impressions else None,
                           'ctr_pct':ratio(clicks, impressions)}
            else:
                key = page_key(fields['landing_page'], origin, ga4=True)
                sessions, engaged = count(fields['sessions']), count(fields['engaged_sessions'])
                events = num(fields['key_events'])
                require(engaged <= sessions and (sessions > 0 or events == 0), 'Inconsistent GA4 session metrics')
                metrics = {'sessions':sessions,'engaged_sessions':engaged,'key_events':round4(events),
                           'engagement_pct':ratio(engaged,sessions),'key_events_per_100_sessions':ratio(events,sessions)}
            require(key not in rows, 'Duplicate dimension row; aggregate consistently before import')
            rows[key] = metrics
        return rows
    except csv.Error:
        raise ValueError('Malformed or oversized CSV field') from None


def totals(kind, rows):
    # These are exported row totals, not property/site totals. Never combine dimensions.
    if kind.startswith('gsc_'):
        clicks = sum(r['clicks'] for r in rows.values())
        imp = sum(r['impressions'] for r in rows.values())
        return {'clicks':clicks,'impressions':imp,'ctr_pct':ratio(clicks,imp)}
    sessions = sum(r['sessions'] for r in rows.values())
    engaged = sum(r['engaged_sessions'] for r in rows.values())
    events = sum((Decimal(str(r['key_events'])) for r in rows.values()), Decimal(0))
    return {'sessions':sessions,'engaged_sessions':engaged,'key_events':round4(events),
            'engagement_pct':ratio(engaged,sessions),'key_events_per_100_sessions':ratio(events,sessions)}


def delta(old, new):
    if old is None or new is None:
        return None
    return {'absolute':round4(Decimal(str(new))-Decimal(str(old))),
            'relative_pct':ratio(Decimal(str(new))-Decimal(str(old)),Decimal(str(old))) if old else None}


def analyze(root, policy, snapshot=None):
    cfg = validate_policy(policy)
    snap = snapshot or Snapshot(root)
    t = cfg['thresholds']; results = {}; actions = []; input_files = []
    def add(kind,key,code,priority,metric,action,evidence):
        actions.append({'dimension':kind,'key':key,'code':code,'priority':priority,
                        'observed_impact_metric':metric,'review':action,'evidence':evidence})
    for kind, spec in sorted(cfg['reports'].items()):
        periods = {}
        for p in ('previous','current'):
            raw = snap.read(spec[p]); rows = csv_rows(raw,kind,cfg['origin']); periods[p] = rows
            input_files.append({'dimension':kind,'period':p,'sha256':sha(raw),'bytes':len(raw),'rows':len(rows)})
        prev, cur = periods['previous'], periods['current']
        shared = sorted(prev.keys() & cur.keys()); missing = sorted(prev.keys()-cur.keys()); added = sorted(cur.keys()-prev.keys())
        matched = []
        for key in shared:
            p,c = prev[key],cur[key]
            # Every change below is matched-row observational evidence, not causation.
            changes = {metric:delta(p[metric],c[metric]) for metric in p}
            matched.append({'key':key,'previous':p,'current':c,'changes':changes})
            if kind.startswith('gsc_'):
                loss = p['clicks']-c['clicks']
                if loss >= t['min_click_loss'] and loss*100 >= p['clicks']*t['drop_pct']:
                    add(kind,key,'GSC_CLICK_DECLINE','P1','clicks_lost','Investigate ranking, demand, indexing and snippet changes; not a causal attribution.',
                        {'previous_clicks':p['clicks'],'current_clicks':c['clicks'],'clicks_lost':loss,'change_pct':changes['clicks']['relative_pct']})
            else:
                loss = p['sessions']-c['sessions']
                if loss >= t['min_session_loss'] and loss*100 >= p['sessions']*t['drop_pct']:
                    add(kind,key,'GA4_SESSION_DECLINE','P1','sessions_lost','Review channel mix, tracking consistency and landing availability.',
                        {'previous_sessions':p['sessions'],'current_sessions':c['sessions'],'sessions_lost':loss,'change_pct':changes['sessions']['relative_pct']})
                if min(p['sessions'],c['sessions']) >= t['min_sessions'] and p['engagement_pct']-c['engagement_pct'] >= t['engagement_drop_pp']:
                    add(kind,key,'GA4_ENGAGEMENT_DECLINE','P2','engagement_drop_pp','Review landing experience and measurement settings.',
                        {'previous_engagement_pct':p['engagement_pct'],'current_engagement_pct':c['engagement_pct'],
                         'engagement_drop_pp':round4(p['engagement_pct']-c['engagement_pct'])})
        if kind.startswith('gsc_'):
            for key,c in sorted(cur.items()):
                if c['impressions'] >= t['min_impressions'] and c['ctr_pct'] is not None and c['clicks']*100 < c['impressions']*t['low_ctr_pct']:
                    near = t['position_min'] <= c['position'] <= t['position_max']
                    # Scenario only: unchanged impression count at an operator-specified hypothetical CTR.
                    gap = max(Decimal(0), Decimal(c['impressions'])*Decimal(str(t['low_ctr_pct']))/100-Decimal(c['clicks']))
                    add(kind,key,'GSC_LOW_CTR','P2','scenario_click_gap','Review search intent, titles and snippets; validate actual search context before editing.',
                        {'impressions':c['impressions'],'clicks':c['clicks'],'ctr_pct':c['ctr_pct'],'position':c['position'],
                         'position_in_review_band':near,'hypothetical_ctr_pct':t['low_ctr_pct'],
                         'hypothetical_additional_clicks':round4(gap)})
                elif c['impressions'] >= t['min_impressions'] and c['position'] is not None and t['position_min'] <= c['position'] <= t['position_max']:
                    add(kind,key,'GSC_POSITION_REVIEW','P2','impressions','Review relevance and competing results; average position is not a fixed rank.',
                        {'impressions':c['impressions'],'position':c['position'],'ctr_pct':c['ctr_pct']})
        # Do not treat omitted/truncated/anonymized rows as zero, or claim change in property totals.
        common_prev={key:prev[key] for key in shared}; common_cur={key:cur[key] for key in shared}
        pt, ct = totals(kind,common_prev),totals(kind,common_cur)
        results[kind] = {'scope':spec['scope'],'previous_rows':len(prev),'current_rows':len(cur),'matched_rows':len(shared),
                         'previous_only_count':len(missing),'current_only_count':len(added),
                         'previous_only_examples':missing[:20],'current_only_examples':added[:20],
                         'observed_previous':totals(kind,prev),'observed_current':totals(kind,cur),
                         'matched_previous':pt if shared else None,'matched_current':ct if shared else None,
                         'matched_changes':{k:delta(pt[k],ct[k]) for k in pt} if shared else None,
                         'matched_examples':matched[:50]}
    actions.sort(key=lambda a:(a['priority'],a['dimension'],a['code'],
                              -max((float(v) for k,v in a['evidence'].items() if k in ('clicks_lost','sessions_lost','hypothetical_additional_clicks','impressions','engagement_drop_pp')),default=0),a['key']))
    # No cross-unit impact score or summing across dimensions; all suggestions are review work.
    return {'format':FORMAT,'origin':cfg['origin'],'label':cfg['label'],'data_mode':cfg['data_mode'],
            'periods':cfg['periods'],'thresholds':t,'policy_sha256':sha(canonical_json(cfg)),
            'inputs':input_files,'evidence_sha256':sha(canonical_json({'policy':cfg,'inputs':input_files})),
            'reports':results,'action_count':len(actions),'actions':actions[:MAX_ACTIONS],
            'actions_truncated':len(actions)>MAX_ACTIONS,'text_trust':'untrusted',
            'limitations':['Operator-supplied CSV; dates, property, filters, sampling and completeness are not independently verified.',
                           'Missing rows are unknown; matched-row changes only. Exported row totals are not site totals; never sum page and query dimensions.',
                           'CTR and engagement rates use underlying counts. Hypothetical CTR scenarios are arithmetic, not forecasts.',
                           'GA4 key events per 100 sessions is not the session key event/conversion rate; repeat events are possible.',
                           'No causality, statistical significance, revenue or search-volume inference.']}


def portfolio(root, manifest):
    snap = Snapshot(root)
    require(isinstance(manifest, dict) and set(manifest)=={'policies'} and isinstance(manifest['policies'],list)
            and 1 <= len(manifest['policies']) <= 12, 'Declare 1-12 analytics policy files')
    paths = manifest['policies']; require(all(isinstance(p,str) for p in paths) and len(set(paths))==len(paths), 'Duplicate analytics policy')
    sites=[]; origins=set()
    for path in paths:
        path_parts(path); require(path.endswith('.json'), 'Expected JSON policy')
        cfg=validate_policy(json.loads(snap.read(path)))
        require(cfg['origin'] not in origins,'Duplicate site origin in analytics portfolio')
        origins.add(cfg['origin']); sites.append(analyze(root,cfg,snapshot=snap))
    return {'format':'wgg-analytics-portfolio/v1','sites':sites,'text_trust':'untrusted'}
