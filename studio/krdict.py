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


def lookup_evidence(client,word,*,fresh=False):
    """Recognize spaced noun phrases, without claiming an exact headword.

    Component coverage is lexical evidence only; strict QA must also obtain
    blind AI agreement for the meaning of the complete phrase.
    """
    result=client.lookup(word,fresh=True) if fresh else client.lookup(word)
    if result.get('exact') or result.get('reason')=='key_missing':return result
    parts=word.split()
    if not 2<=len(parts)<=3 or any(not re.fullmatch(r'[가-힣]{1,20}',p) for p in parts):
        return result
    units=[];choices=[]
    for part in parts:
        check_budget()
        data=client.lookup(part,fresh=True) if fresh else client.lookup(part)
        glosses=[]
        for entry in data.get('entries',[]):
            if entry.get('pos') and entry['pos']!='명사':continue
            for gloss in entry.get('glosses',[]):
                for variant in re.split(r'[,;/]+',gloss):
                    variant=variant.strip()
                    if variant and len(variant)<=45 and variant not in glosses:glosses.append(variant)
        if not data.get('exact') or not glosses:return result
        units.append({'word':part,'glosses':glosses[:6],
                      'headword_codes':[e.get('target_code','') for e in data.get('entries',[])][:5]})
        choices.append(glosses[:6])
    # Vietnamese noun/modifier order differs from Korean. Token matching is
    # order independent; AI reverse review checks the actual composed meaning.
    meanings=[' '.join(values) for values in product(*choices)]
    return {**result,'supported':True,'compound':True,'components':units,
            'entries':[{'word':word,'glosses':meanings,'meanings':meanings,
                        'target_code':'','definitions':[]}],
            'reason':'component_evidence'}

def sense_overlap(vietnamese,translations):
    """Conservative lexical evidence; NOT a semantic equivalence classifier."""
    expected=_tokens(vietnamese)
    if not expected:return 0.0
    maximum=0.0
    for candidate in translations:
        # Parenthetical explanations are not synonyms ("sau mùa đông").
        candidate=re.sub(r"\([^()]*\)","",candidate)
        variants=re.split(r"[,;/]+",candidate)
        for part in variants:
            other=_tokens(part)
            if other:
                maximum=max(maximum,len(expected&other)/len(expected|other))
                # Loanword and compound matching can vary; leave below-threshold
                # matches for AI + manual review rather than guessing.
    return round(maximum,3)

def parse_search(xml_bytes,expected):
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
        if unicodedata.normalize("NFC",word)!=unicodedata.normalize("NFC",expected.strip()):
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
    return {"exact":bool(found),"entries":found}

class KRDict:
    def __init__(self,store,transport=None):
        self.store=store
        self.transport=transport

    def _request(self,word,key):
        query=urllib.parse.urlencode({
            "key":key,"q":word,"translated":"y","trans_lang":"7",
            "part":"word","num":"100"})
        req=urllib.request.Request(ENDPOINT+"?"+query,headers={"User-Agent":UA})
        try:
            if self.transport:
                raw=self.transport(req)
            else:
                with urllib.request.urlopen(req,timeout=network_timeout(6,10)) as handle:
                    if handle.geturl().split("?")[0]!=ENDPOINT:
                        raise RuntimeError("KRDict unexpected redirect")
                    raw=handle.read(MAX_XML_BYTES+1)
            return parse_search(raw,word)
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
