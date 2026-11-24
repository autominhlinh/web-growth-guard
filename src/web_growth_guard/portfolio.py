"""Read-only, script-free portfolio dashboard built from local audit reports."""
import html
import json
from .core import Snapshot, canonical_json, sha, require, compare
from .discovery import compare_live
from .marketing import growth_compare

MAX_SITES = 12


def compare_reports(baseline, current):
    require(isinstance(current, dict), 'Invalid report')
    if current.get('format') == 'wgg-audit/v1':
        return compare(baseline, current)
    if current.get('format') == 'wgg-live/v1':
        return compare_live(baseline, current)
    if current.get('format') == 'wgg-growth/v1':
        return growth_compare(baseline, current)
    raise ValueError('Unsupported report format')


def read_portfolio(root, name='portfolio.json'):
    source=Snapshot(root)
    config=json.loads(source.read(name))
    require(isinstance(config,dict) and set(config)=={'sites'} and isinstance(config['sites'],list)
            and 1 <= len(config['sites']) <= MAX_SITES,'Declare 1-12 sites')
    sites=[]; labels=set()
    for entry in config['sites']:
        require(isinstance(entry,dict) and set(entry)=={'label','report','baseline'},'Invalid portfolio entry')
        label=entry['label']
        require(isinstance(label,str) and bool(label.strip()) and len(label)<=80 and label not in labels
                and not any(ord(c)<32 or ord(c)==127 for c in label),'Invalid or duplicate site label')
        labels.add(label)
        require(isinstance(entry['report'],str) and entry['report'].endswith('.json'),'Report must be JSON')
        report=json.loads(source.read(entry['report']))
        comparison=compare_reports(report,report)
        baseline=entry['baseline']
        if baseline is not None:
            require(isinstance(baseline,str) and baseline.endswith('.json'),'Baseline must be JSON or null')
            comparison=compare_reports(json.loads(source.read(baseline)),report)
        errors=len(comparison['current_errors'])
        warnings=sum(f['severity']=='warning' for f in report['findings'])
        decision=comparison['decision']
        # A portfolio without baseline still surfaces existing warnings for operator review.
        if decision=='CLEAR' and warnings:
            decision='REVIEW'
        sites.append({'label':label,'origin':report['origin'],'format':report['format'], 'decision':decision,
                      'collection_mode':report.get('collection_mode','offline_snapshot'), 'observed_at':report.get('observed_at'),
                      'verified':report['pages_verified'],'expected':report['pages_expected'],
                      'errors':errors,'warnings':warnings,'baseline_used':baseline is not None,
                      'growth': {'landings_verified':len(report['landings']), 'landings_expected':report['landings_expected'],
                                 'cta_matched':sum(x['cta_matched'] for x in report['landings'].values()),
                                 'cta_expected_on_verified_pages':sum(x['cta_expected'] for x in report['landings'].values())}
                                if report['format']=='wgg-growth/v1' else None,
                      'added':len(comparison['added']) if baseline is not None else None,
                      'resolved':len(comparison['resolved']) if baseline is not None else None,
                      'report_sha256':sha(canonical_json(report)),
                      'findings':comparison['current_errors'] + [f for f in report['findings'] if f['severity']=='warning'],
                      'bots': {agent:{state:sum(row['robots'][agent]==state for row in report['pages'].values())
                                      for state in ('ALLOWED','DISALLOWED','UNKNOWN')}
                               for agent in ('Googlebot','OAI-SearchBot','GPTBot')} if report['format']=='wgg-live/v1' else None})
    return {'format':'wgg-portfolio/v1','sites':sites,'limitations':'Saved operator-trusted reports; not live monitoring, ranking or authorization.'}


def esc(x): return html.escape(str(x),quote=True)


