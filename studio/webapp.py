"""Local-only HTTP dashboard API, random topic picker and private TXT import."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import sys
from urllib.parse import parse_qs, unquote, urlsplit, quote

from .keys import import_txt, readiness, write_keys
from .exports import ExportQueue
from .youtube import YouTubeAccount
from .instagram import InstagramAccount
from .facebook import connect as connect_page, page_status
from .models import list_models, selected_models, set_model, test_model
from .quiz import TOPICS
from .scheduler import Scheduler
from .voice import available, readiness as tts_readiness

LOGGER = logging.getLogger("tienghansoeasy.http")
MAX_REQUEST = 4_000_000
from . import __version__
APP_VERSION = "v"+__version__


def _ui_path() -> Path:
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        return Path(frozen) / "studio" / "ui.html"
    return Path(__file__).resolve().with_name("ui.html")


def create_server(service, host="127.0.0.1", port=8765):
    export_queue=ExportQueue(service)
    from .facebook_oauth import FacebookOAuth
    facebook_oauth=FacebookOAuth(service.root)
    youtube=YouTubeAccount(service.root)
    instagram=InstagramAccount(service.root)
    class Handler(BaseHTTPRequestHandler):
        def log_request(self, code="-", size="-"):
            LOGGER.info("%s %s %s",self.command,urlsplit(self.path).path,code)

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
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(payload)

        def _check_origin(self):
            port = self.server.server_address[1]
            if self.headers.get('Host','').lower() not in (
                    f'127.0.0.1:{port}', f'localhost:{port}'):
                self.respond(403, {"error": "Host truy cập không được phép."})
                return False
            origin = self.headers.get("Origin", "")
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
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''"+quote(name,safe=""))
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
            if not self._check_origin():
                return
            url = urlsplit(self.path)
            try:
                if url.path == "/api/facebook/oauth/callback":
                    try:
                        facebook_oauth.callback(parse_qs(url.query))
                        message="Facebook đã cấp quyền. Quay lại Studio, tải danh sách Page rồi chọn Page cần kết nối."
                    except ValueError as exc:
                        message=str(exc)
                    self.respond(200,message,"text/plain")
                elif url.path == "/api/facebook/oauth/pages":
                    self.respond(200,facebook_oauth.list_pages())
                elif url.path == "/":
                    content = _ui_path().read_text(encoding="utf-8")
                    self.respond(200, content, "text/html")
                elif url.path == "/health":
                    self.respond(200, {"status": "ok", "app": "TiengHanSoEasyStudio",
                                       "version":APP_VERSION})
                elif url.path == "/api/topic/random":
                    params = parse_qs(url.query)
                    exclude = params.get("exclude", [None])[0]
                    self.respond(200, service.suggest_topic(exclude=exclude))
                elif url.path == "/api/preview":
                    params=parse_qs(url.query)
                    qid=params.get("id",[""])[0]
                    theme=params.get("theme",["hanji"])[0]
                    data=service.preview(qid,theme,params.get("answer",["1"])[0]=="1",
                                         show_logo=params.get("clean",["0"])[0]!="1")
                    self.send_response(200)
                    self.send_header("Content-Type","image/png")
                    self.send_header("Content-Length",str(len(data)))
                    self.send_header("Cache-Control","no-store")
                    self.end_headers();self.wfile.write(data)
                elif url.path == "/api/render/progress":
                    params=parse_qs(url.query)
                    jid=params.get("job_id",[""])[0]
                    self.respond(200,export_queue.status(jid))
                elif url.path == "/api/render/active":
                    self.respond(200,export_queue.active())
                elif url.path == "/api/answers/history":
                    self.respond(200,{'history':service.store.repair_history(parse_qs(url.query).get('id',[''])[0])})
                elif url.path == "/api/events/upcoming":
                    self.respond(200,{"events":service.upcoming_events()})
                elif url.path == "/api/cache/status":
                    self.respond(200,{"cache":service.cache_stats(),
                                      "busy":export_queue.is_running()})
                elif url.path == "/api/memory/status":
                    self.respond(200, service.store.memory_stats())
                elif url.path == "/api/memory/export":
                    import tempfile
                    with tempfile.TemporaryDirectory() as folder:
                        path=Path(folder)/'question-memory.jsonl'
                        service.store.export_memory(path)
                        self.send_response(200)
                        self.send_header('Content-Type','application/x-ndjson; charset=utf-8')
                        self.send_header('Content-Length',str(path.stat().st_size))
                        self.send_header('Content-Disposition','attachment; filename="question-memory.jsonl"')
                        self.send_header('Cache-Control','no-store')
                        self.end_headers()
                        with path.open('rb') as stream:
                            import shutil
                            shutil.copyfileobj(stream,self.wfile)
                elif url.path == "/api/platform/status":
                    self.respond(200,{"youtube":youtube.status(),"instagram":instagram.status(),
                                      "outbound":service.store.list_outbound(100)})
                elif url.path == "/api/production/runs":
                    self.respond(200,{"runs":service.store.list_production_runs(40)})
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
                        "upcoming_events": service.upcoming_events(),
                        "questions": service.store.list_questions(limit=500),
                        "trash_questions":service.store.list_questions(limit=500,trash=True),
                        "batches": service.store.list_batches(limit=120),
                        "production_runs": service.store.list_production_runs(limit=40),
                        "outbound_posts": service.store.list_outbound(limit=100),
                        "youtube_thumbnails": service.store.list_youtube_thumbnails(),
                        "cache": service.cache_stats(),
                        "youtube":youtube.status(),"instagram":instagram.status(),
                        "total_questions":service.store.count_questions(),
                        "question_memory":service.store.memory_stats(),
                        "production": service.production.settings(),
                        "facebook": page_status(),
                        "jobs": service.store.list_jobs(limit=100),
                        "videos": service.list_videos(limit=1000),
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
                elif url.path.startswith("/api/thumbnails/"):
                    name=unquote(url.path[len("/api/thumbnails/"):])
                    thumbnail=service.thumbnail_for_video(name)
                    payload=thumbnail.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type","image/jpeg")
                    self.send_header("Content-Length",str(len(payload)))
                    self.send_header("Cache-Control","no-store")
                    self.end_headers();self.wfile.write(payload)
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
                      "/api/facebook/oauth/configure", "/api/facebook/oauth/start", "/api/facebook/oauth/select",
                      "/api/platforms/schedule",
                      "/api/render/batch", "/api/preview/clip", "/api/render/start",
                      "/api/answers/enrich", "/api/answers/save", "/api/answers/repair", "/api/videos/metadata",
                      "/api/videos/metadata/inspect", "/api/videos/metadata/repair_bulk",
                      "/api/autopilot/start", "/api/autopilot/cancel", "/api/autopilot/resume", "/api/questions/manage", "/api/cache/clear",
                      "/api/qa/verify_pending", "/api/krdict/test",
                      "/api/thumbnails/legacy",
                      "/api/youtube/configure", "/api/youtube/connect", "/api/youtube/disconnect",
                      "/api/instagram/connect", "/api/instagram/disconnect",
                      "/api/platform/schedule", "/api/memory/import")
            if route not in routes:
                self.respond(404, {"error": "Không tìm thấy chức năng."})
                return
            try:
                if self.headers.get_content_type()!='application/json':
                    raise ValueError("Chỉ nhận Content-Type application/json.")
                if self.headers.get('Transfer-Encoding'):
                    raise ValueError("Không hỗ trợ Transfer-Encoding.")
                length = int(self.headers.get("Content-Length", "0"))
                limit=(18_000_000 if route=='/api/memory/import' else
                       MAX_REQUEST if route=="/api/logo/upload" else 180_000)
                if not 0 < length <= limit:
                    raise ValueError("Dữ liệu gửi vượt giới hạn.")
                raw = json.loads(self.rfile.read(length))
                if not isinstance(raw, dict):
                    raise ValueError("Dữ liệu phải ở định dạng JSON.")
                if route == '/api/memory/import':
                    import tempfile
                    text=raw.get('text')
                    if not isinstance(text,str) or len(text.encode('utf-8'))>8_000_000:
                        raise ValueError('Bản sao lưu bộ nhớ tối đa 8 MB qua dashboard; file lớn hơn dùng CLI.')
                    with tempfile.TemporaryDirectory() as folder:
                        path=Path(folder)/'import.jsonl'
                        # Preserve uploaded CRLF verbatim: Windows text-mode
                        # translation would turn CRLF into CRCRLF and introduce
                        # blank JSONL records when opened with universal newlines.
                        path.write_bytes(text.encode('utf-8'))
                        result=service.store.import_memory(path)
                elif route == "/api/generate":
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
                    testfile=service.test_voice(language,engine)
                    result={"ok":True,"language":language,"engine":engine,
                            "audio_url":"/api/voice/sample/"+testfile.name}
                elif route == "/api/production/settings":
                    result=service.production.update(raw)
                elif route == "/api/production/choose":
                    result=service.production.choose_directory(str(raw.get("kind","")))
                elif route == "/api/logo/upload":
                    result=service.set_logo(raw.get("base64",""))
                elif route == "/api/facebook/oauth/configure":
                    result=facebook_oauth.configure(str(raw.get("app_id","")),str(raw.get("app_secret","")))
                elif route == "/api/facebook/oauth/start":
                    redirect="http://127.0.0.1:"+str(self.server.server_address[1])+"/api/facebook/oauth/callback"
                    result=facebook_oauth.start(redirect)
                elif route == "/api/facebook/oauth/select":
                    result=facebook_oauth.select(str(raw.get("page_id","")))
                elif route == "/api/platforms/schedule":
                    from datetime import datetime,timezone,timedelta
                    when=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat() if raw.get("publish_now") is True else str(raw.get("scheduled_at",""))
                    jobs=service.schedule_platforms(str(raw.get("video_name","")),raw.get("platforms"),
                        str(raw.get("title","")),str(raw.get("caption","")),when,
                        str(raw.get("public_url","")),str(raw.get("privacy","private")))
                    result={"jobs":jobs,"status":"scheduled"}
                elif route == "/api/facebook/connect":
                    result=connect_page(str(raw.get("page_id","")),str(raw.get("token","")))
                elif route == "/api/facebook/disconnect":
                    result=facebook_oauth.disconnect()
                elif route == "/api/seo":
                    result=service.seo_video(str(raw.get("video_name","")))
                elif route == "/api/preview/clip":
                    name=service.preview_motion(str(raw.get("id","")),str(raw.get("theme","hanji")),
                        with_voice=raw.get("with_voice") is True,
                        voice_engine=str(raw.get("voice_engine","auto")))
                    result={"preview_url":"/api/preview/clip/"+name.name}
                elif route == "/api/autopilot/start":
                    result=export_queue.start_autopilot(raw)
                elif route == "/api/autopilot/resume":
                    result=export_queue.resume_autopilot(str(raw.get('job_id','')))
                elif route == "/api/questions/manage":
                    if export_queue.is_running():raise ValueError('Chờ hoặc tạm dừng sản xuất trước khi dọn bản nháp')
                    action=raw.get('action')
                    if action=='trash':result=service.store.trash_questions(raw.get('ids'))
                    elif action=='cleanup':result=service.store.trash_questions(cleanup=True)
                    elif action=='restore':result=service.store.restore_questions(raw.get('ids'))
                    elif action=='purge':result=service.store.empty_question_trash()
                    else:raise ValueError('Thao tác bản nháp không hợp lệ')
                elif route == "/api/autopilot/cancel":
                    result=export_queue.cancel_autopilot(str(raw.get("job_id","")))
                elif route == "/api/qa/verify_pending":
                    if export_queue.is_running():
                        raise ValueError("Không kiểm tra cùng lúc sản xuất hàng loạt.")
                    result=service.verify_pending_questions(
                        int(raw.get("limit",8)),mode=str(raw.get("mode","strict")))
                elif route == "/api/krdict/test":
                    from .krdict import KRDict
                    result=KRDict(service.store).test_connection()
                elif route == "/api/thumbnails/legacy":
                    if export_queue.is_running():
                        raise ValueError("Đang dựng video, không tạo thumbnail cũ.")
                    result=service.generate_legacy_thumbnails(
                        int(raw.get("limit",20)),str(raw.get("theme","hanji")))
                elif route == "/api/cache/clear":
                    if export_queue.is_running():
                        raise ValueError("Không thể dọn cache khi đang sản xuất video.")
                    days=int(raw.get("older_than_days",0))
                    if raw.get("confirmed") is not True:
                        raise ValueError("Bạn phải xác nhận xóa cache.")
                    result=service.clear_cache(days)
                elif route == "/api/youtube/configure":
                    result=youtube.configure(raw.get("client",{}))
                elif route == "/api/youtube/connect":
                    result=youtube.connect_start()
                elif route == "/api/youtube/disconnect":
                    result=youtube.disconnect()
                elif route == "/api/instagram/connect":
                    result=instagram.connect(str(raw.get("ig_id","")),
                                              str(raw.get("token","")))
                elif route == "/api/instagram/disconnect":
                    result=instagram.disconnect()
                elif route == "/api/platform/schedule":
                    platform=str(raw.get("platform",""))
                    name=str(raw.get("video_name",""))
                    if platform=="instagram" and not instagram.status()["connected"]:
                        raise ValueError("Chưa kết nối Instagram Professional.")
                    job=service.schedule_platform(name,platform,
                        str(raw.get("title","")),str(raw.get("caption","")),
                        str(raw.get("scheduled_at","")),
                        str(raw.get("public_url","")),
                        str(raw.get("privacy","private")))
                    result={"job_id":job,"platform":platform,"status":"scheduled"}
                elif route == "/api/render/start":
                    ids=raw.get("ids",[])
                    limit=int(raw.get("per_video",6))
                    result=export_queue.start(ids,
                        theme=str(raw.get("theme","hanji")),per_video=limit,
                        with_voice=raw.get("with_voice") is True,
                        voice_engine=str(raw.get("voice_engine","auto")))
                elif route == "/api/answers/enrich":
                    result=service.enrich_answer_details(raw.get("ids",[]))
                elif route == "/api/answers/repair":
                    result=export_queue.start_repair(raw.get('ids'))
                elif route == "/api/answers/save":
                    result=service.edit_answer_details(str(raw.get("id","")),
                           raw.get("details",{}))
                elif route == "/api/videos/metadata/inspect":
                    result=service.inspect_video_metadata(str(raw.get("video_name","")))
                elif route == "/api/videos/metadata/repair_bulk":
                    if raw.get("confirmed") is not True:
                        raise ValueError("Cần xác nhận trước khi sửa MP4 cũ.")
                    if export_queue.is_running():
                        raise ValueError("Đang có lượt render; không sửa MP4 lúc này.")
                    result=service.repair_legacy_video_metadata(int(raw.get("limit",20)))
                elif route == "/api/videos/metadata":
                    result=service.overwrite_video_metadata(str(raw.get("video_name","")))
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
                from .krdict import KRDictError
                self.respond(400, {"error": str(exc),**(
                    {"code":exc.code} if isinstance(exc,KRDictError) else {})})
            except Exception:
                LOGGER.exception("POST request failed; request body omitted")
                self.respond(500, {"error": "Lỗi máy chủ. Xem studio.log."})

    class LocalServer(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            LOGGER.exception("HTTP handler crashed for %s", client_address[0])

    server=LocalServer((host, port), Handler)
    server.export_queue=export_queue
    return server


def serve(service, host="127.0.0.1", port=8765, on_ready=None):
    server = create_server(service, host, port)
    scheduler = Scheduler(service.store)
    service.store.recover_incomplete_runs()
    try:
        server.export_queue.recover_autopilot()
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
