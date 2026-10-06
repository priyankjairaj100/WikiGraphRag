"""Frozen conventional retrieval diagnostics. No labels or question families enter retrieval."""
from __future__ import annotations
from collections import Counter, defaultdict
from bisect import bisect_right
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import html
import json
import math
import re
from temporal_state.typed_reader_v15_1 import _parse, read_inline_xbrl

STOP = frozenset('a an and are as at be been between by did do does each for from had has have how if in into is it its of on or same state than that the their then these they this to was were what when where which with without company report reported fiscal source sources disclose disclosed please only do not'.split())
BLOCK_TAGS = frozenset(['p','div','li','h1','h2','h3','h4','h5','h6','caption','dt','dd','blockquote'])
SKIP_TAGS = frozenset(['script','style','head','hidden','context','unit','references','resources'])
POLICIES = ('typed','lexical','structural','decomposition')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def local(tag):
    return tag.rsplit('}',1)[-1]


def words(text):
    return len(text.split())


def tokens(text):
    text = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', ' ', text)
    # Generic plural normalization, no domain-specific synonym dictionary.
    return [w[:-1] if len(w)>4 and w.endswith('s') and not w.endswith('ss') else w
            for w in re.findall(r'[a-z0-9]+', text.lower()) if w not in STOP]


def merge_intervals(spans):
    out = []
    for a,b in sorted(set(map(tuple,spans))):
        if not 0 <= a < b:
            raise ValueError('invalid source interval')
        if out and a <= out[-1][1]: out[-1][1] = max(b,out[-1][1])
        else: out.append([a,b])
    return out


@dataclass
class Block:
    block_id: str
    source: str
    source_sha256: str
    kind: str
    text: str
    search_text: str
    start: int
    stop: int
    intervals: list
    table: str | None = None
    links: list = field(default_factory=list)
    dependencies: list = field(default_factory=list)
    typed_context: dict | None = None
    fiscal_year: str | None = None
    table_extent: list | None = None
    header_row: bool = False
    occurrence_anchors: list = field(default_factory=list)

    def render(self):
        return f'[source={self.source} actual_filing_fy={self.fiscal_year} source_sha256={self.source_sha256} block={self.block_id} kind={self.kind}]\n{self.text}'

    def selector(self):
        return {'block_id':self.block_id,'source_version':self.source,
                'source_sha256':self.source_sha256,'actual_filing_fy':self.fiscal_year,'kind':self.kind,
                'byte_start':self.start,'byte_stop':self.stop,
                'rendered_source_intervals':self.intervals,
                'render_sha256':digest(self.render().encode()),
                'render_words':words(self.render()),
                'typed_dependency_anchors':self.dependencies,'typed_occurrence_anchors':self.occurrence_anchors}


def _hidden(node):
    tag = local(node.tag)
    if tag in SKIP_TAGS or node.attrs.get('hidden') is not None: return True
    if tag=='header' and node.tag!='{http://www.w3.org/1999/xhtml}header': return True
    style = re.sub(r'\s+','',node.attrs.get('style','')).lower()
    return 'display:none' in style or 'visibility:hidden' in style


def precision_status(variants):
    values={v['normalized_value'] for v in variants}
    if len(values)==1: return 'identical_reported_values'
    intervals=[]; accuracy_values=defaultdict(set)
    try:
        for v in variants:
            decimal=Decimal(v['normalized_value']); decimals=v.get('decimals')
            if not decimal.is_finite() or abs(decimal.adjusted())>10000:return 'unresolved_precision'
            x=Fraction(decimal)
            if decimals=='INF' or v.get('precision')=='INF': margin=Fraction(0);accuracy_key=('exact',None)
            elif decimals is not None:
                d=int(decimals)
                if abs(d)>10000:return 'unresolved_precision'
                margin=Fraction(10)**(-d)/2;accuracy_key=('decimals',d)
            else:return 'unresolved_precision'
            intervals.append((x-margin,x+margin))
            accuracy_values[accuracy_key].add(x)
    except (InvalidOperation,TypeError,ValueError,OverflowError):return 'unresolved_precision'
    if max(a for a,b in intervals)>min(b for a,b in intervals):return 'distinct_nonoverlapping_reported_values'
    if any(len(v)>1 for v in accuracy_values.values()):return 'distinct_equal_accuracy_reported_values'
    return 'compatible_reported_rounding_intervals'


