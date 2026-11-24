"""Reproducible, synthetic multi-site analytics demo. No real account exports."""
import argparse
import csv
import json
from pathlib import Path
from web_growth_guard.analytics import analyze, portfolio
from web_growth_guard.analytics_view import render


def make_site(base, slug, origin, name, variants):
    def write_csv(path, header, rows):
        with (base / path).open('x',encoding='utf-8',newline='') as f:
            w=csv.writer(f);w.writerow(header);w.writerows(rows)
    reports={}
    for kind,(headers,previous,current) in variants.items():
        p=f'{slug}-{kind}-previous.csv';c=f'{slug}-{kind}-current.csv'
        write_csv(p,headers,previous);write_csv(c,headers,current)
        reports[kind]={'previous':p,'current':c,'scope':'Synthetic matched filter and metric definitions; no real Google property'}
    cfg={'origin':origin,'label':name,'data_mode':'synthetic',
         'periods':{'previous':{'start':'2026-09-01','end':'2026-09-28'},
                    'current':{'start':'2026-09-29','end':'2026-10-26'}},
         'reports':reports,'thresholds':{}}
    path=f'{slug}.json'
    (base/path).write_text(json.dumps(cfg,indent=2),encoding='utf-8')
    return path


def fixtures(base):
    # Use example.invalid only. Counts are deliberately chosen to exercise thresholds.
    a='https://directory.example.invalid';b='https://store.example.invalid';c='https://audio.example.invalid'
    page=['page','clicks','impressions','position']; query=['Top queries','Clicks','Impressions','CTR','Position']
    ga=['landing_page','sessions','engaged_sessions','key_events']
    return [make_site(base,'directory',a,'Community directory',{
        'gsc_pages':(page,[(a+'/vi/',100,2000,7),(a+'/en/',60,1200,12),(a+'/old/',50,800,9)],
                    [(a+'/vi/',30,2100,8),(a+'/en/',80,1500,10),(a+'/new/',10,700,16)]),
        'gsc_queries':(query,[('sample community',80,3000,'2.67%',7),('synthetic group',12,400,'3%',17)],
                       [('sample community',30,3000,'1%',8),('synthetic group',20,500,'4%',12)])}),
        make_site(base,'store',b,'Product catalog',{
        'gsc_pages':(page,[(b+'/products/',120,3500,8)],[(b+'/products/',120,4000,9)]),
        'ga4_landings':(ga,[('/products/',500,350,55),('/sale/',100,80,12)],
                        [('/products/',300,150,45),('/new/',50,35,6)])}),
        make_site(base,'audio',c,'Audio library',{
        'gsc_pages':(page,[(c+'/vi/',100,2200,7)],[(c+'/vi/',150,2400,3)]),
        'ga4_landings':(ga,[('/vi/',200,100,40)],[('/vi/',260,180,60)])})]


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);args=p.parse_args()
    base=args.output; base.mkdir(parents=False,exist_ok=False)
    paths=fixtures(base); manifest={'policies':paths}
    (base/'analytics-portfolio.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    report=portfolio(base,manifest)
    (base/'report.json').write_text(json.dumps(report,ensure_ascii=True,indent=2),encoding='utf-8')
    (base/'dashboard.html').write_text(render(report),encoding='utf-8')
    sites=report['sites']; a,b,c=sites
    codes=lambda r:{x['code'] for x in r['actions']}
    assert {'GSC_CLICK_DECLINE','GSC_LOW_CTR'}<=codes(a)
    assert {'GA4_SESSION_DECLINE','GA4_ENGAGEMENT_DECLINE'}<=codes(b)
    assert c['action_count']==0
    assert a['reports']['gsc_pages']['previous_only_count']==1
    assert a['reports']['gsc_pages']['matched_previous']['clicks']==160
    assert a['reports']['gsc_pages']['matched_current']['clicks']==110
    assert b['reports']['ga4_landings']['matched_current']['key_events_per_100_sessions']==15
    verification={'scope':'synthetic CSV only','sites':[{'label':s['label'],'actions':s['action_count'],
                 'codes':sorted(codes(s)),'data_mode':s['data_mode']} for s in sites], 'verified':'PASS'}
    (base/'verification.json').write_text(json.dumps(verification,indent=2),encoding='utf-8')
    print(json.dumps(verification,indent=2))


if __name__=='__main__':main()
