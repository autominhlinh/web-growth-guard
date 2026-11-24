"""Bounded, offline content coverage, navigation and structured data evidence."""
import json
import re
import unicodedata
from collections import deque, Counter
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.parse import urljoin, urldefrag
from .core import Snapshot, audit_snapshot, validate_manifest, checked_url, path_parts, require, sha, canonical_json

FORMAT = 'wgg-intelligence/v1'
PROFILES = {'Product', 'Article', 'BreadcrumbList', 'Organization', 'AudioObject'}
VOID = set('area base br col embed hr img input link meta param source track wbr'.split())
BLOCK = set('address article aside blockquote br dd div dl dt figcaption figure footer form h1 h2 h3 h4 h5 h6 header hr li main nav ol p pre section table td th tr ul'.split())
INERT = {'script', 'style', 'template', 'noscript', 'head', 'textarea', 'select', 'svg'}


def normalized(value):
    return ' '.join(re.findall(r'[^\W_]+', unicodedata.normalize('NFC', value).casefold(), re.UNICODE))


def validate_policy(obj):
    require(isinstance(obj, dict) and set(obj) == {'site', 'entry_pages', 'max_depth', 'topics', 'required_links', 'schema_profiles'}, 'Invalid intelligence policy')
    site = validate_manifest(obj['site'])
    pages = {p['url']: p for p in site['pages']}
    eligible = {u for u,p in pages.items() if p['indexable'] and p['canonical'] == u}
    entries = obj['entry_pages']
    require(isinstance(entries,list) and 1 <= len(entries) <= len(eligible) and all(isinstance(u,str) and u in eligible for u in entries) and len(set(entries)) == len(entries), 'Entry pages must be unique declared self-canonical indexable pages')
    require(type(obj['max_depth']) is int and 1 <= obj['max_depth'] <= 10, 'Invalid depth threshold')
    topics, ids = obj['topics'], set()
    require(isinstance(topics,list) and len(topics) <= 50, 'Declare at most 50 topics')
    for row in topics:
        require(isinstance(row,dict) and set(row)=={'id','language','target','phrases','priority'}, 'Invalid topic fields')
        require(isinstance(row['id'],str) and re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}',row['id']) and row['id'] not in ids, 'Invalid or duplicate topic ID')
        ids.add(row['id'])
        require(isinstance(row['target'],str) and row['target'] in eligible and row['language']==pages[row['target']]['language'].lower(), 'Topic target/language outside eligible scope')
        require(row['priority'] in ('high','normal'), 'Invalid topic priority')
        phrases=row['phrases']
        require(isinstance(phrases,list) and 1<=len(phrases)<=5 and all(isinstance(p,str) and 1<=len(p)<=120 and normalized(p) and len(normalized(p).split())<=12 and not any(ord(c)<32 for c in p) for p in phrases), 'Invalid topic phrases')
        require(len({normalized(p) for p in phrases})==len(phrases), 'Duplicate normalized phrases')
    links = obj['required_links']
    require(isinstance(links,list) and len(links)<=200, 'Too many required links')
    seen=set()
    for row in links:
        require(isinstance(row,dict) and set(row)=={'from','to'} and all(isinstance(row[k],str) and row[k] in eligible for k in ('from','to')) and row['from']!=row['to'], 'Required link outside eligible scope')
        pair=(row['from'],row['to']); require(pair not in seen,'Duplicate required link'); seen.add(pair)
    schemas=obj['schema_profiles']
    require(isinstance(schemas,dict) and len(schemas)<=len(eligible), 'Invalid schema profiles')
    for url, types in schemas.items():
        require(url in eligible and isinstance(types,list) and 1<=len(types)<=len(PROFILES) and all(isinstance(t,str) and t in PROFILES for t in types) and len(set(types))==len(types),'Invalid schema profile or page')
    return {'site':site,'entry_pages':sorted(entries),'max_depth':obj['max_depth'],'topics':sorted(topics,key=lambda x:x['id']), 'required_links':sorted(links,key=lambda x:(x['from'],x['to'])),'schema_profiles':{u:sorted(v) for u,v in schemas.items()}}


