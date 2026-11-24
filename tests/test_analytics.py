import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from shutil import copytree
from web_growth_guard.analytics import analyze, csv_rows, validate_policy, portfolio, ratio
from web_growth_guard.analytics_view import render
from web_growth_guard.marketing import read_growth_policy

ORIGIN='https://example.invalid'
PAGE=['page','clicks','impressions','position']
GA=['landing_page','sessions','engaged_sessions','key_events']
QUERY=['Top queries','Clicks','Impressions','CTR','Position']


def csv_data(header, rows):
    f=io.StringIO(newline='');w=csv.writer(f);w.writerow(header);w.writerows(rows)
    return f.getvalue().encode('utf-8')


def policy(kind='gsc_pages'):
    return {'origin':ORIGIN,'label':'Synthetic','data_mode':'synthetic',
            'periods':{'previous':{'start':'2026-09-01','end':'2026-09-28'},'current':{'start':'2026-09-29','end':'2026-10-26'}},
            'reports':{kind:{'previous':'previous.csv','current':'current.csv','scope':'Synthetic same scope'}},'thresholds':{}}


class ParsingTests(unittest.TestCase):
    def test_native_gsc_ctr_recomputed(self):
        data=csv_data(QUERY,[('hello, world',5,500,'99%',8.4)])
        result=csv_rows(b'\xef\xbb\xbf'+data,'gsc_queries',ORIGIN)
        self.assertEqual(result['hello, world']['ctr_pct'],1.0)
        self.assertEqual(result['hello, world']['position'],8.4)

    def test_ga4_repeat_events_and_zero(self):
        rows=csv_rows(csv_data(GA,[('/vi/',100,60,150.5),('/en/',0,0,0)]),'ga4_landings',ORIGIN)
        self.assertEqual(rows[ORIGIN+'/vi/']['key_events_per_100_sessions'],150.5)
        self.assertIsNone(rows[ORIGIN+'/en/']['engagement_pct'])
        self.assertIsNone(rows[ORIGIN+'/en/']['key_events_per_100_sessions'])

    def test_gsc_zero_impressions(self):
        rows=csv_rows(csv_data(PAGE,[(ORIGIN+'/zero/',0,0,'')]),'gsc_pages',ORIGIN)
        self.assertIsNone(rows[ORIGIN+'/zero/']['ctr_pct'])
        self.assertIsNone(rows[ORIGIN+'/zero/']['position'])

    def test_reject_bad_numeric_and_duplicate_rows(self):
        for row in [(ORIGIN,5,4,9),(ORIGIN,-1,100,9),(ORIGIN,1,100,'nan'),(ORIGIN,1,100,'1e1'),
                    (ORIGIN,1,100,0),(ORIGIN,'1,000',100,9),(ORIGIN,0,0,1),(ORIGIN,0,100,'')]:
            with self.subTest(row=row),self.assertRaises(ValueError):
                csv_rows(csv_data(PAGE,[row]),'gsc_pages',ORIGIN)
        row=(ORIGIN,1,100,9)
        with self.assertRaises(ValueError):csv_rows(csv_data(PAGE,[row,row]),'gsc_pages',ORIGIN)
        for row in [('/vi/',100,101,2),('/vi/',0,0,1),('/vi/',100,50,'inf')]:
            with self.assertRaises(ValueError):csv_rows(csv_data(GA,[row]),'ga4_landings',ORIGIN)

    def test_reject_scope_query_strings_and_placeholders(self):
        for value in ['https://other.invalid/','http://example.invalid/','https://example.invalid/?email=x','https://example.invalid/#x']:
            with self.assertRaises(ValueError):csv_rows(csv_data(PAGE,[(value,1,10,1)]),'gsc_pages',ORIGIN)
        for value in ['(not set)','//other.invalid/','/vi/?id=x']:
            with self.assertRaises(ValueError):csv_rows(csv_data(GA,[(value,1,1,0)]),'ga4_landings',ORIGIN)
        with self.assertRaises(ValueError):csv_rows(csv_data(QUERY,[('(not set)',1,10,'10%',1)]),'gsc_queries',ORIGIN)

    def test_reject_bad_headers_rows_and_utf8(self):
        for raw in [b'page,clicks,clicks,position\n',b'page,clicks,impressions,position,query\n',b'page,clicks,impressions,position\n"unterminated',
                    b'page,clicks,impressions,position\nonlyone',b'\xff',b'\0']:
            with self.subTest(raw=raw),self.assertRaises(ValueError):csv_rows(raw,'gsc_pages',ORIGIN)

    def test_policy_equal_nonoverlapping_dates_and_unique_paths(self):
        validate_policy(policy())
        for start,end in [('2026-09-28','2026-10-25'),('2026-09-29','2026-10-27'),('2026-02-30','2026-03-29')]:
            cfg=policy();cfg['periods']['current']={'start':start,'end':end}
            with self.assertRaises(ValueError):validate_policy(cfg)
        cfg=policy();cfg['reports']['gsc_pages']['current']='previous.csv'
        with self.assertRaises(ValueError):validate_policy(cfg)
        cfg=policy();cfg['thresholds']={'min_impressions':True}
        with self.assertRaises(ValueError):validate_policy(cfg)
        cfg=policy();cfg['thresholds']={'low_ctr_pct':float('nan')}
        with self.assertRaises(ValueError):validate_policy(cfg)


