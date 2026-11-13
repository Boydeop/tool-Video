"""Local-only UI, localhost access only: not intended for public deployment."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from .scheduler import Scheduler

HTML='''<!DOCTYPE html><html lang="vi"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tiếng Hàn So Easy Studio</title>
<style>body{font:16px system-ui;margin:auto;max-width:1100px;padding:20px;background:#FDFBF7;color:#191612}
h1,h2{color:#1E5BBB}section{background:#fff;padding:18px;border-radius:12px;margin:16px 0}
button{background:#1E5BBB;color:#fff;border:none;padding:10px 16px;border-radius:7px;cursor:pointer}
input,select,textarea{padding:8px;border:1px solid #aaa;border-radius:6px}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #ddd;padding:8px;text-align:left}
pre{white-space:pre-wrap;background:#f0f5fb;padding:12px}
</style>
<h1>🇰🇷 TIẾNG HÀN SO EASY</h1><p>AI → Kiểm duyệt → Video → Lịch đăng Facebook Reels</p>
<section><h2>1. Tạo câu hỏi</h2>
<input id="topic" value="Chào hỏi và giới thiệu" placeholder="Chủ đề">
<select id="level"><option>TOPIK 1</option><option>TOPIK 2</option><option>TOPIK 3</option><option>TOPIK 4</option></select>
<input id="count" type="number" min="1" max="30" value="6">
<button onclick="generate()">Sinh câu hỏi AI</button>
<button onclick="daily()">Chủ đề mỗi ngày</button></section>
<section><h2>2. Ngân hàng câu hỏi</h2>
<button onclick="refresh()">Làm mới</button><button onclick="approve()">Duyệt câu đã chọn</button>
<select id="theme"><option>hanji</option><option>blue</option><option>sejong</option></select>
<label><input id="voice" type="checkbox" checked> Giọng đọc tiếng Việt và Hàn (cần mạng)</label>
<button onclick="render()">Dựng MP4 có tiếng</button>
<div style="overflow:auto"><table><thead><tr><th>Chọn</th><th>Trạng thái</th><th>Chủ đề</th><th>Câu hỏi</th><th>Đáp án đúng</th><th>Giải thích</th></tr></thead><tbody id="questions"></tbody></table></div></section>
<section><h2>3. API Key Pool (chỉ lưu trên máy)</h2>
<p>Nhập nhiều key cùng dịch vụ, ngăn cách bởi dấu phẩy. Không chia sẻ key với người khác.</p>
<select id="provider"><option>gemini</option><option>groq</option><option>openrouter</option></select>
<input id="keys" type="password" size="55" placeholder="Nhập danh sách key mới (ghi đè key đã lưu)">
<button onclick="saveKeys()">Lưu keys</button><p id="keySummary">Chưa kiểm tra</p></section>
<section><h2>4. Lịch đăng Facebook Page</h2>
<p>Đường dẫn MP4</p><input id="video" size="60">
<p>Ngày giờ Việt Nam</p><input id="at" type="datetime-local">
<p>Chú thích</p><textarea id="caption" rows="2" cols="55">Học tiếng Hàn cùng Tiếng Hàn So Easy! #tienghansoeasy</textarea>
<button onclick="schedule()">Lên lịch</button>
<table><thead><tr><th>Job</th><th>Thời gian</th><th>Trạng thái</th><th>Lỗi</th></tr></thead><tbody id="jobs"></tbody></table></section>
<section><h2>Kết quả</h2><pre id="output">Sẵn sàng</pre></section>
<script>
const $=id=>document.getElementById(id);
function show(x){$('output').textContent=typeof x==='string'?x:JSON.stringify(x,null,2)}
async function api(url,data){
const response=await fetch(url,{method:data?'POST':'GET',headers:{'Content-Type':'application/json'},body:data?JSON.stringify(data):undefined});
const result=await response.json();if(!response.ok)throw Error(result.error||response.status);return result}
async function run(f){try{show(await f());await refresh()}catch(e){show('Lỗi: '+e.message)}}
function ids(){return Array.from(document.querySelectorAll('[name=qid]:checked')).map(x=>x.value)}
function generate(){run(()=>api('/api/generate',{topic:$('topic').value,level:$('level').value,count:Number($('count').value)}))}
function daily(){run(()=>api('/api/daily',{count:Number($('count').value)}))}
function approve(){run(()=>api('/api/approve',{ids:ids()}))}
function render(){run(()=>api('/api/render',{ids:ids(),theme:$('theme').value,with_voice:$('voice').checked}))}
function saveKeys(){run(()=>api('/api/keys',{provider:$('provider').value,keys:$('keys').value})) ;$('keys').value=''}
function schedule(){const time=$('at').value;if(!time){show('Chọn ngày giờ');return}
run(()=>api('/api/schedule',{video_path:$('video').value,caption:$('caption').value,scheduled_at:new Date(time).toISOString()}))}
function cell(tr,val){const td=document.createElement('td');td.textContent=val||'';tr.appendChild(td)}
async function refresh(){
try{const d=await api('/api/status');$('keySummary').textContent='TTS: '+(d.tts_ready?'Đã cài giọng đọc (cần Internet)':'Chưa cài giọng đọc')+' | '+Object.entries(d.providers).map(([k,v])=>k+': '+v.keys+' key').join(' · ');$('questions').replaceChildren();$('jobs').replaceChildren();
for(const q of d.questions){const tr=document.createElement('tr'),td=document.createElement('td'),input=document.createElement('input');
input.type='checkbox';input.name='qid';input.value=q.id;td.appendChild(input);tr.appendChild(td);
for(const val of [q.status,q.topic,q.question,q.correct,q.explanation])cell(tr,val);$('questions').appendChild(tr)}
for(const job of d.jobs){const tr=document.createElement('tr');for(const val of [job.id.slice(0,8),job.scheduled_at,job.status,job.error])cell(tr,val);$('jobs').appendChild(tr)}
}catch(err){show(err.message)}}refresh()
</script></html>'''

def serve(service,host="127.0.0.1",port=8765):
    scheduler=Scheduler(service.store);scheduler.start()
    class Handler(BaseHTTPRequestHandler):
        def send(self,status,data,mime="application/json"):
            content=(data if isinstance(data,str) else json.dumps(data,ensure_ascii=False)).encode()
            self.send_response(status);self.send_header("Content-Type",mime+"; charset=utf-8")
            self.send_header("Content-Length",str(len(content)));self.send_header("Cache-Control","no-store")
            self.end_headers();self.wfile.write(content)
        def do_GET(self):
            if self.path=="/":self.send(200,HTML,"text/html")
            elif self.path=="/api/status":
                self.send(200,{"questions":service.store.list_questions(limit=300),
                               "jobs":service.store.list_jobs(),
                               "providers":__import__("studio.keys",fromlist=["readiness"]).readiness(),
                               "tts_ready":__import__("studio.voice",fromlist=["available"]).available()})
            else:self.send(404,{"error":"Not found"})
        def do_POST(self):
            origin=self.headers.get("Origin","")
            if origin and origin not in ("http://127.0.0.1:8765","http://localhost:8765"):
                self.send(403,{"error":"Forbidden origin"});return
            if self.path not in ("/api/generate","/api/daily","/api/approve","/api/render","/api/schedule","/api/keys"):
                self.send(404,{"error":"Not found"});return
            try:
                length=int(self.headers.get("Content-Length","0"))
                if not 0<length<20000:raise ValueError("Invalid payload size")
                raw=json.loads(self.rfile.read(length))
                if not isinstance(raw,dict):raise ValueError("Expected JSON object")
                if self.path=="/api/generate":
                    result=service.generate(str(raw.get("topic","")),
                        str(raw.get("level","TOPIK 1")),int(raw.get("count",6)))
                elif self.path=="/api/daily":
                    result=service.daily(count=int(raw.get("count",6)))
                elif self.path=="/api/approve":
                    ids=raw.get("ids",[])
                    if not isinstance(ids,list) or not ids:raise ValueError("No IDs selected")
                    service.store.approve(ids);result={"approved":ids}
                elif self.path=="/api/render":
                    ids=raw.get("ids",[])
                    if not isinstance(ids,list):raise ValueError("Invalid IDs")
                    result={"video":service.render_approved(ids,str(raw.get("theme","hanji")),
                        with_voice=raw.get("with_voice") is True)}
                elif self.path=="/api/keys":
                    from .keys import write_keys
                    result={"saved_key_count":write_keys(str(raw.get("provider","")),
                        str(raw.get("keys","")))}
                else:
                    result={"job_id":service.schedule(str(raw.get("video_path","")),
                        str(raw.get("caption","")),str(raw.get("scheduled_at","")))}
                self.send(200,result)
            except (ValueError,RuntimeError,KeyError) as exc:self.send(400,{"error":str(exc)})
    print("Open http://127.0.0.1:8765")
    try:ThreadingHTTPServer((host,port),Handler).serve_forever()
    except KeyboardInterrupt:pass
    finally:scheduler.stop()
