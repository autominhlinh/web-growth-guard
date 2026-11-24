"""Opt-in, bounded public HTTPS transport smoke; not an SEO score or site audit."""
import json
from web_growth_guard.discovery import discover, validate_live_report


def main():
    # Fixed public documentation example, not any real user's project.
    origin = 'https://example.com'
    url = origin + '/'
    result = discover({'origin':origin, 'pages':[{'url':url, 'indexable':False,
        'canonical':url, 'language':'en', 'alternates':{}}], 'sitemap':None})
    validate_live_report(result)
    print(json.dumps(result, indent=2, ensure_ascii=True))
    observed = result['pages'][url]['response']
    if (result['collection_mode'] != 'public_https' or result['pages_verified'] != 1
            or not observed or observed['status'] != 200 or observed['final_url'] != url
            or observed['redirects'] or result['robots']['status'] not in ('PARSED','NOT_FOUND')):
        print('PUBLIC HTTPS NOT VERIFIED: see evidence and robots status above')
        return 1
    print('PUBLIC HTTPS TRANSPORT PASS: robots policy checked, one exact HTML page observed. SEO findings are separate.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
