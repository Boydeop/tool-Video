"""Select and verify provider models against the user's own API key.

This module never exposes credentials to the browser. Model discovery uses
official fixed-host APIs and persists only the chosen model identifier.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

from .keys import PROVIDERS, get_keys, rate_limited

DEFAULTS = {
    "gemini": "gemini-3.5-flash-lite",
    "groq": "llama-3.1-8b-instant",
    "openrouter": "openrouter/free",
}
SETTINGS_LOCK = threading.RLock()
MAX_RESPONSE_BYTES = 2_000_000

class ModelError(RuntimeError):
    pass

def _settings_path():
    path = os.getenv("THSE_MODELS_FILE", "").strip()
    if path:
        return Path(path).resolve()
    keys = os.getenv("THSE_KEYS_FILE", "").strip()
    if keys:
        return Path(keys).resolve().with_name("models.local.json")
    return Path("data/models.local.json").resolve()

def validate_provider(provider):
    if provider not in PROVIDERS:
        raise ValueError("Nhà cung cấp không được hỗ trợ.")
    return provider

def validate_model(provider, model):
    validate_provider(provider)
    if not isinstance(model, str):
        raise ValueError("Model ID không hợp lệ.")
    model = model.strip()
    pattern = r"[A-Za-z0-9_.-]{1,128}" if provider == "gemini" else r"[A-Za-z0-9_.:/+-]{1,128}"
    if not re.fullmatch(pattern, model) or ".." in model:
        raise ValueError("Model ID không hợp lệ; vui lòng chọn từ danh sách API.")
    return model

def _read_settings():
    path = _settings_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}

def get_model(provider):
    validate_provider(provider)
    saved = _read_settings().get(provider)
    if saved:
        try:
            return validate_model(provider, saved)
        except ValueError:
            pass
    env = os.getenv(provider.upper() + "_MODEL", "").strip()
    return validate_model(provider, env or DEFAULTS[provider])

def selected_models():
    return {provider: get_model(provider) for provider in PROVIDERS}

def set_model(provider, model):
    model = validate_model(provider, model)
    with SETTINGS_LOCK:
        data = _read_settings()
        data[provider] = model
        path = _settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        try:
            temp.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                            encoding="utf-8")
            if os.name != "nt":
                os.chmod(temp, 0o600)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
    return {"provider": provider, "model": model}

def _get_json(provider, url, key):
    headers = {"Accept": "application/json", "User-Agent": "TiengHanSoEasyStudio/0.3.1"}
    if provider == "gemini":
        headers["x-goog-api-key"] = key
    else:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, headers=headers)
    return _read_json(provider, req)

def _read_json(provider, req):
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ModelError("Danh sách model quá lớn.")
        return json.loads(body)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            rate_limited(provider, 120)
            raise ModelError("HTTP 429: dịch vụ đang giới hạn số yêu cầu, vui lòng thử lại sau.") from exc
        if exc.code in (401, 403):
            raise ModelError(f"HTTP {exc.code}: key không có quyền với dịch vụ này.") from exc
        if exc.code == 404:
            raise ModelError("HTTP 404: model hoặc API endpoint không tồn tại cho key này.") from exc
        raise ModelError(f"HTTP {exc.code}: kiểm tra dịch vụ và model được chọn.") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ModelError("Không kết nối được với nhà cung cấp model.") from exc
    except (ValueError, UnicodeError) as exc:
        raise ModelError("Nhà cung cấp trả phản hồi không hợp lệ.") from exc

def _list_with_key(provider, key):
    if provider == "gemini":
        # Gemini returns the models available to THIS key/project.
        models, token = [], None
        for _ in range(8):
            query = "pageSize=100" + ("&pageToken=" + urllib.parse.quote(token, safe="") if token else "")
            response = _get_json(provider,
                "https://generativelanguage.googleapis.com/v1beta/models?" + query, key)
            models.extend(response.get("models", []))
            token = response.get("nextPageToken")
            if not token:
                break
        rows = []
        for item in models:
            methods = item.get("supportedGenerationMethods", [])
            if "generateContent" not in methods:
                continue
            name = str(item.get("name", "")).removeprefix("models/")
            try:
                name = validate_model(provider, name)
            except ValueError:
                continue
            # Exclude image/live/TTS-specific variants from the text quiz selector.
            low = name.lower()
            if any(s in low for s in ("image", "tts", "native-audio", "embedding", "aqa")):
                continue
            rows.append({"id": name, "label": str(item.get("displayName") or name)[:160],
                         "free": None})
        return rows
    if provider == "groq":
        response = _get_json(provider, "https://api.groq.com/openai/v1/models", key)
        rows = []
        for item in response.get("data", []):
            name = str(item.get("id", ""))
            try:
                name = validate_model(provider, name)
            except ValueError:
                continue
            if item.get("active") is False:
                continue
            low = name.lower()
            if any(x in low for x in ("whisper", "guard", "tts", "transcribe", "embed", "playai")):
                continue
            rows.append({"id": name, "label": name, "free": None})
        return rows
    response = _get_json(provider, "https://openrouter.ai/api/v1/models", key)
    rows = []
    for item in response.get("data", []):
        name = str(item.get("id", ""))
        try:
            name = validate_model(provider, name)
        except ValueError:
            continue
        arch = item.get("architecture") or {}
        if "text" not in (arch.get("input_modalities") or ["text"]):
            continue
        if "text" not in (arch.get("output_modalities") or ["text"]):
            continue
        pricing = item.get("pricing") or {}
        is_free = (str(pricing.get("prompt", "-")) in ("0", "0.0")
                   and str(pricing.get("completion", "-")) in ("0", "0.0"))
        rows.append({"id": name, "label": str(item.get("name") or name)[:160],
                     "free": bool(is_free)})
    return rows

def list_models(provider):
    """List models from authenticated APIs; 429 never triggers key hopping.

    401/403 may try the next user-owned key because the first could be invalid.
    Model availability is per project; another key may still have less access.
    """
    validate_provider(provider)
    keys = get_keys(provider)
    if not keys:
        raise ModelError("Chưa nhập API key cho " + provider + ".")
    errors = []
    for index, key in enumerate(keys):
        try:
            rows = _list_with_key(provider, key)
            unique = {row["id"]: row for row in rows}
            results = list(unique.values())
            results.sort(key=lambda row: (row["free"] is not True, row["id"].casefold()))
            if not results:
                raise ModelError("API không trả model phù hợp để tạo văn bản.")
            return {"provider": provider, "models": results, "count": len(results),
                    "key_position": index + 1, "selected": get_model(provider)}
        except ModelError as exc:
            errors.append(str(exc))
            if "HTTP 429" in str(exc):
                break
            if "HTTP 401" not in str(exc) and "HTTP 403" not in str(exc):
                break
    raise ModelError(errors[-1] if errors else "Không thể tải danh sách model.")

def test_model(provider, model, key_position=1):
    """Send a tiny real generation request; costs tokens and may be rate-limited."""
    model = validate_model(provider, model)
    keys = get_keys(provider)
    if not keys:
        raise ModelError("Chưa có API key để kiểm tra model.")
    # User can explicitly choose which owned key to test (1-based).
    try:
        position = int(key_position)
    except (ValueError, TypeError) as exc:
        raise ValueError("Số thứ tự API key không hợp lệ.") from exc
    if not 1 <= position <= len(keys):
        raise ValueError("API key được chọn không tồn tại trong pool.")
    key = keys[position - 1]
    if provider == "gemini":
        url = "https://generativelanguage.googleapis.com/v1beta/models/" + model + ":generateContent"
        payload = {"contents": [{"parts": [{"text": "Trả lời một chữ: OK"}]}],
                   "generationConfig": {"maxOutputTokens": 64}}
        headers = {"x-goog-api-key": key}
    else:
        url = ("https://api.groq.com/openai/v1/chat/completions" if provider == "groq"
               else "https://openrouter.ai/api/v1/chat/completions")
        payload = {"model": model, "max_tokens": 64,
                   "messages": [{"role": "user", "content": "Reply OK."}]}
        headers = {"Authorization": "Bearer " + key}
    req = urllib.request.Request(url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers}, method="POST")
    response = _read_json(provider, req)
    if provider == "gemini":
        good = bool(response.get("candidates"))
    else:
        good = bool(response.get("choices"))
    if not good:
        raise ModelError("Model phản hồi nhưng không tạo nội dung. Hãy chọn model khác.")
    return {"provider": provider, "model": model, "status": "ok",
            "key_position": position,
            "message": "Model đã trả phản hồi thành công với key được chọn. "
                       "Các key còn lại có thể có quyền truy cập khác."}
