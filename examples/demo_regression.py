"""Show a clean baseline and a broken synthetic release in a temporary directory."""
from pathlib import Path
from tempfile import TemporaryDirectory
from shutil import copytree
from web_growth_guard.core import audit, compare

with TemporaryDirectory(prefix="wgg-demo-") as tmp:
    root = Path(tmp) / "snapshot"
    copytree(Path(__file__).parent / "demo", root)
    baseline = audit(root)
    assert not baseline["findings"], baseline
    page = root / "pages" / "vi.html"
    page.write_text(page.read_text().replace("</head>", '<meta name="robots" content="noindex"></head>').replace(
        'rel="canonical" href="https://example.invalid/vi/"', 'rel="canonical" href="https://example.invalid/en/"'))
    result = compare(baseline, audit(root))
    assert result["decision"] == "BLOCK"
    print("Baseline: no findings in two synthetic pages")
    print("Candidate:", result["decision"])
    for finding in result["added"]:
        print(finding["code"], finding["url"])
