import json
import os
import tempfile
import unittest
from pathlib import Path
from shutil import copytree
from contextlib import redirect_stdout
from io import StringIO
from web_growth_guard.marketing import (campaign_url, campaign_batch, validate_growth,
    growth_audit, growth_compare, preview, read_growth_policy)
from web_growth_guard.cli import main

ORIGIN = 'https://example.invalid'
EN, VI = ORIGIN + '/en/', ORIGIN + '/vi/'
TAGS = {'source': 'newsletter', 'medium': 'email', 'campaign': 'sample_launch'}


class CampaignTests(unittest.TestCase):
    def test_deterministic_utm_and_optional_fields(self):
        item = campaign_url(EN, dict(TAGS, content='hero_a', id='launch_1'))
        self.assertEqual(item['url'], EN + '?utm_source=newsletter&utm_medium=email&utm_campaign=sample_launch&utm_id=launch_1&utm_content=hero_a')
        self.assertEqual(item, campaign_url(EN, dict(reversed(list(dict(TAGS, content='hero_a', id='launch_1').items())))))

    def test_reject_unsafe_or_ambiguous_destinations_and_tags(self):
        for url in ('http://example.invalid/', 'https://' + 'user:pass' + '@example.invalid/',
                    'https://127.0.0.1/', 'https://example.invalid/?token=sample',
                    'https://example.invalid/?', 'https://example.invalid/#', 'https://example.invalid/#x',
                    'https://example.invalid/%0a', 'https://example.invalid:443/', 'javascript:sample'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                campaign_url(url, TAGS)
        for tags in ({'source':'a'}, dict(TAGS, source='UpperCase'), dict(TAGS, source='x&bad=y'),
                     dict(TAGS, source='sample@example.invalid'), dict(TAGS, token='test'), dict(TAGS, source='x'*81)):
            with self.subTest(tags=tags), self.assertRaises(ValueError):
                campaign_url(EN, tags)

    def test_batch_duplicates_and_scope(self):
        rows = [{'destination': EN, 'tags': TAGS}, {'destination': VI, 'tags': TAGS}]
        self.assertEqual(len(campaign_batch(rows)['campaigns']), 2)
        for bad in ([], rows * 26, rows + rows):
            with self.assertRaises(ValueError):
                campaign_batch(bad)
        with self.assertRaises(ValueError):
            campaign_batch(rows, {EN})


@unittest.skipUnless(os.name == 'posix', 'POSIX filesystem required')
class GrowthTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = self.base / 'snapshot'
        copytree(Path(__file__).resolve().parents[1] / 'examples' / 'growth', self.root)
        self.policy = read_growth_policy(self.root)
        self.page = self.root / 'pages' / 'vi.html'

    def edit(self, old, new):
        text = self.page.read_text()
        self.assertIn(old, text)
        self.page.write_text(text.replace(old, new))

    def report(self):
        return growth_audit(self.root, self.policy)

    def codes(self):
        return {row['code'] for row in self.report()['findings']}

    def test_clean_policy_deterministic_and_social_preview(self):
        report = self.report()
        self.assertEqual(report['findings'], [])
        self.assertEqual(report, self.report())
        self.assertEqual(growth_compare(report, report)['decision'], 'CLEAR')
        self.assertEqual(report['landings'][VI]['cta_matched'], 1)
        data = preview(self.root, self.policy, VI)
        self.assertEqual(data['fields']['og:url'], VI)
        self.assertEqual(data['text_trust'], 'untrusted')
        self.assertNotIn('fields', report['landings'][VI])
        with self.assertRaises(ValueError):
            preview(self.root, self.policy, ORIGIN + '/other/')

    def test_cta_removed_or_inert_blocks_gate(self):
        old = self.report()
        link = '<a href="/vi/join/">Join example community</a>'
        self.edit(link, '<template>' + link + '</template>')
        current = self.report()
        self.assertIn('LANDING_CTA_MISSING', self.codes())
        self.assertEqual(growth_compare(old, current)['decision'], 'BLOCK')
        self.assertEqual(current['landings'][VI]['cta_matched'], 0)

    def test_bad_social_url_duplicate_and_missing_image(self):
        self.edit('content="https://example.invalid/vi/"', 'content="https://example.invalid/en/"')
        self.edit('</head>', '<meta property="og:title" content="Duplicate"></head>')
        self.edit('https://example.invalid/preview.png', 'javascript:sample')
        self.assertTrue({'SOCIAL_OG_URL_INVALID', 'SOCIAL_OG_TITLE_INVALID', 'SOCIAL_OG_IMAGE_INVALID'} <= self.codes())

    def test_cluster_extra_language_relative_link_and_lang(self):
        self.edit('</head>', '<link rel="alternate" hreflang="fr" href="https://example.invalid/fr/"></head>')
        self.edit('lang="vi"', 'lang="en"')
        self.assertTrue({'CLUSTER_MEMBERSHIP_DRIFT', 'CLUSTER_LANGUAGE_DRIFT'} <= self.codes())
        self.edit('hreflang="en" href="https://example.invalid/en/"', 'hreflang="en" href="/en/"')
        self.assertIn('CLUSTER_MEMBERSHIP_DRIFT', self.codes())

    def test_cluster_declared_invariants(self):
        for field, value in (('indexable', False), ('canonical', EN), ('alternates', {})):
            cfg = json.loads(json.dumps(self.policy))
            next(p for p in cfg['site']['pages'] if p['url'] == VI)[field] = value
            with self.assertRaises(ValueError):
                validate_growth(cfg)
        cfg = json.loads(json.dumps(self.policy))
        cfg['clusters'][0]['x_default'] = EN
        with self.assertRaises(ValueError):
            validate_growth(cfg)
        for page in cfg['site']['pages']:
            page['alternates']['x-default'] = EN
        validate_growth(cfg)
        self.assertIn('CLUSTER_MEMBERSHIP_DRIFT', {r['code'] for r in growth_audit(self.root, cfg)['findings']})

    def test_warning_and_unavailable_page(self):
        old = self.report()
        self.edit('width=device-width', 'width=1000')
        self.assertEqual(growth_compare(old, self.report())['decision'], 'REVIEW')
        self.page.unlink()
        current = self.report()
        self.assertEqual(growth_compare(old, current)['decision'], 'BLOCK')
        self.assertIn('LANDING_UNVERIFIED', self.codes())

    def test_policy_drift_rejected(self):
        old = self.report()
        cfg = json.loads(json.dumps(self.policy))
        cfg['landings'][0]['cta_targets'] = [ORIGIN + '/other/']
        with self.assertRaisesRegex(ValueError, 'policy changed'):
            growth_compare(old, growth_audit(self.root, cfg))

    def test_cli_gate_uses_fresh_markup(self):
        baseline = self.base / 'baseline.json'
        baseline.write_text(json.dumps(self.report()))
        self.edit('href="/vi/join/"', 'href="/unapproved/"')
        output = StringIO()
        with redirect_stdout(output):
            status = main(['growth-gate', '--root', str(self.root), '--baseline', str(baseline)])
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(output.getvalue())['decision'], 'BLOCK')

    def test_symlink_page_is_not_read(self):
        self.page.unlink()
        outside = self.base / 'outside.html'
        outside.write_text('PRIVATE_FIXTURE_MARKER')
        self.page.symlink_to(outside)
        data = self.report()
        self.assertNotIn(VI, data['landings'])
        self.assertNotIn('PRIVATE_FIXTURE_MARKER', json.dumps(data))
        with self.assertRaises(ValueError):
            preview(self.root, self.policy, VI)

    def test_template_metadata_does_not_count_as_social_evidence(self):
        self.edit('<meta property="og:title" content="Example directory">',
                  '<template><meta property="og:title" content="Fake template title"></template>')
        self.assertIn('SOCIAL_OG_TITLE_INVALID', self.codes())
        self.assertNotIn('og:title', preview(self.root, self.policy, VI)['fields'])

    def test_growth_portfolio_includes_cta_evidence(self):
        from web_growth_guard.portfolio import read_portfolio, render
        root = self.base / 'reports'
        root.mkdir()
        old = self.report()
        self.edit('href="/vi/join/"', 'href="/unapproved/"')
        current = self.report()
        (root / 'baseline.json').write_text(json.dumps(old))
        (root / 'current.json').write_text(json.dumps(current))
        (root / 'portfolio.json').write_text(json.dumps({'sites': [{'label': 'Synthetic growth', 'report': 'current.json', 'baseline': 'baseline.json'}]}))
        port = read_portfolio(root)
        self.assertEqual(port['sites'][0]['decision'], 'BLOCK')
        self.assertEqual(port['sites'][0]['growth']['cta_matched'], 1)
        self.assertIn('CTA markup: 1/2', render(port))
        current['landings'][VI]['cta_matched'] = 999
        (root / 'current.json').write_text(json.dumps(current))
        with self.assertRaises(ValueError):
            read_portfolio(root)
