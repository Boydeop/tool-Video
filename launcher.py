"""Windows launcher: responsive startup, HTTP health check and file diagnostics.

PyInstaller --windowed has neither stdout nor stderr. All runtime logs are
written under LocalAppData, so server failures are not silently swallowed.
"""
from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox
import urllib.error
import urllib.request
import webbrowser

from studio.service import Service
from studio.webapp import serve

URL = "http://127.0.0.1:8765"

def app_directory() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or str(Path.home())) / "TiengHanSoEasyStudio"

def setup_logging(folder: Path) -> Path:
    log_path = folder / "studio.log"
    handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.basicConfig(handlers=[handler], level=logging.INFO, force=True)
    return log_path

def healthcheck() -> bool:
    # Bypass system proxies for loopback; validate app identity, not just port.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(URL + "/health", timeout=0.5) as response:
            data = json.load(response)
        return data.get("app") == "TiengHanSoEasyStudio" and data.get("status") == "ok"
    except (OSError, ValueError, TimeoutError, urllib.error.URLError):
        return False

def main() -> None:
    folder = app_directory()
    folder.mkdir(parents=True, exist_ok=True)
    log_path = setup_logging(folder)
    logger = logging.getLogger("tienghansoeasy.launcher")
    events: queue.Queue[tuple[str,str]] = queue.Queue()
    root = tk.Tk()
    root.title("Tiếng Hàn So Easy Studio")
    root.geometry("630x290")
    root.resizable(False,False)
    root.configure(bg="#FDFBF7")

    tk.Label(root,text="TIẾNG HÀN SO EASY",font=("Segoe UI",19,"bold"),
             fg="#1E5BBB",bg="#FDFBF7").pack(pady=(24,6))
    tk.Label(root,text="AI câu hỏi  •  Giọng đọc Việt–Hàn  •  Video  •  Lịch Page",
             font=("Segoe UI",10),bg="#FDFBF7").pack(pady=5)
    status = tk.StringVar(value="Đang khởi động máy chủ nội bộ...")
    tk.Label(root,textvariable=status,font=("Segoe UI",10),
             bg="#FDFBF7",fg="#0F4C81").pack(pady=9)

    def open_studio():
        if healthcheck():
            webbrowser.open(URL)
        else:
            messagebox.showerror("Studio chưa kết nối",
                "Máy chủ nội bộ chưa phản hồi.\n"
                "Vui lòng xem file nhật ký:\n" + str(log_path))

    button=tk.Button(root,text="Mở Studio trong trình duyệt",
                     font=("Segoe UI",11,"bold"),bg="#1E5BBB",fg="white",
                     command=open_studio,state="disabled")
    button.pack(pady=5)
    tk.Label(root,text="Dữ liệu: "+str(folder),wraplength=600,
             font=("Segoe UI",8),fg="#5C6873",bg="#FDFBF7").pack(pady=5)
    tk.Label(root,text="Nhật ký lỗi: "+str(log_path),wraplength=600,
             font=("Segoe UI",8),fg="#5C6873",bg="#FDFBF7").pack()

    def worker():
        try:
            serve(Service(folder),on_ready=lambda addr:events.put(("bound",str(addr))))
        except OSError as exc:
            # If an older copy is already running, allow opening its dashboard.
            if healthcheck():
                events.put(("bound","Existing Studio process"))
            else:
                logger.exception("Could not bind Studio server")
                events.put(("error","Không mở được cổng 8765: "+str(exc)))
        except Exception as exc:
            logger.exception("Studio server crashed on launch")
            events.put(("error",type(exc).__name__+": "+str(exc)))

    threading.Thread(target=worker,name="studio-web-server",daemon=True).start()
    deadline=time.monotonic()+30
    opened=False
    error_message=None
    bound=False

    def poll():
        nonlocal opened,error_message,bound
        while True:
            try: kind,message=events.get_nowait()
            except queue.Empty:break
            if kind=="bound":
                bound=True
                logger.info("Server bound: %s",message)
            elif kind=="error":
                error_message=message
        if not opened and healthcheck():
            status.set("Studio đã sẵn sàng tại "+URL)
            button.configure(state="normal")
            opened=True
            if os.getenv("THSE_DISABLE_BROWSER","")!="1":
                webbrowser.open(URL)
        elif error_message and not opened:
            status.set("Lỗi khởi động. Kiểm tra studio.log.")
            button.configure(state="normal")
            messagebox.showerror("Studio không thể khởi động",
                error_message+"\n\nChi tiết: "+str(log_path))
            opened=True
        elif not opened and time.monotonic()>deadline:
            status.set("Máy chủ chưa sẵn sàng. Kiểm tra studio.log.")
            button.configure(state="normal")
            logger.error("Timed out waiting for Studio HTTP health check; bound=%s",bound)
            opened=True
        root.after(400,poll)

    root.after(200,poll)
    root.mainloop()

if __name__=="__main__":
    if "--smoke-server" in sys.argv:
        folder=app_directory()
        folder.mkdir(parents=True,exist_ok=True)
        setup_logging(folder)
        serve(Service(folder))
    else:
        main()
