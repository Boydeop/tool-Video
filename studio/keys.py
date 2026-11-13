"""Local key management and TXT import for user-owned API keys.

Windows key files are DPAPI encrypted per Windows user; existing plaintext
files migrate on first access. Non-Windows files are chmod 0600.
We obey provider-level rate-limit cooldown (HTTP 429), not rotate to evade limits.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import threading
import time
from .facebook import _protect,_unprotect

PROVIDERS = ("gemini", "groq", "openrouter")
_LOCK = threading.RLock()
_CURSOR = {}
_BLOCKED_UNTIL = {}
MAX_PER_PROVIDER = 30
MAX_TXT_BYTES = 64 * 1024


def _path():
    return Path(os.getenv("THSE_KEYS_FILE", "data/keys.local.json")).resolve()


def _split(value):
    if isinstance(value, list):
        items = value
    else:
        items = re.split(r"[,;\n\r]+", str(value or ""))
    seen = set()
    results = []
    for item in items:
        key = str(item).strip()
        if key and key not in seen:
            results.append(key)
            seen.add(key)
    return results


def _settings():
    path = _path()
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(parsed,dict):return {}
        if "encrypted" in parsed:
            return json.loads(_unprotect(parsed["encrypted"]))
        if os.name=="nt" and any(provider in parsed for provider in PROVIDERS):
            # Migrate existing key file without exposing its contents to UI/logs.
            _save(parsed)
        return parsed
    except (OSError, ValueError):
        return {}


def get_keys(provider):
    if provider not in PROVIDERS:
        return []
    local = _split(_settings().get(provider, []))
    environment = _split(os.getenv(provider.upper() + "_API_KEYS", ""))
    legacy = os.getenv(provider.upper() + "_API_KEY", "").strip()
    return _split(local + environment + ([legacy] if legacy else []))[:MAX_PER_PROVIDER]


def key_counts():
    return {provider: len(get_keys(provider)) for provider in PROVIDERS}


def _validate(keys):
    if len(keys) > MAX_PER_PROVIDER:
        raise ValueError("Mỗi dịch vụ hỗ trợ tối đa 30 key trong một bộ.")
    if any(not 4 <= len(k) <= 500 or any(ch.isspace() for ch in k) for k in keys):
        raise ValueError("Key không hợp lệ: phải dài 4-500 ký tự và không có khoảng trắng.")


def _save(data):
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        payload={"encrypted":_protect(json.dumps(data,ensure_ascii=False))} if os.name=="nt" else data
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if os.name != "nt":
            os.chmod(tmp, 0o600)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def write_keys(provider, value):
    if provider not in PROVIDERS:
        raise ValueError("Dịch vụ không hợp lệ.")
    keys = _split(value)
    _validate(keys)
    with _LOCK:
        data = _settings()
        data[provider] = keys
        _save(data)
        _CURSOR[provider] = 0
    return len(keys)


def parse_txt(text, default_provider="gemini"):
    """Parse plaintext keys, optional provider sections and provider: key lines.

    Plain TXT: one key per line, provider selected by UI.
    Combined TXT: [gemini], [groq], [openrouter] section headers.
    Named TXT: groq:gsk_..., gemini=AIza..., OPENROUTER_API_KEY=...
    Only returns provider->keys; never logs or sends keys to third parties.
    """
    if default_provider not in PROVIDERS:
        raise ValueError("Dịch vụ mặc định không hợp lệ.")
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_TXT_BYTES:
        raise ValueError("File TXT vượt quá 64 KB.")
    current = default_provider
    grouped = {provider: [] for provider in PROVIDERS}
    encountered = set()
    for raw in text.lstrip("\ufeff").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        section = re.fullmatch(r"\[([a-zA-Z_]+)\]", line)
        if section:
            current = section.group(1).lower()
            if current not in PROVIDERS:
                raise ValueError("Tên nhóm trong TXT không hợp lệ.")
            encountered.add(current)
            continue
        prefixed = re.match(r"^(gemini|groq|openrouter)(?:_api_keys?)?\s*[:=]\s*(.+)$", line, re.I)
        if prefixed:
            current_for_line = prefixed.group(1).lower()
            values = _split(prefixed.group(2))
            encountered.add(current_for_line)
        else:
            # Generic KEY=value is permitted for single-provider files.
            generic = re.match(r"^(?:API_KEY|KEY)\s*=\s*(.+)$", line, re.I)
            values = _split(generic.group(1) if generic else line)
            current_for_line = current
            encountered.add(current_for_line)
        for val in values:
            if val.startswith("#") or val.startswith("//"):
                continue
            grouped[current_for_line].append(val)
    result = {provider: _split(keys) for provider, keys in grouped.items() if keys}
    if not result:
        raise ValueError("File TXT không chứa API key nào.")
    for keys in result.values():
        _validate(keys)
    return result


def import_txt(text, default_provider="gemini", replace=False):
    parsed = parse_txt(text, default_provider)
    with _LOCK:
        current = _settings()
        updated = {}
        for provider, keys in parsed.items():
            previous = [] if replace else _split(current.get(provider, []))
            merged = _split(previous + keys)
            _validate(merged)
            updated[provider] = merged
        current.update(updated)
        _save(current)
    return {
        "imported": {p: len(values) for p, values in parsed.items()},
        "saved": {p: len(values) for p, values in updated.items()},
        "mode": "replace" if replace else "append"
    }


def next_key(provider):
    with _LOCK:
        if time.monotonic() < _BLOCKED_UNTIL.get(provider, 0):
            raise RuntimeError("Nhà cung cấp đang tạm nghỉ sau giới hạn yêu cầu.")
        keys = get_keys(provider)
        if not keys:
            raise RuntimeError("Chưa cấu hình API key cho " + provider)
        index = _CURSOR.get(provider, 0) % len(keys)
        _CURSOR[provider] = index + 1
        return keys[index]


def rate_limited(provider, seconds=120):
    with _LOCK:
        _BLOCKED_UNTIL[provider] = max(_BLOCKED_UNTIL.get(provider, 0), time.monotonic() + seconds)


def readiness():
    now = time.monotonic()
    with _LOCK:
        return {provider: {
            "keys": len(get_keys(provider)),
            "cooldown_seconds": max(0, int(_BLOCKED_UNTIL.get(provider, 0) - now))
        } for provider in PROVIDERS}
