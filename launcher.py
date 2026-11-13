"""Windows one-click launcher: localhost dashboard + persistent AppData."""
from pathlib import Path
import os
import threading
import tkinter as tk
from tkinter import messagebox
import webbrowser
from studio.service import Service
from studio.webapp import serve

URL="http://127.0.0.1:8765"

def main():
    base=Path(os.environ.get("LOCALAPPDATA",str(Path.home())))/"TiengHanSoEasyStudio"
    base.mkdir(parents=True,exist_ok=True)
    root=tk.Tk()
    root.title("Tiếng Hàn So Easy Studio")
    root.geometry("590x235")
    root.configure(background="#FDFBF7")
    tk.Label(root,text="🇰🇷 TIẾNG HÀN SO EASY",font=("Segoe UI",18,"bold"),
             fg="#1E5BBB",bg="#FDFBF7").pack(pady=(22,6))
    tk.Label(root,text="AI câu hỏi • Giọng đọc Việt–Hàn • Video • Lịch Page",
             font=("Segoe UI",10),bg="#FDFBF7").pack(pady=4)
    tk.Button(root,text="Mở Studio trong trình duyệt",font=("Segoe UI",11,"bold"),
              bg="#1E5BBB",fg="white",command=lambda:webbrowser.open(URL)).pack(pady=12)
    tk.Label(root,text="Dữ liệu lưu tại: "+str(base),wraplength=550,
             font=("Segoe UI",8),fg="#5C6873",bg="#FDFBF7").pack()
    def start():
        try:
            serve(Service(base))
        except Exception as exc:
            error=str(exc)
            root.after(0,lambda:messagebox.showerror("Không khởi động được",error))
    threading.Thread(target=start,daemon=True,name="studio-server").start()
    root.after(1500,lambda:webbrowser.open(URL))
    root.mainloop()

if __name__=="__main__":main()
