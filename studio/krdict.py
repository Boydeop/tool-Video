"""KRDict (National Institute of Korean Language) evidence for educational QA.

Official API: https://krdict.korean.go.kr/api/search
`translated=y&trans_lang=7` yields Vietnamese dictionary senses.
An exact dictionary headword is evidence of existence, NOT proof that the
selected Vietnamese clue matches the correct sense in its context.
API calls use only user-owned keys and are cached; never log credentials.
"""
from __future__ import annotations
import re
import socket
import ssl
import unicodedata
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from itertools import product

from .keys import get_keys
from .run_limits import network_timeout, check_budget

ENDPOINT="https://krdict.korean.go.kr/api/search"
UA="TiengHanSoEasyStudio/1.2 (dictionary evidence)"
MAX_XML_BYTES=512_000
CACHE_VERSION=3

class KRDictError(RuntimeError):
    """A safe diagnostic; never includes the request URL or API key."""
    def __init__(self,code,message,*,retryable=True,retry_after=30):
        self.code=code
        self.retryable=retryable
        self.retry_after=retry_after
        super().__init__(message)

def normalize_vi(text):
    # Vietnamese tones distinguish meanings (cá/cà, đắt/đặt). Never erase
    # them when deciding whether dictionary evidence matches an answer.
    text=unicodedata.normalize("NFC",str(text or "").casefold())
    return re.sub(r"[\W_]+"," ",text).strip()

def _tokens(value):
    skips={"là","một","của","và","ở","có","những","cho","từ","nào","tiếng","hàn",
           "đây","được","để","với","nhiều"}
    words=normalize_vi(value).split()
    if len(words)>1 and words[0] in ('cái','chiếc') and words[1] not in ('tôi','gì','này','kia'):
        words=words[1:]
    return {v for v in words if v not in skips}


def lookup_headword(client,word,*,fresh=False):
    """Production QA accepts the whole headword only, never composed glosses."""
    data=client.lookup(word,fresh=True) if fresh else client.lookup(word)
    exact=data.get('exact') is True
    return {**data,'exact':exact,'supported':exact,'compound':False,'components':[]}


def lookup_evidence(client,word,*,fresh=False):
    """Legacy diagnostic analysis only; production QA uses lookup_headword."""
    cache={}
    def lookup(value,**_):
        check_budget()
        if value not in cache:
            cache[value]=client.lookup(value,fresh=True) if fresh else client.lookup(value)
        return cache[value]
    from types import SimpleNamespace
    cached=SimpleNamespace(lookup=lookup)
    result=lookup(word)
    if result.get('exact') or result.get('reason')=='key_missing':return result
    parts=word.split()
    if 2<=len(parts)<=3 and all(re.fullmatch(r'[가-힣]{1,20}',p) for p in parts):
        return _phrase_evidence(cached,parts) or result
    # Explicit hobby constructions only. Do not guess arbitrary syllable splits
    # (e.g. an unknown Christmas compound must not trigger a dictionary scan).
    if re.fullmatch(r'[가-힣]{4,10}',word):
        for ending in ('그리기','찍기','하기','감상'):
            if not word.endswith(ending) or len(word)<=len(ending):continue
            parts=[word[:-len(ending)],ending]
            composed=_phrase_evidence(cached,parts)
            if composed:
                return {**composed,'surface':word,'analysis':'compact_hobby_construction'}
    return result


def _phrase_evidence(client,parts):
    if len(parts)==2:
        predicate=_predicate_evidence(client,parts)
        if predicate is not None:return predicate
        action=_action_evidence(client,parts)
        if action is not None:return action
    units=[];choices=[]
    for part in parts:
        check_budget()
        data=client.lookup(part)
        glosses=[]
        for entry in data.get('entries',[]):
            if entry.get('pos') and entry['pos']!='명사':continue
            for gloss in entry.get('glosses',[]):
                for variant in re.split(r'[,;/]+',gloss):
                    variant=variant.strip()
                    if variant and len(variant)<=45 and variant not in glosses:glosses.append(variant)
        if not data.get('exact') or not glosses:return None
        units.append({'word':part,'glosses':glosses[:6],
                      'headword_codes':[e.get('target_code','') for e in data.get('entries',[])][:5]})
        choices.append(glosses[:6])
    # Vietnamese noun/modifier order differs from Korean. Token matching is
    # order independent; AI reverse review checks the actual composed meaning.
    meanings=[' '.join(values) for values in product(*choices)]
    return {'exact':False,'supported':True,'compound':True,'components':units,
            'entries':[{'word':' '.join(parts),'glosses':meanings,'meanings':meanings,
                        'target_code':'','definitions':[]}],
            'reason':'component_evidence'}



