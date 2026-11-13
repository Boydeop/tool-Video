"""User-owned API key pools, round-robin selection and quota-safe failover.

429 applies a cooldown to the WHOLE provider, never to an individual key;
switching keys to avoid an active provider limit is intentionally prohibited.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import threading
import time

PROVIDERS = ("gemini", "groq", "openrouter")
_LOCK = threading.RLock()
_CURSOR = {}
_BLOCKED_UNTIL = {}

def _path():
    return Path(os.getenv("THSE_KEYS_FILE", "data/keys.local.json")).resolve()

def _split(value):
    if isinstance(value,list):
        candidates=value
    else:
        candidates=re.split(r"[,;\n\r]+",str(value or ""))
    seen=set()
    output=[]
    for key in candidates:
        key=str(key).strip()
        if key and key not in seen:
            output.append(key)
            seen.add(key)
    return output[:30]

def _settings():
    path=_path()
    if not path.exists(): return {}
    try:
        payload=json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload,dict) else {}
    except (OSError, ValueError): return {}

def get_keys(provider):
    if provider not in PROVIDERS: return []
    from_file=_split(_settings().get(provider,[]))
    from_env=_split(os.getenv(provider.upper()+"_API_KEYS",""))
    single=os.getenv(provider.upper()+"_API_KEY","").strip()
    return _split(from_file+from_env+([single] if single else []))

def key_counts():
    return {provider:len(get_keys(provider)) for provider in PROVIDERS}

def write_keys(provider,value):
    if provider not in PROVIDERS: raise ValueError("Invalid provider")
    keys=_split(value)
    if any(len(k)>500 for k in keys): raise ValueError("API key too long")
    path=_path()
    path.parent.mkdir(parents=True,exist_ok=True)
    with _LOCK:
        current=_settings()
        current[provider]=keys
        tmp=path.with_suffix(".tmp")
        tmp.write_text(json.dumps(current,ensure_ascii=False,indent=2),encoding="utf-8")
        if os.name!="nt": os.chmod(tmp,0o600)
        tmp.replace(path)
        _CURSOR[provider]=0
        _BLOCKED_UNTIL.pop(provider,None)
    return len(keys)

def next_key(provider):
    with _LOCK:
        if time.monotonic()<_BLOCKED_UNTIL.get(provider,0):
            raise RuntimeError("Provider is cooling down after rate limit; retry later")
        keys=get_keys(provider)
        if not keys: raise RuntimeError("No API keys configured for "+provider)
        index=_CURSOR.get(provider,0) % len(keys)
        _CURSOR[provider]=index+1
        return keys[index]

def rate_limited(provider,seconds=120):
    with _LOCK:
        _BLOCKED_UNTIL[provider]=max(_BLOCKED_UNTIL.get(provider,0),time.monotonic()+seconds)

def readiness():
    now=time.monotonic()
    with _LOCK:
        return {provider:{"keys":len(get_keys(provider)),
                           "cooldown_seconds":max(0,int(_BLOCKED_UNTIL.get(provider,0)-now))}
                for provider in PROVIDERS}
