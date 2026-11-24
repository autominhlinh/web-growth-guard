import copy
import io
import socket
import unittest
from pathlib import Path
from web_growth_guard.http import Collector, Response, FetchError, resolve_public, public_url, UA
from web_growth_guard.robots import Robots, matches, normalize
from web_growth_guard.discovery import discover, compare_live, header_directives, StructuredData

ORIGIN = 'https://example.invalid'
PAGES = Path(__file__).resolve().parents[1] / 'examples/demo/pages'
SITEMAP = (PAGES.parent / 'sitemap.xml').read_bytes()
POLICY = {'origin': ORIGIN, 'sitemap': ORIGIN + '/sitemap.xml', 'pages': [
    {'url': ORIGIN + '/' + lang + '/', 'canonical': ORIGIN + '/' + lang + '/',
     'indexable': True, 'language': lang, 'alternates': {'en': ORIGIN + '/en/', 'vi': ORIGIN + '/vi/'}}
    for lang in ('en', 'vi')]}


def resp(path, body=b'', status=200, content_type='text/html', extra=()):
    return Response(ORIGIN + path, status, (('Content-Type', content_type), *extra), body)


class Fixture:
    def __init__(self, robots=b'User-agent: *\nAllow: /\n'):
        self.responses = {'/robots.txt': resp('/robots.txt', robots, content_type='text/plain'),
                          '/sitemap.xml': resp('/sitemap.xml', SITEMAP, content_type='application/xml')}
        self.responses.update({f'/{lang}/': resp(f'/{lang}/', (PAGES / f'{lang}.html').read_bytes()) for lang in ('en', 'vi')})
        self.calls = []

    def get(self, url, allowed=None, limit=1024*1024):
        path = url[len(ORIGIN):]
        if allowed and not allowed(path):
            raise FetchError('ROBOTS_TOOL_DISALLOWED')
        self.calls.append(path)
        return self.responses[path]


class RobotsTests(unittest.TestCase):
    def test_exact_groups_merge_override_wildcard(self):
        r = Robots(b'User-agent: *\nDisallow: /\nUser-agent: Googlebot\nDisallow: /private\n'
                   b'Sitemap: https://example.invalid/sitemap.xml\nUser-agent: Googlebot\nAllow: /private/public\n')
        self.assertTrue(r.allowed('googlebot', '/home'))
        self.assertFalse(r.allowed('Googlebot', '/private/x'))
        self.assertTrue(r.allowed('Googlebot', '/private/public'))
        self.assertFalse(r.allowed('OAI-SearchBot', '/home'))

    def test_wildcards_end_tie_percent_case(self):
        r = Robots(b'User-agent: *\nDisallow: /*.pdf$\nDisallow: /a\nAllow: /a\nDisallow: /%62\nDisallow: /A\n')
        self.assertFalse(r.allowed('Googlebot', '/x.pdf'))
        self.assertTrue(r.allowed('Googlebot', '/x.pdf/other'))
        self.assertTrue(r.allowed('Googlebot', '/a'))
        self.assertFalse(r.allowed('Googlebot', '/b'))
        self.assertFalse(r.allowed('Googlebot', '/A'))
        self.assertFalse(matches('/a*b$', '/a'))
        self.assertTrue(matches('/a*b$', '/abb'))
        self.assertFalse(matches('/a*a$', '/a'))
        self.assertTrue(matches('/a*a$', '/aa'))
        self.assertEqual(normalize('/caf%C3%A9'), normalize('/caf\u00e9'))
        self.assertNotEqual(normalize('/a%2Fb'), normalize('/a/b'))

    def test_empty_rules_and_invalid(self):
        self.assertTrue(Robots(b'User-agent: *\nDisallow:\n').allowed('Googlebot', '/'))
        for data in (b'\xff', b'User-agent: bot/1\nDisallow: /', b'User-agent: *\nDisallow: /bad%q'):
            with self.assertRaises((ValueError, UnicodeError)):
                Robots(data)