def render(portfolio):
    sites=portfolio['sites']
    totals={status:sum(s['decision']==status for s in sites) for status in ('CLEAR','REVIEW','BLOCK')}
    stats=''.join(f'<div class="metric"><span>{label}</span><strong class="{label.lower()}">{value}</strong></div>'
                  for label,value in [('Sites',len(sites)),('Clear',totals['CLEAR']),('Review',totals['REVIEW']),('Block',totals['BLOCK'])])
    cards=[]
    for s in sorted(sites,key=lambda x:({'BLOCK':0,'REVIEW':1,'CLEAR':2}[x['decision']],x['label'])):
        finding_rows=''.join('<tr><td>'+esc(f['severity'])+'</td><td>'+esc(f['code'])+'</td><td>'+esc(f['url'])+'</td><td>'+esc(f['detail'])+'</td></tr>'
                             for f in s['findings'][:100])
        bots=''
        if s['bots']:
            bots='<div class="bots">'+''.join('<div><strong>'+esc(agent)+'</strong><span>'+esc(counts['ALLOWED'])+' allowed / '+esc(counts['DISALLOWED'])+' disallowed / '+esc(counts['UNKNOWN'])+' unknown</span></div>'
                                              for agent,counts in s['bots'].items())+'</div>'
        growth=s.get('growth')
        growth_detail=('<p class="meta">Landing coverage: '+esc(growth['landings_verified'])+'/'+esc(growth['landings_expected'])+' &middot; CTA markup: '+esc(growth['cta_matched'])+'/'+esc(growth['cta_expected_on_verified_pages'])+' on verified pages</p>') if growth else ''
        delta=(f'{s["added"]} added / {s["resolved"]} resolved' if s['baseline_used'] else 'No baseline supplied')
        cards.append(f'<section><div class="heading"><div><h2>{esc(s["label"])}</h2><p>{esc(s["origin"])}</p></div><span class="status {s["decision"].lower()}">{s["decision"]}</span></div>'
                     f'<div class="metrics compact"><div class="metric"><span>Coverage</span><strong>{s["verified"]}/{s["expected"]}</strong></div>'
                     f'<div class="metric"><span>Errors</span><strong>{s["errors"]}</strong></div><div class="metric"><span>Warnings</span><strong>{s["warnings"]}</strong></div></div>'
                     f'<p class="meta">{esc(s["format"])} &middot; {esc(s["collection_mode"])} &middot; {esc(delta)}</p>'
                     f'<p class="meta">Observed: {esc(s["observed_at"] or "not timestamped")}</p>{bots}{growth_detail}'
                     '<details '+('open' if s['decision']=='BLOCK' else '')+'><summary>Findings (up to 100)</summary><div class="table"><table><thead><tr><th>Severity</th><th>Rule</th><th>URL</th><th>Evidence</th></tr></thead><tbody>'+
                     (finding_rows or '<tr><td colspan="4">No findings in saved report.</td></tr>')+'</tbody></table></div></details>'
                     f'<p class="digest">Report SHA-256: {esc(s["report_sha256"])}</p></section>')
    return '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Web Growth Guard | Portfolio</title><style>
:root{font-family:system-ui,sans-serif;color:#18333e;background:#edf3f4;--muted:#60747e}*{box-sizing:border-box}body{margin:0;padding:24px}main{max-width:1100px;margin:auto}header{margin-bottom:24px}.eyebrow{letter-spacing:.12em;font-weight:700;font-size:12px;color:#087c73}h1{font-size:clamp(26px,4vw,40px);margin:6px 0}h2{font-size:21px;margin:0}p{color:var(--muted);line-height:1.5;margin:4px 0 10px}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin:16px 0}.metric{background:#fff;border:1px solid #dbe5e8;border-radius:12px;padding:14px;display:grid;gap:5px}.metric span{color:var(--muted);font-size:13px}.metric strong{font-size:28px}.compact .metric{background:#f6f9f9;padding:10px}.compact strong{font-size:22px}section{padding:20px;background:#fff;border:1px solid #dbe5e8;border-radius:14px;margin:16px 0}.heading{display:flex;justify-content:space-between;gap:12px;align-items:start;overflow-wrap:anywhere}.status{font-size:13px;font-weight:700;padding:5px 10px;border:1px solid currentColor;border-radius:8px}.clear{color:#13745d}.review{color:#945c0a}.block{color:#ad3b39}.bots{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px;margin:12px 0}.bots div{background:#eef5f6;padding:10px;border-radius:8px;display:grid;gap:4px;font-size:12px}.bots span{color:var(--muted)}details{margin:12px 0;border-top:1px solid #dbe5e8;padding-top:12px}summary{font-weight:600;cursor:pointer}.table{overflow:auto}table{width:100%;border-collapse:collapse;font-size:13px;margin-top:10px}td,th{text-align:left;vertical-align:top;padding:9px;border-bottom:1px solid #e3ebed;overflow-wrap:anywhere}th{color:var(--muted)}.digest{font-size:11px;overflow-wrap:anywhere}.meta,footer{font-size:13px}footer{padding:12px 0;color:var(--muted)}
@media(prefers-color-scheme:dark){:root{color:#e6eff2;background:#14272e;--muted:#b4c6cc}section,.metric{background:#203a42;border-color:#39535d}.compact .metric,.bots div{background:#2b454e}.clear{color:#86dbbd}.review{color:#f3c780}.block{color:#f6a3a3}td,th,details{border-color:#39535d}}
</style></head><body><main><header><div class="eyebrow">WEB GROWTH GUARD / OFFLINE REPORT</div><h1>Website portfolio</h1><p>Saved technical evidence across your declared websites. Not a live ranking or traffic report.</p></header><div class="metrics">'''+stats+'</div>'+''.join(cards)+'''<footer>Operator-trusted reports, not independently signed. CLEAR does not prove indexing, authorize deployment or guarantee growth. Keep real site reports private. No remote assets or scripts.</footer></main></body></html>'''
