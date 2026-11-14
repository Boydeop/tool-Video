"""Fault-injection load tests. AI/QA/encoding are simulated here;
real FFmpeg, HTTP and browser checks remain separate from 100-slot orchestration.
"""
from dataclasses import replace
from pathlib import Path
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
import uuid
from unittest.mock import Mock,patch
from studio.autopilot import Plan,choose_distinct_topics
from studio.batch_runner import BatchRunner
from studio.database import Store,DuplicateQuestion
from studio.quiz import Quiz
from studio.service import Service,_PRODUCTION
from studio.events import choose_production_topics

class Clock:
    def __init__(self):self.now=1000.;self.waits=[]
    def __call__(self):return self.now
    def wait(self,seconds):self.waits.append(seconds);self.now+=max(seconds,.01)

class FixtureService(Service):
    def __init__(self,root,failures=None,cancel_at=None,cancel=None):
        super().__init__(root);self.failures=failures or {};self.calls=[]
        self.cancel_at=cancel_at;self.cancel=cancel
    def verify_generated(self,qid,mode='strict'):
        self.store.set_question_qa(qid,'verified',{'mode':mode,'tier':'dictionary+blind_ai'})
        return {'id':qid,'status':'verified'}
    def render_approved(self,ids,**kwargs):
        task=_PRODUCTION.get();key=(task['slot'],task['attempts'])
        self.calls.append(key)
        path=Path(self.production.settings()['export_dir'])/task['output_name'];path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(b'fixture encoding output')
        if key in self.failures:raise RuntimeError(self.failures[key])
        self.store.get_questions(ids,status=('approved','auto_approved'))
        self.store.mark_rendered(ids,path)
        if self.cancel_at==len(self.calls) and self.cancel:self.cancel.set()
        return str(path)

class Factory:
    def __init__(self):self.counter=0
    def __call__(self,topic,level,count,*args,**kwargs):
        rows=[]
        for _ in range(count):
            self.counter+=1;word=chr(0xAC00+self.counter)
            rows.append(Quiz(topic=topic,level=level,question='Câu '+uuid.uuid4().hex[:8],clue='Nước',
                correct=word,wrong1='봄',wrong2='여름',wrong3='가을',meaning='Nước',romanization='mul',
                wrong1_meaning='Xuân',wrong1_romanization='bom',wrong2_meaning='Hè',wrong2_romanization='yeoreum',
                wrong3_meaning='Thu',wrong3_romanization='gaeul'))
        return 'fixture',rows

def create(service,number=2,attempts=3):
    plan=Plan.parse({'videos':number,'per_video':3,'max_attempts':attempts})
    topics,_=choose_production_topics(service.store.topic_counts(),number)
    run_id=uuid.uuid4().hex;service.store.create_batch(run_id,plan,topics)
    return run_id

def run(service,run_id,**kwargs):
    clock=kwargs.pop('clock',Clock());cancel=kwargs.pop('cancel',threading.Event());updates=[]
    factory=kwargs.pop('factory',Factory())
    runner=BatchRunner(service,run_id,cancel,updates.append,clock=clock,wait=clock.wait,**kwargs)
    with patch('studio.service.fallback',side_effect=factory):
        result=runner.run()
    return result,updates,clock

