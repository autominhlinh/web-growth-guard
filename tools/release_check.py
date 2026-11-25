"""Create an explicit source-only archive and verify its installed wheel's contents.

No Git history, network, credentials or local reports are included. The source list
is an operator-reviewed allowlist, not an independent security audit.
"""
import argparse
import base64
import csv
import hashlib
import io
import json
import re
import stat
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

MAX_FILE = 1024 * 1024
MAX_TOTAL = 16 * 1024 * 1024
TOP = {'README.md', 'LICENSE', 'SECURITY.md', 'CHANGELOG.md', 'CONTRIBUTING.md',
       'MANIFEST.in', 'pyproject.toml', 'release-files.txt', '.gitignore'}
RULES = [
    re.compile(r'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----'),
    re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|sk-(?:proj-)?[A-Za-z0-9_-]{24,}|AKIA[A-Z0-9]{16})'),
    re.compile(r'https?://[^/\s:@]+:[^/\s@]+@', re.I),
]


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_name(name):
    p = PurePosixPath(name)
    if not name or str(p) != name or p.is_absolute() or '\\' in name or any(
            x in ('', '.', '..') or not re.fullmatch(r'[A-Za-z0-9_.-]+', x) for x in p.parts):
        raise ValueError('Unsafe archive member')
    return p


def allowed_source(name):
    p = safe_name(name)
    if name in TOP:
        return True
    if any(x.startswith('.') for x in p.parts[1:]):
        return False
    if name in {'.github/ISSUE_TEMPLATE/bug.yml', '.github/ISSUE_TEMPLATE/compatibility.yml', '.github/PULL_REQUEST_TEMPLATE.md'}:
        return True
    if name.startswith('.github/workflows/'):
        return len(p.parts) == 3 and p.suffix in ('.yml', '.yaml')
    if name.startswith('examples/analytics/') and p.suffix == '.csv':
        return bool(re.fullmatch(r'(?:ga4_landings|gsc_pages|gsc_queries)-(?:current|previous)\.csv', p.name)) and len(p.parts) == 3
    roots = {'src': {'.py'}, 'tests': {'.py'}, 'tools': {'.py'}, 'docs': {'.md'},
             'examples': {'.py', '.json', '.html', '.xml'}}
    return len(p.parts) > 1 and p.suffix in roots.get(p.parts[0], set())


def read_source(root, name):
    if not allowed_source(name):
        raise ValueError('Non-allowlisted source type: ' + name)
    path = root
    for component in safe_name(name).parts:
        path = path / component
        if path.is_symlink():
            raise ValueError('Source symlink rejected: ' + name)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_FILE:
        raise ValueError('Invalid source file: ' + name)
    with path.open('rb') as stream:
        data = stream.read(MAX_FILE + 1)
    if len(data) > MAX_FILE or b'\x00' in data:
        raise ValueError('Invalid source bytes: ' + name)
    text = data.decode('utf-8')
    if any(rule.search(text) for rule in RULES):
        raise ValueError('Sensitive-looking pattern in: ' + name)
    return data


def source_map(root):
    root = Path(root).resolve(strict=True)
    listing = read_source(root, 'release-files.txt').decode().splitlines()
    if listing != sorted(set(listing)) or not TOP.issubset(listing) or len(listing) > 200:
        raise ValueError('Invalid or incomplete source allowlist')
    files, size = {}, 0
    for name in listing:
        data = read_source(root, name)
        size += len(data)
        if size > MAX_TOTAL:
            raise ValueError('Source archive exceeds total budget')
        files[name] = data
    return files


def wheel_check(path, files, version):
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        names = [x.filename for x in infos]
        if len(names) != len(set(names)) or len(names) > 100 or sum(x.file_size for x in infos) > MAX_TOTAL:
            raise ValueError('Wheel member budget or uniqueness failed')
        dist = 'web_growth_guard-' + version + '.dist-info/'
        actual = {}
        for info in infos:
            name = info.filename
            safe_name(name)
            mode = info.external_attr >> 16
            if info.is_dir() or stat.S_ISLNK(mode) or info.file_size > MAX_FILE:
                raise ValueError('Invalid wheel member')
            if name.startswith('web_growth_guard/') and name.endswith('.py'):
                if files.get('src/' + name) != z.read(info):
                    raise ValueError('Wheel code differs from source: ' + name)
            elif name not in {dist+x for x in ('METADATA', 'WHEEL', 'RECORD', 'entry_points.txt',
                                             'top_level.txt', 'licenses/LICENSE', 'LICENSE')}:
                raise ValueError('Unexpected wheel member: ' + name)
            actual[name] = z.read(info)
        expected = {name[4:] for name in files if name.startswith('src/web_growth_guard/')}
        if not expected.issubset(actual):
            raise ValueError('Wheel module missing')
        metadata = BytesParser().parsebytes(actual[dist+'METADATA'])
        if metadata['Name'] != 'web-growth-guard' or metadata['Version'] != version or metadata.get_all('Requires-Dist'):
            raise ValueError('Wheel metadata mismatch or unexpected dependency')
        if actual.get(dist+'licenses/LICENSE', actual.get(dist+'LICENSE')) != files['LICENSE']:
            raise ValueError('Wheel license mismatch')
        rows = list(csv.reader(io.StringIO(actual[dist+'RECORD'].decode('utf-8'))))
        if any(len(row) != 3 for row in rows) or len(rows) != len(actual) or set(row[0] for row in rows) != set(actual):
            raise ValueError('Invalid wheel RECORD')
        for name, checksum, size in rows:
            if name == dist+'RECORD':
                if checksum or size:
                    raise ValueError('Invalid self RECORD')
                continue
            expected_hash = 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(actual[name]).digest()).decode().rstrip('=')
            if checksum != expected_hash or size != str(len(actual[name])):
                raise ValueError('Wheel RECORD mismatch: ' + name)
        return len(names)


def export(root, wheel, output):
    files = source_map(root)
    version = tomllib.loads(files['pyproject.toml'].decode())['project']['version']
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:rc[0-9]+|\.dev[0-9]+)?', version):
        raise ValueError('Invalid release version')
    count = wheel_check(wheel, files, version)
    output = Path(output)
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    archive = output / ('web-growth-guard-' + version + '-source.zip')
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo('web-growth-guard/' + name, (1980,1,1,0,0,0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    with zipfile.ZipFile(archive) as z:
        if z.comment or set(z.namelist()) != {'web-growth-guard/'+n for n in files}:
            raise ValueError('Invalid source archive layout')
        for name, raw in files.items():
            if z.read('web-growth-guard/'+name) != raw:
                raise ValueError('Source archive mismatch')
    manifest = {'version':version, 'archive':archive.name, 'archive_sha256':digest(archive.read_bytes()),
                'wheel':Path(wheel).name, 'wheel_sha256':digest(Path(wheel).read_bytes()),
                'wheel_entries':count, 'files':[{'path':n,'bytes':len(b),'sha256':digest(b)} for n,b in sorted(files.items())],
                'scope':'Explicit allowlisted source, limited credential-pattern scan, wheel RECORD and code-byte verification; not a security audit.'}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n', encoding='utf-8')
    print(f'SOURCE AND WHEEL CHECK PASS: {len(files)} source files; {count} wheel members; zero declared runtime dependencies')
    return manifest


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path('.'))
    p.add_argument('--wheel', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    export(args.root, args.wheel, args.output)
