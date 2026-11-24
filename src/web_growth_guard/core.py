"""Bounded offline HTML snapshot audit. No HTTP requests or site modifications."""
import hashlib
import json
import os
import re
import stat
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

RULESET = "wgg-static-v1"
MAX_FILE = 1024 * 1024
MAX_PAGES = 100
MAX_TOTAL = 16 * 1024 * 1024


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical_json(obj):
    return json.dumps(obj, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def path_parts(name):
    require(isinstance(name, str) and 0 < len(name) <= 512 and "\\" not in name,
            "Invalid snapshot path")
    p = name.split("/")
    require(all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", x) for x in p),
            "Snapshot path must contain ordinary relative components")
    return p


class Snapshot:
    """Read regular single-link files from a dedicated trusted local root on POSIX."""
    def __init__(self, root):
        require(os.name == "posix" and hasattr(os, "O_NOFOLLOW"), "POSIX filesystem required")
        self.root = Path(root).resolve(strict=True)
        require(self.root.is_dir(), "Snapshot root must be a directory")
        self.total = 0
        self.cache = {}

    def read(self, path):
        if path in self.cache:
            return self.cache[path]
        names = path_parts(path)
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for name in names[:-1]:
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            child = os.open(names[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(child, "rb") as stream:
                info = os.fstat(stream.fileno())
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "Not a single-link regular file")
                require(info.st_size <= MAX_FILE, "Snapshot file exceeds 1 MiB")
                raw = stream.read(MAX_FILE + 1)
                require(len(raw) <= MAX_FILE and self.total + len(raw) <= MAX_TOTAL, "Snapshot byte limit exceeded")
                self.total += len(raw)
                self.cache[path] = raw
                return raw
        except OSError as exc:
            raise ValueError("Snapshot file unavailable: " + path) from exc
        finally:
            os.close(fd)


def checked_url(value, origin=None):
    require(isinstance(value, str) and 0 < len(value) <= 2048 and not any(ord(c) <= 32 for c in value)
            and "\\" not in value, "Invalid URL")
    u = urlsplit(value)
    require(u.scheme == "https" and bool(u.hostname) and not u.username and not u.password
            and u.port is None and not u.fragment, "Use absolute HTTPS URLs without credentials, port or fragment")
    require(u.netloc == u.hostname, "Use lowercase hostname")
    require(not u.query, "Snapshot page URLs must not include query parameters")
    if origin:
        require(f"https://{u.netloc}" == origin, "Snapshot URL must match declared origin")
    return value


def validate_manifest(obj):
    require(isinstance(obj, dict) and set(obj) == {"origin", "pages", "sitemap"}, "Invalid manifest fields")
    origin = checked_url(obj["origin"])
    require(origin == "https://" + urlsplit(origin).hostname, "Origin must not have a path")
    pages = obj["pages"]
    require(isinstance(pages, list) and 1 <= len(pages) <= MAX_PAGES, "Declare 1-100 pages")
    seen = set()
    for row in pages:
        require(isinstance(row, dict) and set(row) == {"url", "file", "indexable", "canonical", "language", "alternates"},
                "Page fields must be url, file, indexable, canonical, language, alternates")
        checked_url(row["url"], origin)
        checked_url(row["canonical"], origin)
        path_parts(row["file"])
        require(row["file"].endswith((".html", ".htm")), "Page must be an HTML snapshot")
        require(type(row["indexable"]) is bool, "indexable must be boolean")
        require(isinstance(row["language"], str) and re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", row["language"]), "Invalid language")
        alternates = row["alternates"]
        require(isinstance(alternates, dict) and len(alternates) <= 30, "Invalid expected alternates")
        for language, target in alternates.items():
            require(isinstance(language, str) and language == language.lower() and
                    re.fullmatch(r"(?:x-default|[a-z]{2,3}(?:-[a-z0-9]{2,8})*)", language),
                    "Expected hreflang keys must be lowercase")
            checked_url(target, origin)
        require(row["url"] not in seen, "Duplicate page URL")
        seen.add(row["url"])
    if obj["sitemap"] is not None:
        path_parts(obj["sitemap"])
        require(obj["sitemap"].endswith(".xml"), "Sitemap must be XML")
    return {"origin": origin, "pages": sorted(pages, key=lambda p: p["url"]), "sitemap": obj["sitemap"]}


class Head(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.in_head = False
        self.inert = []
        self.title_open = False
        self.titles = []
        self.meta = {}
        self.links = []
        self.lang = ""
        self.base_seen = False
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        if self.inert:
            if tag in ("template", "noscript"):
                self.inert.append(tag)
            return
        if self.in_head and tag in ("template", "noscript"):
            self.inert.append(tag)
            return
        a = {k.lower(): v or "" for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang", "")
        if tag == "head":
            self.in_head = True
        if tag == "body":
            self.in_head = False
            self.title_open = False
        if not self.in_head:
            return
        if tag == "title":
            self.titles.append("")
            self.title_open = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").strip().lower()
            self.meta.setdefault(key, []).append(a.get("content", "").strip())
        elif tag == "link":
            self.links.append(a)
        elif tag == "base" and a.get("href"):
            self.base_seen = True

    def handle_endtag(self, tag):
        if self.inert:
            if tag == self.inert[-1]:
                self.inert.pop()
            return
        if tag == "head":
            self.in_head = False
        if tag == "title":
            self.title_open = False

    def handle_data(self, data):
        if self.in_head and self.title_open and not self.inert:
            self.titles[-1] += data


def safe_join(url, target):
    try:
        return urljoin(url, target)
    except ValueError:
        return None


def robots_tokens(values):
    return set(re.findall(r"[a-z][a-z0-9_-]*", " ".join(values).lower()))


def parse_sitemap(raw):
    # Reject entity/DTD declarations before parsing. Only simple urlset is supported.
    text = raw.decode("utf-8-sig")
    require(not re.search(r"<!\s*(?:DOCTYPE|ENTITY)", text, re.I), "Sitemap DTD/entities unsupported")
    root = ET.fromstring(text)
    ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    require(root.tag == ns + "urlset", "Only a namespaced sitemap urlset is supported")
    result = set()
    for entry in root.findall(ns + "url"):
        loc = entry.findall(ns + "loc")
        require(len(loc) == 1 and bool(loc[0].text), "Invalid sitemap loc")
        result.add(checked_url(loc[0].text.strip()))
    return result


def audit(root, manifest_name="manifest.json"):
    snap = Snapshot(root)
    manifest = validate_manifest(json.loads(snap.read(manifest_name)))
    return audit_snapshot(snap, manifest)


def audit_snapshot(snap, manifest):
    """Audit a trusted manifest using an object with read(path) and cache."""
    findings, pages, languages = [], {}, {}

    def add(url, code, severity, detail):
        findings.append({"url": url, "code": code, "severity": severity, "detail": detail})

    sitemap = None
    if manifest["sitemap"]:
        try:
            sitemap = parse_sitemap(snap.read(manifest["sitemap"]))
        except (ValueError, ET.ParseError, UnicodeError):
            add(manifest["origin"], "SITEMAP_UNVERIFIABLE", "error", "Missing or unsupported sitemap XML")
    else:
        add(manifest["origin"], "SITEMAP_NOT_DECLARED", "warning", "Sitemap not checked")
    for row in manifest["pages"]:
        url = row["url"]
        try:
            raw = snap.read(row["file"])
            h = Head(raw.decode("utf-8-sig"))
        except (ValueError, UnicodeError):
            add(url, "HTML_UNVERIFIABLE", "error", "HTML snapshot missing, unreadable or unsupported")
            continue
        pages[url] = {"sha256": sha(raw), "bytes": len(raw)}
        if h.base_seen:
            add(url, "BASE_HREF_UNSUPPORTED", "error", "Relative URL evidence cannot be verified with a base href")
        if row["indexable"]:
            general = robots_tokens(h.meta.get("robots", []))
            for agent, code in (("robots", "NOINDEX"), ("googlebot", "GOOGLEBOT_NOINDEX"),
                                ("oai-searchbot", "OAI_SEARCHBOT_NOINDEX")):
                tokens = general if agent == "robots" else robots_tokens(h.meta.get(agent, []))
                if "noindex" in tokens or "none" in tokens:
                    add(url, code, "error", "Declared indexable page has a restrictive robots meta directive")
            if len(h.titles) != 1 or not h.titles[0].strip():
                add(url, "TITLE_MISSING_OR_AMBIGUOUS", "error", "Expected one nonempty head title")
            desc = h.meta.get("description", [])
            if len(desc) != 1 or not desc[0]:
                add(url, "DESCRIPTION_MISSING_OR_AMBIGUOUS", "warning", "Expected one nonempty meta description")
            for tag in ("og:title", "og:description", "og:image"):
                if not any(h.meta.get(tag, [])):
                    add(url, "SOCIAL_" + tag.upper().replace(":", "_"), "warning", "Missing social preview metadata")
            if sitemap is not None and url not in sitemap:
                add(url, "SITEMAP_PAGE_MISSING", "error", "Expected indexable page absent from declared sitemap")
        elif sitemap is not None and url in sitemap:
            add(url, "SITEMAP_NONINDEXABLE_PAGE", "warning", "Nonindexable page present in sitemap")
        canonicals = [x.get("href", "") for x in h.links if "canonical" in x.get("rel", "").lower().split()]
        if len(canonicals) != 1 or not canonicals[0] or h.base_seen or safe_join(url, canonicals[0]) != row["canonical"]:
            add(url, "CANONICAL_MISMATCH", "error", "Expected exactly one matching canonical URL")
        if h.lang.lower() != row["language"].lower():
            add(url, "HTML_LANGUAGE_MISMATCH", "warning", "HTML language differs from manifest")
        alternate = {}
        for x in h.links:
            if "alternate" not in x.get("rel", "").lower().split() or not x.get("hreflang"):
                continue
            lang = x["hreflang"].lower()
            href = x.get("href", "")
            if not href or h.base_seen or lang in alternate:
                add(url, "HREFLANG_AMBIGUOUS", "error", "Missing or duplicate hreflang link, or unsupported base href")
                continue
            alternate[lang] = safe_join(url, href)
        if any(alternate.get(lang) != target for lang, target in row["alternates"].items()):
            add(url, "HREFLANG_EXPECTED_MISMATCH", "error", "An explicitly required alternate is missing or points elsewhere")
        languages[url] = alternate
    by_url = {row["url"]: row for row in manifest["pages"]}
    for url, links in languages.items():
        if not links:
            continue
        lang = by_url[url]["language"].lower()
        if links.get(lang) != url:
            add(url, "HREFLANG_SELF_MISSING", "error", "Expected self-reference matching declared language")
        for target_lang, target in links.items():
            if target_lang == "x-default":
                continue
            if target not in by_url or target not in languages:
                add(url, "HREFLANG_TARGET_UNVERIFIED", "warning", "Alternate is outside verified snapshot scope")
            elif by_url[target]["language"].lower() != target_lang or languages[target].get(lang) != url:
                add(url, "HREFLANG_RECIPROCITY", "error", "Declared alternate language or return link does not match")
    # One stable identity per page and rule; detail text is intentionally not scraped content.
    unique = {(f["url"], f["code"]): f for f in findings}
    result = {"format": "wgg-audit/v1", "ruleset": RULESET, "scope_sha256": sha(canonical_json(manifest)),
              "origin": manifest["origin"], "pages_expected": len(manifest["pages"]),
              "pages_verified": len(pages), "pages": pages,
              "sitemap_sha256": sha(snap.cache[manifest["sitemap"]]) if manifest["sitemap"] in snap.cache else None,
              "findings": sorted(unique.values(), key=lambda f: (f["url"], f["code"])),
              "limitations": "Offline HTML/sitemap only; no HTTP status, response headers, robots.txt, rendered JavaScript, indexing, ranking or traffic verification."}
    return result


def compare(baseline, current):
    for report in (baseline, current):
        require(isinstance(report, dict) and report.get("format") == "wgg-audit/v1" and
                report.get("ruleset") == RULESET and isinstance(report.get("findings"), list), "Unsupported audit report")
    for report in (baseline, current):
        require(type(report.get("pages_expected")) is int and type(report.get("pages_verified")) is int
                and 1 <= report["pages_expected"] <= MAX_PAGES and
                0 <= report["pages_verified"] <= report["pages_expected"] and
                isinstance(report.get("pages"), dict) and len(report["pages"]) == report["pages_verified"],
                "Invalid audit coverage")
        require(all(isinstance(f, dict) and set(f) == {"url", "code", "severity", "detail"} and
                    all(isinstance(v, str) for v in f.values()) and f["severity"] in ("error", "warning")
                    for f in report["findings"]), "Invalid audit findings")
    require(baseline["scope_sha256"] == current["scope_sha256"] and baseline["origin"] == current["origin"],
            "Scope changed; review and establish a new baseline")
    old = {(f["url"], f["code"]): f for f in baseline["findings"]}
    new = {(f["url"], f["code"]): f for f in current["findings"]}
    added = [new[k] for k in sorted(new) if k not in old or (old[k]["severity"] != "error" and new[k]["severity"] == "error")]
    resolved = [old[k] for k in sorted(old.keys() - new.keys())]
    blockers = [f for f in current["findings"] if f["severity"] == "error"]
    if current["pages_verified"] != current["pages_expected"]:
        blockers.append({"url": current["origin"], "code": "INCOMPLETE_COVERAGE",
                         "severity": "error", "detail": "Not all declared pages were verified"})
    return {"format": "wgg-regression/v1", "decision": "BLOCK" if blockers else "REVIEW" if added else "CLEAR",
            "added": added, "resolved": resolved, "current_errors": blockers,
            "baseline_digest": sha(canonical_json(baseline)), "current_digest": sha(canonical_json(current)),
            "scope": "Snapshot policy only. CLEAR does not certify SEO performance or authorize deployment."}
