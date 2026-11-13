"""Locate FFmpeg and FFprobe, including PyInstaller one-file bundled binaries."""
from pathlib import Path
import os
import shutil
import sys

def binary(name):
    key="THSE_"+name.upper()+"_BIN"
    configured=os.getenv(key)
    if configured:
        if Path(configured).is_file():return str(Path(configured).resolve())
        raise RuntimeError(key+" points to an invalid file")
    executable=name+".exe" if os.name=="nt" else name
    root=Path(getattr(sys,"_MEIPASS",Path(__file__).resolve().parent))
    candidate=root/executable
    if candidate.is_file():return str(candidate)
    external=shutil.which(name)
    if external:return external
    raise RuntimeError(name+" not available. Install FFmpeg or use bundled Windows EXE.")
