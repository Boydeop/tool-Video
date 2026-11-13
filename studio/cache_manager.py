"""Least-privilege media cache cleanup.

Never recursively delete an arbitrary user-configured directory or scan outside
named generated assets. Symlinks are excluded. Do not clear while any render,
preview or TTS generation is active.
"""
from __future__ import annotations
from pathlib import Path
import re
import time

_HASHED_AUDIO=re.compile(r"^[0-9a-f]{64}(?:\.working)?\.mp3$")
_PREVIEW=re.compile(r"^preview_[0-9a-f]{12}\.mp4$")

class CacheManager:
    def __init__(self,folder):
        self.folder=Path(folder).expanduser().resolve()

    def _files(self):
        root=self.folder
        if not root.exists():return
        # Only match hashed audio at root and Studio's generated previews.
        for candidate in root.glob("*.mp3"):
            if candidate.is_file() and not candidate.is_symlink() and _HASHED_AUDIO.fullmatch(candidate.name):
                yield candidate
        preview=root/"video_previews"
        if preview.is_dir() and not preview.is_symlink():
            for candidate in preview.glob("preview_*.mp4"):
                if candidate.is_file() and not candidate.is_symlink() and _PREVIEW.fullmatch(candidate.name):
                    yield candidate

    def stats(self):
        items=list(self._files())
        size=sum(p.stat().st_size for p in items)
        return {"files":len(items),"bytes":size,"mb":round(size/1048576,2),
                "directory":str(self.folder)}

    def clear(self,older_than_days=0,dry_run=True):
        if not isinstance(older_than_days,int) or not 0<=older_than_days<=3650:
            raise ValueError("Days must be from 0 to 3650")
        threshold=time.time()-older_than_days*86400
        candidates=[p for p in self._files() if p.stat().st_mtime<=threshold]
        removed=0;total_bytes=0;errors=[]
        for path in candidates:
            try:
                length=path.stat().st_size
                if not dry_run:
                    path.unlink()
                removed+=1;total_bytes+=length
            except OSError as exc:
                errors.append(path.name+":"+type(exc).__name__)
        return {"dry_run":bool(dry_run),"files":removed,"bytes":total_bytes,
                "mb":round(total_bytes/1048576,2),"errors":errors,"remaining":self.stats()}