def _action_evidence(client,parts):
    noun,surface=parts
    # Productive -기 nominalization: 찍기 -> 찍다, 그리기 -> 그리다.
    # Only regular dictionary spelling is proposed, and the API must confirm it.
    verb=surface[:-1]+'다' if surface.endswith('기') else surface
    if not verb.endswith('다') or len(verb)<2:return None
    if verb=='하다':
        data=client.lookup(noun+'하다')
        entries=[e for e in data.get('entries',[]) if e.get('pos')=='동사' and e.get('glosses')]
        if not data.get('exact') or not entries:return None
        return {'exact':False,'supported':True,'compound':True,
                'components':[{'word':noun+'하다','surface':' '.join(parts),
                    'headword_codes':[e.get('target_code','') for e in entries]}],
                'entries':entries,'reason':'dictionary_nominalized_lemma'}
    units=[];choices=[]
    for word,pos in ((noun,'명사'),(verb,'동사')):
        data=client.lookup(word)
        entries=[e for e in data.get('entries',[]) if e.get('pos')==pos and e.get('glosses')]
        glosses=[]
        for e in entries:
            for gloss in e['glosses']:
                for value in re.split(r'[,;/]+',gloss):
                    value=value.strip()
                    if value and len(value)<=45 and value not in glosses:glosses.append(value)
        if not data.get('exact') or not glosses:return None
        units.append({'word':word,'surface':noun if pos=='명사' else surface,
                      'glosses':glosses[:6],'headword_codes':[e.get('target_code','') for e in entries][:5]})
        choices.append(glosses[:6])
    meanings=[' '.join(values) for values in product(*choices)]
    return {'exact':False,'supported':True,'compound':True,'components':units,
            'entries':[{'word':' '.join(parts),'glosses':meanings,'meanings':meanings,
                        'target_code':'','definitions':[]}],
            'reason':'noun_action_components'}


def _particle_stem(token, pairs):
    """Strip only an explicit, correctly selected subject/object particle."""
    for consonant,vowel in pairs:
        if len(token)<2:continue
        stem,particle=token[:-1],token[-1]
        if not all('가'<=ch<='힣' for ch in stem):continue
        has_final=(ord(stem[-1])-0xAC00)%28!=0
        if particle==(consonant if has_final else vowel):return stem
    return None


def _predicate_evidence(client,parts,*,fresh=False):
    """Ground limited dictionary-form predicates; never guess conjugations.

    Object+하다 first needs the complete compound in KRDict (기침을 하다 ->
    기침하다). Subject+predicate needs both a noun and an adjective, and
    still requires blind AI agreement for the whole phrase in strict QA.
    """
    first,predicate=parts
    def lookup(value):
        check_budget()
        return client.lookup(value,fresh=True) if fresh else client.lookup(value)
    obj=_particle_stem(first,[("을","를")])
    if obj and predicate=='하다':
        lemma=obj+'하다'
        data=lookup(lemma)
        entries=[entry for entry in data.get('entries',[]) if
                 entry.get('pos')=='동사' and entry.get('glosses')]
        if data.get('exact') and entries:
            return {'exact':False,'supported':True,'compound':True,
                'components':[{'word':lemma,'surface':first+' '+predicate,
                    'headword_codes':[e.get('target_code','') for e in entries]}],
                'entries':entries,'reason':'dictionary_predicate_lemma'}
        return None
    subject=_particle_stem(first,[("이","가")])
    if not subject or not predicate.endswith('다'):return None
    units=[];choices=[]
    for word,allowed in ((subject,{'명사'}),(predicate,{'형용사'})):
        data=lookup(word)
        glosses=[];codes=[]
        for entry in data.get('entries',[]):
            if entry.get('pos') not in allowed:continue
            codes.append(entry.get('target_code',''))
            for gloss in entry.get('glosses',[]):
                for variant in re.split(r'[,;/]+',gloss):
                    variant=variant.strip()
                    if variant and len(variant)<=45 and variant not in glosses:glosses.append(variant)
        if not data.get('exact') or not glosses:return None
        units.append({'word':word,'glosses':glosses[:6],'headword_codes':codes[:5]})
        choices.append(glosses[:6])
    meanings=[' '.join(values) for values in product(*choices)]
    return {'exact':False,'supported':True,'compound':True,'components':units,
        'entries':[{'word':' '.join(parts),'glosses':meanings,'meanings':meanings,
                    'target_code':'','definitions':[]}],
        'reason':'subject_predicate_components'}

