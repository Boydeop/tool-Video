"""History retention, migration, portable backups and concurrent admission."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from studio.database import DuplicateQuestion, Store
from studio.memory import canonical
from studio.quiz import Quiz
from studio.service import Service


def quiz():
    return Quiz(topic='Đồ uống', question='Nước', correct='물',
                wrong1='맥주', wrong2='차', wrong3='우유')


class PermanentMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.store=Store(self.root/'db.sqlite3')

    def test_purge_and_restart_never_free_content(self):
        qid=self.store.insert_question(quiz())
        self.store.trash_questions([qid]);self.store.empty_question_trash()
        reopened=Store(self.store.path)
        self.assertEqual(reopened.list_questions(),[])
        self.assertEqual(reopened.memory_stats()['questions_seen'],1)
        with self.assertRaises(DuplicateQuestion):reopened.insert_question(quiz())
        self.assertIn('물',' '.join(reopened.recent_prompts()))

    def test_scratch_retry_and_cache_cleanup_keep_memory(self):
        service=Service(self.root/'app')
        qid=service.store.insert_question(quiz(),production_task='failed-task')
        self.assertEqual(service.store.discard_task_questions('failed-task'),1)
        service.clear_cache()
        with self.assertRaises(DuplicateQuestion):service.store.insert_question(quiz())
        self.assertEqual(service.store.repair_history(qid),[])

    def test_clue_alias_and_answer_spacing_and_hidden_chars_are_blocked(self):
        original=replace(quiz(),question='Từ tiếng Hàn nào chỉ nước?',clue='Nước')
        qid=self.store.insert_question(original)
        self.store.trash_questions([qid]);self.store.empty_question_trash()
        with self.assertRaises(DuplicateQuestion):
            self.store.insert_question(replace(quiz(),question='NƯỚC！',correct='불'))
        with self.assertRaises(DuplicateQuestion):
            self.store.insert_question(replace(quiz(),question='Lửa',correct='물\u200b'))
        self.assertEqual(canonical('한 국', 'answer'),canonical('한국','answer'))
        self.assertNotEqual(canonical('cá'),canonical('cà'))

    def test_near_duplicate_survives_purge(self):
        qid=self.store.insert_question(replace(quiz(),question='Làm việc tại văn phòng hôm nay'))
        self.store.trash_questions([qid]);self.store.empty_question_trash()
        with self.assertRaises(DuplicateQuestion):
            self.store.insert_question(replace(quiz(),question='Làm việc tại văn phòng hôm nay nhé',correct='불'))

    def test_repairs_keep_old_and_new_answers(self):
        before=quiz();qid=self.store.insert_question(before)
        after=replace(before,correct='생수')
        self.store.apply_repair(qid,before,after,{'outcome':'verified','mode':'strict'})
        for word in (before.correct,after.correct):
            with self.subTest(word=word),self.assertRaises(DuplicateQuestion):
                self.store.insert_question(replace(quiz(),question='Một đề khác',correct=word))
        self.store.trash_questions([qid]);self.store.restore_questions([qid])
        self.assertEqual(self.store.get_questions([qid]),[after])

    def test_failed_repair_does_not_reserve_new_content(self):
        qid=self.store.insert_question(quiz())
        before=quiz();after=replace(before,correct='생수')
        with self.assertRaises(ValueError):self.store.apply_repair(qid,before,after,{'outcome':'needs_review'})
        self.assertEqual(self.store.memory_stats()['answers'],1)

    def test_concurrent_connections_accept_exactly_one(self):
        stores=[Store(self.store.path),Store(self.store.path)]
        barrier=threading.Barrier(2)
        def insert(store):
            barrier.wait()
            try:return store.insert_question(quiz())
            except DuplicateQuestion:return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(insert,stores))
        self.assertEqual(sum(value is not None for value in results),1)
        self.assertEqual(self.store.memory_stats()['answers'],1)

    def test_backup_roundtrip_merge_and_corrupt_import_roll_back(self):
        self.store.insert_question(quiz())
        backup=self.root/'memory.jsonl'
        result=self.store.export_memory(backup)
        self.assertEqual(result['entries'],2)
        other=Store(self.root/'other.sqlite3')
        self.assertEqual(other.import_memory(backup)['added'],2)
        self.assertEqual(other.import_memory(backup)['added'],0)
        with self.assertRaises(DuplicateQuestion):other.insert_question(quiz())
        with self.assertRaises(ValueError):self.store.export_memory(backup)
        bad=self.root/'bad.jsonl'
        bad.write_text(backup.read_text('utf-8')+'invalid JSON\n',encoding='utf-8')
        empty=Store(self.root/'empty.sqlite3')
        with self.assertRaises(ValueError):empty.import_memory(bad)
        self.assertEqual(empty.memory_stats()['questions_seen'],0)

    def test_memory_is_append_only(self):
        self.store.insert_question(quiz())
        for sql in ('DELETE FROM question_memory',"UPDATE question_memory SET value='reset'"):
            with self.subTest(sql=sql),self.assertRaises(sqlite3.IntegrityError),self.store.db() as c:
                c.execute(sql)

    def test_legacy_trash_and_repair_history_backfill_once(self):
        # A minimal legacy database, created without the new memory schema.
        path=self.root/'legacy.sqlite3'
        before=quiz();after=replace(before,correct='생수')
        with closing(sqlite3.connect(path)) as c, c:
            c.executescript('''CREATE TABLE questions(id TEXT PRIMARY KEY,topic TEXT,question TEXT,correct TEXT,
              payload TEXT,question_hash TEXT UNIQUE,answer_hash TEXT UNIQUE,status TEXT,provider TEXT,created_at TEXT,
              trashed INTEGER DEFAULT 0);
              CREATE TABLE question_revisions(id INTEGER PRIMARY KEY,question_id TEXT,before_json TEXT,
              after_json TEXT,report_json TEXT,created_at TEXT);''')
            c.execute('INSERT INTO questions VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                      ('old','Đồ uống','Nước','생수',json.dumps(after.to_dict()),'trash:q','trash:a','draft','manual','old',1))
            c.execute('INSERT INTO question_revisions VALUES(1,?,?,?,?,?)',
                      ('old',json.dumps(before.to_dict()),json.dumps(after.to_dict()),'{}','old'))
        migrated=Store(path)
        self.assertEqual(migrated.memory_stats()['answers'],2)
        migrated.empty_question_trash()
        self.assertEqual(Store(path).memory_stats()['answers'],2)
        for word in ('물','생수'):
            with self.assertRaises(DuplicateQuestion):migrated.insert_question(replace(quiz(),correct=word))

    def test_length_index_preserves_full_history_ratio_decisions(self):
        from difflib import SequenceMatcher
        import math
        for a in range(1,65):
            for b in range(1,65):
                threshold=.89
                score=SequenceMatcher(None,'x'*a,'x'*b,autojunk=False).ratio()
                if score>=threshold:
                    self.assertLessEqual(math.ceil(threshold*b/(2-threshold)),a)
                    self.assertGreaterEqual(math.floor((2-threshold)*b/threshold),a)


class ContentConsistencyTests(unittest.TestCase):
    def test_old_machine_approval_is_rechecked_but_human_approval_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            store=Store(Path(td)/'db.sqlite3')
            qid=store.insert_question(quiz())
            manual=store.insert_question(replace(quiz(),question='Trường học',correct='학교'))
            store.set_question_qa(qid,'verified',{'version':3});store.approve([manual])
            with store.db() as c:c.execute("DELETE FROM app_migrations WHERE name='display_meanings_qa_v4'")
            reopened=Store(store.path)
            states={row['id']:row['status'] for row in reopened.list_questions()}
            self.assertEqual(states[qid],'draft');self.assertEqual(states[manual],'approved')
            self.assertEqual(reopened.get_question_qa(qid)['outcome'],'stale')

    def test_invisible_characters_cannot_create_duplicate_choices(self):
        with self.assertRaises(ValueError):replace(quiz(),wrong1='물\u200b').validate()
        with self.assertRaises(ValueError):replace(quiz(),question='!!!').validate()

    def test_qa_rejects_stale_snapshot_and_trash(self):
        with tempfile.TemporaryDirectory() as td:
            service=Service(td);store=service.store;q=quiz();qid=store.insert_question(q)
            def review(*args,**kwargs):
                store.update_answer_details(qid,{'meaning':'Nước uống'})
                return {'outcome':'verified'}
            with patch('studio.review.verify_quiz',side_effect=review),self.assertRaises(ValueError):
                service.verify_generated(qid)
            self.assertEqual(store.get_question_qa(qid)['outcome'],'stale')
            store.trash_questions([qid])
            with self.assertRaises(ValueError):store.set_question_qa(qid,'verified',{})

    def test_enrichment_does_not_overwrite_concurrent_edit(self):
        with tempfile.TemporaryDirectory() as td:
            service=Service(td);qid=service.store.insert_question(quiz())
            def suggest(q):
                service.store.update_answer_details(qid,{'meaning':'Người dùng sửa'})
                return replace(q,meaning='AI trả về'),'mock'
            with patch('studio.answer_enrichment.suggest',side_effect=suggest),self.assertRaises(ValueError):
                service.enrich_answer_details([qid])
            self.assertEqual(service.store.get_questions([qid])[0].meaning,'Người dùng sửa')

    def test_render_commit_rejects_changed_reapproved_or_trashed_questions(self):
        with tempfile.TemporaryDirectory() as td:
            store=Store(Path(td)/'db.sqlite3');q=quiz();qid=store.insert_question(q);store.approve([qid])
            with self.assertRaises(ValueError):store.get_questions([qid,qid])
            store.update_answer_details(qid,{'meaning':'Đã sửa'});store.approve([qid])
            with self.assertRaises(ValueError):store.mark_rendered([qid],Path(td)/'wrong.mp4',expected=[q])
            store.trash_questions([qid])
            with self.assertRaises(ValueError):store.mark_rendered([qid],Path(td)/'wrong.mp4')
            self.assertEqual(store.list_videos(),[])

    def test_failed_metadata_and_late_encoder_callback_leave_no_orphan(self):
        for failure in ('metadata','encoder_callback'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as td:
                service=Service(td);store=service.store;qid=store.insert_question(quiz());store.approve([qid])
                def render(questions,path,**kwargs):
                    path.parent.mkdir(parents=True,exist_ok=True)
                    path.write_bytes(b'incomplete result')
                    if failure=='encoder_callback':raise RuntimeError('late callback')
                    return str(path)
                with patch('studio.service.render',side_effect=render),patch('studio.metadata.overwrite_metadata',side_effect=RuntimeError('metadata failed')):
                    with self.assertRaises(RuntimeError):service.render_approved([qid])
                self.assertEqual(service.list_videos(),[])
                self.assertEqual(store.list_videos(),[])
                self.assertEqual(store.list_questions()[0]['status'],'approved')


if __name__=='__main__':unittest.main()
