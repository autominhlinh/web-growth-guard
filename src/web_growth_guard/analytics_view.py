"""Private, script-free HTML analytics reports. All imported text is escaped."""
from html import escape


def e(v):
    return escape(str(v), quote=True)


def fmt(v):
    if v is None:
        return 'Unknown'
    if type(v) is bool:
        return 'Yes' if v else 'No'
    if isinstance(v,(int,float)):
        return f'{v:,.4f}'.rstrip('0').rstrip('.') if isinstance(v,float) else f'{v:,}'
    return e(v)


def render(result):
    sites = result['sites'] if result['format']=='wgg-analytics-portfolio/v1' else [result]
    cards=[]
    for site in sites:
        periods=site['periods']; groups=[]
        for kind,group in sorted(site['reports'].items()):
            counts = f"{group['matched_rows']} matched · {group['previous_only_count']} previous-only · {group['current_only_count']} current-only"
            metrics=[]
            for metric,value in group['observed_current'].items():
                previous=group['matched_previous']; current=group['matched_current']; change=group['matched_changes']
                d=change[metric] if change else None
                unit=' pp' if metric.endswith('_pct') else (' per 100 sessions' if metric=='key_events_per_100_sessions' else '')
                diff=(fmt(d['absolute'])+unit if d is not None else 'Unknown')
                metrics.append('<tr><td>'+e(metric.replace('_',' '))+'</td><td>'+fmt(previous[metric] if previous else None)+'</td><td>'+fmt(current[metric] if current else None)+'</td><td>'+e(diff)+'</td><td>'+fmt(value)+'</td></tr>')
            groups.append('<details open><summary>'+e(kind.replace('_',' ').upper())+'</summary><p>'+e(group['scope'])+'</p><p>'+e(counts)+'</p><div class="scroll"><table><thead><tr><th>Metric</th><th>Matched previous</th><th>Matched current</th><th>Absolute change</th><th>Current exported rows</th></tr></thead><tbody>'+''.join(metrics)+'</tbody></table></div><p class="hint">Missing rows are unknown, not zero. Export totals are not site totals.</p></details>')
        actions=[]
        for a in site['actions']:
            detail=' · '.join(f'{k.replace("_"," ")}: {fmt(v)}' for k,v in a['evidence'].items())
            # fmt already escapes string values; escape the aggregate once instead of trusting input.
            actions.append('<tr><td><span class="'+('p1' if a['priority']=='P1' else 'p2')+'">'+e(a['priority'])+'</span></td><td>'+e(a['dimension'])+'<br>'+e(a['key'])+'</td><td>'+e(a['code'])+'<small>'+detail+'</small></td><td>'+e(a['review'])+'</td></tr>')
        window=lambda p:e(periods[p]['start'])+' to '+e(periods[p]['end'])
        notes=''.join('<li>'+e(x)+'</li>' for x in site['limitations'])
        cards.append('<section><div class="head"><div><h2>'+e(site['label'])+'</h2><p>'+e(site['origin'])+' · '+e(site['data_mode'])+'</p></div><div class="badge">'+e(site['action_count'])+' review items</div></div><p>Previous: '+window('previous')+' | Current: '+window('current')+'</p>'+''.join(groups)+'<h3>Prioritized review queue</h3><p>Showing '+e(len(actions))+' of '+e(site['action_count'])+' items. Priorities are policy heuristics, not revenue or traffic predictions.</p><div class="scroll"><table><thead><tr><th>Priority</th><th>Dimension / target</th><th>Observation</th><th>Review</th></tr></thead><tbody>'+(''.join(actions) or '<tr><td colspan="4">No threshold triggered in imported rows.</td></tr>')+'</tbody></table></div><details><summary>Evidence and limitations</summary><ul>'+notes+'</ul><p class="digest">Evidence SHA-256: '+e(site['evidence_sha256'])+'</p></details></section>')
    return '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><title>Web Growth Guard | Analytics Intelligence</title><style>
:root{font:15px/1.55 system-ui,sans-serif;background:#f0f5f4;color:#19363d;--muted:#526b72;--line:#d7e4e3}*{box-sizing:border-box}body{margin:0;padding:26px}main{max-width:1200px;margin:auto}h1{font-size:clamp(28px,5vw,42px);line-height:1.1;margin:6px 0 12px}h2{font-size:23px;margin:0}h3{font-size:17px;margin:22px 0 6px}p{margin:6px 0 12px;color:var(--muted)}.eyebrow{font-size:12px;font-weight:800;letter-spacing:.13em;color:#107b71}section{background:#fff;border:1px solid var(--line);border-radius:14px;padding:20px;margin:20px 0}.head{display:flex;justify-content:space-between;gap:12px;align-items:start;overflow-wrap:anywhere}.badge{border:1px solid #9dbdb9;padding:6px 11px;border-radius:8px;white-space:nowrap;font-weight:650}.scroll{overflow:auto}table{width:100%;border-collapse:collapse;font-size:13px;min-width:580px}td,th{padding:10px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;overflow-wrap:anywhere}th{font-size:11px;text-transform:uppercase;letter-spacing:.03em;color:var(--muted)}td:nth-child(1){font-weight:600}details{padding:12px 0;border-top:1px solid var(--line)}summary{cursor:pointer;font-weight:650}.hint,small{font-size:12px;color:var(--muted)}small{display:block;margin-top:4px}.p1{color:#a2442d}.p2{color:#126e68}.digest{font-size:12px;overflow-wrap:anywhere}footer{color:var(--muted);font-size:13px}@media(prefers-color-scheme:dark){:root{background:#152c31;color:#e4eeee;--muted:#b7c9cc;--line:#39565b}section{background:#213b40}.eyebrow,.p2{color:#8bdfd2}.p1{color:#ffbba4}}</style></head><body><main><header><div class="eyebrow">WEB GROWTH GUARD / ANALYTICS</div><h1>Growth analytics intelligence</h1><p>Offline comparison of operator-selected CSV exports. Review decisions, not automated marketing actions.</p><p><strong>Private report.</strong> Query text and metrics may contain sensitive information. No external assets, tracking or network requests.</p></header>'''+''.join(cards)+'''<footer>Do not publish real reports or share query-level text with an AI host without reviewing privacy. The tool does not verify Google account provenance or calculate causal impact.</footer></main></body></html>'''
