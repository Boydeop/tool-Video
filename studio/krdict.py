"""KRDict (National Institute of Korean Language) evidence for educational QA.

Official API: https://krdict.korean.go.kr/api/search
`translated=y&trans_lang=7` yields Vietnamese dictionary senses.
An exact dictionary headword is evidence of existence, NOT proof that the
selected Vietnamese clue matches the correct sense in its context.
API calls use only user-owned keys and are cached; never log credentials.
"""
from __future__ import annotations
import re
import unicodedata
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

from .keys import get_keys

ENDPOINT="https://krdict.korean.go.kr/api/search"
UA="TiengHanSoEasyStudio/1.2 (dictionary evidence)"
MAX_XML_BYTES=512_000

def normalize_vi(text):
    stripped=unicodedata.normalize("NFKD",str(text or "").casefold())
    stripped="".join("d" if char=="đ" else char for char in stripped
                     if not unicodedata.combining(char))
    return re.sub(r"\s+"," ",re.sub(r"[^a-z0-9]+"," ",stripped)).strip()

def _tokens(value):
    skips={"la","mot","cua","va","o","co","nhung","cho","tu","nao","tieng","han",
           "day","duoc","de","voi","nhieu"}
    return {v for v in normalize_vi(value).split() if len(v)>1 and v not in skips}

def sense_overlap(vietnamese,translations):
    """Conservative lexical evidence; NOT a semantic equivalence classifier."""
    expected=_tokens(vietnamese)
    if not expected:return 0.0
    maximum=0.0
    for candidate in translations:
        variants=re.split(r"[,;/()]+",candidate)
        for part in variants:
            other=_tokens(part)
            if other:
                maximum=max(maximum,len(expected&other)/max(1,len(expected)))
                # Loanword and compound matching can vary; leave below-threshold
                # matches for AI + manual review rather than guessing.
    return round(maximum,3)

def parse_search(xml_bytes,expected):
    if not isinstance(xml_bytes,bytes) or len(xml_bytes)>MAX_XML_BYTES:
        raise ValueError("KRDict response too large")
    try:root=ET.fromstring(xml_bytes)
    except ET.ParseError as exc:raise ValueError("KRDict XML invalid") from exc
    if root.tag=="error":
        code=root.findtext("error_code","unknown")
        raise RuntimeError("KRDict error "+str(code)+"; check key/quota")
    found=[]
    for entry in root.findall("./item"):
        word=(entry.findtext("word") or "").strip()
        if unicodedata.normalize("NFC",word)!=unicodedata.normalize("NFC",expected.strip()):
            continue  # search may return fuzzy matches; exact headword only
        meanings=[]
        for sense in entry.findall("./sense"):
            # trans_lang=7 is requested but we still filter bilingual elements
            for part in sense.findall("./translation"):
                language=(part.findtext("trans_lang") or "").strip().casefold()
                if language and "베트남" not in language and "viet" not in language:
                    continue
                for tag in ("trans_word","trans_dfn"):
                    value=(part.findtext(tag) or "").strip()
                    if value and value not in meanings:meanings.append(value)
        found.append({"word":word,
                      "target_code":(entry.findtext("target_code") or "").strip(),
                      "pos":(entry.findtext("pos") or "").strip(),
                      "meanings":meanings,
                      "link":(entry.findtext("link") or "").strip()})
    return {"exact":bool(found),"entries":found[:5]}

class KRDict:
    def __init__(self,store,transport=None):
        self.store=store
        self.transport=transport

    def _request(self,word,key):
        query=urllib.parse.urlencode({
            "key":key,"q":word,"translated":"y","trans_lang":"7",
            "part":"word","num":"10"})
        req=urllib.request.Request(ENDPOINT+"?"+query,headers={"User-Agent":UA})
        try:
            if self.transport:
                raw=self.transport(req)
            else:
                with urllib.request.urlopen(req,timeout=10) as handle:
                    if handle.geturl().split("?")[0]!=ENDPOINT:
                        raise RuntimeError("KRDict unexpected redirect")
                    raw=handle.read(MAX_XML_BYTES+1)
            return parse_search(raw,word)
        except urllib.error.HTTPError as exc:
            raise RuntimeError("KRDict HTTP "+str(exc.code)+"; key/quota issue") from None
        except (urllib.error.URLError,TimeoutError) as exc:
            raise RuntimeError("KRDict unavailable; try later") from None

    def lookup(self,word):
        word=unicodedata.normalize("NFC",str(word).strip())
        if not 1<=len(word)<=60 or not re.search(r"[\uac00-\ud7a3]",word):
            return {"exact":False,"entries":[],"reason":"not_a_lexical_headword"}
        cached=self.store.krdict_cache_get(word)
        if cached is not None:return {**cached,"cache_hit":True}
        keys=get_keys("krdict")
        if not keys:
            return {"exact":False,"entries":[],"reason":"key_missing"}
        # Do not rotate on rate-limit to evade provider's quota.
        result=self._request(word,keys[0])
        self.store.krdict_cache_put(word,result)
        return {**result,"cache_hit":False}
