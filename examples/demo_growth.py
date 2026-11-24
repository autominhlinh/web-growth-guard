"""Generate a three-state marketing regression demo without visiting any URL."""
import argparse
import json
import tempfile
from pathlib import Path
from shutil import copytree
from web_growth_guard.marketing import (read_growth_policy, growth_audit, growth_compare,
                                        campaign_batch, preview)
from web_growth_guard.portfolio import read_portfolio, render

HERE = Path(__file__).resolve().parent


def write_json(path, data):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2, ensure_ascii=True)
        stream.write('\n')


def generate(output):
    output.mkdir(mode=0o700, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='wgg-growth-') as temp:
        root = Path(temp) / 'snapshot'
        copytree(HERE / 'growth', root)
        policy = read_growth_policy(root)
        clean = growth_audit(root, policy)
        if clean['findings']:
            raise RuntimeError('Synthetic baseline unexpectedly has findings')
        url = policy['site']['origin'] + '/vi/'
        write_json(output / 'campaign-links.json', campaign_batch(json.loads((root / 'campaigns.json').read_text())))
        write_json(output / 'social-preview.json', preview(root, policy, url))
        page = root / 'pages' / 'vi.html'
        original = page.read_text()
        page.write_text(original.replace('width=device-width', 'width=1000'))
        review = growth_audit(root, policy)
        page.write_text(original.replace('href="/vi/join/"', 'href="/other/"')
                        .replace('hreflang="en" href="https://example.invalid/en/"', 'hreflang="en" href="/en/"')
                        .replace('https://example.invalid/preview.png', 'javascript:sample'))
        blocked = growth_audit(root, policy)
        reports = {'clear': clean, 'review': review, 'block': blocked}
        outcomes = {name: growth_compare(clean, report)['decision'] for name, report in reports.items()}
        if outcomes != {'clear': 'CLEAR', 'review': 'REVIEW', 'block': 'BLOCK'}:
            raise RuntimeError('Unexpected regression decisions')
        for name, report in reports.items():
            write_json(output / (name + '.json'), report)
        write_json(output / 'portfolio.json', {'sites': [
            {'label': label, 'report': name + '.json', 'baseline': 'clear.json'}
            for name, label in [('clear', 'Synthetic / Healthy landing pages'),
                                ('review', 'Synthetic / Mobile markup warning'),
                                ('block', 'Synthetic / CTA, language and social regression')]]})
        portfolio = read_portfolio(output)
        write_json(output / 'summary.json', portfolio)
        with (output / 'dashboard.html').open('x', encoding='utf-8') as stream:
            stream.write(render(portfolio))
        write_json(output / 'verification.json', {'decisions': outcomes,
            'block_codes': [f['code'] for f in blocked['findings'] if f['severity'] == 'error'],
            'scope': 'Synthetic HTML only; no website requests or traffic measurement'})
    print('GROWTH DEMO PASS: CLEAR, REVIEW, BLOCK; no website requests')
    return outcomes


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True, type=Path)
    generate(p.parse_args().output)
