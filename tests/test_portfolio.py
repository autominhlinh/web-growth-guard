import copy
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import importlib.util
from web_growth_guard.portfolio import read_portfolio, render
from web_growth_guard.cli import main


def load_demo():
    path=Path(__file__).resolve().parents[1]/'examples/demo_portfolio.py'
    spec=importlib.util.spec_from_file_location('demo_portfolio',path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.root=Path(t.name)/'demo'
        with redirect_stdout(StringIO()): self.portfolio=load_demo().generate(self.root)

    def test_synthetic_dashboard_and_cli(self):
        sites=self.portfolio['sites']
        self.assertEqual([s['decision'] for s in sites],['CLEAR','REVIEW','BLOCK'])
        self.assertEqual(sites[-1]['bots']['OAI-SearchBot']['DISALLOWED'],1)
        html=render(self.portfolio)
        self.assertIn('default-src \'none\'',html)
        self.assertNotIn('<script',html)
        self.assertIn('HTTP_GOOGLEBOT_NOINDEX',html)
        with redirect_stdout(StringIO()) as out:
            code=main(['portfolio','--root',str(self.root),'--json'])
        self.assertEqual(code,0);self.assertEqual(json.loads(out.getvalue()),self.portfolio)

    def test_escapes_all_dynamic_text(self):
        report=copy.deepcopy(self.portfolio)
        report['sites'][0]['label']='<img src=x onerror=alert(1)>'
        report['sites'][0]['findings']=[{'severity':'warning','code':'TEST','url':'<svg>','detail':'<script>alert(1)</script>'}]
        html=render(report)
        self.assertIn('&lt;img',html);self.assertIn('&lt;script&gt;',html)
        self.assertNotIn('<img src=x',html);self.assertNotIn('<script>alert',html)

    def test_path_and_scope_controls(self):
        p=self.root/'portfolio.json';data=json.loads(p.read_text())
        data['sites'][0]['report']='../outside.json';p.write_text(json.dumps(data))
        with self.assertRaises(ValueError): read_portfolio(self.root)
        data['sites'][0]['report']='clear.json';data['sites'][0]['baseline']='wrong.json'
        bad=json.loads((self.root/'clear.json').read_text());bad['scope_sha256']='0'*64
        (self.root/'wrong.json').write_text(json.dumps(bad));p.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'scope changed'):read_portfolio(self.root)

    def test_output_never_overwrites_demo(self):
        with self.assertRaises(FileExistsError): load_demo().generate(self.root)
