import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from shutil import copytree
from web_growth_guard.core import canonical_json, sha
from web_growth_guard.intelligence import Document, analyze, plan, portfolio, structured, validate_policy
from web_growth_guard.intelligence_view import render

ORIGIN='https://example.invalid'
EN,VI=ORIGIN+'/en/',ORIGIN+'/vi/'


def config(root):
    site=json.loads((root/'growth.json').read_text())['site']
    return {'site':site,'entry_pages':[EN],'max_depth':2,
            'topics':[{'id':'directory-en','language':'en','target':EN,'phrases':['synthetic directory'],'priority':'high'},
                      {'id':'directory-vi','language':'vi','target':VI,'phrases':['hướng dẫn nhóm'],'priority':'normal'}],
            'required_links':[{'from':EN,'to':VI}], 'schema_profiles':{EN:['Product'],VI:['AudioObject']}}


def markup(node): return '<script type="application/ld+json">'+json.dumps(node)+'</script>'


class ParserTests(unittest.TestCase):
    def test_hidden_inert_and_inline_normalization(self):
        d=Document('<html><head><title>Head only</title></head><body><p>HƯỚNG <b>dẫn</b> nhóm</p><template><a href="/fake">hidden phrase</a></template><div hidden><a href="/hidden">fake</a></div><p aria-hidden="true">fake two</p><script>fake three</script><a href="/ok">ok</a></body></html>')
        self.assertIn(' hướng dẫn nhóm ',d.chunks)
        self.assertEqual([x[0] for x in d.links],['/ok'])
        self.assertNotIn('fake',' '.join(d.chunks))
        self.assertNotIn('head only',' '.join(d.chunks))

    def test_structured_profiles_and_invalid_json(self):
        node={'@context':'https://schema.org','@graph':[
            {'@type':'Product','name':'Synthetic','offers':{'@type':'Offer','price':'0','priceCurrency':'USD'}},
            {'@type':'BlogPosting','headline':'Example','author':{'name':'Example team'}},
            {'@type':'BreadcrumbList','itemListElement':[{'@type':'ListItem','position':1,'name':'Home','item':EN},{'@type':'ListItem','position':2,'name':'Item'}]},
            {'@type':'Organization','name':'Synthetic','url':EN},
            {'@type':'AudioObject','name':'Sample','contentUrl':ORIGIN+'/audio.mp3'}]}
        raw='<html><head>'+markup(node)+'</head><body></body></html>'
        types=['Product','Article','BreadcrumbList','Organization','AudioObject']
        self.assertEqual({v['status'] for v in structured(Document(raw),types)['profiles'].values()},{'MATCHED'})
        node['@graph'][0]['offers']['price']=-1
        self.assertEqual(structured(Document('<body>'+markup(node)+'</body>'),['Product'])['profiles']['Product']['status'],'INCOMPLETE')
        for invalid in ('{"@context":"https://schema.org","@type":"Product","name":"a","name":"b"}', '{"price":NaN}', '{'):
            result=structured(Document('<body><script type="application/ld+json">'+invalid+'</script></body>'),['Product'])
            self.assertEqual(result['invalid_scripts'],1)
            self.assertEqual(result['profiles']['Product']['status'],'UNKNOWN')

    def test_remote_context_and_inert_jsonld_never_match(self):
        fake={'@context':'https://other.invalid/context','@type':'Product','name':'Fake'}
        self.assertEqual(structured(Document('<body>'+markup(fake)+'</body>'),['Product'])['profiles']['Product']['status'],'UNKNOWN')
        fake['@context']='https://schema.org'
        d=Document('<html><head><template>'+markup(fake)+'</template></head><body><div hidden>'+markup(fake)+'</div></body></html>')
        self.assertEqual(structured(d,['Product'])['profiles']['Product']['status'],'MISSING')