_HOBBY_EQUIVALENTS=(
    ('nghe nhạc','thưởng thức âm nhạc','sự thưởng thức âm nhạc'),
    ('xem phim','thưởng thức phim','sự thưởng thức phim'),
    ('chụp ảnh','chụp hình','việc chụp ảnh','việc chụp hình'),
)
_HOBBY_TOKEN_GROUPS=[({frozenset(_tokens(phrase)) for phrase in group},_tokens(group[0]))
                     for group in _HOBBY_EQUIVALENTS]


def _meaning_tokens(value):
    tokens=_tokens(value)
    for group,canonical in _HOBBY_TOKEN_GROUPS:
        if frozenset(tokens) in group:return canonical
    return tokens


def sense_overlap(vietnamese,translations):
    """Conservative lexical evidence; NOT a semantic equivalence classifier."""
    expected=_meaning_tokens(vietnamese)
    if not expected:return 0.0
    maximum=0.0
    for candidate in translations:
        # Parenthetical explanations are not synonyms ("sau mùa đông").
        candidate=re.sub(r"\([^()]*\)","",candidate)
        variants=re.split(r"[,;/]+",candidate)
        for part in variants:
            other=_meaning_tokens(part)
            if other:
                maximum=max(maximum,len(expected&other)/len(expected|other))
                # Loanword and compound matching can vary; leave below-threshold
                # matches for AI + manual review rather than guessing.
    return round(maximum,3)

def parse_search(xml_bytes,expected=None):
    if not isinstance(xml_bytes,bytes) or len(xml_bytes)>MAX_XML_BYTES:
        raise ValueError("KRDict response too large")
    try:root=ET.fromstring(xml_bytes)
    except ET.ParseError:
        raise KRDictError("invalid_response","KRDict trả dữ liệu không phải XML hợp lệ; kiểm tra proxy hoặc dịch vụ.") from None
    # Handle both standalone errors and errors wrapped in an API response.
    for node in root.iter():
        if node.tag=="error_code":
            code=(node.text or "").strip()
            code=code if re.fullmatch(r"[A-Za-z0-9_-]{1,24}",code) else "unknown"
            raise KRDictError("api_error","KRDict từ chối yêu cầu (mã "+code+
                              "). Kiểm tra trạng thái key và hạn mức trên trang KRDict.")
    if root.tag!="channel":
        raise KRDictError("invalid_response","KRDict trả cấu trúc không hợp lệ; chưa thể xác nhận API key.")
    try:
        if int(root.findtext("total",""))<0:raise ValueError
    except ValueError:
        raise KRDictError("invalid_response","KRDict thiếu tổng số kết quả hợp lệ.") from None
    found=[]
    for entry in root.findall("./item"):
        word=(entry.findtext("word") or "").strip()
        if expected is not None and unicodedata.normalize("NFC",word)!=unicodedata.normalize("NFC",expected.strip()):
            continue  # search may return fuzzy matches; exact headword only
        meanings=[];glosses=[];definitions=[]
        for sense in entry.findall("./sense"):
            # trans_lang=7 is requested but we still filter bilingual elements
            for part in sense.findall("./translation"):
                language=(part.findtext("trans_lang") or "").strip().casefold()
                if language and "베트남" not in language and "viet" not in language:
                    continue
                for tag in ("trans_word","trans_dfn"):
                    value=(part.findtext(tag) or "").strip()
                    if value and value not in meanings:meanings.append(value)
                    values=glosses if tag=="trans_word" else definitions
                    if value and value not in values:values.append(value)
        found.append({"word":word,
                      "target_code":(entry.findtext("target_code") or "").strip(),
                      "pos":(entry.findtext("pos") or "").strip(),
                      "meanings":meanings,
                      "glosses":glosses,"definitions":definitions,
                      "link":(entry.findtext("link") or "").strip()})
    return {"exact":expected is not None and bool(found),"entries":found}