class DurableBatchTests(unittest.TestCase):
    def test_missing_krdict_key_stops_batch_before_repairing_100_slots(self):
        with tempfile.TemporaryDirectory() as td:
            service=Service(td);jid=create(service,100)
            with patch('studio.krdict.get_keys',return_value=[]),patch.object(service,'repair_generated') as repair:
                result,_,_=run(service,jid)
            self.assertEqual(result['status'],'blocked')
            self.assertEqual(sum(t['attempts'] for t in service.store.batch_tasks(jid)),1)
            self.assertEqual(service.store.list_questions(),[])
            repair.assert_not_called()

    def test_reviewer_and_dictionary_outages_keep_retry_metadata(self):
        from studio.ai import ProviderError
        from studio.review import blind_review,verify_quiz
        from studio.krdict import KRDictError
        with tempfile.TemporaryDirectory() as td:
            service=Service(td);_,rows=Factory()('Test','TOPIK 1',1);quiz=rows[0]
            with patch('studio.review.get_keys',side_effect=lambda p:['fixture'] if p=='gemini' else []):
                report=blind_review(quiz,call=Mock(side_effect=ProviderError('HTTP 503',retry_after=30)))
            self.assertEqual(report['retry_after'],30);self.assertFalse(report['blocked'])
            dictionary=Mock();dictionary.lookup.side_effect=KRDictError('timeout','fixture timeout')
            reviewer=Mock()
            report=verify_quiz(quiz,service.store,dictionary=dictionary,reviewer=reviewer)
            self.assertEqual(report['retry_after'],30)
            self.assertEqual(dictionary.lookup.call_count,1);reviewer.assert_not_called()

    def test_startup_recovers_interrupted_queue_without_resetting_completed_slots(self):
        from studio.exports import ExportQueue
        with tempfile.TemporaryDirectory() as td:
            service=FixtureService(td);jid=create(service,2)
            task=service.store.claim_batch_task(jid)
            service.store.recover_incomplete_runs()
            queue=ExportQueue(service)
            with patch.object(queue,'_launch_batch') as launch:
                recovered=queue.recover_autopilot()
            self.assertEqual(recovered['job_id'],jid)
            launch.assert_called_once()
            self.assertEqual(service.store.batch_tasks(jid)[0]['attempts'],0)
            self.assertEqual(service.store.batch_tasks(jid)[0]['status'],'queued')

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg/FFprobe required')
    def test_two_slots_use_real_encoder_metadata_and_completion_checkpoint(self):
        with tempfile.TemporaryDirectory() as td:
            service=Service(td)
            service.production.update({'width':540,'fps':24,'think':4,'reveal':1.5,
                                       'transition':.3,'intro':.5,'sfx':False})
            plan=Plan.parse({'videos':2,'per_video':1,'with_voice':False,'seo':False})
            jid=uuid.uuid4().hex
            service.store.create_batch(jid,plan,['Chào hỏi','Gia đình'])
            factory=Factory()
            def processor(task,progress):
                with service.production_scope(task),patch('studio.service.fallback',side_effect=factory):
                    ids=service.generate(task['topic'],task['level'],1)['accepted']
                    service.store.approve(ids)
                    service.render_approved(ids,with_voice=False,progress=progress)
                return {}
            result,_,_=run(service,jid,processor=processor)
            self.assertEqual(result['status'],'completed',result)
            self.assertEqual(service.store.list_questions(),[])
            self.assertEqual(len(service.list_videos()),2)
            for video in result['videos']:
                info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format',
                    '-show_streams','-of','json',video],text=True,encoding='utf-8',timeout=20))
                self.assertGreater(float(info['format']['duration']),5)
                self.assertTrue(any(s['codec_type']=='video' and s['width']==540 for s in info['streams']))
                self.assertEqual(info['format']['tags']['artist'],'Tiếng Hàn So Easy')

    def test_100_slots_retry_from_scratch_without_polluting_bank(self):
        with tempfile.TemporaryDirectory() as td:
            faults={(slot,1):'injected encoder failure' for slot in range(0,100,9)}
            service=FixtureService(td,faults);jid=create(service,100)
            result,updates,_=run(service,jid)
            self.assertEqual(result['status'],'completed')
            self.assertEqual(len(set(result['videos'])),100)
            self.assertEqual(len(service.calls),112)
            self.assertEqual(service.store.list_questions(limit=1000),[])
            with service.store.db() as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM questions WHERE status='rendered'").fetchone()[0],300)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM questions WHERE status!='rendered'").fetchone()[0],0)
                self.assertEqual(c.execute('SELECT COUNT(*) FROM video_registry').fetchone()[0],100)
            self.assertEqual(len(list((Path(td)/'videos').glob('*.mp4'))),100)
            self.assertTrue(all(0<=update['percent']<=100 for update in updates))
            self.assertTrue(all(t['attempts']==(2 if t['slot']%9==0 else 1) for t in service.store.batch_tasks(jid)))

    def test_cancel_then_reopen_resumes_without_reencoding_finished_slots(self):
        with tempfile.TemporaryDirectory() as td:
            cancel=threading.Event();first=FixtureService(td,cancel_at=2,cancel=cancel);jid=create(first,5)
            factory=Factory();clock=Clock();updates=[]
            with patch('studio.service.fallback',side_effect=factory):
                result=BatchRunner(first,jid,cancel,updates.append,clock=clock,wait=clock.wait).run()
                self.assertEqual(result['status'],'cancelled');self.assertEqual(len(result['videos']),2)
                self.assertEqual(first.store.batch_run(jid)['status'],'paused')
                hashes={p:Path(p).read_bytes() for p in result['videos']}
                second=FixtureService(td);second.store.resume_batch(jid)
                result=BatchRunner(second,jid,threading.Event(),updates.append,clock=clock,wait=clock.wait).run()
            self.assertEqual(result['status'],'completed');self.assertEqual(len(result['videos']),5)
            self.assertEqual([slot for slot,_ in second.calls],[2,3,4])
            self.assertTrue(all(Path(p).read_bytes()==data for p,data in hashes.items()))

    def test_crash_scratch_is_hidden_and_discarded_before_retry(self):
        with tempfile.TemporaryDirectory() as td:
            first=FixtureService(td);jid=create(first,1)
            task=first.store.claim_batch_task(jid,1000)
            with first.production_scope(task),patch('studio.service.fallback',side_effect=Factory()):
                ids=first.generate(task['topic'],task['level'],3)['accepted']
            self.assertEqual(first.store.list_questions(),[])
            second=FixtureService(td);second.store.recover_incomplete_runs();second.store.resume_batch(jid,reset_failed=False)
            with second.store.db() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM questions WHERE production_task=?",(task['id'],)).fetchone()[0],3)
            result,_,_=run(second,jid)
            self.assertEqual(result['status'],'completed')
            with second.store.db() as c:
                self.assertEqual(c.execute('SELECT COUNT(*) FROM questions WHERE id IN (?,?,?)',ids).fetchone()[0],0)

    def test_last_attempt_failure_keeps_checkpoint_and_can_be_retried_later(self):
        with tempfile.TemporaryDirectory() as td:
            faults={(0,1):'disk issue',(0,2):'disk issue'}
            service=FixtureService(td,faults);jid=create(service,2,2)
            result,_,_=run(service,jid)
            self.assertEqual(result['status'],'needs_retry');self.assertEqual(len(result['videos']),1)
            self.assertEqual(service.store.list_questions(),[])
            self.assertEqual(len(list((Path(td)/'videos').glob('*.mp4'))),1)
            service.store.resume_batch(jid);service.failures={};service.calls=[]
            result,_,_=run(service,jid)
            self.assertEqual(result['status'],'completed');self.assertEqual(service.calls,[(0,1)])

    def test_shared_503_backoff_does_not_hammer_other_slots(self):
        with tempfile.TemporaryDirectory() as td:
            service=FixtureService(td);jid=create(service,3);clock=Clock();calls=[]
            def processor(task,progress):
                calls.append((task['slot'],clock()))
                if len(calls)==1:return {'topics':[{'reason':'HTTP 503','retry_after':30}]}
                with service.production_scope(task),patch('studio.service.fallback',side_effect=Factory()):
                    ids=service.generate(task['topic'],task['level'],3)['accepted']
                    service.store.auto_approve(ids);service.render_approved(ids)
                return {}
            result,_,_=run(service,jid,clock=clock,processor=processor)
            self.assertEqual(result['status'],'completed');self.assertGreaterEqual(calls[1][1]-calls[0][1],30)
            self.assertTrue(clock.waits);self.assertTrue(all(wait<=1 for wait in clock.waits))

    def test_late_error_after_atomic_completion_never_duplicates_video(self):
        with tempfile.TemporaryDirectory() as td:
            service=FixtureService(td);jid=create(service,1)
            def processor(task,progress):
                with service.production_scope(task),patch('studio.service.fallback',side_effect=Factory()):
                    ids=service.generate(task['topic'],task['level'],3)['accepted']
                    service.store.auto_approve(ids);service.render_approved(ids)
                raise RuntimeError('callback crashed after commit')
            result,_,_=run(service,jid,processor=processor)
            self.assertEqual(result['status'],'completed');self.assertEqual(len(service.calls),1)

    def test_missing_credentials_blocks_once_without_100_api_attempts(self):
        with tempfile.TemporaryDirectory() as td:
            service=FixtureService(td);jid=create(service,100);process=Mock(return_value={'topics':[{'blocked':True,'reason':'missing key'}]})
            result,_,_=run(service,jid,processor=process)
            self.assertEqual(result['status'],'blocked');process.assert_called_once()
            self.assertEqual(service.store.batch_run(jid)['status'],'blocked')

    def test_plan_accepts_100_and_rejects_101_and_unbounded_retry(self):
        self.assertEqual(Plan.parse({'videos':100}).videos,100)
        for data in [{'videos':0},{'videos':101},{'max_attempts':6}]:
            with self.assertRaises(ValueError):Plan.parse(data)
        self.assertEqual(len(choose_distinct_topics({},100)),100)

class DraftCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.service=Service(self.temp.name)
    def quiz(self,word='물'):
        return Quiz(topic='Test',question='Nước',correct=word,wrong1='봄',wrong2='여름',wrong3='가을')
    def test_old_surplus_migration_is_reversible_and_runs_only_once(self):
        store=self.service.store
        used=store.insert_question(self.quiz(),provider='gemini',batch_id='old')
        spare=replace(self.quiz('차'),question='Trà')
        unused=store.insert_question(spare,provider='gemini',batch_id='old')
        store.approve([used]);store.mark_rendered([used],Path(self.temp.name)/'old.mp4')
        with store.db() as c:c.execute("DELETE FROM app_migrations WHERE name='archive_old_surplus'")
        reopened=Store(store.path)
        self.assertEqual([q['id'] for q in reopened.list_questions(trash=True)],[unused])
        reopened.restore_questions([unused])
        reopened=Store(store.path)
        self.assertEqual(reopened.list_questions(trash=True),[])
        self.assertEqual(len(reopened.list_questions()),2)

    def test_mixed_cleanup_selection_rolls_back_instead_of_losing_drafts(self):
        store=self.service.store
        draft=store.insert_question(self.quiz(),provider='gemini')
        used=store.insert_question(replace(self.quiz('차'),question='Trà'))
        store.approve([used]);store.mark_rendered([used],Path(self.temp.name)/'used.mp4')
        with self.assertRaises(ValueError):store.trash_questions([draft,used])
        self.assertEqual(store.list_questions(trash=True),[])
        self.assertEqual(len(store.list_questions()),2)
    def test_trash_releases_dedupe_but_restore_never_overwrites_new_work(self):
        first=self.service.store.insert_question(self.quiz(),provider='gemini')
        self.service.store.trash_questions([first])
        self.assertEqual(self.service.store.list_questions(),[])
        self.assertEqual(len(self.service.store.list_questions(trash=True)),1)
        second=self.service.store.insert_question(self.quiz(),provider='gemini')
        with self.assertRaises(DuplicateQuestion):self.service.store.restore_questions([first])
        self.assertEqual(self.service.store.get_questions([second])[0].correct,'물')
    def test_cleanup_preserves_manual_and_rendered_questions(self):
        store=self.service.store
        manual=store.insert_question(self.quiz(),provider='manual')
        unused=store.insert_question(replace(self.quiz('차'),question='Trà'),provider='gemini')
        used=store.insert_question(replace(self.quiz('학교'),question='Trường'),provider='gemini')
        store.approve([used]);store.mark_rendered([used],Path(self.temp.name)/'real-video.mp4')
        self.assertEqual(store.trash_questions(cleanup=True)['trashed'],1)
        self.assertEqual({r['id'] for r in store.list_questions()},{manual,used})
        with self.assertRaises(ValueError):store.trash_questions([used])
        store.empty_question_trash()
        self.assertEqual(store.list_questions(trash=True),[])
        self.assertEqual(store.get_questions([used])[0].correct,'학교')
    def test_restore_and_purge_preserve_revision_evidence_until_explicit_delete(self):
        store=self.service.store;qid=store.insert_question(self.quiz(),provider='gemini')
        with store.db(immediate=True) as c:c.execute('INSERT INTO question_revisions(question_id,before_json,after_json,report_json,created_at) VALUES(?,?,?,?,?)',(qid,'{}','{}','{}','now'))
        store.trash_questions([qid]);self.assertEqual(len(store.repair_history(qid)),1)
        store.restore_questions([qid]);self.assertEqual(len(store.list_questions()),1)
        store.trash_questions([qid]);store.empty_question_trash();self.assertEqual(store.repair_history(qid),[])

if __name__=='__main__':unittest.main()
