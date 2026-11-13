"""Local-only HTTP dashboard API, random topic picker and private TXT import."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import sys
from urllib.parse import parse_qs, unquote, urlsplit

from .keys import import_txt, readiness, write_keys
from .facebook import connect as connect_page, disconnect as disconnect_page, page_status
from .models import list_models, selected_models, set_model, test_model
from .quiz import TOPICS
from .scheduler import Scheduler
from .voice import available, readiness as tts_readiness, test_speech, ENGINES

LOGGER = logging.getLogger("tienghansoeasy.http")
MAX_REQUEST = 4_000_000
APP_VERSION = "v0.4.0"


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
            try:
                path = service.video_path(name)
            except ValueError:
                # Compatibility with legacy output created before custom folders.
                folder=(service.root/"videos").resolve()
                path=(folder/name).resolve()
                if path.parent != folder or not path.is_file():
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
                elif url.path == "/api/preview":
                    params=parse_qs(url.query)
                    qid=params.get("id",[""])[0]
                    theme=params.get("theme",["hanji"])[0]
                    data=service.preview(qid,theme,params.get("answer",["1"])[0]=="1")
                    self.send_response(200)
                    self.send_header("Content-Type","image/png")
                    self.send_header("Content-Length",str(len(data)))
                    self.send_header("Cache-Control","no-store")
                    self.end_headers();self.wfile.write(data)
                elif url.path == "/api/logo":
                    path=service.production.logo()
                    if not path:
                        self.respond(404,{"error":"No logo"});return
                    data=path.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type","image/png")
                    self.send_header("Content-Length",str(len(data)))
                    self.send_header("Cache-Control","no-store")
                    self.end_headers();self.wfile.write(data)
                elif url.path.startswith("/api/preview/clip/"):
                    name=unquote(url.path[len("/api/preview/clip/"):])
                    path=service.preview_clip_path(name)
                    self.send_response(200)
                    self.send_header("Content-Type","video/mp4")
                    self.send_header("Content-Length",str(path.stat().st_size))
                    self.send_header("Cache-Control","private, no-store")
                    self.end_headers()
                    with path.open("rb") as fp:
                        while True:
                            chunk=fp.read(256*1024)
                            if not chunk:break
                            self.wfile.write(chunk)
                elif url.path == "/api/models":
                    params = parse_qs(url.query)
                    self.respond(200, list_models(params.get("provider", [""])[0]))
                elif url.path == "/api/status":
                    self.respond(200, {
                        "version": APP_VERSION,
                        "topics_count": len(TOPICS),
                        "questions": service.store.list_questions(limit=500),
                        "batches": service.store.list_batches(limit=120),
                        "production": service.production.settings(),
                        "facebook": page_status(),
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
                      "/api/models/select", "/api/models/test", "/api/voice/test",
                      "/api/production/settings", "/api/production/choose", "/api/logo/upload",
                      "/api/facebook/connect", "/api/facebook/disconnect", "/api/seo",
                      "/api/render/batch", "/api/preview/clip")
            if route not in routes:
                self.respond(404, {"error": "Không tìm thấy chức năng."})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                limit=MAX_REQUEST if route=="/api/logo/upload" else 180_000
                if not 0 < length <= limit:
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
                elif route == "/api/production/settings":
                    result=service.production.update(raw)
                elif route == "/api/production/choose":
                    result=service.production.choose_directory(str(raw.get("kind","")))
                elif route == "/api/logo/upload":
                    result=service.production.set_logo(raw.get("base64",""))
                elif route == "/api/facebook/connect":
                    result=connect_page(str(raw.get("page_id","")),str(raw.get("token","")))
                elif route == "/api/facebook/disconnect":
                    result=disconnect_page()
                elif route == "/api/seo":
                    result=service.seo_video(str(raw.get("video_name","")))
                elif route == "/api/preview/clip":
                    name=service.preview_motion(str(raw.get("id","")),str(raw.get("theme","hanji")))
                    result={"preview_url":"/api/preview/clip/"+name.name}
                elif route == "/api/render/batch":
                    result=service.render_many(raw.get("ids",[]),
                        theme=str(raw.get("theme","hanji")),
                        per_video=int(raw.get("per_video",6)),
                        with_voice=raw.get("with_voice") is True,
                        voice_engine=str(raw.get("voice_engine","auto")))
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
                        str(raw.get("scheduled_at", "")),
                        str(raw.get("title", "")))}
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