def build_index(source: bytes, name: str, fiscal_year: str | None = None):
    """One-owner visible text partition plus separately charged parsed-aspect cards."""
    nodes = _parse(source)
    sha = digest(source)
    visible, owner, table = {}, {}, {}
    by_start = {n.start:n for n in nodes}
    for n in nodes:
        parent = n.parent
        visible[n.start] = not _hidden(n) and (parent is None or visible[parent.start])
        inherited_table = table.get(parent.start) if parent else None
        table[n.start] = n.start if local(n.tag)=='table' else inherited_table
        old = owner.get(parent.start) if parent else None
        if local(n.tag)=='tr': own = n.start
        elif old is not None and local(by_start[old].tag)=='tr': own = old
        elif local(n.tag) in BLOCK_TAGS: own = n.start
        else: own = old
        owner[n.start] = own
    fragments = defaultdict(list)
    subtree_owners = defaultdict(set)
    hidden_descendant = set()
    link_targets = {}
    omitted_markup = re.compile(br'<!--.*?-->|<\?.*?\?>|<!\[CDATA\[.*?\]\]>',re.S)
    for n in nodes:
        if 'id' in n.attrs: link_targets[n.attrs['id']] = n.start
        if not visible[n.start]:
            for a in n.ancestors(): hidden_descendant.add(a.start)
            continue
        cursor = n.opening_stop
        ranges = []
        for child in n.children:
            ranges.append((cursor,child.start)); cursor=child.stop
        close = n.opening_stop if n.self_closing else source.rfind(b'</', n.opening_stop, n.stop)
        ranges.append((cursor,max(cursor,close)))
        own = owner[n.start] if owner[n.start] is not None else n.start
        for begin,end in ranges:
            cursor=begin; pieces=[]
            for m in omitted_markup.finditer(source,begin,end):
                pieces.append((cursor,m.start(),False))
                if source[m.start():m.start()+9]==b'<![CDATA[':
                    pieces.append((m.start()+9,m.end()-3,True))
                else:
                    hidden_descendant.add(n.start)
                    for a in n.ancestors(): hidden_descendant.add(a.start)
                cursor=m.end()
            pieces.append((cursor,end,False))
            for a,z,is_cdata in pieces:
                if z<=a: continue
                decoded=source[a:z].decode('utf-8')
                text=decoded if is_cdata else html.unescape(decoded)
                if not text.strip(): continue
                fragments[own].append((a,z,text))
                subtree_owners[n.start].add(own)
                for anc in n.ancestors(): subtree_owners[anc.start].add(own)
    def owned_text(n,own):
        if not visible[n.start]: return ''
        node_owner=owner[n.start] if owner[n.start] is not None else n.start
        if node_owner!=own: return ' '
        out=[]
        for part in n.parts:
            if isinstance(part,str): out.append(part)
            else:
                value=owned_text(part,own)
                if local(part.tag) in ('td','th','br','tr','p','div','li'): value=' '+value+' '
                out.append(value)
        return ''.join(out)
    blocks=[]
    for own,frags in fragments.items():
        n=by_start[own]
        frags.sort()
        text=' '.join(owned_text(n,own).split())
        intervals = ([[n.start,n.stop]] if subtree_owners[n.start]=={own} and n.start not in hidden_descendant
                     else merge_intervals([[a,b] for a,b,_ in frags]))
        kind='row' if local(n.tag)=='tr' else 'text'
        block_id=f'{name}:{own}:{kind}'
        links=[]
        for sub in n.walk():
            href=sub.attrs.get('href','')
            if visible.get(sub.start) and href.startswith('#') and href[1:] in link_targets:
                links.append(link_targets[href[1:]])
        blocks.append(Block(block_id,name,sha,kind,text,text,n.start,n.stop,intervals,
                            f'{name}:table:{table[n.start]}' if table[n.start] is not None else None,
                            sorted(set(links)),header_row=(kind=='row' and any(local(x.tag)=='th' for x in n.walk()))))
    blocks.sort(key=lambda b:b.start)
    visible_blocks=blocks[:]
    visible_by_start={b.start:b for b in visible_blocks}
    document=read_inline_xbrl(source,source_version=name)
    grouped=defaultdict(list)
    for fact in document['facts']:
        a=fact['anchor']; start=a['byte_start']; n=by_start.get(start)
        if n is None or not visible.get(start) or fact['binding_status']!='reported_aspects_resolved' or fact.get('status')!='normalized': continue
        grouped[canonical(fact['reported_aspects'])].append(fact)
        term=local(fact['concept']); own=owner.get(start)
        if own in visible_by_start: visible_by_start[own].search_text += ' '+term
    def variant(f):
        return {'normalized_value':f.get('normalized_value'),'lexical_text':f.get('lexical_text'),
                'decimals':f.get('accuracy',{}).get('decimals'),
                'precision':f.get('accuracy',{}).get('precision')}
    card_groups=[]
    for facts in grouped.values():
        status=precision_status([variant(f) for f in facts])
        if status in ('identical_reported_values','compatible_reported_rounding_intervals'):card_groups.append(facts)
        else:
            separate=defaultdict(list)
            for f in facts:separate[f['normalized_value']].append(f)
            card_groups.extend(separate.values())
    for facts in card_groups:
        # Exact same reported binding only; every distinct reported value remains.
        fact=facts[0]; a=fact['anchor']; start=a['byte_start']
        variants=[]; seen=set()
        for f in facts:
            v=variant(f)
            key=canonical(v)
            if key not in seen: variants.append(v);seen.add(key)
        precision=precision_status(variants)
        card={'concept':fact['concept'],'reported_aspects':fact['reported_aspects'],
              'reported_variants':variants,'duplicate_precision_status':precision,
              'source_occurrences':len(facts)}
        text=json.dumps(card,sort_keys=True,ensure_ascii=False,indent=2)
        term=local(fact['concept'])
        deps={canonical(a):a for f in facts for a in f['evidence_anchors']}
        blocks.append(Block(f'{name}:{start}:typed',name,sha,'typed',text,term+' '+text,start,a['byte_stop'],[],
                            f'{name}:table:{table[start]}' if table[start] is not None else None,
                            dependencies=list(deps.values()),typed_context=card,
                            occurrence_anchors=[{'fact_ordinal':f['fact_ordinal'],**f['anchor']} for f in facts]))
    blocks.sort(key=lambda b:(b.start,b.kind))
    for b in blocks:
        b.fiscal_year=fiscal_year
        if b.table:
            tnode=by_start[int(b.table.rsplit(":",1)[-1])]
            b.table_extent=[tnode.start,tnode.stop]
    return blocks, {'source_version':name,'source_sha256':sha,'source_bytes':len(source),
                    'visible_blocks':len(visible_blocks),'typed_cards':len(blocks)-len(visible_blocks),
                    'visible_text_words':sum(words(b.text) for b in visible_blocks),
                    'index_sha256':digest(canonical([b.__dict__ for b in blocks]).encode()),
                    'xml_nodes':len(nodes),'fully_hidden_nodes':sum(not x for x in visible.values())}


