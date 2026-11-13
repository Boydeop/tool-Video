"""Local-only HTTP dashboard API, random topic picker and private TXT import."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import sys
from urllib.parse import parse_qs, unquote, urlsplit

from .keys import import_txt, readiness, write_keys
from .models import list_models, selected_models, set_model, test_model
from .quiz import TOPICS
from .scheduler import Scheduler
from .voice import available, readiness as tts_readiness, test_speech, ENGINES

LOGGER = logging.getLogger("tienghansoeasy.http")
MAX_REQUEST = 80_000
APP_VERSION = "v0.3.2"


def _ui_path() -> Path:
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        return Path(frozen) / "studio" / "ui.html"
    return Path(__file__).resolve().with_name("ui.html")


def create_server(service, host="127.0.0.1", port=8765):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            # PyInstaller --windowed uses sys.stderr=None; never write there.
            # Do not log POST bodies, uploaded TXT contents or key values.
            LOGGER.info("Request from %s: %s", self.client_address[0], fmt % args)

        def respond(self, status, data, mime="application/json"):
            payload = (data if isinstance(data, str) else
                       json.dumps(data, ensure_ascii=False)).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(payload)

        def _check_origin(self):
            origin = self.headers.get("Origin", "")
            port = self.server.server_address[1]
            allowed = ("http://127.0.0.1:" + str(port),
                       "http://localhost:" + str(port))
            if origin and origin not in allowed:
                self.respond(403, {"error": "Nguồn truy cập không được phép."})
                return False
            return True

        def _send_video(self, name):
            # Exact file name only. A malicious URL cannot read keys or database.
            if not name or name != Path(name).name or not name.endswith(".mp4"):
                self.respond(404, {"error": "Không tìm thấy video."})
                return
            folder = (service.root / "videos").resolve()
            path = (folder / name).resolve()
            if folder not in path.parents or not path.is_file():
                self.respond(404, {"error": "Không tìm thấy video."})
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(path.stat().st_size))
            self.send_header("Content-Disposition", 'attachment; filename="' + name + '"')
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            with path.open("rb") as stream:
                while True:
                    chunk = stream.read(1024 * 256)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

        def do_GET(self):
            url = urlsplit(self.path)
            try:
                if url.path == "/":
                    content = _ui_path().read_text(encoding="utf-8")
                    self.respond(200, content, "text/html")
                elif url.path == "/health":
                    self.respond(200, {"status": "ok", "app": "TiengHanSoEasyStudio"})
                elif url.path == "/api/topic/random":
                    params = parse_qs(url.query)
                    exclude = params.get("exclude", [None])[0]
                    self.respond(200, service.suggest_topic(exclude=exclude))
                elif url.path == "/api/models":
                    params = parse_qs(url.query)
                    self.respond(200, list_models(params.get("provider", [""])[0]))
                elif url.path == "/api/status":
                    self.respond(200, {
                        "version": APP_VERSION,
                        "topics_count": len(TOPICS),
                        "questions": service.store.list_questions(limit=300),
                        "jobs": service.store.list_jobs(limit=100),
                        "videos": service.list_videos(),
                        "providers": readiness(),
                        "models": selected_models(),
                        "tts_ready": available(),
                        "tts_engines": tts_readiness()
                    })
                elif url.path.startswith("/api/voice/sample/"):
                    name = unquote(url.path[len("/api/voice/sample/"):])
                    if not name.endswith(".mp3") or len(name)!=68 or not all(c in "0123456789abcdef" for c in name[:-4]):
                        self.respond(404,{"error":"Không có mẫu âm thanh."})
                        return
                    path=(service.root/"tts_samples"/name).resolve()
                    if not path.is_file():
                        self.respond(404,{"error":"Không tìm thấy mẫu."})
                        return
                    body=path.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type","audio/mpeg")
                    self.send_header("Content-Length",str(len(body)))
                    self.send_header("Cache-Control","no-store")
                    self.end_headers()
                    self.wfile.write(body)
                elif url.path.startswith("/api/videos/"):
                    self._send_video(unquote(url.path[len("/api/videos/"):]))
                else:
                    self.respond(404, {"error": "Không tìm thấy đường dẫn."})
            except (ValueError, RuntimeError) as exc:
                self.respond(400, {"error": str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                pass  # The browser stopped downloading.
            except Exception:
                LOGGER.exception("GET request failed")
                self.respond(500, {"error": "Lỗi máy chủ. Xem studio.log."})

        def do_POST(self):
            if not self._check_origin():
                return
            route = urlsplit(self.path).path
            routes = ("/api/generate", "/api/daily", "/api/approve", "/api/render",
                      "/api/schedule", "/api/keys", "/api/keys/import",
                      "/api/models/select", "/api/models/test", "/api/voice/test")
            if route not in routes:
                self.respond(404, {"error": "Không tìm thấy chức năng."})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_REQUEST:
                    raise ValueError("Dữ liệu gửi vượt giới hạn.")
                raw = json.loads(self.rfile.read(length))
                if not isinstance(raw, dict):
                    raise ValueError("Dữ liệu phải ở định dạng JSON.")
                if route == "/api/generate":
                    level = str(raw.get("level", "TOPIK 1"))
                    count = int(raw.get("count", 6))
                    topic = str(raw.get("topic", "")).strip()
                    if topic:
                        if topic not in TOPICS:
                            raise ValueError("Chủ đề không có trong chương trình.")
                        result = service.generate(topic, level, count)
                        result["plan"] = {"topic": topic, "level": level}
                    else:
                        result = service.generate_random(level=level, count=count)
                elif route == "/api/daily":
                    result = service.daily(count=int(raw.get("count", 6)))
                elif route == "/api/approve":
                    ids = raw.get("ids", [])
                    if not isinstance(ids, list) or not ids or len(ids) > 300:
                        raise ValueError("Vui lòng chọn câu hỏi trước.")
                    status = str(raw.get("status", "approved"))
                    service.store.approve(ids, status=status)
                    result = {"updated": len(ids), "status": status}
                elif route == "/api/voice/test":
                    language=str(raw.get("language",""))
                    engine=str(raw.get("engine","auto"))
                    testfile=test_speech(language,engine,service.root/"tts_samples")
                    result={"ok":True,"language":language,"engine":engine,
                            "audio_url":"/api/voice/sample/"+testfile.name}
                elif route == "/api/render":
                    ids = raw.get("ids", [])
                    if not isinstance(ids, list) or not 0 < len(ids) <= 30:
                        raise ValueError("Chọn từ 1 đến 30 câu đã duyệt.")
                    result = {"video": service.render_approved(
                        ids, str(raw.get("theme", "hanji")),
                        with_voice=raw.get("with_voice") is True,
                        voice_engine=str(raw.get("voice_engine","auto")))}
                elif route == "/api/models/select":
                    result = set_model(str(raw.get("provider", "")),
                                       str(raw.get("model", "")))
                elif route == "/api/models/test":
                    result = test_model(str(raw.get("provider", "")),
                                        str(raw.get("model", "")), raw.get("key_position", 1))
                elif route == "/api/keys":
                    # Keep the older endpoint for backwards compatibility.
                    result = {"saved_key_count": write_keys(
                        str(raw.get("provider", "")), str(raw.get("keys", "")))}
                elif route == "/api/keys/import":
                    text = raw.get("text", "")
                    if not isinstance(text, str):
                        raise ValueError("File TXT không hợp lệ.")
                    result = import_txt(
                        text, default_provider=str(raw.get("provider", "gemini")),
                        replace=raw.get("replace") is True)
                else:
                    result = {"job_id": service.schedule(
                        str(raw.get("video_path", "")),
                        str(raw.get("caption", "")),
                        str(raw.get("scheduled_at", "")))}
                self.respond(200, result)
            except (ValueError, RuntimeError, KeyError) as exc:
                self.respond(400, {"error": str(exc)})
            except Exception:
                LOGGER.exception("POST request failed; request body omitted")
                self.respond(500, {"error": "Lỗi máy chủ. Xem studio.log."})

    class LocalServer(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            LOGGER.exception("HTTP handler crashed for %s", client_address[0])

    return LocalServer((host, port), Handler)


def serve(service, host="127.0.0.1", port=8765, on_ready=None):
    server = create_server(service, host, port)
    scheduler = Scheduler(service.store)
    try:
        scheduler.start()
        LOGGER.info("Studio server bound to %s:%s", *server.server_address)
        if on_ready:
            on_ready(server.server_address)
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        scheduler.stop()
        server.server_close()