class HTTPTests(unittest.TestCase):
    @staticmethod
    def resolver(*ips):
        return lambda *args: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443)) for ip in ips]

    def test_dns_rejects_mixed_private_and_special_addresses(self):
        for ip in ('127.0.0.1', '10.0.0.2', '169.254.169.254', '100.64.0.1', '224.0.0.1', '192.0.2.1', '0.0.0.0'):
            with self.subTest(ip=ip), self.assertRaises(FetchError):
                resolve_public('example.invalid', self.resolver('8.8.8.8', ip))
        self.assertEqual(resolve_public('example.invalid', self.resolver('8.8.8.8')), '8.8.8.8')

    def test_url_policy(self):
        for url in ('http://example.invalid/', 'https://localhost/', 'https://127.0.0.1/',
                    'https://a.internal/', 'https://example.invalid:443/', 'https://a@b.invalid/',
                    ORIGIN + '/a?secret=1', ORIGIN + '/%2e%2e/x', ORIGIN + '/a%0d', ORIGIN + '/a#x'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                public_url(url)

    def test_redirect_scope_and_robots(self):
        c = Collector(ORIGIN, pause=lambda _: None)
        calls = []
        def once(url, limit):
            calls.append(url)
            return resp('/a', status=302, extra=(('Location', 'https://other.invalid/'),))
        c.once = once
        with self.assertRaisesRegex(FetchError, 'OUT_OF_SCOPE'):
            c.get(ORIGIN + '/a')
        self.assertEqual(calls, [ORIGIN + '/a'])
        c.once = lambda url, limit: resp('/a', status=302, extra=(('Location', '/blocked'),))
        with self.assertRaisesRegex(FetchError, 'ROBOTS_TOOL_DISALLOWED'):
            c.get(ORIGIN + '/a', allowed=lambda p: p != '/blocked')
        c.once = lambda url, limit: resp('/a', status=302, extra=(('Location', '/a'),))
        with self.assertRaisesRegex(FetchError, 'REDIRECT_LOOP'):
            c.get(ORIGIN + '/a')

    def test_numeric_pin_headers_and_byte_limits(self):
        class Wire:
            status = 200
            def __init__(self, body, headers):
                self.body = io.BytesIO(body)
                self.headers = headers
            def getheaders(self): return self.headers
            def getheader(self, key, default): return dict(self.headers).get(key, default)
            def read1(self, n): return self.body.read(n)
        seen = []
        class Connection:
            def __init__(self, host, addr, timeout): seen.append((host, addr))
            def request(self, method, path, headers): seen.append((method, path, headers))
            def getresponse(self): return wire
            def close(self): seen.append('closed')
        c = Collector(ORIGIN, resolver=self.resolver('8.8.8.8'), connection=Connection, pause=lambda _: None)
        wire = Wire(b'hello', [('Content-Length', '5')])
        self.assertEqual(c.get(ORIGIN+'/page').body, b'hello')
        self.assertEqual(seen[0], ('example.invalid', '8.8.8.8'))
        self.assertEqual(seen[1][0:2], ('GET', '/page'))
        self.assertEqual(seen[1][2]['User-Agent'], UA)
        self.assertNotIn('Cookie', seen[1][2]); self.assertNotIn('Authorization', seen[1][2])
        for body, headers, error in ((b'123456', [], 'BODY_BUDGET'), (b'hi', [('Content-Length','5')], 'INCOMPLETE_BODY'),
                                      (b'hello', [('Content-Encoding','gzip')], 'ENCODING_UNSUPPORTED')):
            wire = Wire(body, headers)
            with self.assertRaisesRegex(FetchError, error):
                c.get(ORIGIN+'/page', limit=5)
        c.requests = 32
        with self.assertRaisesRegex(FetchError, 'REQUEST_BUDGET'):
            c.get(ORIGIN+'/page')


class DiscoveryTests(unittest.TestCase):
    def codes(self, report): return {x['code'] for x in report['findings']}

    def test_clean_fixture_and_stable_scope(self):
        a = discover(POLICY, Fixture())
        self.assertEqual(a['pages_verified'], 2)
        self.assertEqual(a['findings'], [])
        changed_order = copy.deepcopy(POLICY); changed_order['pages'].reverse()
        b = discover(changed_order, Fixture())
        self.assertEqual(compare_live(a, b)['decision'], 'CLEAR')

    def test_searchbot_separate_from_gptbot(self):
        a = discover(POLICY, Fixture(b'User-agent: GPTBot\nDisallow: /\n'))
        self.assertEqual(a['findings'], [])
        self.assertEqual(a['pages'][ORIGIN+'/vi/']['robots']['GPTBot'], 'DISALLOWED')
        b = discover(POLICY, Fixture(b'User-agent: OAI-SearchBot\nDisallow: /vi/\n'))
        self.assertIn('OAI_SEARCHBOT_ROBOTS_DISALLOWED', self.codes(b))
        self.assertEqual(compare_live(a,b)['decision'], 'BLOCK')

    def test_robots_unavailable_and_tool_disallowed(self):
        f = Fixture(); f.responses['/robots.txt'] = resp('/robots.txt', status=503)
        a = discover(POLICY, f)
        self.assertEqual(f.calls, ['/robots.txt'])
        self.assertEqual(a['pages_verified'], 0)
        self.assertIn('ROBOTS_UNVERIFIABLE', self.codes(a))
        f = Fixture(b'User-agent: WebGrowthGuard\nDisallow: /vi/\n')
        b = discover(POLICY, f)
        self.assertNotIn('/vi/', f.calls)
        self.assertIn('ROBOTS_TOOL_DISALLOWED', self.codes(b))
        self.assertEqual(b['pages_verified'], 1)

    def test_header_scope_jsonld_redirect_and_mime(self):
        flags = header_directives(['otherbot: noindex, none', 'googlebot: noindex', 'max-snippet: 10'])
        self.assertEqual(flags, {'all': False, 'googlebot': True, 'oai-searchbot': False})
        f = Fixture()
        f.responses['/vi/'] = resp('/vi/', f.responses['/vi/'].body.replace(b'</head>', b'<script type="application/ld+json">{broken}</script></head>'),
                                   extra=(('X-Robots-Tag', 'noindex'),))
        a = discover(POLICY, f)
        self.assertTrue({'HTTP_NOINDEX','JSONLD_INVALID'} <= self.codes(a))
        f.responses['/vi/'] = Response(ORIGIN+'/en/', 200, (('Content-Type','text/html'),), b'hello', ({'status':301,'to':ORIGIN+'/en/'},))
        self.assertIn('PAGE_REDIRECTED', self.codes(discover(POLICY,f)))
        f.responses['/vi/'] = resp('/vi/', b'hello', content_type='text/plain')
        self.assertIn('PAGE_HTTP_UNVERIFIABLE', self.codes(discover(POLICY,f)))

    def test_404_robots_and_incomplete_sitemap(self):
        f = Fixture(); f.responses['/robots.txt'] = resp('/robots.txt',status=404)
        self.assertEqual(discover(POLICY,f)['robots']['status'],'NOT_FOUND')
        f.responses['/sitemap.xml'] = resp('/sitemap.xml',status=500)
        self.assertIn('SITEMAP_HTTP_UNVERIFIABLE',self.codes(discover(POLICY,f)))

    def test_bad_policy_and_scope(self):
        p = copy.deepcopy(POLICY); p['pages'][0]['url'] = 'https://other.invalid/'
        with self.assertRaises(ValueError): discover(p, Fixture())
        p = copy.deepcopy(POLICY); p['pages'].pop()
        a,b = discover(POLICY, Fixture()), discover(p, Fixture())
        with self.assertRaises(ValueError): compare_live(a,b)
        b = copy.deepcopy(a); b['pages_verified']=1
        with self.assertRaisesRegex(ValueError, 'Inconsistent live report coverage'):
            compare_live(a,b)

    def test_raw_content_not_exposed(self):
        import json
        f=Fixture(); f.responses['/vi/']=resp('/vi/', f.responses['/vi/'].body+b'PRIVATE_FIXTURE_MARKER', extra=(('Set-Cookie','PRIVATE_FIXTURE_COOKIE'),))
        report=json.dumps(discover(POLICY,f))
        self.assertNotIn('PRIVATE_FIXTURE_MARKER',report)
        self.assertNotIn('PRIVATE_FIXTURE_COOKIE',report)
        self.assertEqual(StructuredData('<script type>foo</script><script type="application/ld+json">{"@type":"Thing"}</script>').invalid,0)

    def test_collection_mode_isolation_and_gate_refuses_synthetic_baseline(self):
        import tempfile, json
        from contextlib import redirect_stderr
        from unittest.mock import patch
        from web_growth_guard.cli import main
        a=discover(POLICY, Fixture())
        self.assertEqual(a['collection_mode'],'injected_transport')
        changed=copy.deepcopy(a);changed['collection_mode']='public_https'
        with self.assertRaisesRegex(ValueError,'Collection mode changed'):
            compare_live(a,changed)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'baseline.json';path.write_text(json.dumps(a))
            with patch('web_growth_guard.cli.discover') as collect, redirect_stderr(io.StringIO()):
                result=main(['live-gate','--baseline',str(path),'--policy','unused.json'])
            self.assertEqual(result,1);collect.assert_not_called()
