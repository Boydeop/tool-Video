"""Official Meta Page Reels three-stage upload. No password automation."""
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

class PublishingError(RuntimeError): pass

def publish_reel(video_path,caption):
    page=os.getenv("FB_PAGE_ID","").strip()
    token=os.getenv("FB_PAGE_ACCESS_TOKEN","").strip()
    version=os.getenv("META_GRAPH_VERSION","v24.0")
    if not page.isdigit() or not token: raise PublishingError("Set valid Page ID and Page access token")
    if not version.startswith("v") or not version[1:].replace(".","").isdigit():
        raise PublishingError("Invalid Meta Graph version")
    path=Path(video_path).resolve()
    if not path.is_file() or path.suffix.lower()!=".mp4": raise PublishingError("Missing MP4")
    base="https://graph.facebook.com/"+version+"/"+page+"/video_reels"
    def form(fields):
        return urllib.parse.urlencode({"access_token":token,**fields}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(base,data=form({"upload_phase":"start"})),timeout=45) as r:
            init=json.load(r)
    except (urllib.error.HTTPError,urllib.error.URLError) as exc:
        raise PublishingError("Cannot initialize Reel; check Page permissions") from exc
    video_id=str(init.get("video_id",""))
    upload_url=init.get("upload_url","")
    if not video_id.isdigit() or not upload_url.startswith("https://rupload.facebook.com/"):
        raise PublishingError("Meta upload response invalid")
    with path.open("rb") as stream:
        req=urllib.request.Request(upload_url,data=stream,method="POST",headers={
            "Authorization":"OAuth "+token,"offset":"0",
            "file_size":str(path.stat().st_size),
            "Content-Type":"application/octet-stream"})
        try:
            with urllib.request.urlopen(req,timeout=900) as r: response=json.load(r)
        except (urllib.error.HTTPError,urllib.error.URLError) as exc:
            raise PublishingError("Upload uncertain; check Page before retry") from exc
    if response.get("success") is False: raise PublishingError("Meta did not confirm upload")
    finish=urllib.request.Request(base,data=form({"upload_phase":"finish","video_id":video_id,
        "video_state":"PUBLISHED","description":caption[:2000]}))
    try:
        with urllib.request.urlopen(finish,timeout=120) as r: result=json.load(r)
    except (urllib.error.HTTPError,urllib.error.URLError) as exc:
        raise PublishingError("Finalization uncertain; check Page before retry") from exc
    if not result.get("success"): raise PublishingError("Meta did not confirm finalization")
    return video_id