class KRDict:
    def __init__(self,store,transport=None):
        self.store=store
        self.transport=transport

    def _request(self,word,key,*,whole_word=True):
        query=urllib.parse.urlencode({
            "key":key,"q":word,"translated":"y","trans_lang":"7",
            "part":"word","num":"100","method":"exact" if whole_word else "include"})
        req=urllib.request.Request(ENDPOINT+"?"+query,headers={"User-Agent":UA})
        try:
            if self.transport:
                raw=self.transport(req)
            else:
                with urllib.request.urlopen(req,timeout=network_timeout(6,10)) as handle:
                    if handle.geturl().split("?")[0]!=ENDPOINT:
                        raise RuntimeError("KRDict unexpected redirect")
                    raw=handle.read(MAX_XML_BYTES+1)
            return parse_search(raw,word if whole_word else None)
        except urllib.error.HTTPError as exc:
            hint=("Kiểm tra key và quyền API." if exc.code in (401,403) else
                  "Đã chạm giới hạn yêu cầu; thử lại sau." if exc.code==429 else
                  "Kiểm tra trạng thái dịch vụ KRDict.")
            raise KRDictError("http_error","KRDict HTTP "+str(exc.code)+". "+hint,
                              retryable=exc.code not in (401,403),
                              retry_after=120 if exc.code==429 else 30) from None
        except (urllib.error.URLError,TimeoutError) as exc:
            reason=getattr(exc,"reason",exc)
            if isinstance(reason,ssl.SSLError):
                message="Không xác minh được TLS của KRDict. Kiểm tra chứng chỉ Windows/proxy; không tắt xác minh TLS."
                code="tls_error"
            elif isinstance(reason,(TimeoutError,socket.timeout)):
                message="KRDict hết thời gian chờ. Kiểm tra mạng và thử lại."
                code="timeout"
            else:
                message="Không kết nối được krdict.korean.go.kr. Kiểm tra DNS, proxy hoặc tường lửa."
                code="network_error"
            raise KRDictError(code,message) from None

    def search(self,query):
        """Retrieve official headwords; a search query is never an answer proof."""
        query=unicodedata.normalize('NFC',str(query).strip())
        if not re.fullmatch(r'[가-힣 ]{1,20}',query):raise ValueError('Từ tra KRDict cần là tiếng Hàn ngắn')
        check_budget()
        keys=get_keys('krdict')
        if not keys:raise KRDictError('key_missing','Chưa có KRDict API key',retryable=False)
        data=self._request(query,keys[0],whole_word=False)
        entries=[entry for entry in data.get('entries',[]) if entry.get('word') and
                 entry.get('target_code') and entry.get('glosses')]
        # Cache only actual returned headwords under their own spelling. Never
        # turn the query itself into an exact hit or synthesize headword IDs.
        by_word={}
        for entry in entries:by_word.setdefault(entry['word'],[]).append(entry)
        for word,rows in by_word.items():
            previous=self.store.krdict_cache_get(word)
            old=previous.get('entries',[]) if isinstance(previous,dict) and previous.get('cache_version')==CACHE_VERSION else []
            merged={str(row['target_code']):row for row in [*old,*rows] if row.get('target_code') and row.get('word')==word}
            self.store.krdict_cache_put(word,{'exact':True,'entries':list(merged.values()),'cache_version':CACHE_VERSION})
        return {'entries':entries,'source':'KRDict','query':query}

    def lookup(self,word,*,fresh=False):
        word=unicodedata.normalize("NFC",str(word).strip())
        if not 1<=len(word)<=60 or not re.search(r"[\uac00-\ud7a3]",word):
            return {"exact":False,"entries":[],"reason":"not_a_lexical_headword"}
        check_budget()
        keys=get_keys("krdict")
        if not keys:
            return {"exact":False,"entries":[],"reason":"key_missing"}
        cached=None if fresh else self.store.krdict_cache_get(word)
        if isinstance(cached,dict) and cached.get("cache_version")==CACHE_VERSION:
            return {**cached,"cache_hit":True}
        # Do not rotate on rate-limit to evade provider's quota.
        result={**self._request(word,keys[0]),"cache_version":CACHE_VERSION}
        self.store.krdict_cache_put(word,result)
        return {**result,"cache_hit":False}

    def test_connection(self):
        """Always authenticate against the service, even with a warm cache."""
        result=self.lookup("물",fresh=True)
        if result.get("reason")=="key_missing":
            raise KRDictError("key_missing","Chưa nhập KRDict API key trong mục API & Model.")
        vietnamese=any(entry.get("meanings") for entry in result["entries"])
        return {"connected":True,"word":"물","entries":result["entries"][:2],
                "has_vietnamese":vietnamese,"cache_hit":False,
                "message":("KRDict đã nhận key và trả nghĩa tiếng Việt." if vietnamese else
                           "KRDict đã nhận yêu cầu nhưng chưa trả nghĩa Việt cho mục từ thử; chưa đủ bằng chứng để QA strict.")}
