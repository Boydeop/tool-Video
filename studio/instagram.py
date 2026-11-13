"""Official Instagram professional account Reels publishing via Meta Graph API.

Instagram fetches the actual MP4 from a public HTTPS URL; a localhost file path
is not sufficient. This module does not host user files or scrape Instagram.
"""
from __future__ import annotations
import ipaddress
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from .facebook import _protect,_unprotect

GRAPH_VERSION="v27.0"

def validate_public_url(value):
    if not isinstance(value,str) or len(value)>1200:
        raise ValueError("Cần đường dẫn MP4 HTTPS công khai (không phải file local).")
    url=urllib.parse.urlsplit(value)
    if url.scheme!="https" or not url.hostname or url.username or url.password:
        raise ValueError("Instagram cần URL HTTPS công khai, không chứa mật khẩu.")
    hostname=url.hostname.lower().rstrip(".")
    if hostname=="localhost" or hostname.endswith((".local",".internal")):
        raise ValueError("Instagram không truy cập được địa chỉ nội bộ.")
    try:
        address=ipaddress.ip_address(hostname)
        if not address.is_global:raise ValueError("URL không được trỏ vào IP nội bộ.")
    except ValueError as exc:
        if "không được" in str(exc):raise
        if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}",hostname):
            raise ValueError("Tên miền URL không hợp lệ.") from exc
    if url.port not in (None,443):
        raise ValueError("URL Instagram chỉ hỗ trợ HTTPS thông thường.")
    return value

def _graph(path,token,values=None,method="GET"):
    url="https://graph.facebook.com/"+GRAPH_VERSION+"/"+path.lstrip("/")
    if method=="GET":
        if values:url+="?"+urllib.parse.urlencode(values)
        payload=None
    else:
        payload=urllib.parse.urlencode(values or {}).encode("utf-8")
    request=urllib.request.Request(url,data=payload,method=method,
                                   headers={"Authorization":"Bearer "+token,
                                            "Accept":"application/json"})
    try:
        with urllib.request.urlopen(request,timeout=45) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError("Instagram Graph API HTTP "+str(exc.code)+" (kiểm tra quyền)") from exc
    except (urllib.error.URLError,TimeoutError,ValueError) as exc:
        raise RuntimeError("Không kết nối được Instagram Graph API.") from exc

class InstagramAccount:
    def __init__(self,root):
        self.path=Path(root).resolve()/"instagram.local.json"
        self._lock=threading.RLock()

    def _read(self):
        if not self.path.is_file():return None
        obj=json.loads(self.path.read_text("utf-8"))
        return {"ig_id":obj["ig_id"],"username":obj.get("username",""),
                "token":_unprotect(obj["token"])}

    def status(self):
        row=self._read()
        return {"connected":bool(row),"ig_id":row["ig_id"] if row else "",
                "username":row["username"] if row else "",
                "publishing_verified":False,
                "note":"Cần tài khoản Instagram Professional, quyền Meta và MP4 tại URL HTTPS công khai."}

    def connect(self,ig_id,token):
        if not re.fullmatch(r"\d{5,30}",str(ig_id)) or not isinstance(token,str) or len(token)<20:
            raise ValueError("Instagram User ID hoặc token không hợp lệ.")
        response=_graph(str(ig_id),token,{"fields":"id,username"})
        if str(response.get("id"))!=str(ig_id):
            raise ValueError("Instagram user ID không khớp kết quả Meta.")
        info={"ig_id":str(ig_id),"username":str(response.get("username",""))[:100],
              "token":_protect(token)}
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temp=self.path.with_suffix(".tmp")
        try:
            temp.write_text(json.dumps(info,ensure_ascii=False),encoding="utf-8")
            if os.name!="nt":os.chmod(temp,0o600)
            temp.replace(self.path)
        finally:temp.unlink(missing_ok=True)
        return self.status()

    def disconnect(self):
        self.path.unlink(missing_ok=True)
        return self.status()

    def publish(self,public_video_url,caption):
        url=validate_public_url(public_video_url)
        row=self._read()
        if not row:raise ValueError("Chưa kết nối Instagram Professional.")
        ig_id,token=row["ig_id"],row["token"]
        created=_graph(ig_id+"/media",token,
             {"media_type":"REELS","video_url":url,"caption":str(caption)[:2000],
              "share_to_feed":"true"},method="POST")
        container=str(created.get("id",""))
        if not container.isdigit():
            raise RuntimeError("Meta không trả mã container Instagram.")
        for attempt in range(20):
            time.sleep(5)
            result=_graph(container,token,{"fields":"status_code,status"})
            state=str(result.get("status_code",""))
            if state=="FINISHED":break
            if state in ("ERROR","EXPIRED"):
                raise RuntimeError("Instagram media container "+state+"; kiểm tra Meta.")
        else:
            raise RuntimeError("Instagram chưa xử lý xong container "+container+
                               "; cần kiểm tra, không tự đăng lại.")
        published=_graph(ig_id+"/media_publish",token,
                         {"creation_id":container},method="POST")
        media_id=str(published.get("id",""))
        if not media_id.isdigit():
            raise RuntimeError("Instagram chưa xác nhận media ID.")
        return media_id
