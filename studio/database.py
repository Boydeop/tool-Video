"""Durable SQLite storage: exact uniqueness, approximate checks, jobs and audit."""
from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from .quiz import Quiz, fingerprint, similar, normalize

class DuplicateQuestion(ValueError):
    pass

def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()

class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.init()

    @contextmanager
    def db(self,*,immediate=False):
        """Close every connection; reserve writes before read/modify/write."""
        con=sqlite3.connect(str(self.path),timeout=30)
        try:
            con.row_factory=sqlite3.Row
            with con:
                if immediate:con.execute("BEGIN IMMEDIATE")
                yield con
        finally:
            con.close()

    def init(self):
        with self.db() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript("""
            CREATE TABLE IF NOT EXISTS questions(
                id TEXT PRIMARY KEY, topic TEXT NOT NULL,
                question TEXT NOT NULL, correct TEXT NOT NULL,
                payload TEXT NOT NULL, question_hash TEXT UNIQUE NOT NULL,
                answer_hash TEXT UNIQUE NOT NULL, status TEXT NOT NULL,
                provider TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS questions_status ON questions(status);
            CREATE TABLE IF NOT EXISTS jobs(
                id TEXT PRIMARY KEY, video_path TEXT NOT NULL,
                caption TEXT NOT NULL, scheduled_at TEXT NOT NULL,
                status TEXT NOT NULL, remote_id TEXT, error TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS jobs_due ON jobs(status, scheduled_at);
            CREATE TABLE IF NOT EXISTS daily_runs(
                day TEXT PRIMARY KEY, topic TEXT NOT NULL,
                result TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS video_registry(
                id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE,
                question_ids TEXT NOT NULL, topic TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'rendered',
                seo_title TEXT NOT NULL DEFAULT '',
                seo_caption TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS question_qa(
                question_id TEXT PRIMARY KEY,
                outcome TEXT NOT NULL,
                report_json TEXT NOT NULL,
                reviewed_at TEXT NOT NULL,
                FOREIGN KEY(question_id) REFERENCES questions(id)
            );
            CREATE TABLE IF NOT EXISTS youtube_thumbnail_jobs(
                video_id TEXT PRIMARY KEY,
                video_path TEXT NOT NULL,
                image_path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS yt_thumb_due ON youtube_thumbnail_jobs(status,next_attempt);
            CREATE TABLE IF NOT EXISTS krdict_cache(
                query TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                fetched_unix INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS counters(
                name TEXT PRIMARY KEY,value INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS production_runs(
                id TEXT PRIMARY KEY,status TEXT NOT NULL,
                plan TEXT NOT NULL,results TEXT NOT NULL DEFAULT '[]',
                error TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS production_runs_created ON production_runs(created_at);
            CREATE TABLE IF NOT EXISTS outbound_posts(
                id TEXT PRIMARY KEY,video_path TEXT NOT NULL,
                platform TEXT NOT NULL,caption TEXT NOT NULL,
                title TEXT NOT NULL,publish_at TEXT NOT NULL,
                public_url TEXT NOT NULL DEFAULT '',
                privacy TEXT NOT NULL DEFAULT 'private',
                status TEXT NOT NULL DEFAULT 'scheduled',
                remote_id TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                UNIQUE(video_path,platform)
            );
            CREATE INDEX IF NOT EXISTS outbound_due ON outbound_posts(status,publish_at);
            CREATE TABLE IF NOT EXISTS audit(
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
                kind TEXT NOT NULL, detail TEXT NOT NULL
            );
            """)
            # Migrate old 0.3.x SQLite in place without removing questions.
            columns={row["name"] for row in c.execute("PRAGMA table_info(questions)")}
            if "batch_id" not in columns:
                c.execute("ALTER TABLE questions ADD COLUMN batch_id TEXT NOT NULL DEFAULT ''")
            if "last_video" not in columns:
                c.execute("ALTER TABLE questions ADD COLUMN last_video TEXT NOT NULL DEFAULT ''")
            c.execute("CREATE INDEX IF NOT EXISTS questions_batch_id ON questions(batch_id)")
            c.execute("CREATE INDEX IF NOT EXISTS questions_status_created ON questions(status,created_at)")
            job_cols={row["name"] for row in c.execute("PRAGMA table_info(jobs)")}
            if "title" not in job_cols:
                c.execute("ALTER TABLE jobs ADD COLUMN title TEXT NOT NULL DEFAULT ''")

    def krdict_cache_get(self,query,max_age_seconds=2592000):
        import time
        with self.db() as c:
            row=c.execute("SELECT payload_json,fetched_unix FROM krdict_cache WHERE query=?",
                          (str(query),)).fetchone()
            if not row or (time.time()-row["fetched_unix"])>max_age_seconds:
                return None
            return json.loads(row["payload_json"])

    def krdict_cache_put(self,query,payload):
        import time
        with self._lock,self.db(immediate=True) as c:
            c.execute("""INSERT OR REPLACE INTO krdict_cache(query,payload_json,fetched_unix)
                         VALUES(?,?,?)""",
                      (str(query),json.dumps(payload,ensure_ascii=False),int(time.time())))

    def set_question_qa(self,qid,outcome,report):
        """Keep QA outcome independent of production and manual approvals."""
        if outcome not in ("verified","needs_review","unavailable","stale"):
            raise ValueError("Invalid QA outcome")
        with self._lock,self.db(immediate=True) as c:
            row=c.execute("SELECT status FROM questions WHERE id=?",(qid,)).fetchone()
            if not row:raise ValueError("Question does not exist")
            if row["status"]=="rendered":
                raise ValueError("Cannot modify QA of an already rendered question")
            c.execute("""INSERT OR REPLACE INTO question_qa
                 (question_id,outcome,report_json,reviewed_at) VALUES(?,?,?,?)""",
                (qid,outcome,json.dumps(report,ensure_ascii=False),utcnow()))
            if outcome=="verified" and row["status"]=="draft":
                c.execute("UPDATE questions SET status='auto_approved' WHERE id=?",(qid,))
            elif outcome!="verified" and row["status"]=="auto_approved":
                c.execute("UPDATE questions SET status='draft' WHERE id=?",(qid,))

    def get_question_provider(self,qid):
        with self.db() as c:
            row=c.execute("SELECT provider FROM questions WHERE id=?",(qid,)).fetchone()
            if not row:raise ValueError("Question does not exist")
            return row["provider"]

    def pending_verification(self,limit=8):
        with self.db() as c:
            return [dict(row) for row in c.execute("""
                SELECT q.id FROM questions q
                LEFT JOIN question_qa qa ON qa.question_id=q.id
                WHERE q.provider!='manual' AND
                  (q.status='draft' OR (q.status='auto_approved' AND
                   (qa.outcome IS NULL OR qa.outcome IN ('not_checked','stale'))))
                ORDER BY q.created_at DESC LIMIT ?""",(limit,))]

    def get_question_qa(self,qid):
        with self.db() as c:
            r=c.execute("SELECT outcome,report_json,reviewed_at FROM question_qa WHERE question_id=?",
                        (qid,)).fetchone()
            return ({"outcome":r["outcome"],"report":json.loads(r["report_json"]),
                     "reviewed_at":r["reviewed_at"]} if r else
                    {"outcome":"not_checked","report":{},"reviewed_at":""})

    def allocate_video_number(self):
        """Atomic monotonically increasing sequence; gaps after failures are fine."""
        with self._lock,self.db(immediate=True) as c:
            c.execute("INSERT OR IGNORE INTO counters(name,value) VALUES('video',0)")
            c.execute("UPDATE counters SET value=value+1 WHERE name='video'")
            return int(c.execute("SELECT value FROM counters WHERE name='video'").fetchone()[0])

    def auto_approve(self,ids):
        """Records machine checks separately from human approval."""
        if not ids:return
        with self._lock,self.db(immediate=True) as c:
            placeholders=",".join("?" for _ in ids)
            rows=c.execute("SELECT id,status FROM questions WHERE id IN ("+placeholders+")",ids).fetchall()
            if len(rows)!=len(set(ids)) or any(r["status"]!="draft" for r in rows):
                raise ValueError("Only drafts can pass automated checks")
            c.execute("UPDATE questions SET status='auto_approved' WHERE id IN ("+
                      placeholders+")",ids)

    def create_production_run(self,run_id,plan):
        with self._lock,self.db(immediate=True) as c:
            c.execute("""INSERT INTO production_runs(id,status,plan,results,created_at,updated_at)
                         VALUES(?,'running',?,'[]',?,?)""",
                     (run_id,json.dumps(plan,ensure_ascii=False),utcnow(),utcnow()))

    def update_production_run(self,run_id,status,results,error=""):
        if status not in ("running","completed","partial","failed","interrupted"):
            raise ValueError("Invalid run status")
        with self._lock,self.db(immediate=True) as c:
            row=c.execute("""UPDATE production_runs SET status=?,results=?,error=?,updated_at=?
                         WHERE id=?""",(status,json.dumps(results,ensure_ascii=False),
                                         str(error)[:900],utcnow(),run_id))
            if row.rowcount!=1:raise ValueError("Production run not found")

    def list_production_runs(self,limit=40):
        with self.db() as c:
            return [{**dict(r),"plan":json.loads(r["plan"]),
                     "results":json.loads(r["results"])}
                    for r in c.execute("SELECT * FROM production_runs ORDER BY created_at DESC LIMIT ?",(limit,))]

    def recover_incomplete_runs(self):
        # Crash-restart recovery: never misreport a partially generated run as complete.
        with self._lock,self.db(immediate=True) as c:
            c.execute("UPDATE production_runs SET status='interrupted',updated_at=? WHERE status='running'",
                      (utcnow(),))
            c.execute("UPDATE outbound_posts SET status='needs_review',error='App restarted during upload; check the platform before trying again',updated_at=? WHERE status='publishing'",
                      (utcnow(),))
            c.execute("UPDATE youtube_thumbnail_jobs SET status='pending',updated_at=? WHERE status='processing'",
                      (utcnow(),))
            c.execute("UPDATE jobs SET status='needs_review',error='App restarted during Page upload; check Page before retry',updated_at=? WHERE status='publishing'",
                      (utcnow(),))

    def enqueue_youtube_thumbnail(self,video_path,video_id):
        from .thumbnails import thumbnail_path
        file=thumbnail_path(video_path)
        if not file.is_file():return False
        with self._lock,self.db(immediate=True) as c:
            c.execute("""INSERT OR IGNORE INTO youtube_thumbnail_jobs
                (video_id,video_path,image_path,status,next_attempt,updated_at)
                VALUES(?,?,?,'pending',?,?)""",
                (str(video_id),str(Path(video_path).resolve()),str(file),utcnow(),utcnow()))
        return True

    def claim_youtube_thumbnails(self,limit=1):
        with self._lock,self.db(immediate=True) as c:
            rows=c.execute("""SELECT * FROM youtube_thumbnail_jobs
                WHERE status='pending' AND next_attempt<=?
                ORDER BY next_attempt LIMIT ?""",(utcnow(),limit)).fetchall()
            for row in rows:
                c.execute("UPDATE youtube_thumbnail_jobs SET status='processing',updated_at=? WHERE video_id=?",
                          (utcnow(),row["video_id"]))
            return [dict(row) for row in rows]

    def update_youtube_thumbnail(self,video_id,success=False,error="",retry=False):
        from datetime import timedelta
        with self._lock,self.db(immediate=True) as c:
            row=c.execute("SELECT attempts FROM youtube_thumbnail_jobs WHERE video_id=?",
                          (video_id,)).fetchone()
            if row is None:raise ValueError("Thumbnail job not found")
            tries=int(row["attempts"])
            if success:
                status="completed";next_date=utcnow()
            elif retry:
                status="needs_review" if tries+1>=8 else "pending"
                next_date=(datetime.now(timezone.utc)+timedelta(
                    seconds=min(3600,30*2**min(tries,6)))).isoformat()
            else:
                status="needs_review";next_date=utcnow()
            c.execute("""UPDATE youtube_thumbnail_jobs
                SET status=?,attempts=attempts+1,next_attempt=?,error=?,updated_at=?
                WHERE video_id=?""",
                (status,next_date,str(error)[:240],utcnow(),video_id))

    def list_youtube_thumbnails(self):
        with self.db() as c:
            return [dict(row) for row in c.execute(
                "SELECT * FROM youtube_thumbnail_jobs ORDER BY updated_at DESC LIMIT 80")]

    def schedule_outbound(self,video_path,platform,title,caption,when,
                          public_url="",privacy="private"):
        from datetime import timedelta
        if platform not in ("youtube","instagram"):
            raise ValueError("Unsupported automated publishing platform")
        point=datetime.fromisoformat(str(when).replace("Z","+00:00"))
        if point.tzinfo is None or point.astimezone(timezone.utc)<=datetime.now(timezone.utc):
            raise ValueError("Schedule must be in the future with timezone")
        path=Path(video_path).resolve()
        if path.suffix.lower()!=".mp4" or not path.is_file():
            raise ValueError("Missing MP4")
        if privacy not in ("private","unlisted","public"):
            raise ValueError("Unknown YouTube privacy")
        if len(title)>150 or len(caption)>2000:raise ValueError("Publishing text is too long")
        jid=uuid.uuid4().hex
        with self._lock,self.db(immediate=True) as c:
            if c.execute("SELECT 1 FROM outbound_posts WHERE video_path=? AND platform=?",
                         (str(path),platform)).fetchone():
                raise ValueError("MP4 đã được lên lịch trên nền tảng này; kiểm tra danh sách lịch.")
            c.execute("""INSERT INTO outbound_posts
              (id,video_path,platform,title,caption,publish_at,public_url,privacy,status,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,'scheduled',?,?)""",
              (jid,str(path),platform,title,caption,point.astimezone(timezone.utc).isoformat(),
               public_url,privacy,utcnow(),utcnow()))
        return jid

    def claim_outbound(self,limit=2):
        with self._lock,self.db(immediate=True) as c:
            rows=c.execute("""SELECT * FROM outbound_posts WHERE status='scheduled'
                              AND publish_at<=? ORDER BY publish_at LIMIT ?""",
                          (utcnow(),limit)).fetchall()
            result=[]
            for row in rows:
                c.execute("UPDATE outbound_posts SET status='publishing',updated_at=? WHERE id=? AND status='scheduled'",
                          (utcnow(),row["id"]))
                result.append(dict(row))
            return result

    def finish_outbound(self,jid,status,remote_id="",error=""):
        if status not in ("submitted","needs_review","cancelled"):
            raise ValueError("Unknown publishing status")
        with self._lock,self.db(immediate=True) as c:
            c.execute("""UPDATE outbound_posts SET status=?,remote_id=?,error=?,updated_at=?
                         WHERE id=?""",(status,str(remote_id)[:200],str(error)[:700],utcnow(),jid))

    def list_outbound(self,limit=100):
        with self.db() as c:
            return [dict(r) for r in c.execute("SELECT * FROM outbound_posts ORDER BY created_at DESC LIMIT ?",(limit,))]

    def has_outbound(self,video_path):
        with self.db() as c:
            return c.execute("SELECT 1 FROM outbound_posts WHERE video_path=? LIMIT 1",
                             (str(Path(video_path).resolve()),)).fetchone() is not None

    def log(self, kind, detail):
        with self._lock, self.db(immediate=True) as c:
            c.execute("INSERT INTO audit(created_at,kind,detail) VALUES(?,?,?)",
                      (utcnow(), kind, str(detail)[:1500]))

    def insert_question(self, quiz: Quiz, provider="manual", batch_id="") -> str:
        quiz.validate()
        qhash = fingerprint(quiz.question)
        ahash = fingerprint(quiz.correct)
        with self._lock, self.db(immediate=True) as c:
            exact = c.execute(
                "SELECT 1 FROM questions WHERE question_hash=? OR answer_hash=?",
                (qhash, ahash)).fetchone()
            if exact:
                raise DuplicateQuestion("Trùng câu hỏi hoặc đáp án tiếng Hàn đã lưu.")
            # For near duplicates, scan question history. Exact hashes are indexed.
            for r in c.execute("SELECT question, correct FROM questions"):
                if similar(r["question"], quiz.question) >= 0.89:
                    raise DuplicateQuestion("Câu hỏi gần trùng với dữ liệu cũ.")
                if len(normalize(quiz.correct)) > 8 and similar(r["correct"], quiz.correct) >= 0.94:
                    raise DuplicateQuestion("Đáp án gần trùng với dữ liệu cũ.")
            qid = uuid.uuid4().hex
            c.execute("""INSERT INTO questions
                (id,topic,question,correct,payload,question_hash,answer_hash,status,provider,created_at,batch_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (qid,quiz.topic,quiz.question,quiz.correct,
                 json.dumps(quiz.to_dict(),ensure_ascii=False),
                 qhash,ahash,"draft",provider,utcnow(),batch_id))
            return qid

    def list_questions(self, status=None, limit=100):
        with self.db() as c:
            if status:
                rows = c.execute("SELECT q.*,qa.outcome qa_outcome,qa.report_json qa_report_json FROM questions q LEFT JOIN question_qa qa ON qa.question_id=q.id WHERE q.status=? ORDER BY q.created_at DESC LIMIT ?",
                                 (status,limit)).fetchall()
            else:
                rows = c.execute("SELECT q.*,qa.outcome qa_outcome,qa.report_json qa_report_json FROM questions q LEFT JOIN question_qa qa ON qa.question_id=q.id ORDER BY q.created_at DESC LIMIT ?",
                                 (limit,)).fetchall()
            result=[]
            for r in rows:
                payload=json.loads(r["payload"])
                quiz=Quiz.parse(payload)
                item=dict(id=r["id"],status=r["status"],provider=r["provider"],
                    created_at=r["created_at"],batch_id=r["batch_id"],
                    last_video=r["last_video"],**payload)
                item["qa_outcome"]=r["qa_outcome"] or "not_checked"
                item["qa_report"]=json.loads(r["qa_report_json"]) if r["qa_report_json"] else {}
                item["short_clue"]=quiz.short_clue
                item["meaning"]=quiz.vietnamese_meaning
                result.append(item)
            return result

    def get_questions(self, ids: list[str], status=None) -> list[Quiz]:
        if not ids:
            return []
        with self.db() as c:
            placeholders = ",".join("?" for _ in ids)
            query = "SELECT id,payload,status FROM questions WHERE id IN (" + placeholders + ")"
            data = {r["id"]: r for r in c.execute(query, ids)}
            if len(data) != len(set(ids)):
                raise ValueError("Không tìm thấy một số câu hỏi.")
            if status and any(data[i]["status"] not in
                (status if isinstance(status,(tuple,list,set)) else (status,))
                for i in ids):
                raise ValueError("Câu hỏi phải được duyệt trước khi dựng video.")
            return [Quiz.parse(json.loads(data[i]["payload"])) for i in ids]

    def update_answer_details(self,qid,fields):
        """Update only per-answer educational data; require reapproval."""
        from .answer_enrichment import DETAIL_KEYS
        from dataclasses import replace
        if not isinstance(fields,dict) or any(k not in DETAIL_KEYS for k in fields):
            raise ValueError("Trường nghĩa/phiên âm không hợp lệ.")
        sanitized={}
        for key,value in fields.items():
            if not isinstance(value,str) or len(value)>120:
                raise ValueError("Phiên âm hoặc nghĩa vượt giới hạn.")
            sanitized[key]=value.strip()
        with self._lock,self.db(immediate=True) as c:
            row=c.execute("SELECT payload,status FROM questions WHERE id=?",(qid,)).fetchone()
            if not row:raise ValueError("Không tìm thấy câu hỏi.")
            if row["status"]=="rendered":
                raise ValueError("Không sửa câu đã xuất video. Tạo phiên bản câu mới.")
            quiz=Quiz.parse(json.loads(row["payload"]))
            updated=replace(quiz,**sanitized)
            updated.validate()
            c.execute("""INSERT OR REPLACE INTO question_qa(question_id,outcome,report_json,reviewed_at)
                         VALUES(?,'stale','{}',?)""",(qid,utcnow()))
            c.execute("UPDATE questions SET payload=?,status='draft' WHERE id=?",
                      (json.dumps(updated.to_dict(),ensure_ascii=False),qid))
        return {"id":qid,"status":"draft","missing":updated.missing_answer_details}

    def approve(self, ids, status="approved"):
        if status not in ("approved","rejected","draft"):
            raise ValueError("Trạng thái không hợp lệ.")
        if not ids:
            raise ValueError("Chưa chọn câu hỏi.")
        with self._lock, self.db(immediate=True) as c:
            for qid in ids:
                row = c.execute("UPDATE questions SET status=? WHERE id=? AND status!='rendered'", (status,qid))
                if row.rowcount != 1:
                    raise ValueError("Không tìm thấy câu: " + str(qid))

    def list_batches(self,limit=80):
        with self.db() as c:
            return [dict(r) for r in c.execute("""
                SELECT COALESCE(NULLIF(batch_id,''),'legacy') batch_id,
                       MAX(topic) topic,MAX(created_at) created_at,
                       COUNT(*) total,
                       SUM(CASE WHEN status='draft' THEN 1 ELSE 0 END) drafts,
                       SUM(CASE WHEN status IN ('approved','auto_approved') THEN 1 ELSE 0 END) approved,
                       SUM(CASE WHEN status='auto_approved' THEN 1 ELSE 0 END) auto_approved,
                       SUM(CASE WHEN status='rendered' THEN 1 ELSE 0 END) rendered
                FROM questions GROUP BY COALESCE(NULLIF(batch_id,''),'legacy')
                ORDER BY created_at DESC LIMIT ?""",(limit,))]

    def mark_rendered(self,ids,path):
        if not ids:raise ValueError("Empty selection.")
        target=str(Path(path).resolve())
        with self._lock,self.db(immediate=True) as c:
            placeholders=",".join("?" for _ in ids)
            rows=c.execute("SELECT id,topic,status FROM questions WHERE id IN ("+placeholders+")",ids).fetchall()
            if len(rows)!=len(set(ids)) or any(row["status"] not in ("approved","auto_approved") for row in rows):
                raise ValueError("All questions must be approved and not yet rendered.")
            vid=uuid.uuid4().hex
            c.execute("""INSERT INTO video_registry(id,path,question_ids,topic,status,created_at)
                         VALUES(?,?,?,?,'rendered',?)""",
                (vid,target,json.dumps(ids),rows[0]["topic"],utcnow()))
            c.execute("UPDATE questions SET status='rendered',last_video=? WHERE id IN ("+placeholders+")", [target,*ids])
            return vid

    def list_videos(self,limit=80):
        with self.db() as c:
            return [dict(r) for r in c.execute("SELECT * FROM video_registry ORDER BY created_at DESC LIMIT ?",(limit,))]

    def get_video(self,path):
        with self.db() as c:
            row=c.execute("SELECT * FROM video_registry WHERE path=?",(str(Path(path).resolve()),)).fetchone()
            if not row:raise ValueError("Video is not in the production registry.")
            return dict(row)

    def set_video_seo(self,path,title,caption):
        if len(title)>180 or len(caption)>2000:raise ValueError("SEO too long")
        with self._lock,self.db(immediate=True) as c:
            row=c.execute("UPDATE video_registry SET seo_title=?,seo_caption=? WHERE path=?",
                           (title,caption,str(Path(path).resolve())))
            if row.rowcount!=1:raise ValueError("Video not registered.")
        return {"title":title,"caption":caption}

    def has_pending_page_job(self,path):
        with self.db() as c:
            row=c.execute("""SELECT 1 FROM jobs WHERE video_path=?
                AND status IN ('scheduled','publishing','submitted','needs_review') LIMIT 1""",
                (str(Path(path).resolve()),)).fetchone()
            return bool(row)

    def mark_scheduled(self,path):
        with self._lock,self.db(immediate=True) as c:
            row=c.execute("UPDATE video_registry SET status='scheduled' WHERE path=? AND status='rendered'",
                          (str(Path(path).resolve()),))
            if row.rowcount!=1:
                raise ValueError("Video already scheduled or not part of Studio registry.")

    def recent_prompts(self, limit=40):
        with self.db() as c:
            return [r["question"] + " => " + r["correct"] for r in c.execute(
                "SELECT question,correct FROM questions ORDER BY created_at DESC LIMIT ?",(limit,))]

    def topic_counts(self):
        with self.db() as c:
            return {r["topic"]:r["n"] for r in c.execute(
                "SELECT topic, COUNT(*) n FROM questions GROUP BY topic")}

    def count_questions(self):
        with self.db() as c:
            return c.execute("SELECT COUNT(*) FROM questions").fetchone()[0]

    def add_job(self, video_path, caption, scheduled_at, title=""):
        run_at = datetime.fromisoformat(scheduled_at.replace("Z","+00:00"))
        if run_at.tzinfo is None:
            raise ValueError("Cần thời gian ISO 8601 có múi giờ.")
        if run_at.astimezone(timezone.utc) <= datetime.now(timezone.utc):
            raise ValueError("Lịch đăng phải ở tương lai.")
        path = Path(video_path).resolve()
        if not path.is_file() or path.suffix.lower() != ".mp4":
            raise ValueError("Không tìm thấy video MP4.")
        jid=uuid.uuid4().hex
        with self._lock, self.db(immediate=True) as c:
            existing=c.execute("SELECT 1 FROM jobs WHERE video_path=? AND status IN ('scheduled','publishing','submitted','published','needs_review')",
                               (str(path),)).fetchone()
            if existing:
                raise ValueError("Video đã được lên lịch hoặc đã gửi Meta; kiểm tra lịch sử trước khi đặt lại.")
            c.execute("""INSERT INTO jobs(id,video_path,caption,scheduled_at,status,created_at,updated_at,title)
                         VALUES(?,?,?,?,'scheduled',?,?,?)""",
                      (jid,str(path),caption[:2000],run_at.astimezone(timezone.utc).isoformat(),
                       utcnow(),utcnow(),str(title)[:160]))
        return jid

    def list_jobs(self, limit=100):
        with self.db() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",(limit,))]

    def claim_due(self):
        now=utcnow()
        with self._lock, self.db(immediate=True) as c:
            rows=c.execute("""SELECT * FROM jobs WHERE status='scheduled'
                              AND scheduled_at<=? ORDER BY scheduled_at LIMIT 3""",(now,)).fetchall()
            claimed=[]
            for r in rows:
                c.execute("UPDATE jobs SET status='publishing',updated_at=? WHERE id=? AND status='scheduled'",
                          (now,r["id"]))
                claimed.append(dict(r))
            return claimed

    def finish_job(self, jid, status, remote_id=None, error=None):
        if status not in ("published","submitted","needs_review","cancelled"):
            raise ValueError("Trạng thái job không hợp lệ.")
        with self._lock,self.db(immediate=True) as c:
            c.execute("""UPDATE jobs SET status=?,remote_id=?,error=?,updated_at=?
                         WHERE id=?""",(status,remote_id,str(error or "")[:1000],utcnow(),jid))

    def cancel_job(self, jid):
        with self._lock,self.db(immediate=True) as c:
            row=c.execute("UPDATE jobs SET status='cancelled',updated_at=? WHERE id=? AND status='scheduled'",
                          (utcnow(),jid))
            if not row.rowcount:
                raise ValueError("Chỉ hủy được lịch chưa bắt đầu.")

    def daily_record(self,day, topic, result):
        with self._lock,self.db(immediate=True) as c:
            c.execute("INSERT OR IGNORE INTO daily_runs(day,topic,result,created_at) VALUES(?,?,?,?)",
                      (day,topic,json.dumps(result,ensure_ascii=False),utcnow()))
            return c.total_changes > 0

    def daily_exists(self,day):
        with self.db() as c:
            return c.execute("SELECT 1 FROM daily_runs WHERE day=?",(day,)).fetchone() is not None