class BM25:
    def __init__(self,blocks,k1=1.2,b=0.75):
        self.blocks=blocks; self.k1=k1; self.b=b
        self.tf=[Counter(tokens(x.search_text)) for x in blocks]
        self.length=[sum(c.values()) for c in self.tf]
        self.avg=sum(self.length)/len(blocks) if blocks else 1
        self.df=Counter(t for c in self.tf for t in c)
    def rank(self,query):
        terms=set(tokens(query)); n=len(self.blocks); ranked=[]
        for i,c in enumerate(self.tf):
            score=0.0
            for t in terms:
                tf=c.get(t,0)
                if not tf: continue
                idf=math.log(1+(n-self.df[t]+0.5)/(self.df[t]+0.5))
                score+=idf*tf*(self.k1+1)/(tf+self.k1*(1-self.b+self.b*self.length[i]/self.avg))
            if score>0: ranked.append((i,score))
        return sorted(ranked,key=lambda p:(-p[1],self.blocks[p[0]].source,self.blocks[p[0]].start,self.blocks[p[0]].kind))


def decompose(question):
    clauses=[x.strip(' ,:') for x in re.split(r'[?;.]+|\b(?:and|whether|while)\b',question,flags=re.I)]
    queries=[question]
    for c in clauses:
        if len(tokens(c))>=2 and c not in queries: queries.append(c)
    # Content-only variant is generic, and reveals no answer/family/source annotation.
    content=' '.join(tokens(question))
    if content and content not in queries: queries.append(content)
    return queries