class Document(HTMLParser):
    """Static approximation only: no DOM repair, CSS evaluation or script execution."""
    def __init__(self,text):
        super().__init__(convert_charrefs=True)
        self.stack=[]; self.body=False; self.in_head=False; self.links=[]; self.chunks=[]; self.buffer=[]
        self.jsonld=[]; self.script=None; self.limited=False; self.base=False; self.body_seen=False
        self.feed(text); self.close(); self.flush()
        if self.script is not None: self.limited=True

    def flush(self):
        if self.buffer:
            segment=normalized(''.join(self.buffer))
            if segment: self.chunks.append(' '+segment+' ')
            self.buffer=[]

    def handle_starttag(self,tag,attrs):
        a=dict(attrs); parent_hidden=bool(self.stack and self.stack[-1][1])
        hidden=parent_hidden or tag in INERT or 'hidden' in a or (a.get('aria-hidden') or '').lower()=='true'
        # Head is inert for content, but normal head scripts can contain JSON-LD.
        in_head_context=self.in_head and all(t=='head' or not h for t,h in self.stack)
        if tag=='head' and not parent_hidden: self.in_head=True
        if tag=='body' and not parent_hidden: self.body=True; self.body_seen=True
        if tag=='base' and not any(t in ('template','noscript') for t,_ in self.stack) and a.get('href'): self.base=True
        if tag=='script' and (a.get('type') or '').strip().lower()=='application/ld+json' and (in_head_context or self.body and not parent_hidden) and 'hidden' not in a:
            if len(self.jsonld)>=20: self.limited=True
            else: self.script=[]
        if not hidden and self.body:
            if tag in BLOCK: self.flush()
            if tag=='a' and a.get('href'):
                if len(self.links)>=2000: self.limited=True
                else: self.links.append((a['href'],set((a.get('rel') or '').lower().split())))
        if tag not in VOID:
            if len(self.stack)>=256: raise ValueError('Markup nesting limit')
            self.stack.append((tag,hidden))

    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if tag not in VOID: self.handle_endtag(tag)

    def handle_endtag(self,tag):
        if tag=='script' and self.script is not None:
            value=''.join(self.script)
            if len(value.encode('utf-8'))>128*1024: self.limited=True
            else: self.jsonld.append(value)
            self.script=None
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:
                hidden=self.stack[i][1]
                del self.stack[i:]
                if tag in BLOCK and not hidden: self.flush()
                if tag=='body': self.body=False; self.flush()
                if tag=='head': self.in_head=False
                break

    def handle_data(self,data):
        if self.script is not None: self.script.append(data)
        elif self.body and not (self.stack and self.stack[-1][1]): self.buffer.append(data)


def strict_ld(raw):
    def pairs(items):
        d={}
        for k,v in items:
            require(k not in d,'Duplicate JSON key'); d[k]=v
        return d
    def invalid(_): raise ValueError('Nonfinite JSON')
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=invalid,parse_float=Decimal)


def types(node):
    v=node.get('@type',[])
    if isinstance(v,str): v=[v]
    if not isinstance(v,list): return set()
    return {t.removeprefix('https://schema.org/').removeprefix('http://schema.org/') for t in v if isinstance(t,str)}


def text(value): return isinstance(value,str) and bool(value.strip())


