"""Facebook Page token verification and locally protected credential storage.

A verified Page read does not guarantee publish permission or Meta App Review.
The tool never starts publishing without a user-scheduled job.
"""
from __future__ import annotations
import base64
import ctypes
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

class PageError(RuntimeError):
    pass

def _path():
    return Path(os.getenv("THSE_FACEBOOK_PATH","data/facebook.local.json")).resolve()

def _version():
    version=os.getenv("META_GRAPH_VERSION","v24.0")
    if not re.fullmatch(r"v\d+\.\d+",version):
        raise PageError("Phiên bản Meta API không hợp lệ.")
    return version

def _protect(data):
    if os.name!="nt":
        return "plain:"+base64.b64encode(data.encode()).decode()
    # Windows DPAPI: encrypted to the current Windows user, not committed.
    from ctypes import wintypes
    class BLOB(ctypes.Structure):
        _fields_=[("cbData",wintypes.DWORD),("pbData",ctypes.POINTER(ctypes.c_ubyte))]
    def blob(raw):
        buff=ctypes.create_string_buffer(raw)
        return BLOB(len(raw),ctypes.cast(buff,ctypes.POINTER(ctypes.c_ubyte))),buff
    source,hold=blob(data.encode("utf-8"))
    result=BLOB()
    api=ctypes.WinDLL("crypt32",use_last_error=True)
    api.CryptProtectData.argtypes=[ctypes.POINTER(BLOB),ctypes.c_void_p,ctypes.c_void_p,
                                   ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(BLOB)]
    api.CryptProtectData.restype=wintypes.BOOL
    if not api.CryptProtectData(ctypes.byref(source),None,None,None,None,0,ctypes.byref(result)):
        raise PageError("Windows không mã hóa được Page token.")
    try:encrypted=ctypes.string_at(result.pbData,result.cbData)
    finally:
        kernel=ctypes.WinDLL("kernel32",use_last_error=True)
        kernel.LocalFree.argtypes=[ctypes.c_void_p]
        kernel.LocalFree.restype=ctypes.c_void_p
        kernel.LocalFree(ctypes.cast(result.pbData,ctypes.c_void_p))
    return "dpapi:"+base64.b64encode(encrypted).decode()

def _unprotect(value):
    if value.startswith("plain:") and os.name!="nt":
        return base64.b64decode(value[6:]).decode()
    if not value.startswith("dpapi:") or os.name!="nt":
        raise PageError("Token không tương thích với tài khoản Windows hiện tại.")
    from ctypes import wintypes
    class BLOB(ctypes.Structure):
        _fields_=[("cbData",wintypes.DWORD),("pbData",ctypes.POINTER(ctypes.c_ubyte))]
    raw=base64.b64decode(value[6:]);buffer=ctypes.create_string_buffer(raw)
    source=BLOB(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    dest=BLOB()
    api=ctypes.WinDLL("crypt32",use_last_error=True)
    api.CryptUnprotectData.argtypes=[ctypes.POINTER(BLOB),ctypes.c_void_p,ctypes.c_void_p,
                                     ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(BLOB)]
    api.CryptUnprotectData.restype=wintypes.BOOL
    if not api.CryptUnprotectData(ctypes.byref(source),None,None,None,None,0,ctypes.byref(dest)):
        raise PageError("Không thể giải mã Page token. Kết nối lại Page.")
    try:plain=ctypes.string_at(dest.pbData,dest.cbData).decode("utf-8")
    finally:
        kernel=ctypes.WinDLL("kernel32",use_last_error=True)
        kernel.LocalFree.argtypes=[ctypes.c_void_p]
        kernel.LocalFree.restype=ctypes.c_void_p
        kernel.LocalFree(ctypes.cast(dest.pbData,ctypes.c_void_p))
    return plain

def _read():
    path=_path()
    if not path.is_file():return {}
    try:return json.loads(path.read_text(encoding="utf-8"))
    except (OSError,ValueError):return {}

def page_status():
    data=_read()
    return {"connected":bool(data.get("token")),"page_id":data.get("page_id",""),
            "page_name":data.get("page_name",""),
            "verified_read":bool(data.get("verified_read")),
            "publishing_verified":False,
            "note":"Xác minh Page đọc được. Quyền đăng Reel chỉ xác nhận qua lần đăng thật."}

def credentials():
    data=_read()
    if data.get("token"):
        return str(data["page_id"]),_unprotect(str(data["token"]))
    return os.getenv("FB_PAGE_ID",""),os.getenv("FB_PAGE_ACCESS_TOKEN","")

def verify_page(page_id,token):
    if not re.fullmatch(r"\d{5,30}",str(page_id)):
        raise PageError("Page ID phải là số.")
    if not isinstance(token,str) or not 20<=len(token)<=1500:
        raise PageError("Page Access Token không hợp lệ.")
    url="https://graph.facebook.com/"+_version()+"/"+str(page_id)+"?fields=id%2Cname%2Ccategory"
    req=urllib.request.Request(url,headers={"Authorization":"Bearer "+token,
                                               "Accept":"application/json"})
    try:
        with urllib.request.urlopen(req,timeout=25) as res:
            payload=json.load(res)
    except urllib.error.HTTPError as exc:
        raise PageError(f"Meta HTTP {exc.code}: token hoặc Page ID không có quyền đọc.") from exc
    except (urllib.error.URLError,TimeoutError,ValueError) as exc:
        raise PageError("Không kết nối được Meta Graph API.") from exc
    if str(payload.get("id"))!=str(page_id) or not payload.get("name"):
        raise PageError("Meta trả Page ID không khớp.")
    return {"page_id":str(page_id),"page_name":str(payload["name"])[:150],"verified_read":True}

def connect(page_id,token):
    info=verify_page(page_id,token)
    info["token"]=_protect(token)
    path=_path();path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(".tmp")
    try:
        temp.write_text(json.dumps(info,ensure_ascii=False),encoding="utf-8")
        if os.name!="nt":os.chmod(temp,0o600)
        temp.replace(path)
    finally:temp.unlink(missing_ok=True)
    return page_status()

def disconnect():
    _path().unlink(missing_ok=True)
    return page_status()
