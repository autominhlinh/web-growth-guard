import json
import os
import tempfile
import unittest
from pathlib import Path
from shutil import copytree
from web_growth_guard.core import audit, compare, Snapshot


@unittest.skipUnless(os.name == "posix", "POSIX required")
class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "snapshot"
        copytree(Path(__file__).resolve().parents[1] / "examples" / "demo", self.root)
        self.page = self.root / "pages" / "vi.html"

    def change(self, old, new):
        raw = self.page.read_text()
        assert old in raw
        self.page.write_text(raw.replace(old, new))

    def codes(self):
        return {f["code"] for f in audit(self.root)["findings"]}

    def test_clean_and_repeatable(self):
        a = audit(self.root)
        self.assertEqual(a, audit(self.root))
        self.assertEqual(a["pages_verified"], 2)
        self.assertEqual(a["findings"], [])
        self.assertEqual(compare(a, a)["decision"], "CLEAR")

    def test_noindex_regression(self):
        old = audit(self.root)
        self.change("</head>", '<meta name="robots" content="nofollow, noindex"></head>')
        report = compare(old, audit(self.root))
        self.assertEqual(report["decision"], "BLOCK")
        self.assertIn("NOINDEX", {x["code"] for x in report["added"]})
        self.assertEqual(compare(audit(self.root), old)["decision"], "CLEAR")

    def test_agent_specific_directive(self):
        self.change("</head>", '<meta name="googlebot" content="none"><meta name="OAI-SearchBot" content="noindex"></head>')
        self.assertTrue({"GOOGLEBOT_NOINDEX", "OAI_SEARCHBOT_NOINDEX"} <= self.codes())

    def test_canonical_and_base(self):
        self.change('rel="canonical" href="https://example.invalid/vi/"', 'rel="canonical" href="https://example.invalid/en/"')
        self.assertIn("CANONICAL_MISMATCH", self.codes())
        self.change("</head>", '<base href="https://example.invalid/elsewhere/"></head>')
        self.assertIn("BASE_HREF_UNSUPPORTED", self.codes())

    def test_alternate_reciprocity(self):
        self.change('<link rel="alternate" hreflang="en" href="https://example.invalid/en/">', '')
        self.assertIn("HREFLANG_RECIPROCITY", self.codes())

    def test_sitemap_absent_page(self):
        p = self.root / "sitemap.xml"
        p.write_text(p.read_text().replace('<url><loc>https://example.invalid/vi/</loc></url>', ''))
        self.assertIn("SITEMAP_PAGE_MISSING", self.codes())

    def test_sitemap_entities_rejected(self):
        (self.root / "sitemap.xml").write_text('<!DOCTYPE x [<!ENTITY z "data">]><x>&z;</x>')
        self.assertIn("SITEMAP_UNVERIFIABLE", self.codes())

    def test_warning_requires_review(self):
        old = audit(self.root)
        self.change('<meta property="og:title" content="Example directory">', '')
        self.assertEqual(compare(old, audit(self.root))["decision"], "REVIEW")

    def test_existing_error_blocks(self):
        self.change("</head>", '<meta name="robots" content="noindex"></head>')
        bad = audit(self.root)
        self.assertEqual(compare(bad, bad)["decision"], "BLOCK")

    def test_scope_drift_rejected(self):
        old = audit(self.root)
        f = self.root / "manifest.json"
        data = json.loads(f.read_text())
        data["pages"].pop()
        f.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "Scope changed"):
            compare(old, audit(self.root))

    def test_unavailable_page_blocks(self):
        self.page.unlink()
        report = audit(self.root)
        self.assertEqual(report["pages_verified"], 1)
        self.assertIn("HTML_UNVERIFIABLE", {f["code"] for f in report["findings"]})

    def test_traversal_symlink_hardlink_and_fifo(self):
        outside = Path(self.tmp.name) / "outside.html"
        outside.write_text("Synthetic outside content")
        (self.root / "link.html").symlink_to(outside)
        os.link(outside, self.root / "hard.html")
        os.mkfifo(self.root / "fifo.html")
        for path in ("../outside.html", "link.html", "hard.html", "fifo.html", "/etc/passwd"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                Snapshot(self.root).read(path)

    def test_size_limit(self):
        self.page.write_bytes(b"x" * (1024 * 1024 + 1))
        self.assertIn("HTML_UNVERIFIABLE", self.codes())

    def test_all_hreflang_links_removed(self):
        for page in (self.root / "pages").glob("*.html"):
            page.write_text("\n".join(line for line in page.read_text().splitlines() if 'hreflang=' not in line))
        self.assertIn("HREFLANG_EXPECTED_MISMATCH", self.codes())

    def test_incomplete_report_cannot_clear_gate(self):
        old = audit(self.root)
        current = json.loads(json.dumps(old))
        current["pages"].pop("https://example.invalid/vi/")
        current["pages_verified"] = 1
        self.assertEqual(compare(old, current)["decision"], "BLOCK")

    def test_gate_audits_candidate_directly(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from web_growth_guard.cli import main
        baseline = Path(self.tmp.name) / "baseline.json"
        baseline.write_text(json.dumps(audit(self.root)))
        self.change("</head>", '<meta name="robots" content="noindex"></head>')
        buf = StringIO()
        with redirect_stdout(buf):
            code = main(["gate", "--baseline", str(baseline), "--root", str(self.root)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(buf.getvalue())["decision"], "BLOCK")
