"""Bounded REP interpretation for explicit paths; not a crawler impersonator."""
import re
from urllib.parse import quote
from .core import require

UNRESERVED = frozenset(b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~')
AGENTS = ('WebGrowthGuard', 'Googlebot', 'OAI-SearchBot', 'GPTBot')


def normalize(value):
    require(not re.search(r'%(?![0-9a-fA-F]{2})', value), 'Invalid percent encoding in REP')
    value = quote(value, safe="/%!*'();:@&=+$,[]-._~?")
    def pct(match):
        n = int(match.group(0)[1:], 16)
        return chr(n) if n in UNRESERVED else '%%%02X' % n
    return re.sub(r'%[0-9a-fA-F]{2}', pct, value)


def matches(pattern, target):
    # Linear segment matching, avoiding untrusted regex backtracking.
    anchored = pattern.endswith('$')
    body = pattern[:-1] if anchored else pattern
    chunks = body.split('*')
    if len(chunks) == 1:
        return target == body if anchored else target.startswith(body)
    if not target.startswith(chunks[0]):
        return False
    pos = len(chunks[0])
    end = len(target)
    if anchored and chunks[-1]:
        if not target.endswith(chunks[-1]):
            return False
        end -= len(chunks[-1])
        if end < pos:
            return False
        chunks = chunks[:-1]
    for part in chunks[1:]:
        if part:
            found = target.find(part, pos, end)
            if found < 0:
                return False
            pos = found + len(part)
    return pos <= end


class Robots:
    def __init__(self, raw):
        require(len(raw) <= 512 * 1024, 'REP file too large')
        text = raw.decode('utf-8-sig')
        require('\x00' not in text, 'Invalid REP text')
        self.groups = []
        self.sitemaps = []
        agents, rules, has_rule = [], [], False
        count = 0
        for line in text.splitlines():
            line = line.split('#', 1)[0].strip()
            if not line or ':' not in line:
                continue
            key, value = (x.strip() for x in line.split(':', 1))
            key = key.lower()
            if key == 'user-agent':
                require(bool(re.fullmatch(r'(?:\*|[A-Za-z_-]+)', value)), 'Unsupported REP user agent')
                if has_rule:
                    self.groups.append((agents, rules))
                    agents, rules, has_rule = [], [], False
                agents.append(value.lower())
            elif key in ('allow', 'disallow') and agents:
                has_rule = True
                if value:
                    require(len(value) <= 2048 and (value.startswith('/') or value.startswith('*')),
                            'Unsupported REP path rule')
                    count += 1
                    require(count <= 2000, 'REP rule budget exceeded')
                    rules.append((key == 'allow', normalize(value)))
            elif key == 'sitemap':
                if value and len(self.sitemaps) < 100:
                    self.sitemaps.append(value)
        if agents:
            self.groups.append((agents, rules))

    def allowed(self, agent, path):
        exact = [rules for agents, rules in self.groups if agent.lower() in agents]
        selected = exact or [rules for agents, rules in self.groups if '*' in agents]
        target = normalize(path)
        matched = [(len(re.sub(r'%[0-9A-F]{2}', 'x', pattern.rstrip('$'))), allow)
                   for rules in selected for allow, pattern in rules if matches(pattern, target)]
        return max(matched)[1] if matched else True
