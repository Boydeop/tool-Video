"""Append-only local deduplication ledger, independent of editable drafts.

Lives in the same SQLite database so reserving content and saving a question
commit together. Deleting drafts, retries and repairs never erase reservations.
This detects normalized text/answers and lexical similarity, not all paraphrases.
"""
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
import math
from pathlib import Path
import re
import unicodedata
import uuid


def canonical(value, kind="prompt"):
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    text = "".join(" " if unicodedata.category(c).startswith("P") else c for c in text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace(" ", "") if kind == "answer" else text


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class QuestionMemory:
    def init_memory(self, c):
        c.executescript("""
          CREATE TABLE IF NOT EXISTS question_memory(
            id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('prompt','answer')),
            digest TEXT NOT NULL, value TEXT NOT NULL, length INTEGER NOT NULL,
            source_id TEXT NOT NULL, topic TEXT NOT NULL, first_seen TEXT NOT NULL,
            UNIQUE(kind,digest));
          CREATE INDEX IF NOT EXISTS memory_length ON question_memory(kind,length);
          CREATE INDEX IF NOT EXISTS memory_source ON question_memory(source_id);
          CREATE TRIGGER IF NOT EXISTS memory_no_delete BEFORE DELETE ON question_memory
            BEGIN SELECT RAISE(ABORT,'Question memory is append-only'); END;
          CREATE TRIGGER IF NOT EXISTS memory_no_update BEFORE UPDATE ON question_memory
            BEGIN SELECT RAISE(ABORT,'Question memory is append-only'); END;
        """)
        c.execute("BEGIN IMMEDIATE")
        if c.execute("SELECT 1 FROM app_migrations WHERE name='permanent_memory_v1'").fetchone():
            return
        # Read actual content, including trash hashes and both sides of repairs.
        for row in c.execute("SELECT id,payload,created_at FROM questions"):
            self._remember(c, json.loads(row["payload"]), row["id"], row["created_at"])
        for row in c.execute("SELECT question_id,before_json,after_json,created_at FROM question_revisions"):
            for field in ("before_json", "after_json"):
                self._remember(c, json.loads(row[field]), row["question_id"], row["created_at"])
        c.execute("INSERT INTO app_migrations VALUES('permanent_memory_v1')")

    @staticmethod
    def _memory_values(payload):
        from .quiz import Quiz
        quiz = Quiz.parse(payload)
        return {("prompt", canonical(quiz.question)),
                ("prompt", canonical(quiz.short_clue)),
                ("answer", canonical(quiz.correct, "answer"))}

    def _remember(self, c, payload, source_id, first_seen=None):
        stamp = first_seen or datetime.now(timezone.utc).isoformat()
        for kind, value in sorted(self._memory_values(payload)):
            if value:
                c.execute("""INSERT OR IGNORE INTO question_memory
                  (kind,digest,value,length,source_id,topic,first_seen) VALUES(?,?,?,?,?,?,?)""",
                  (kind, digest(value), value, len(value), source_id, payload["topic"], stamp))

    def _check_memory(self, c, quiz, source_id=""):
        from .database import DuplicateQuestion
        for kind, value in sorted(self._memory_values(quiz.to_dict())):
            if not value:
                continue
            if c.execute("SELECT 1 FROM question_memory WHERE kind=? AND digest=? AND source_id!=?",
                         (kind, digest(value), source_id)).fetchone():
                raise DuplicateQuestion("Bộ nhớ local: câu hỏi hoặc đáp án đã từng xuất hiện, kể cả đã xóa.")
            threshold = .89 if kind == "prompt" else .94
            if kind == "answer" and len(value) <= 8:
                continue
            # Safe length bound: ratio <= 2*min(a,b)/(a+b). Indexed range,
            # then cheap upper bounds before the expensive alignment.
            low = math.ceil(threshold * len(value) / (2-threshold))
            high = math.floor((2-threshold) * len(value) / threshold)
            matcher = SequenceMatcher(None, "", value, autojunk=False)
            for row in c.execute("""SELECT value FROM question_memory
                WHERE kind=? AND length BETWEEN ? AND ? AND source_id!=?""",
                (kind, low, high, source_id)):
                matcher.set_seq1(row["value"])
                if matcher.quick_ratio() >= threshold and matcher.ratio() >= threshold:
                    raise DuplicateQuestion("Bộ nhớ local: nội dung gần trùng với lịch sử đã lưu.")

    def memory_stats(self):
        with self.db() as c:
            rows = dict(c.execute("SELECT kind,COUNT(*) FROM question_memory GROUP BY kind"))
            total = c.execute("SELECT COUNT(DISTINCT source_id) FROM question_memory").fetchone()[0]
        return {"questions_seen": total, "prompts": rows.get("prompt", 0),
                "answers": rows.get("answer", 0), "permanent": True,
                "semantic_guarantee": False}

    def remembered_prompts(self, limit=50):
        """A bounded AI hint; the complete ledger still enforces admission."""
        with self.db() as c:
            result=[]
            seen=set()
            for row in c.execute("SELECT source_id,value FROM question_memory WHERE kind='prompt' ORDER BY id DESC LIMIT ?", (2*limit,)):
                if row['source_id'] in seen:continue
                seen.add(row['source_id'])
                answer=c.execute("SELECT value FROM question_memory WHERE kind='answer' AND source_id=? ORDER BY id DESC LIMIT 1",(row['source_id'],)).fetchone()
                result.append(row['value']+' => '+(answer['value'] if answer else ''))
                if len(result)>=limit:break
        return result

    def export_memory(self, destination):
        """Portable JSONL backup without API keys or personal credentials."""
        target = Path(destination).resolve()
        if target.exists():
            raise ValueError("File sao lưu đã tồn tại; chọn tên mới để không ghi đè.")
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
        count = 0
        try:
            with self.db() as c, temp.open("w", encoding="utf-8", newline="\n") as out:
                out.write(json.dumps({"format": "thse-question-memory", "version": 1}) + "\n")
                for row in c.execute("SELECT kind,value,source_id,topic,first_seen FROM question_memory ORDER BY id"):
                    out.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
                    count += 1
            # Exclusive creation also prevents a concurrent backup overwrite.
            with target.open("xb") as out, temp.open("rb") as src:
                import shutil
                shutil.copyfileobj(src, out)
        finally:
            temp.unlink(missing_ok=True)
        return {"path": str(target), "entries": count}

    def import_memory(self, source):
        """Merge a backup atomically; never clear existing reservations."""
        added = 0
        with Path(source).open(encoding="utf-8") as inp, self._lock, self.db(immediate=True) as c:
            header = json.loads(inp.readline(16385))
            if header != {"format": "thse-question-memory", "version": 1}:
                raise ValueError("Không đúng định dạng bộ nhớ câu hỏi phiên bản 1.")
            while line := inp.readline(16385):
                if len(line) > 16384:
                    raise ValueError("Mục sao lưu vượt giới hạn.")
                row = json.loads(line)
                if not isinstance(row, dict) or any(not isinstance(row.get(k), str) for k in
                        ("kind", "value", "source_id", "topic", "first_seen")):
                    raise ValueError("Mục sao lưu không hợp lệ.")
                kind = row["kind"]
                if kind not in ("prompt", "answer") or not 1 <= len(row["value"]) <= 500:
                    raise ValueError("Nội dung bộ nhớ không hợp lệ.")
                if any(not row[k] or len(row[k]) > 500 for k in ("source_id", "topic", "first_seen")):
                    raise ValueError("Metadata bộ nhớ quá dài.")
                value = canonical(row["value"], kind)
                if not value:
                    raise ValueError("Nội dung bộ nhớ trống.")
                added += c.execute("""INSERT OR IGNORE INTO question_memory
                    (kind,digest,value,length,source_id,topic,first_seen) VALUES(?,?,?,?,?,?,?)""",
                    (kind, digest(value), value, len(value), row["source_id"], row["topic"], row["first_seen"])).rowcount
        return {"added": added, **self.memory_stats()}
