"""Official YouTube Data API upload with a user's own OAuth desktop client.

Setup needs a Google Cloud OAuth Desktop client JSON, *not* a Gemini API key.
The app requests only youtube.upload scope. No impersonation or scraping.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import threading
from .facebook import _protect,_unprotect

SCOPE="https://www.googleapis.com/auth/youtube.upload"

class YouTubeAccount:
    def __init__(self,root):
        self.root=Path(root).resolve()
        self.root.mkdir(parents=True,exist_ok=True)
        self.client_path=self.root/"youtube_oauth_client.local.json"
        self.token_path=self.root/"youtube_token.local.json"
        self._lock=threading.RLock()
        self._status="idle"
        self._error=""

    @staticmethod
    def _load_private(path):
        if not path.is_file():return None
        obj=json.loads(path.read_text("utf-8"))
        return json.loads(_unprotect(obj["encrypted"]))

    @staticmethod
    def _save_private(path,content):
        temp=path.with_suffix(".tmp")
        try:
            temp.write_text(json.dumps({"encrypted":_protect(json.dumps(content,ensure_ascii=False))}),
                            encoding="utf-8")
            if os.name!="nt":os.chmod(temp,0o600)
            temp.replace(path)
        finally:temp.unlink(missing_ok=True)

    def configure(self,client):
        if not isinstance(client,dict):raise ValueError("OAuth client JSON không hợp lệ.")
        data=client.get("installed")
        if not isinstance(data,dict):raise ValueError("Chọn OAuth client loại Desktop.")
        if (not re.fullmatch(r"[A-Za-z0-9_.-]{15,200}\.apps\.googleusercontent\.com",
                             str(data.get("client_id","")))
           or not 8<=len(str(data.get("client_secret","")))<=512):
            raise ValueError("Client ID/secret Google không hợp lệ.")
        if data.get("auth_uri")!="https://accounts.google.com/o/oauth2/auth" or data.get("token_uri")!="https://oauth2.googleapis.com/token":
            raise ValueError("Chỉ nhận Google OAuth endpoints chính thức.")
        self._save_private(self.client_path,{"installed":data})
        return self.status()

    def status(self):
        with self._lock:
            return {"client_configured":self.client_path.is_file(),
                    "connected":self.token_path.is_file(),
                    "oauth_status":self._status,"error":self._error,
                    "note":"Yêu cầu OAuth quyền youtube.upload; API upload phụ thuộc hạn mức/xác minh Google."}

    def connect_start(self):
        with self._lock:
            if self._status=="authorizing":
                raise ValueError("Đang mở đăng nhập Google OAuth.")
            if not self.client_path.is_file():
                raise ValueError("Trước hết nhập file OAuth Desktop client JSON.")
            self._status="authorizing";self._error=""
        threading.Thread(target=self._connect,daemon=True,name="thse-youtube-oauth").start()
        return self.status()

    def _connect(self):
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
            config=self._load_private(self.client_path)
            flow=InstalledAppFlow.from_client_config(config,scopes=[SCOPE])
            creds=flow.run_local_server(host="127.0.0.1",port=0,open_browser=True,
                 authorization_prompt_message="",success_message="Đã kết nối YouTube. Có thể đóng tab.")
            if not creds.refresh_token:
                raise RuntimeError("Google chưa cấp refresh token; kiểm tra consent và thử lại.")
            self._save_private(self.token_path,json.loads(creds.to_json()))
            with self._lock:self._status="connected"
        except Exception as exc:
            with self._lock:
                self._status="error"
                self._error=type(exc).__name__+": "+str(exc)[:220]

    def disconnect(self):
        with self._lock:
            if self._status=="authorizing":
                raise ValueError("Không thể ngắt khi trình duyệt đang xác thực.")
            self.token_path.unlink(missing_ok=True)
            self._status="idle";self._error=""
        return self.status()

    def credentials(self):
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        token=self._load_private(self.token_path)
        if not token:raise ValueError("Chưa kết nối YouTube.")
        creds=Credentials.from_authorized_user_info(token,scopes=[SCOPE])
        if not creds.valid:
            if not creds.refresh_token:raise ValueError("YouTube OAuth đã hết hạn. Kết nối lại.")
            creds.refresh(Request())
            self._save_private(self.token_path,json.loads(creds.to_json()))
        return creds

    def upload(self,path,title,caption,privacy="private"):
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        if privacy not in ("private","unlisted","public"):
            raise ValueError("Chế độ hiển thị YouTube không hợp lệ.")
        target=Path(path).resolve()
        if not target.is_file() or target.suffix.lower()!=".mp4":
            raise ValueError("Không tìm thấy MP4.")
        api=build("youtube","v3",credentials=self.credentials(),cache_discovery=False)
        body={"snippet":{"title":str(title)[:100],"description":str(caption)[:4500],
                         "categoryId":"27","defaultLanguage":"vi"},
              "status":{"privacyStatus":privacy,"selfDeclaredMadeForKids":False}}
        media=MediaFileUpload(str(target),mimetype="video/mp4",chunksize=8*1024*1024,resumable=True)
        request=api.videos().insert(part="snippet,status",body=body,media_body=media)
        response=None
        while response is None:
            _status,response=request.next_chunk(num_retries=0)
        video_id=str(response.get("id",""))
        if not re.fullmatch(r"[\w-]{8,40}",video_id):
            raise RuntimeError("YouTube không trả video ID hợp lệ.")
        return video_id
