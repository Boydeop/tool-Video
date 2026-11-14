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
from .facebook import _protect,_unprotect,PageError

PROVIDERS = ("gemini", "groq", "openrouter", "krdict")
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
        key = str(item).strip().lstrip("\ufeff")
        if len(key)>=2 and key[0]==key[-1] and key[0] in "\"'":
            key=key[1:-1].strip()
        if key and key not in seen:
            results.append(key)
            seen.add(key)
    return results


def _settings():
    with _LOCK:
        return _settings_locked()


def _settings_locked():
    path = _path()
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(parsed,dict):raise ValueError("invalid key store")
        if "encrypted" in parsed:
            if not isinstance(parsed["encrypted"],str):raise ValueError("invalid encrypted key store")
            parsed=json.loads(_unprotect(parsed["encrypted"]))
            if not isinstance(parsed,dict):raise ValueError("invalid key store")
            _validate_store(parsed)
            return parsed
        _validate_store(parsed)
        if os.name=="nt" and any(provider in parsed for provider in PROVIDERS):
            # Migrate existing key file without exposing its contents to UI/logs.
            _save(parsed)
        return parsed
    except PageError:
        raise ValueError("Không giải mã được kho API key bằng tài khoản Windows hiện tại. "
                         "Dùng tài khoản đã lưu key hoặc sao lưu keys.local.json trước khi khôi phục.") from None
    except (OSError, ValueError, TypeError):
        # Never silently replace a damaged/unreadable store with an empty pool.
        raise ValueError("Không đọc được keys.local.json. Sao lưu file và kiểm tra quyền truy cập/định dạng trước khi nhập lại key.") from None


def _validate_store(data):
    for provider in PROVIDERS:
        value=data.get(provider)
        if value is not None and not isinstance(value,(str,list)):
            raise ValueError("invalid provider key pool")
        if isinstance(value,list) and any(not isinstance(key,str) for key in value):
            raise ValueError("invalid provider key pool")


def get_keys(provider):
    if provider not in PROVIDERS:
        return []
    return _merge_keys(provider,_settings())


def _merge_keys(provider,settings):
    local = _split(settings.get(provider, []))
    environment = _split(os.getenv(provider.upper() + "_API_KEYS", ""))
    legacy = os.getenv(provider.upper() + "_API_KEY", "").strip()
    return _split(local + environment + ([legacy] if legacy else []))[:MAX_PER_PROVIDER]


def key_counts():
    settings=_settings()
    return {provider:len(_merge_keys(provider,settings)) for provider in PROVIDERS}


def _validate(keys):
    if len(keys) > MAX_PER_PROVIDER:
        raise ValueError("Mỗi dịch vụ hỗ trợ tối đa 30 key trong một bộ.")
    if any(not 4 <= len(k) <= 500 or any(ch.isspace() or ord(ch)<32 for ch in k) for k in keys):
        raise ValueError("Key không hợp lệ: phải dài 4-500 ký tự và không có khoảng trắng.")


def _save(data):
    with _LOCK:
        try:_save_locked(data)
        except PageError:
            raise ValueError("Windows không mã hóa được kho API key. Kiểm tra tài khoản/quyền lưu dữ liệu; kho key cũ được giữ nguyên.") from None


def _save_locked(data):
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
    # Provider-issued credentials are opaque; the live API validates them.
    with _LOCK:
        data = _settings()
        data[provider] = keys
        _save(data)
        _CURSOR[provider] = 0
    return len(keys)


def parse_txt(text, default_provider="gemini"):
    """Parse plaintext keys, optional provider sections and provider: key lines.

    Plain TXT: one key per line, provider selected by UI.
    Combined TXT: [gemini], [groq], [openrouter], [krdict] section headers.
    Named TXT: groq:gsk_..., gemini=AIza..., KRDICT_API_KEY=...
    Only returns provider->keys; never logs or sends keys to third parties.
    """
    if default_provider not in PROVIDERS:
        raise ValueError("Dịch vụ mặc định không hợp lệ.")
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_TXT_BYTES:
        raise ValueError("File TXT vượt quá 64 KB.")
    if "\x00" in text:
        raise ValueError("File TXT chưa được giải mã đúng. Lưu bằng UTF-8 hoặc UTF-16 có BOM rồi nhập lại.")
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
        prefixed = re.match(r"^(gemini|groq|openrouter|krdict)(?:_api_keys?)?\s*[:=]\s*(.+)$", line, re.I)
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
    for provider,keys in result.items():
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
        settings=_settings()
        return {provider: {
            "keys": len(_merge_keys(provider,settings)),
            "cooldown_seconds": max(0, int(_BLOCKED_UNTIL.get(provider, 0) - now))
        } for provider in PROVIDERS}
