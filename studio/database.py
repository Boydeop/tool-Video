"""Durable SQLite storage: exact uniqueness, approximate checks, jobs and audit."""
from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
import uuid
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

    def db(self):
        # sqlite3.Connection.__exit__ commits but does NOT close the handle.
        # Windows cannot remove a temp SQLite file while a handle stays open.
        from contextlib import contextmanager

        @contextmanager
        def transaction():
            con = sqlite3.connect(str(self.path), timeout=30)
            try:
                con.row_factory = sqlite3.Row
                con.execute("PRAGMA busy_timeout=30000")
                con.execute("PRAGMA journal_mode=WAL")
                with con:
                    yield con
            finally:
                con.close()

        return transaction()

    def init(self):
        with self.db() as c:
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

    def log(self, kind, detail):
        with self._lock, self.db() as c:
            c.execute("INSERT INTO audit(created_at,kind,detail) VALUES(?,?,?)",
                      (utcnow(), kind, str(detail)[:1500]))

    def insert_question(self, quiz: Quiz, provider="manual", batch_id="") -> str:
        quiz.validate()
        qhash = fingerprint(quiz.question)
        ahash = fingerprint(quiz.correct)
        with self._lock, self.db() as c:
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
                rows = c.execute("SELECT * FROM questions WHERE status=? ORDER BY created_at DESC LIMIT ?",
                                 (status,limit)).fetchall()
            else:
                rows = c.execute("SELECT * FROM questions ORDER BY created_at DESC LIMIT ?",
                                 (limit,)).fetchall()
            result=[]
            for r in rows:
                payload=json.loads(r["payload"])
                quiz=Quiz.parse(payload)
                result.append(dict(id=r["id"],status=r["status"],provider=r["provider"],
                    created_at=r["created_at"],batch_id=r["batch_id"],
                    last_video=r["last_video"],**payload,
                    short_clue=quiz.short_clue,meaning=quiz.vietnamese_meaning))
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
            if status and any(data[i]["status"] != status for i in ids):
                raise ValueError("Câu hỏi phải được duyệt trước khi dựng video.")
            return [Quiz.parse(json.loads(data[i]["payload"])) for i in ids]

    def approve(self, ids, status="approved"):
        if status not in ("approved","rejected","draft"):
            raise ValueError("Trạng thái không hợp lệ.")
        if not ids:
            raise ValueError("Chưa chọn câu hỏi.")
        with self._lock, self.db() as c:
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
                       SUM(CASE WHEN status='approved' THEN 1 ELSE 0 END) approved,
                       SUM(CASE WHEN status='rendered' THEN 1 ELSE 0 END) rendered
                FROM questions GROUP BY COALESCE(NULLIF(batch_id,''),'legacy')
                ORDER BY created_at DESC LIMIT ?""",(limit,))]

    def mark_rendered(self,ids,path):
        if not ids:raise ValueError("Empty selection.")
        target=str(Path(path).resolve())
        with self._lock,self.db() as c:
            placeholders=",".join("?" for _ in ids)
            rows=c.execute("SELECT id,topic,status FROM questions WHERE id IN ("+placeholders+")",ids).fetchall()
            if len(rows)!=len(set(ids)) or any(row["status"]!="approved" for row in rows):
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
        with self._lock,self.db() as c:
            row=c.execute("UPDATE video_registry SET seo_title=?,seo_caption=? WHERE path=?",
                           (title,caption,str(Path(path).resolve())))
            if row.rowcount!=1:raise ValueError("Video not registered.")
        return {"title":title,"caption":caption}

    def mark_scheduled(self,path):
        with self._lock,self.db() as c:
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
        with self._lock, self.db() as c:
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
        with self._lock, self.db() as c:
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
        with self._lock,self.db() as c:
            c.execute("""UPDATE jobs SET status=?,remote_id=?,error=?,updated_at=?
                         WHERE id=?""",(status,remote_id,str(error or "")[:1000],utcnow(),jid))

    def cancel_job(self, jid):
        with self._lock,self.db() as c:
            row=c.execute("UPDATE jobs SET status='cancelled',updated_at=? WHERE id=? AND status='scheduled'",
                          (utcnow(),jid))
            if not row.rowcount:
                raise ValueError("Chỉ hủy được lịch chưa bắt đầu.")

    def daily_record(self,day, topic, result):
        with self._lock,self.db() as c:
            c.execute("INSERT OR IGNORE INTO daily_runs(day,topic,result,created_at) VALUES(?,?,?,?)",
                      (day,topic,json.dumps(result,ensure_ascii=False),utcnow()))
            return c.total_changes > 0

    def daily_exists(self,day):
        with self.db() as c:
            return c.execute("SELECT 1 FROM daily_runs WHERE day=?",(day,)).fetchone() is not None
