"""Actual subprocess MCP JSON-RPC wire tests, with no external SDK dependency."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from shutil import copytree
from web_growth_guard.marketing import read_growth_policy, growth_audit
from web_growth_guard.mcp_server import MAX_FRAME, strict_json
from web_growth_guard import __version__

ORIGIN = 'https://example.invalid'
VI = ORIGIN + '/vi/'
TAGS = {'source': 'newsletter', 'medium': 'email', 'campaign': 'sample_launch'}


@unittest.skipUnless(os.name == 'posix', 'POSIX required')
class WireTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = self.base / 'snapshot'
        copytree(Path(__file__).resolve().parents[1] / 'examples' / 'growth', self.root)
        self.config = read_growth_policy(self.root)
        self.policy = self.base / 'policy.json'
        self.policy.write_text(json.dumps(self.config))
        self.baseline = self.base / 'baseline.json'
        self.baseline.write_text(json.dumps(growth_audit(self.root, self.config)))

    def run_wire(self, requests, *, baseline=True, policy=None, extra=b''):
        args = [sys.executable, '-m', 'web_growth_guard.mcp_server', '--root', str(self.root),
                '--policy', str(policy or self.policy)]
        if baseline:
            args += ['--baseline', str(self.baseline)]
        raw = b''.join(json.dumps(m).encode() + b'\n' for m in requests) + extra
        p = subprocess.run(args, input=raw, capture_output=True, timeout=20)
        self.assertEqual(p.returncode, 0, p.stderr.decode())
        return [json.loads(line) for line in p.stdout.splitlines()]

    @staticmethod
    def req(i, method, params=None):
        return {'jsonrpc': '2.0', 'id': i, 'method': method, 'params': params or {}}

    def start(self, version='2025-11-25'):
        return [self.req(1, 'initialize', {'protocolVersion': version, 'capabilities': {},
                                           'clientInfo': {'name': 'wire-test', 'version': '1'}}),
                {'jsonrpc': '2.0', 'method': 'notifications/initialized'}]

    def call(self, i, name, **args):
        return self.req(i, 'tools/call', {'name': name, 'arguments': args})

    def test_all_six_tools_on_real_wire(self):
        messages = self.start() + [self.req(2, 'tools/list'), self.call(3, 'list_landings'),
            self.call(4, 'growth_audit'), self.call(5, 'growth_gate'), self.call(6, 'social_preview', url=VI),
            self.call(7, 'campaign_url', destination=VI, tags=TAGS),
            self.call(8, 'campaign_batch', rows=[{'destination': VI, 'tags': TAGS}])]
        out = self.run_wire(messages)
        self.assertEqual(len(out), 8)
        self.assertEqual(out[0]['result']['protocolVersion'], '2025-11-25')
        self.assertEqual(out[0]['result']['serverInfo']['version'], __version__)
        self.assertEqual(len(out[1]['result']['tools']), 6)
        self.assertEqual({t['name'] for t in out[1]['result']['tools']}, {'list_landings','growth_audit','growth_gate','social_preview','campaign_url','campaign_batch'})
        for row in out[2:]:
            self.assertFalse(row['result']['isError'])
            self.assertEqual(json.loads(row['result']['content'][0]['text']), row['result']['structuredContent'])
        self.assertEqual(out[4]['result']['structuredContent']['decision'], 'CLEAR')
        self.assertEqual(out[5]['result']['structuredContent']['text_trust'], 'untrusted')
        self.assertTrue(out[6]['result']['structuredContent']['url'].startswith(VI+'?utm_source='))

    def test_restart_fresh_audit_detects_cta_regression(self):
        start = self.start() + [self.call(2, 'growth_gate')]
        self.assertEqual(self.run_wire(start)[1]['result']['structuredContent']['decision'], 'CLEAR')
        page = self.root / 'pages' / 'vi.html'
        page.write_text(page.read_text().replace('href="/vi/join/"', 'href="/unapproved/"'))
        data = self.run_wire(start)[1]['result']['structuredContent']
        self.assertEqual(data['decision'], 'BLOCK')
        self.assertIn('LANDING_CTA_MISSING', {x['code'] for x in data['current_errors']})

    def test_tools_reject_new_scope_paths_and_forged_reports(self):
        calls = [self.call(2, 'campaign_url', destination='https://other.invalid/', tags=TAGS),
                 self.call(3, 'social_preview', url='https://other.invalid/'),
                 self.call(4, 'growth_audit', root='/etc'),
                 self.call(5, 'growth_gate', baseline={'findings': []}),
                 self.call(6, 'campaign_batch', rows=[{'destination': VI, 'tags': dict(TAGS, source='bad&x')}]),
                 self.call(7, 'shell', command='echo test'),
                 self.call(8, 'growth_audit', manifest='../secret')]
        self.assertTrue(all(x['result']['isError'] for x in self.run_wire(self.start() + calls)[1:]))

    def test_negotiation_notifications_and_optional_baseline(self):
        out = self.run_wire([self.req(0, 'tools/list')] + self.start('2025-06-18') +
                            [self.req(2, 'tools/list'), self.call(3, 'growth_gate'), self.req(4, 'ping')], baseline=False)
        self.assertEqual(out[0]['error']['code'], -32002)
        self.assertEqual(out[1]['result']['protocolVersion'], '2025-06-18')
        self.assertEqual(len(out[2]['result']['tools']), 5)
        self.assertTrue(out[3]['result']['isError'])
        self.assertEqual(out[4]['result'], {})
        out = self.run_wire([self.req(0, 'server/discover')] + self.start('2026-07-28'))
        self.assertEqual(out[0]['error']['code'], -32601)
        self.assertEqual(out[1]['result']['protocolVersion'], '2025-11-25')

    def test_duplicate_json_batch_and_oversized_frame(self):
        extra = b'{"jsonrpc":"2.0","id":1,"id":2,"method":"ping"}\n[{}]\n'
        out = self.run_wire([], extra=extra)
        self.assertEqual([x['error']['code'] for x in out], [-32700, -32600])
        self.assertEqual(self.run_wire([], extra=b'x' * (MAX_FRAME + 1) + b'\n'), [])

    def test_nonfinite_json_exponents_rejected(self):
        for value in (b'NaN', b'Infinity', b'-Infinity', b'1e999', b'-1e999'):
            with self.assertRaises(ValueError):
                strict_json(b'{"value":' + value + b'}')
        self.assertEqual(strict_json(b'{"value":1.25}'), {'value':1.25})
        extra = b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"value":1e999}}\n'
        out = self.run_wire([], extra=extra)
        self.assertEqual(out[0]['error']['code'], -32700)

    def test_policy_must_be_outside_snapshot_and_baseline_match(self):
        args = [sys.executable, '-m', 'web_growth_guard.mcp_server', '--root', str(self.root),
                '--policy', str(self.root / 'growth.json')]
        p = subprocess.run(args, input=b'', capture_output=True, timeout=10)
        self.assertEqual(p.returncode, 1)
        self.assertFalse(p.stdout)
        doc = json.loads(self.baseline.read_text())
        doc['scope_sha256'] = '0'*64
        self.baseline.write_text(json.dumps(doc))
        args[-1] = str(self.policy)
        p = subprocess.run(args + ['--baseline', str(self.baseline)], input=b'', capture_output=True, timeout=10)
        self.assertEqual(p.returncode, 1)
        self.assertFalse(p.stdout)

    def test_server_reads_no_arbitrary_file_or_credentials(self):
        outside = self.base / 'outside.html'
        outside.write_text('PRIVATE_FIXTURE_MARKER')
        page = self.root / 'pages' / 'vi.html'
        page.unlink()
        page.symlink_to(outside)
        out = self.run_wire(self.start() + [self.call(2, 'growth_gate'), self.call(3, 'social_preview', url=VI)])
        self.assertEqual(out[1]['result']['structuredContent']['decision'], 'BLOCK')
        self.assertTrue(out[2]['result']['isError'])
        self.assertNotIn('PRIVATE_FIXTURE_MARKER', json.dumps(out))
