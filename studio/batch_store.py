"""Durable per-video checkpoints and reversible draft cleanup.

Only the queue's private, unrendered scratch questions are physically discarded
between attempts. User-visible drafts go through reversible trash first.
"""
from datetime import datetime,timezone
import json
import time
import uuid

def stamp():return datetime.now(timezone.utc).isoformat()

class BatchStore:
    @staticmethod
    def init_batch_schema(c):
        columns={row['name'] for row in c.execute('PRAGMA table_info(questions)')}
        for name,definition in [('production_task',"TEXT NOT NULL DEFAULT ''"),('trashed','INTEGER NOT NULL DEFAULT 0')]:
            if name not in columns:c.execute(f'ALTER TABLE questions ADD COLUMN {name} {definition}')
        c.executescript('''
          CREATE TABLE IF NOT EXISTS production_tasks(
            id TEXT PRIMARY KEY,run_id TEXT NOT NULL,slot INTEGER NOT NULL,
            topic TEXT NOT NULL,level TEXT NOT NULL,format TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL,next_attempt REAL NOT NULL DEFAULT 0,
            output_name TEXT NOT NULL,video_path TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL,
            UNIQUE(run_id,slot));
          CREATE INDEX IF NOT EXISTS tasks_due ON production_tasks(run_id,status,next_attempt,slot);
          CREATE INDEX IF NOT EXISTS question_task ON questions(production_task,trashed);
          CREATE TABLE IF NOT EXISTS app_migrations(name TEXT PRIMARY KEY);
        ''')
        # Old surplus from an already-rendered batch is recognizable. Archive it
        # once, reversibly; do not classify all manually-generated drafts as junk.
        if not c.execute("SELECT 1 FROM app_migrations WHERE name='archive_old_surplus'").fetchone():
            rows=c.execute("""SELECT id,question_hash,answer_hash FROM questions
              WHERE status!='rendered' AND last_video='' AND batch_id!='' AND provider!='manual'
              AND batch_id IN (SELECT batch_id FROM questions WHERE status='rendered')""").fetchall()
            for row in rows:
                c.execute('UPDATE questions SET trashed=1,question_hash=?,answer_hash=? WHERE id=?',
                          ('trash:'+row['id']+':q','trash:'+row['id']+':a',row['id']))
            c.execute("INSERT OR IGNORE INTO app_migrations VALUES('archive_old_surplus')")

    def create_batch(self,run_id,plan,topics):
        if len(topics)!=plan.videos:raise ValueError('Số tác vụ không khớp số video')
        with self._lock,self.db(immediate=True) as c:
            c.execute("INSERT INTO production_runs(id,status,plan,results,created_at,updated_at) VALUES(?,'running',?,'[]',?,?)",
                      (run_id,json.dumps({'mode':'autopilot',**plan.public_dict()}),stamp(),stamp()))
            for slot,topic in enumerate(topics):
                task_id=uuid.uuid4().hex
                c.execute('''INSERT INTO production_tasks(id,run_id,slot,topic,level,format,max_attempts,output_name,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?)''',(task_id,run_id,slot,topic,plan.levels[slot%len(plan.levels)],
                    plan.formats[slot%len(plan.formats)],plan.max_attempts,
                    f'THSE_{run_id[:8]}_{slot+1:03d}.mp4',stamp()))

    def batch_run(self,run_id):
        with self.db() as c:
            row=c.execute('SELECT * FROM production_runs WHERE id=?',(run_id,)).fetchone()
            if row is None:raise ValueError('Không tìm thấy lượt sản xuất')
            return {**dict(row),'plan':json.loads(row['plan'])}

    def batch_tasks(self,run_id):
        with self.db() as c:
            return [dict(row) for row in c.execute('SELECT * FROM production_tasks WHERE run_id=? ORDER BY slot',(run_id,))]

    def claim_batch_task(self,run_id,now=None):
        now=time.time() if now is None else now
        with self._lock,self.db(immediate=True) as c:
            row=c.execute("""SELECT * FROM production_tasks WHERE run_id=?
              AND status IN ('queued','retry_wait') AND next_attempt<=? AND attempts<max_attempts
              ORDER BY slot LIMIT 1""",(run_id,now)).fetchone()
            if row is None:return None
            c.execute("UPDATE production_tasks SET status='running',attempts=attempts+1,updated_at=? WHERE id=?",(stamp(),row['id']))
            return {**dict(row),'status':'running','attempts':row['attempts']+1}

    def fail_batch_task(self,task_id,error,delay=0,blocked=False,now=None,shared=False):
        now=time.time() if now is None else now
        with self._lock,self.db(immediate=True) as c:
            row=c.execute('SELECT * FROM production_tasks WHERE id=?',(task_id,)).fetchone()
            if row is None or row['status']=='completed':return
            status='blocked' if blocked else 'failed' if row['attempts']>=row['max_attempts'] else 'retry_wait'
            c.execute('UPDATE production_tasks SET status=?,error=?,next_attempt=?,updated_at=? WHERE id=?',
                      (status,str(error)[:900],now+delay,stamp(),task_id))
            # Provider outage/cooldown is shared by the run, not 100 rapid
            # calls from 100 queued slots. Wait once before dispatching any slot.
            if delay and shared:
                c.execute("UPDATE production_tasks SET next_attempt=MAX(next_attempt,?) WHERE run_id=? AND status IN ('queued','retry_wait')",
                          (now+delay,row['run_id']))

    def pause_batch_task(self,task_id):
        with self._lock,self.db(immediate=True) as c:
            c.execute("UPDATE production_tasks SET status='queued',attempts=MAX(0,attempts-1),next_attempt=0,updated_at=? WHERE id=? AND status='running'",(stamp(),task_id))

    def resume_batch(self,run_id,reset_failed=True):
        with self._lock,self.db(immediate=True) as c:
            if not c.execute('SELECT 1 FROM production_tasks WHERE run_id=?',(run_id,)).fetchone():
                raise ValueError('Lượt cũ không có hàng đợi; hãy tạo lượt mới')
            c.execute("UPDATE production_tasks SET status='queued',attempts=MAX(0,attempts-1),next_attempt=0,updated_at=? WHERE run_id=? AND status='running'",(stamp(),run_id))
            if reset_failed:
                c.execute("UPDATE production_tasks SET status='queued',attempts=0,next_attempt=0,error='',updated_at=? WHERE run_id=? AND status IN ('failed','blocked')",(stamp(),run_id))
            c.execute("UPDATE production_runs SET status='running',updated_at=? WHERE id=?",(stamp(),run_id))

    def discard_task_questions(self,task_id):
        with self._lock,self.db(immediate=True) as c:
            rows=c.execute("SELECT id FROM questions WHERE production_task=? AND status!='rendered' AND last_video=''",(task_id,)).fetchall()
            self._delete_question_rows(c,[row['id'] for row in rows])
            return len(rows)

    @staticmethod
    def _delete_question_rows(c,ids):
        for qid in ids:
            c.execute('DELETE FROM question_qa WHERE question_id=?',(qid,))
            c.execute('DELETE FROM question_revisions WHERE question_id=?',(qid,))
            c.execute('DELETE FROM questions WHERE id=?',(qid,))

    def trash_questions(self,ids=None,cleanup=False):
        with self._lock,self.db(immediate=True) as c:
            if cleanup:
                rows=c.execute("SELECT * FROM questions WHERE trashed=0 AND production_task='' AND status!='rendered' AND last_video='' AND provider!='manual'").fetchall()
            else:
                if not isinstance(ids,list) or not ids or len(ids)>500 or any(not isinstance(qid,str) for qid in ids):
                    raise ValueError('Chọn 1–500 bản nháp để xóa')
                marks=','.join('?' for _ in ids)
                rows=c.execute('SELECT * FROM questions WHERE id IN ('+marks+')',ids).fetchall()
                if len(rows)!=len(set(ids)):raise ValueError('Một số câu không còn tồn tại')
            if any(r['status']=='rendered' or r['last_video'] or r['production_task'] for r in rows):
                raise ValueError('Không xóa câu đã dùng trong video hoặc câu đang sản xuất')
            for row in rows:
                c.execute('UPDATE questions SET trashed=1,question_hash=?,answer_hash=? WHERE id=?',
                          ('trash:'+row['id']+':q','trash:'+row['id']+':a',row['id']))
            return {'trashed':len(rows)}

    def restore_questions(self,ids):
        from .quiz import fingerprint
        from .database import DuplicateQuestion
        if not isinstance(ids,list) or not ids or len(ids)>500 or any(not isinstance(qid,str) for qid in ids):
            raise ValueError('Chọn bản nháp trong thùng rác để khôi phục')
        with self._lock,self.db(immediate=True) as c:
            for qid in ids:
                row=c.execute('SELECT * FROM questions WHERE id=? AND trashed=1',(qid,)).fetchone()
                if row is None:raise ValueError('Không tìm thấy câu trong thùng rác')
                qhash,ahash=fingerprint(row['question']),fingerprint(row['correct'])
                if c.execute('SELECT 1 FROM questions WHERE question_hash=? OR answer_hash=?',(qhash,ahash)).fetchone():
                    raise DuplicateQuestion('Đã có câu tương tự sau khi xóa; không ghi đè khi khôi phục')
                c.execute('UPDATE questions SET trashed=0,question_hash=?,answer_hash=? WHERE id=?',(qhash,ahash,qid))
        return {'restored':len(ids)}

    def empty_question_trash(self):
        with self._lock,self.db(immediate=True) as c:
            ids=[r['id'] for r in c.execute("SELECT id FROM questions WHERE trashed=1 AND status!='rendered' AND last_video='' AND production_task=''")]
            self._delete_question_rows(c,ids)
        return {'deleted':len(ids)}
