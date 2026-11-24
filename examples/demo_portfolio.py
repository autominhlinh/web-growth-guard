"""Synthetic portfolio: no website requests. New output directory only."""
import argparse
import json
from pathlib import Path
from web_growth_guard.discovery import discover
from web_growth_guard.http import Response
from web_growth_guard.portfolio import read_portfolio, render

HERE=Path(__file__).resolve().parent
ORIGIN='https://example.invalid'


def demo_policy():
    manifest=json.loads((HERE/'demo/manifest.json').read_text())
    return {'origin':manifest['origin'],'pages':[{k:v for k,v in p.items() if k!='file'} for p in manifest['pages']],
            'sitemap':ORIGIN+'/sitemap.xml'}


class SyntheticCollector:
    def __init__(self, scenario): self.scenario=scenario
    def get(self,url,allowed=None,limit=1024*1024):
        path=url[len(ORIGIN):]
        if path=='/robots.txt':
            raw=b'User-agent: *\nAllow: /\n'
            if self.scenario=='block': raw+=b'User-agent: OAI-SearchBot\nDisallow: /vi/\n'
            return Response(url,200,(('Content-Type','text/plain'),),raw)
        if path=='/sitemap.xml':
            return Response(url,200,(('Content-Type','application/xml'),),(HERE/'demo/sitemap.xml').read_bytes())
        raw=(HERE/'demo/pages'/('en.html' if path=='/en/' else 'vi.html')).read_bytes()
        if self.scenario=='review' and path=='/vi/':
            raw=raw.replace(b'</head>',b'<script type="application/ld+json">{invalid}</script></head>')
        headers=(('Content-Type','text/html; charset=utf-8'),)
        if self.scenario=='block' and path=='/vi/': headers+=(('X-Robots-Tag','googlebot: noindex'),)
        return Response(url,200,headers,raw)


def generate(output):
    output.mkdir(mode=0o700,exist_ok=False)
    config=demo_policy()
    (output/'live-policy.json').write_text(json.dumps(config,indent=2)+'\n')
    reports={scenario:discover(config,SyntheticCollector(scenario)) for scenario in ('clear','review','block')}
    for name,report in reports.items(): (output/(name+'.json')).write_text(json.dumps(report,indent=2)+'\n')
    entries={'sites':[{'label':'Demo / '+label,'report':scenario+'.json','baseline':'clear.json'} for scenario,label in
                      [('clear','Healthy baseline'),('review','JSON-LD needs review'),('block','Search discovery blocked')]]}
    (output/'portfolio.json').write_text(json.dumps(entries,indent=2)+'\n')
    portfolio=read_portfolio(output)
    states={site['decision'] for site in portfolio['sites']}
    assert states=={'CLEAR','REVIEW','BLOCK'}
    (output/'dashboard.html').write_text(render(portfolio),encoding='utf-8')
    (output/'summary.json').write_text(json.dumps(portfolio,indent=2)+'\n')
    print('PORTFOLIO DEMO PASS: CLEAR, REVIEW, BLOCK; synthetic HTTP responses only')
    return portfolio


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);args=p.parse_args();generate(args.output)