def retrieve(blocks,question,budget,policy):
    if policy not in POLICIES: raise ValueError('unknown policy')
    if type(budget) is not int or budget < 1: raise ValueError('invalid budget')
    fiscal_years=sorted(set(re.findall(r'\bfiscal\s+(20\d{2})\b',question,re.I)))
    eligible_sources=sorted(set(b.source for b in blocks))
    if policy in ('typed','structural','decomposition') and len(fiscal_years)==1:
        matching=[b for b in blocks if b.fiscal_year==fiscal_years[0]]
        if matching:
            blocks=matching
            eligible_sources=sorted(set(b.source for b in blocks))
    candidates=[b for b in blocks if b.kind=='typed'] if policy=='typed' else blocks
    index=BM25(candidates)
    queries=decompose(question) if policy=='decomposition' else [question]
    rankings=[index.rank(q) for q in queries]
    if len(rankings)==1: ranking=rankings[0]
    else:
        scores=defaultdict(float)
        for rr in rankings:
            for rank,(i,_) in enumerate(rr,1): scores[i]+=1/(60+rank)
        ranking=sorted(scores.items(),key=lambda x:(-x[1],candidates[x[0]].source,candidates[x[0]].start,candidates[x[0]].kind))
    by_source=defaultdict(list); tables=defaultdict(list)
    for b in blocks:
        if b.kind!='typed':
            by_source[b.source].append(b)
            if b.table: tables[b.table].append(b)
    for bs in by_source.values(): bs.sort(key=lambda b:b.start)
    closed_tables=set()
    for b in blocks:
        if b.table and b.table_extent and b.table not in closed_tables:
            closed_tables.add(b.table)
            a,z=b.table_extent
            tables[b.table]=[x for x in by_source[b.source] if a<=x.start and x.stop<=z]
    for bs in tables.values(): bs.sort(key=lambda b:b.start)
    source_starts={source:[b.start for b in bs] for source,bs in by_source.items()}
    preceding_headers={}
    for bs in by_source.values():
        prior=[]
        for b in bs:
            preceding_headers[b.block_id]=prior[-2:]
            if b.kind!='row' and words(b.text)<=80:prior.append(b)
    selected=[]; ids=set(); used=0; decisions=[]
    def add(bundle):
        nonlocal used
        fresh=[]; seen=set(ids)
        for b in bundle:
            if b.block_id not in seen:
                fresh.append(b); seen.add(b.block_id)
        cost=sum(words(b.render()) for b in fresh)
        if used+cost>budget: return False
        selected.extend(fresh); ids.update(b.block_id for b in fresh); used+=cost
        return True
    def link_blocks(bundle):
        out=[]
        for item in bundle:
            visible=by_source[item.source]
            for loc in item.links:
                hits=[x for x in visible if any(a<=loc<z for a,z in x.intervals)]
                if hits: out.append(min(hits,key=lambda x:(abs(x.start-loc),x.stop-x.start)))
                else:
                    following=[x for x in visible if x.start>=loc]
                    if following:out.append(min(following,key=lambda x:x.start))
        return out
    def closure(b):
        visible=by_source[b.source]
        if not visible:return [b],[b]
        starts=source_starts[b.source]
        pos=max(0,bisect_right(starts,b.start)-1)
        around=visible[max(0,pos-2):min(len(visible),pos+3)]
        heading=preceding_headers[visible[pos].block_id]
        tableblocks=tables.get(b.table,[]) if b.table else []
        if tableblocks:
            headers=[x for x in tableblocks[:5] if x.header_row or not re.search(r'\d',x.text)
                     or re.search(r'\b20\d\d\b|million|thousand|year|percent|\(%\)|\(\$\)',x.text,re.I)]
            tail=bisect_right(starts,max(x.stop for x in tableblocks))
            after=visible[tail:tail+2]
            core=headers+[b]
            if b.kind=='typed' and visible[pos].start<=b.start<visible[pos].stop:core.append(visible[pos])
            whole=heading+tableblocks+[b]+after
            return whole+link_blocks(whole),core+link_blocks(core)
        whole=heading+[b]+around
        core=heading+[b]
        return whole+link_blocks(whole),core+link_blocks(core)
    for i,score in ranking:
        b=candidates[i]
        if b.block_id in ids: continue
        if policy in ('structural','decomposition'):
            whole,fallback=closure(b)
            if add(whole): mode='whole_closure'
            elif add(fallback): mode='row_header_fallback'
            else: mode='over_budget'
        else: mode='block' if add([b]) else 'over_budget'
        decisions.append({'seed_block_id':b.block_id,'rank_score':score,'decision':mode})
    rendered='\n\n'.join(b.render() for b in selected)
    if words(rendered)!=used or used>budget: raise AssertionError('rendered budget mismatch')
    spans=defaultdict(list)
    for b in selected: spans[b.source].extend(b.intervals)
    return {'policy':policy,'budget_words':budget,'actual_queries':queries,
            'eligible_source_files_after_date_rule':eligible_sources,
            'question_fiscal_years':fiscal_years,
            'selected_block_ids':[b.block_id for b in selected],
            'selected_blocks':[b.selector() for b in selected],
            'rendered_words':used,'rendered_sha256':digest(rendered.encode()),
            'rendered_source_intervals':{s:merge_intervals(v) for s,v in sorted(spans.items())},
            'ranking_candidates':len(ranking),'decisions':decisions},rendered