def numeric(value,positive=False):
    if isinstance(value,bool) or not isinstance(value,(str,int,Decimal)): return False
    try:
        if isinstance(value,str) and (len(value)>40 or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?',value)): return False
        n=Decimal(value); return n.is_finite() and (n>0 if positive else n>=0)
    except (InvalidOperation,ValueError): return False


def web_url(value):
    try: return bool(checked_url(value))
    except (ValueError,TypeError): return False


def rows(value): return value if isinstance(value,list) else [value]


def profile_valid(node,profile):
    if profile=='Product':
        offers=any(isinstance(o,dict) and ('Offer' in types(o) and numeric(o.get('price')) or 'AggregateOffer' in types(o) and numeric(o.get('lowPrice'))) and isinstance(o.get('priceCurrency'),str) and re.fullmatch(r'[A-Z]{3}',o['priceCurrency']) for o in rows(node.get('offers')))
        rating=node.get('aggregateRating'); rating_ok=isinstance(rating,dict) and 'AggregateRating' in types(rating) and numeric(rating.get('ratingValue'),True) and (numeric(rating.get('ratingCount'),True) or numeric(rating.get('reviewCount'),True))
        review_ok=any(isinstance(r,dict) and 'Review' in types(r) and isinstance(r.get('author'),dict) and text(r['author'].get('name')) and isinstance(r.get('reviewRating'),dict) and numeric(r['reviewRating'].get('ratingValue'),True) for r in rows(node.get('review')))
        return text(node.get('name')) and (offers or rating_ok or review_ok)
    if profile=='Article':
        return text(node.get('headline')) and any(isinstance(a,dict) and text(a.get('name')) for a in rows(node.get('author')))
    if profile=='BreadcrumbList':
        items=node.get('itemListElement');
        if not isinstance(items,list) or not 2<=len(items)<=50: return False
        for i,item in enumerate(items,1):
            if not isinstance(item,dict) or 'ListItem' not in types(item) or type(item.get('position')) is not int or item['position']!=i: return False
            dest=item.get('item'); name=item.get('name')
            if isinstance(dest,dict): name=name or dest.get('name'); dest=dest.get('@id')
            if not text(name) or not (i==len(items) and dest is None or web_url(dest)): return False
        return True
    if profile=='Organization': return text(node.get('name')) and web_url(node.get('url'))
    if profile=='AudioObject': return text(node.get('name')) and web_url(node.get('contentUrl'))
    return False


def structured(doc,profiles):
    nodes=[]; errors=0; unsupported=0
    def visit(value,context=False,depth=0):
        nonlocal unsupported
        require(depth<=24 and len(nodes)<200,'JSON-LD graph limit')
        if isinstance(value,list):
            require(len(value)<=200,'JSON-LD array limit')
            for item in value: visit(item,context,depth+1)
            return
        require(isinstance(value,dict),'Invalid JSON-LD node')
        if '@context' in value: context=value['@context'] in ('https://schema.org','https://schema.org/','http://schema.org','http://schema.org/')
        if not context: unsupported+=1; return
        if '@type' in value: nodes.append(value)
        if '@graph' in value: visit(value['@graph'],context,depth+1)
    # Do not interpret nested context aliases or remote contexts as Schema.org data.
    def check_contexts(value,depth=0):
        nonlocal unsupported
        require(depth<=32,'JSON-LD nesting limit')
        if isinstance(value,dict):
            if '@context' in value and value['@context'] not in ('https://schema.org','https://schema.org/','http://schema.org','http://schema.org/'): unsupported+=1
            for v in value.values(): check_contexts(v,depth+1)
        elif isinstance(value,list):
            for v in value: check_contexts(v,depth+1)
    for raw in doc.jsonld:
        try:
            value=strict_ld(raw)
            check_contexts(value)
            visit(value)
        except (ValueError,RecursionError,TypeError,InvalidOperation): errors+=1
    checks={}
    for profile in profiles:
        accepted={'Article','NewsArticle','BlogPosting'} if profile=='Article' else {profile}
        matches=[n for n in nodes if types(n)&accepted]
        if errors or unsupported or doc.limited: status='UNKNOWN'
        elif not matches: status='MISSING'
        elif all(profile_valid(n,profile) for n in matches): status='MATCHED'
        else: status='INCOMPLETE'
        checks[profile]={'status':status,'nodes_observed':len(matches)}
    return {'scripts':len(doc.jsonld),'invalid_scripts':errors,'unsupported_contexts':unsupported,'profiles':checks}


def _scan(snap,policy):
    cfg=validate_policy(policy); site=cfg['site']; static=audit_snapshot(snap,site)
    eligible={p['url']:p for p in site['pages'] if p['indexable'] and p['canonical']==p['url']}
    docs={}; graph={}; observations={}; findings=list(static['findings'])
    def add(code,url,detail,severity='warning',subject=None):
        row={'code':code,'url':url,'severity':severity,'detail':detail}
        if subject is not None: row['subject']=subject
        findings.append(row)
    for url,p in sorted(eligible.items()):
        try:
            require(url in static['pages'],'Missing source')
            doc=Document(snap.read(p['file']).decode('utf-8-sig'))
            require(doc.body_seen and not doc.base and not doc.limited,'Unsupported or bounded markup')
        except (ValueError,UnicodeError,RecursionError):
            add('INTELLIGENCE_PAGE_UNVERIFIED',url,'No complete supported body/link observation','error'); continue
        docs[url]=doc; targets=set(); unverified=set(); qualified=0
        for href,rel in doc.links:
            if rel & {'nofollow','ugc','sponsored'}: qualified+=1; continue
            if len(href)>2048 or any(ord(c)<32 or ord(c)==127 for c in href) or '\\' in href: continue
            try:
                dest=checked_url(urldefrag(urljoin(url,href))[0],site['origin'])
            except (ValueError,TypeError): continue
            if dest==url: continue
            if dest in eligible: targets.add(dest)
            else: unverified.add(dest)
        graph[url]=targets
        sd=structured(doc,cfg['schema_profiles'].get(url,[]))
        if sd['invalid_scripts']: add('JSONLD_INVALID',url,'One or more JSON-LD scripts could not be parsed')
        if sd['unsupported_contexts']: add('JSONLD_CONTEXT_UNVERIFIED',url,'Only literal Schema.org context and top-level/@graph nodes supported')
        for typ,check in sd['profiles'].items():
            if check['status']!='MATCHED': add('SCHEMA_'+check['status'],url,'Declared local shape check did not match','warning',typ)
        observations[url]={'sha256':static['pages'][url]['sha256'],'outbound_declared':len(targets),'outbound_unverified':len(unverified),'qualified_anchors':qualified,'structured_data':sd}
    complete=len(docs)==len(eligible)
    depths={u:0 for u in cfg['entry_pages'] if u in docs}; q=deque(depths)
    while q:
        source=q.popleft()
        for dest in sorted(graph.get(source,())):
            if dest in docs and dest not in depths: depths[dest]=depths[source]+1; q.append(dest)
    inbound=Counter(dest for targets in graph.values() for dest in targets)
    for url,obs in observations.items():
        obs['inbound_observed']=inbound[url]; obs['depth']=depths.get(url)
        obs['reachability']='REACHABLE' if url in depths else 'UNREACHED_IN_SCOPE' if complete else 'UNKNOWN'
        if complete and url not in cfg['entry_pages'] and not inbound[url]: add('NO_OBSERVED_INBOUND',url,'No inbound unqualified anchor from the declared eligible snapshot')
        if complete and url not in depths: add('UNREACHED_IN_SCOPE',url,'No observed path from declared entries; not a whole-site orphan claim')
        elif url in depths and depths[url]>cfg['max_depth']: add('DEPTH_THRESHOLD',url,'Observed shortest path exceeds configured threshold')
    for link in cfg['required_links']:
        source,dest=link['from'],link['to']
        if source not in docs or dest not in docs: add('REQUIRED_LINK_UNVERIFIED',source,'Required link evidence incomplete','error',dest)
        elif dest not in graph[source]: add('REQUIRED_LINK_MISSING',source,'Declared unqualified internal anchor missing','error',dest)
    topic_rows=[]
    for topic in cfg['topics']:
        needles=[' '+normalized(p)+' ' for p in topic['phrases']]
        same={u for u,p in eligible.items() if p['language'].lower()==topic['language']}
        matching=sorted(u for u in same & docs.keys() if any(n in segment for segment in docs[u].chunks for n in needles))
        if topic['target'] in matching: state='COVERED'
        elif not same <= docs.keys(): state='UNKNOWN'
        elif matching: state='OTHER_PAGE'
        else: state='GAP'
        topic_rows.append({'id':topic['id'],'language':topic['language'],'target':topic['target'],'priority':topic['priority'],'status':state,'matching_pages':matching})
        if state!='COVERED': add('TOPIC_'+state,topic['target'],'Operator-declared phrase coverage requires review','warning',topic['id'])
    findings.sort(key=lambda x:(x['severity']!='error',x['url'],x['code'],x.get('subject','')))
    return {'format':FORMAT,'origin':site['origin'],'policy_sha256':sha(canonical_json(cfg)), 'pages_expected':static['pages_expected'],'pages_verified':static['pages_verified'],'eligible_expected':len(eligible),'eligible_verified':len(docs),'graph_complete_in_scope':complete,'pages':observations,'topics':topic_rows,'findings':findings,'scope':'Declared static snapshots only; lexical coverage, observed links and local JSON-LD shapes; no search demand, indexing, ranking, traffic, conversion, rendering or rich-result guarantee.'}


def analyze(root,policy): return _scan(Snapshot(root),policy)


def plan(report):
    """Deterministic, evidence-linked priorities; not forecast revenue or automatic edits."""
    topics={t['id']:t for t in report['topics']}; actions=[]
    for f in report['findings']:
        code=f['code']; topic=topics.get(f.get('subject')) if code.startswith('TOPIC_') else None
        priority='P0' if f['severity']=='error' else 'P1' if topic and topic['priority']=='high' or code in ('NO_OBSERVED_INBOUND','UNREACHED_IN_SCOPE','SCHEMA_MISSING','SCHEMA_INCOMPLETE','SCHEMA_UNKNOWN','TOPIC_UNKNOWN') else 'P2'
        if code.startswith('TOPIC_'): action='Review actual user need and relevant content; improve or consolidate only if useful. Do not stuff phrases.'
        elif code.startswith(('SCHEMA_','JSONLD_')): action='Review declared structured data against visible page facts and official validators; never invent ratings or offers.'
        elif code.startswith(('REQUIRED_LINK','NO_OBSERVED_INBOUND','UNREACHED','DEPTH_')): action='Review navigation and add useful contextual links if appropriate; verify destination separately.'
        else: action='Review technical evidence and fix verified defects before release.'
        actions.append({'priority':priority,'code':code,'url':f['url'],'subject':f.get('subject'),'reason':f['detail'],'action':action})
    actions.sort(key=lambda a:(a['priority'],a['url'],a['code'],a['subject'] or ''))
    return {'format':'wgg-growth-plan/v1','origin':report['origin'],'evidence_sha256':sha(canonical_json(report)), 'priority_counts':{p:sum(a['priority']==p for a in actions) for p in ('P0','P1','P2')},'actions':actions,'scope':'Review queue only; no measured demand, forecast, edits, advertising or deployment authority.'}


class ScopedSnapshot:
    def __init__(self,source,prefix): self.source=source; self.prefix=prefix; self.cache={}
    def read(self,path):
        path_parts(path); raw=self.source.read(self.prefix+'/'+path); self.cache[path]=raw; return raw


def portfolio(root,name='intelligence-portfolio.json'):
    source=Snapshot(root); obj=json.loads(source.read(name)); require(isinstance(obj,dict) and set(obj)=={'sites'} and isinstance(obj['sites'],list) and 1<=len(obj['sites'])<=12,'Declare 1-12 sites')
    sites=[]; labels=set(); origins=set()
    for row in obj['sites']:
        require(isinstance(row,dict) and set(row)=={'label','snapshot','policy'},'Invalid intelligence portfolio entry')
        label=row['label']; require(isinstance(label,str) and bool(label.strip()) and len(label)<=80 and label not in labels and not any(ord(c)<32 for c in label),'Invalid or duplicate site label')
        labels.add(label); path_parts(row['snapshot']); path_parts(row['policy']); snap=ScopedSnapshot(source,row['snapshot'])
        cfg=validate_policy(json.loads(snap.read(row['policy']))); origin=cfg['site']['origin']; require(origin not in origins,'Duplicate portfolio origin'); origins.add(origin)
        report=_scan(snap,cfg); queue=plan(report)
        sites.append({'label':label,'report':report,'plan':queue})
    return {'format':'wgg-intelligence-portfolio/v1','sites':sites,'scope':'Fresh local snapshots within one bounded root; no live data or forecasts.'}
