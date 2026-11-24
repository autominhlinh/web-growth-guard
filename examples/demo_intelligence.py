"""Three entirely synthetic websites and their evidence-based action queue."""
import argparse
import json
from pathlib import Path
from shutil import copytree
from web_growth_guard.intelligence import portfolio
from web_growth_guard.intelligence_view import render

HERE=Path(__file__).resolve().parent


def write_json(path,obj):
    with path.open('x',encoding='utf-8') as stream: json.dump(obj,stream,indent=2,ensure_ascii=True); stream.write('\n')


def generate(output):
    output.mkdir(mode=0o700,exist_ok=False)
    snapshots=output/'snapshots'; snapshots.mkdir()
    labels=[]
    for key,label,profile,topic in [('directory','Synthetic community directory','Organization','hướng dẫn nhóm'),('store','Synthetic product store','Product','hướng dẫn sản phẩm'),('audio','Synthetic audio library','AudioObject','hướng dẫn nghe')]:
        root=snapshots/key; copytree(HERE/'growth',root)
        origin='https://'+key+'.example.invalid'; en,vi=origin+'/en/',origin+'/vi/'
        for path in root.rglob('*'):
            if path.is_file(): path.write_text(path.read_text().replace('https://example.invalid',origin),encoding='utf-8')
        site=json.loads((root/'growth.json').read_text())['site']
        nodes={'Organization':{'@type':'Organization','name':'Example directory','url':en},'Product':{'@type':'Product','name':'Example product','offers':{'@type':'Offer','price':'19.00','priceCurrency':'USD'}},'AudioObject':{'@type':'AudioObject','name':'Example audio','contentUrl':origin+'/sample.mp3'}}
        node={'@context':'https://schema.org',**nodes[profile]}
        # Directory stays clean, store loses link and price, audio has a content gap.
        if key=='store': node['offers'].pop('price')
        en_page=root/'pages/en.html'; vi_page=root/'pages/vi.html'
        en_page.write_text(en_page.read_text().replace('</body>',('<a href="/vi/">Vietnamese</a>' if key!='store' else '')+'<script type="application/ld+json">'+json.dumps(node)+'</script></body>'),encoding='utf-8')
        vi_page.write_text(vi_page.read_text().replace('</body>','<p>'+(topic if key!='audio' else 'Nội dung mẫu')+'</p></body>'),encoding='utf-8')
        policy={'site':site,'entry_pages':[en],'max_depth':2,'topics':[{'id':key+'-vi','language':'vi','target':vi,'phrases':[topic],'priority':'high'}], 'required_links':[{'from':en,'to':vi}],'schema_profiles':{en:[profile]}}
        write_json(root/'intelligence.json',policy)
        labels.append({'label':label,'snapshot':'snapshots/'+key,'policy':'intelligence.json'})
    write_json(output/'intelligence-portfolio.json',{'sites':labels})
    result=portfolio(output)
    checks={s['label']:{'priorities':s['plan']['priority_counts'],'topics':{t['id']:t['status'] for t in s['report']['topics']},'codes':sorted({f['code'] for f in s['report']['findings']})} for s in result['sites']}
    assert checks['Synthetic community directory']['priorities']=={'P0':0,'P1':0,'P2':0}
    assert {'REQUIRED_LINK_MISSING','SCHEMA_INCOMPLETE','UNREACHED_IN_SCOPE'}<=set(checks['Synthetic product store']['codes'])
    assert checks['Synthetic audio library']['topics']=={'audio-vi':'GAP'}
    write_json(output/'portfolio-report.json',result)
    write_json(output/'verification.json',{'scope':'Synthetic local HTML only','sites':checks})
    (output/'dashboard.html').write_text(render(result),encoding='utf-8')
    print('INTELLIGENCE DEMO PASS: three sites, missing link, incomplete Product, Vietnamese topic gap')
    return checks


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--output',required=True,type=Path); generate(p.parse_args().output)
