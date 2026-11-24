"""Read-only CLI: output evidence JSON to stdout, never edit pages."""
import argparse
import json
import sys
from pathlib import Path
from . import __version__
from .core import audit, compare
from .discovery import discover, compare_live
from .portfolio import read_portfolio, render
from .intelligence import analyze, plan, portfolio as intelligence_portfolio
from .intelligence_view import render as render_intelligence
from .analytics import analyze as analytics_analyze, portfolio as analytics_portfolio
from .analytics_view import render as render_analytics
from .core import Snapshot
from .marketing import (campaign_url, campaign_batch, read_growth_policy, growth_audit,
                        growth_compare, preview, UTM_REQUIRED, UTM_OPTIONAL)


def read_report(path):
    with Path(path).open("rb") as stream:
        data = stream.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise ValueError("Audit report too large")
    return json.loads(data)


def main(argv=None):
    p = argparse.ArgumentParser(description="Evidence-based website growth regression checks")
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("audit")
    a.add_argument("--root", required=True)
    a.add_argument("--manifest", default="manifest.json")
    c = sub.add_parser("compare")
    c.add_argument("--baseline", required=True)
    c.add_argument("--current", required=True)
    g = sub.add_parser("gate", help="Audit the candidate directly against an operator-reviewed baseline")
    g.add_argument("--baseline", required=True)
    g.add_argument("--root", required=True)
    g.add_argument("--manifest", default="manifest.json")
    d = sub.add_parser("discover", help="Read-only same-origin public HTTPS audit")
    d.add_argument("--policy", required=True)
    lg = sub.add_parser("live-gate", help="Collect fresh HTTP evidence against a trusted baseline")
    lg.add_argument("--policy", required=True)
    lg.add_argument("--baseline", required=True)
    lc = sub.add_parser("live-compare")
    lc.add_argument("--baseline", required=True)
    lc.add_argument("--current", required=True)
    po = sub.add_parser("portfolio", help="Offline multi-site dashboard from saved reports")
    po.add_argument("--root", required=True)
    po.add_argument("--manifest", default="portfolio.json")
    po.add_argument("--json", action="store_true")
    utm = sub.add_parser("campaign", help="Construct a tagged HTTPS URL; never visits it")
    utm.add_argument("--destination", required=True)
    for name in UTM_REQUIRED + UTM_OPTIONAL:
        utm.add_argument("--" + name.replace("_", "-"), required=name in UTM_REQUIRED)
    batch = sub.add_parser("campaign-batch")
    batch.add_argument("--input", required=True)
    ga = sub.add_parser("growth-audit")
    gg = sub.add_parser("growth-gate")
    gc = sub.add_parser("growth-compare")
    sp = sub.add_parser("social-preview")
    for parser in (ga, gg, sp):
        parser.add_argument("--root", required=True)
        parser.add_argument("--policy", default="growth.json")
    gg.add_argument("--baseline", required=True)
    gc.add_argument("--baseline", required=True)
    gc.add_argument("--current", required=True)
    sp.add_argument("--url", required=True)
    for name in ("intelligence-audit", "growth-plan", "intelligence-portfolio"):
        ip = sub.add_parser(name)
        ip.add_argument("--root", required=True)
        ip.add_argument("--policy", default="intelligence-portfolio.json" if name == "intelligence-portfolio" else "intelligence.json")
        if name == "intelligence-portfolio":
            ip.add_argument("--html", action="store_true")
    for name in ("analytics-audit", "analytics-portfolio"):
        ap = sub.add_parser(name, help="Analyze operator-selected offline aggregate CSV exports")
        ap.add_argument("--root", required=True)
        ap.add_argument("--policy", default="analytics-portfolio.json" if name == "analytics-portfolio" else "analytics.json")
        ap.add_argument("--html", action="store_true")
    args = p.parse_args(argv)
    try:
        if args.command in ("analytics-audit", "analytics-portfolio"):
            cfg = json.loads(Snapshot(args.root).read(args.policy))
            result = analytics_portfolio(args.root, cfg) if args.command == "analytics-portfolio" else analytics_analyze(args.root, cfg)
            if args.html:
                print(render_analytics(result))
                return 0
            status = 0
        elif args.command in ("intelligence-audit", "growth-plan", "intelligence-portfolio"):
            if args.command == "intelligence-portfolio":
                result = intelligence_portfolio(args.root, args.policy)
                if args.html:
                    print(render_intelligence(result))
                    return 0
                status = 0
            else:
                cfg = json.loads(Snapshot(args.root).read(args.policy))
                report = analyze(args.root, cfg)
                result = report if args.command == "intelligence-audit" else plan(report)
                status = 2 if any(f["severity"] == "error" for f in report["findings"]) else 0
        elif args.command == "campaign":
            result = campaign_url(args.destination, {key: getattr(args, key) for key in UTM_REQUIRED + UTM_OPTIONAL if getattr(args, key) is not None})
            status = 0
        elif args.command == "campaign-batch":
            result = campaign_batch(read_report(args.input))
            status = 0
        elif args.command in ("growth-audit", "growth-gate", "growth-compare", "social-preview"):
            if args.command == "growth-compare":
                result = growth_compare(read_report(args.baseline), read_report(args.current))
            else:
                config = read_growth_policy(args.root, args.policy)
                if args.command == "social-preview":
                    result = preview(args.root, config, args.url)
                else:
                    result = growth_audit(args.root, config)
                    if args.command == "growth-gate":
                        result = growth_compare(read_report(args.baseline), result)
            if "decision" in result:
                status = {"BLOCK": 2, "REVIEW": 3, "CLEAR": 0}[result["decision"]]
            elif args.command == "growth-audit":
                status = 2 if any(f["severity"] == "error" for f in result["findings"]) else 0
            else:
                status = 0
        elif args.command == "portfolio":
            result = read_portfolio(args.root, args.manifest)
            if not args.json:
                print(render(result))
                return 0
            status = 0
        elif args.command == "discover":
            result = discover(read_report(args.policy))
            status = 2 if any(f["severity"] == "error" for f in result["findings"]) or result["pages_expected"] != result["pages_verified"] else 0
        elif args.command in ("live-gate", "live-compare"):
            baseline = read_report(args.baseline)
            if args.command == "live-gate" and baseline.get("collection_mode") != "public_https":
                raise ValueError("Live gate requires an operator-reviewed public HTTPS baseline")
            current = discover(read_report(args.policy)) if args.command == "live-gate" else read_report(args.current)
            result = compare_live(baseline, current)
            status = {"BLOCK": 2, "REVIEW": 3, "CLEAR": 0}[result["decision"]]
        elif args.command == "audit":
            result = audit(args.root, args.manifest)
            status = 2 if any(f["severity"] == "error" for f in result["findings"]) else 0
        else:
            current = audit(args.root, args.manifest) if args.command == "gate" else read_report(args.current)
            result = compare(read_report(args.baseline), current)
            status = {"BLOCK": 2, "REVIEW": 3, "CLEAR": 0}[result["decision"]]
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
        return status
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print("Check rejected: " + str(exc), file=sys.stderr)
        return 1