@unittest.skipUnless(os.name=='posix','POSIX required')
class IntelligenceTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.base=Path(tmp.name); self.root=self.base/'snapshot'
        copytree(Path(__file__).resolve().parents[1]/'examples'/'growth',self.root)
        self.policy=config(self.root)
        self.en=self.root/'pages/en.html'; self.vi=self.root/'pages/vi.html'
        self.edit(self.en,'</body>','<a href="/vi/#intro">Vietnamese</a>'+markup({'@context':'https://schema.org','@type':'Product','name':'Synthetic product','offers':{'@type':'Offer','price':10,'priceCurrency':'USD'}})+'</body>')
        self.edit(self.vi,'</body>','<p>Hướng dẫn nhóm</p><a href="/en/">English</a>'+markup({'@context':'https://schema.org','@type':'AudioObject','name':'Synthetic audio','contentUrl':ORIGIN+'/audio.mp3'})+'</body>')

    def edit(self,path,old,new):
        raw=path.read_text(); self.assertIn(old,raw); path.write_text(raw.replace(old,new))

    def report(self): return analyze(self.root,self.policy)

    def test_clean_coverage_graph_and_determinism(self):
        r=self.report(); self.assertEqual(r,self.report()); self.assertEqual(r['findings'],[])
        self.assertEqual([t['status'] for t in r['topics']],['COVERED','COVERED'])
        self.assertEqual(r['pages'][VI]['depth'],1)
        self.assertEqual(r['pages'][VI]['inbound_observed'],1)
        self.assertEqual(r['pages'][EN]['structured_data']['profiles']['Product']['status'],'MATCHED')
        self.assertEqual(plan(r)['evidence_sha256'],sha(canonical_json(r)))

    def test_missing_link_and_unverified_target_not_broken(self):
        self.edit(self.en,'href="/vi/#intro"','href="/vi/#intro" rel="nofollow"')
        self.edit(self.en,'</body>','<a href="/not-declared/">Unverified</a></body>')
        r=self.report(); codes={f['code'] for f in r['findings']}
        self.assertTrue({'REQUIRED_LINK_MISSING','NO_OBSERVED_INBOUND','UNREACHED_IN_SCOPE'}<=codes)
        self.assertNotIn('BROKEN_LINK',codes)
        self.assertEqual(r['pages'][EN]['qualified_anchors'],1)
        self.assertEqual(r['pages'][VI]['reachability'],'UNREACHED_IN_SCOPE')
        self.assertEqual(plan(r)['priority_counts']['P0'],1)

    def test_content_gap_language_boundary_and_no_leaked_body(self):
        self.edit(self.vi,'Hướng dẫn nhóm','<span hidden>Hướng dẫn nhóm</span>PRIVATE_SAMPLE_TEXT')
        self.edit(self.en,'</body>','<p>Hướng dẫn nhóm</p></body>')
        r=self.report(); self.assertEqual(r['topics'][1]['status'],'GAP')
        self.assertNotIn('PRIVATE_SAMPLE_TEXT',json.dumps(r))
        self.assertNotIn('Hướng dẫn nhóm',json.dumps(r,ensure_ascii=False))
        self.assertIn('TOPIC_GAP',{a['code'] for a in plan(r)['actions']})

    def test_other_page_in_same_language(self):
        # Add a third independently declared English page to the bounded scope.
        new=copy.deepcopy(self.policy['site']['pages'][0]); new.update(url=ORIGIN+'/guide/',file='pages/guide.html',canonical=ORIGIN+'/guide/',alternates={})
        self.policy['site']['pages'].append(new)
        (self.root/'pages/guide.html').write_text('<html lang="en"><head><title>Guide</title><meta name="description" content="Example"><link rel="canonical" href="'+new['url']+'"></head><body>unique guide phrase</body></html>')
        self.policy['topics'][0]['phrases']=['unique guide phrase']
        t=self.report()['topics'][0]; self.assertEqual(t['status'],'OTHER_PAGE'); self.assertEqual(t['matching_pages'],[new['url']])

    def test_incomplete_graph_does_not_claim_orphan_or_gap(self):
        self.en.unlink()
        r=self.report(); codes={f['code'] for f in r['findings']}
        self.assertFalse(r['graph_complete_in_scope']); self.assertEqual(r['pages'][VI]['reachability'],'UNKNOWN')
        self.assertEqual(r['topics'][0]['status'],'UNKNOWN')
        self.assertNotIn('NO_OBSERVED_INBOUND',codes); self.assertNotIn('UNREACHED_IN_SCOPE',codes)
        self.assertIn('REQUIRED_LINK_UNVERIFIED',codes)

    def test_base_and_symlink_fail_closed(self):
        self.edit(self.en,'</head>','<base href="https://outside.invalid/"/></head>')
        self.assertIn('INTELLIGENCE_PAGE_UNVERIFIED',{f['code'] for f in self.report()['findings']})
        outside=self.base/'outside.html'; outside.write_text('SECRET_OUTSIDE_FIXTURE')
        self.vi.unlink(); self.vi.symlink_to(outside)
        self.assertNotIn('SECRET_OUTSIDE_FIXTURE',json.dumps(self.report()))
        self.assertEqual(self.report()['eligible_verified'],0)

    def test_invalid_policy_scope_and_depth(self):
        for mutator in (lambda p:p.update(entry_pages=[ORIGIN+'/outside/']),lambda p:p.update(max_depth=True),lambda p:p['topics'][0].update(language='vi'),lambda p:p['topics'][0].update(phrases=['A','a']),lambda p:p.update(schema_profiles={EN:['Unknown']}),lambda p:p.update(required_links=[{'from':EN,'to':EN}])):
            bad=copy.deepcopy(self.policy); mutator(bad)
            with self.assertRaises(ValueError): validate_policy(bad)

    def test_portfolio_fresh_evidence_and_escaping(self):
        (self.root/'intelligence.json').write_text(json.dumps(self.policy))
        manifest=self.base/'intelligence-portfolio.json'
        manifest.write_text(json.dumps({'sites':[{'label':'<script>Sample</script>','snapshot':'snapshot','policy':'intelligence.json'}]}))
        first=portfolio(self.base); self.assertEqual(first['sites'][0]['plan']['actions'],[])
        self.edit(self.en,'href="/vi/#intro"','href="/wrong/"')
        second=portfolio(self.base); self.assertGreater(second['sites'][0]['plan']['priority_counts']['P0'],0)
        html=render(second); self.assertNotIn('<script>',html); self.assertIn('&lt;script&gt;',html); self.assertIn("default-src 'none'",html)
        manifest.write_text(json.dumps({'sites':[{'label':'Bad','snapshot':'../outside','policy':'intelligence.json'}]}))
        with self.assertRaises(ValueError): portfolio(self.base)

    def test_actual_mcp_optional_tools_and_scope(self):
        path=self.base/'intelligence.json'; path.write_text(json.dumps(self.policy))
        growth=self.base/'growth.json'; growth.write_text((self.root/'growth.json').read_text())
        args=[sys.executable,'-m','web_growth_guard.mcp_server','--root',str(self.root),'--policy',str(growth),'--intelligence-policy',str(path)]
        msgs=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'test','version':'1'}}},{'jsonrpc':'2.0','method':'notifications/initialized'},{'jsonrpc':'2.0','id':2,'method':'tools/list'}]
        for i,name,params in [(3,'intelligence_audit',{}),(4,'growth_plan',{}),(5,'growth_plan',{'root':'/etc'}),(6,'intelligence_audit',{'policy':{}})]:
            msgs.append({'jsonrpc':'2.0','id':i,'method':'tools/call','params':{'name':name,'arguments':params}})
        def wire():
            proc=subprocess.run(args,input=b''.join(json.dumps(m).encode()+b'\n' for m in msgs),capture_output=True,timeout=20)
            self.assertEqual(proc.returncode,0,proc.stderr.decode()); return [json.loads(line) for line in proc.stdout.splitlines()]
        out=wire(); self.assertEqual(len(out[1]['result']['tools']),7)
        from web_growth_guard.marketing import growth_audit, read_growth_policy
        baseline=self.base/'baseline.json'; baseline.write_text(json.dumps(growth_audit(self.root,read_growth_policy(self.root))))
        args.extend(['--baseline',str(baseline)])
        self.assertEqual(len(wire()[1]['result']['tools']),8)
        self.assertEqual(out[2]['result']['structuredContent']['findings'],[])
        self.assertEqual(out[3]['result']['structuredContent']['actions'],[])
        self.assertTrue(out[4]['result']['isError']); self.assertTrue(out[5]['result']['isError'])
        self.edit(self.en,'href="/vi/#intro"','href="/missing/"')
        self.assertGreater(wire()[3]['result']['structuredContent']['priority_counts']['P0'],0)
        bad=copy.deepcopy(self.policy); bad['site']['pages'][0]['indexable']=False; path.write_text(json.dumps(bad))
        proc=subprocess.run(args,input=b'',capture_output=True,timeout=10); self.assertEqual(proc.returncode,1)

    def test_depth_threshold_and_nested_schema_context(self):
        self.policy['max_depth']=1
        guide=copy.deepcopy(self.policy['site']['pages'][0]); guide.update(url=ORIGIN+'/guide/',file='pages/guide.html',canonical=ORIGIN+'/guide/',alternates={})
        self.policy['site']['pages'].append(guide)
        (self.root/'pages/guide.html').write_text('<html lang="en"><head><title>Guide</title><meta name="description" content="Example"><link rel="canonical" href="'+guide['url']+'"></head><body>Guide</body></html>')
        self.edit(self.vi,'</body>','<a href="/guide/">Guide</a></body>')
        self.edit(self.en,'"priceCurrency": "USD"','"priceCurrency": "USD", "@context": {"price": "https://other.invalid/price"}')
        report=self.report()
        self.assertEqual(report['pages'][guide['url']]['depth'],2)
        self.assertIn('DEPTH_THRESHOLD',{f['code'] for f in report['findings']})
        self.assertEqual(report['pages'][EN]['structured_data']['profiles']['Product']['status'],'UNKNOWN')

    def test_cli_fresh_plan_exit_codes(self):
        (self.root/'intelligence.json').write_text(json.dumps(self.policy))
        cmd=[sys.executable,'-m','web_growth_guard','growth-plan','--root',str(self.root)]
        r=subprocess.run(cmd,capture_output=True,timeout=10)
        self.assertEqual(r.returncode,0,r.stderr.decode()); self.assertEqual(json.loads(r.stdout)['actions'],[])
        self.edit(self.en,'href="/vi/#intro"','href="/wrong/"')
        r=subprocess.run(cmd,capture_output=True,timeout=10)
        self.assertEqual(r.returncode,2,r.stderr.decode()); self.assertGreater(json.loads(r.stdout)['priority_counts']['P0'],0)