@unittest.skipUnless(os.name=='posix','POSIX required')
class AnalysisTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.base=Path(tmp.name);self.root=self.base/'csv';self.root.mkdir()

    def write(self,kind,headers,previous,current):
        cfg=policy(kind)
        (self.root/'previous.csv').write_bytes(csv_data(headers,previous));(self.root/'current.csv').write_bytes(csv_data(headers,current));return cfg

    def test_matched_only_declines_ctr_and_scenario(self):
        a,b,c,d=[ORIGIN+'/'+x+'/' for x in 'abcd']
        cfg=self.write('gsc_pages',PAGE,[(a,100,1000,8),(b,20,500,10),(c,500,1000,4)],[(a,30,3000,9),(b,25,1000,8),(d,10,2000,14)])
        result=analyze(self.root,cfg);group=result['reports']['gsc_pages']
        self.assertEqual(group['matched_rows'],2);self.assertEqual(group['previous_only_examples'],[c]);self.assertEqual(group['current_only_examples'],[d]);
        self.assertEqual(group['matched_previous']['clicks'],120);self.assertEqual(group['matched_current']['clicks'],55)
        self.assertEqual(group['matched_changes']['clicks']['absolute'],-65)
        self.assertEqual(group['matched_current']['ctr_pct'],1.375)
        declines=[r for r in result['actions'] if r['code']=='GSC_CLICK_DECLINE'];self.assertEqual([r['key'] for r in declines],[a]);self.assertEqual(declines[0]['evidence']['clicks_lost'],70)
        low={r['key']:r for r in result['actions'] if r['code']=='GSC_LOW_CTR'};self.assertEqual(low[a]['evidence']['hypothetical_additional_clicks'],30)
        self.assertIn(d,low);self.assertEqual(len(result['inputs']),2)
        self.assertEqual(result['evidence_sha256'],analyze(self.root,cfg)['evidence_sha256'])
        self.assertEqual(group['observed_previous']['clicks'],620)

    def test_empty_or_no_matched_rows_is_unknown(self):
        cfg=self.write('gsc_pages',PAGE,[(ORIGIN+'/a/',100,1000,8)],[])
        r=analyze(self.root,cfg);g=r['reports']['gsc_pages'];self.assertEqual(r['action_count'],0);self.assertIsNone(g['matched_changes']);self.assertIsNone(g['matched_current']);self.assertEqual(g['previous_only_count'],1)
        self.assertIsNone(g['observed_current']['ctr_pct'])

    def test_ga4_session_engagement_and_key_event_deltas(self):
        cfg=self.write('ga4_landings',GA,[('/vi/',500,350,55),('/old/',300,200,50)],[('/vi/',300,150,45),('/new/',50,25,4)])
        r=analyze(self.root,cfg);g=r['reports']['ga4_landings'];self.assertEqual(g['matched_changes']['sessions']['absolute'],-200)
        self.assertEqual(g['matched_changes']['engagement_pct']['absolute'],-20)
        self.assertEqual(g['matched_changes']['key_events_per_100_sessions']['absolute'],4)
        self.assertEqual({a['code'] for a in r['actions']},{'GA4_SESSION_DECLINE','GA4_ENGAGEMENT_DECLINE'})

    def test_no_combined_property_total_and_portfolio(self):
        cfg=self.write('gsc_pages',PAGE,[(ORIGIN,30,1000,8)],[(ORIGIN,20,1000,8)])
        for p in ('previous','current'):(self.root/f'query-{p}.csv').write_bytes(csv_data(QUERY,[('sample',20,1000,'2%',8)]))
        cfg['reports']['gsc_queries']={'previous':'query-previous.csv','current':'query-current.csv','scope':'Separate query dimension'}
        (self.root/'policy.json').write_text(json.dumps(cfg));r=portfolio(self.root,{'policies':['policy.json']})
        self.assertEqual(set(r['sites'][0]['reports']),{'gsc_pages','gsc_queries'});self.assertNotIn('totals',r)
        with self.assertRaises(ValueError):portfolio(self.root,{'policies':['policy.json','policy.json']})
        (self.root/'policy2.json').write_text(json.dumps(cfg))
        with self.assertRaises(ValueError):portfolio(self.root,{'policies':['policy.json','policy2.json']})

    def test_escape_report_and_no_network_assets(self):
        cfg=self.write('gsc_queries',QUERY,[('<script>alert(1)</script>',100,1000,'10%',8)],[('<script>alert(1)</script>',1,1000,'0.1%',8)])
        cfg['label']='<img src=x onerror=alert(1)>';html=render(analyze(self.root,cfg))
        self.assertNotIn('<script>',html);self.assertNotIn('<img src=',html);self.assertIn('&lt;script&gt;',html);self.assertIn("default-src 'none'",html)

    def test_snapshot_paths_and_links_rejected_and_evidence_changes(self):
        cfg=self.write('gsc_pages',PAGE,[(ORIGIN,30,1000,8)],[(ORIGIN,20,1000,8)])
        before=analyze(self.root,cfg)['evidence_sha256'];(self.root/'current.csv').write_bytes(csv_data(PAGE,[(ORIGIN,21,1000,8)]))
        self.assertNotEqual(before,analyze(self.root,cfg)['evidence_sha256'])
        outside=self.base/'outside.csv';outside.write_bytes(csv_data(PAGE,[(ORIGIN,50,100,4)]))
        (self.root/'current.csv').unlink();(self.root/'current.csv').symlink_to(outside)
        with self.assertRaises(ValueError):analyze(self.root,cfg)
        cfg['reports']['gsc_pages']['current']='../outside.csv'
        with self.assertRaises(ValueError):analyze(self.root,cfg)

    def test_cli_and_real_mcp_opt_in_wire(self):
        cfg=self.write('gsc_pages',PAGE,[(ORIGIN+'/vi/',100,1000,8)],[(ORIGIN+'/vi/',30,3000,8)])
        path=self.root/'analytics.json';path.write_text(json.dumps(cfg))
        cli=subprocess.run([sys.executable,'-m','web_growth_guard','analytics-audit','--root',str(self.root)],capture_output=True,timeout=15)
        self.assertEqual(cli.returncode,0,cli.stderr);self.assertEqual(json.loads(cli.stdout)['format'],'wgg-analytics/v1')
        root=self.base/'snapshot';copytree(Path(__file__).resolve().parents[1]/'examples'/'growth',root)
        growth=self.base/'growth.json';growth.write_text(json.dumps(read_growth_policy(root)))
        trusted=self.base/'analytics.json';trusted.write_text(json.dumps(cfg))
        cmds=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'test','version':'1'}}},
              {'jsonrpc':'2.0','method':'notifications/initialized'}, {'jsonrpc':'2.0','id':2,'method':'tools/list'},
              {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'analytics_audit','arguments':{}}},
              {'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'analytics_audit','arguments':{'root':'/etc'}}}]
        command=[sys.executable,'-m','web_growth_guard.mcp_server','--root',str(root),'--policy',str(growth)]
        raw=b''.join(json.dumps(c).encode()+b'\n' for c in cmds)
        result=subprocess.run(command+['--analytics-root',str(self.root),'--analytics-policy',str(trusted)],input=raw,capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr);out=[json.loads(x) for x in result.stdout.splitlines()]
        self.assertIn('analytics_audit',{x['name'] for x in out[1]['result']['tools']});self.assertFalse(out[2]['result']['isError']);self.assertTrue(out[3]['result']['isError'])
        self.assertEqual(out[2]['result']['structuredContent']['evidence_sha256'],json.loads(cli.stdout)['evidence_sha256'])
        result=subprocess.run(command,input=raw,capture_output=True,timeout=15);out=[json.loads(x) for x in result.stdout.splitlines()]
        self.assertNotIn('analytics_audit',{x['name'] for x in out[1]['result']['tools']});self.assertTrue(out[2]['result']['isError'])
        result=subprocess.run(command+['--analytics-root',str(self.root),'--analytics-policy',str(path)],input=b'',capture_output=True,timeout=15);self.assertEqual(result.returncode,1)
        cfg['origin']='https://other.invalid';trusted.write_text(json.dumps(cfg));result=subprocess.run(command+['--analytics-root',str(self.root),'--analytics-policy',str(trusted)],input=b'',capture_output=True,timeout=15);self.assertEqual(result.returncode,1)
