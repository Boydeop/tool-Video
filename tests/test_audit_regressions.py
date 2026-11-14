"""Audit regressions: synthetic credentials, bounded concurrency, no live APIs."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone, timedelta
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

from studio.database import Store
from studio.exports import ExportQueue
from studio.facebook import PageError, _protect, _unprotect
from studio.keys import get_keys, import_txt, parse_txt
from studio.krdict import KRDict, KRDictError, parse_search, CACHE_VERSION,sense_overlap
from studio.publisher import publish_reel, PublishingError
from studio.quiz import Quiz
from studio.review import blind_review, verify_quiz
from studio.run_limits import BudgetExpired, RunCancelled
from studio.scheduler import Scheduler
from studio.service import Service
from studio.webapp import create_server
from studio.youtube import SCOPES, YouTubeAccount

XML='''<channel><total>1</total><item><word>물</word><target_code>1</target_code>
<sense><translation><trans_lang>베트남어</trans_lang><trans_word>nước</trans_word>
</translation></sense></item></channel>'''.encode()

def quiz():
    return Quiz(topic="Đồ uống",question="Nước",clue="Nước",correct="물",
        wrong1="맥주",wrong2="차",wrong3="우유",meaning="Nước",romanization="mul",
        wrong1_meaning="Bia",wrong1_romanization="maekju",wrong2_meaning="Trà",
        wrong2_romanization="cha",wrong3_meaning="Sữa",wrong3_romanization="uyu")

class KRDictAuditTests(unittest.TestCase):
    def test_vietnamese_tones_and_single_character_words_keep_their_meaning(self):
        self.assertEqual(sense_overlap('cá',['cà']),0)
        self.assertEqual(sense_overlap('đắt',['đặt']),0)
        self.assertEqual(sense_overlap('ô',['Ô']),1)
        self.assertEqual(sense_overlap('Nước',['nước']),1)

    def test_cached_word_does_not_validate_a_changed_key(self):
        with tempfile.TemporaryDirectory() as td:
            store=Store(Path(td)/"db.sqlite3")
            client=KRDict(store,transport=Mock(return_value=XML))
            with patch('studio.krdict.get_keys',return_value=['synthetic-key']):
                client.lookup('물')
                client.transport=Mock(side_effect=KRDictError('api_error','Rejected'))
                with self.assertRaises(KRDictError):client.test_connection()
                client.transport.assert_called_once()

    def test_cache_does_not_mask_missing_key(self):
        with tempfile.TemporaryDirectory() as td:
            store=Store(Path(td)/'db.sqlite3')
            store.krdict_cache_put('물',{'exact':True,'entries':[], 'cache_version':CACHE_VERSION})
            with patch('studio.krdict.get_keys',return_value=[]):
                with self.assertRaises(KRDictError) as caught:KRDict(store).test_connection()
            self.assertEqual(caught.exception.code,'key_missing')

    def test_legacy_cache_is_refreshed(self):
        with tempfile.TemporaryDirectory() as td:
            store=Store(Path(td)/'db.sqlite3')
            store.krdict_cache_put('물',{'exact':False,'entries':[]})
            transport=Mock(return_value=XML)
            with patch('studio.krdict.get_keys',return_value=['synthetic-key']):
                self.assertTrue(KRDict(store,transport).lookup('물')['exact'])
            transport.assert_called_once()

    def test_html_and_wrapped_api_errors_are_not_dictionary_misses(self):
        for response,code in [(b'<html>Proxy failure</html>','invalid_response'),
                              (b'<channel><error><error_code>020</error_code></error></channel>','api_error'),
                              (b'<channel><total>unknown</total></channel>','invalid_response')]:
            with self.subTest(code=code),self.assertRaises(KRDictError) as caught:
                parse_search(response,'물')
            self.assertEqual(caught.exception.code,code)

    def test_valid_empty_search_is_not_reported_as_invalid_key(self):
        with tempfile.TemporaryDirectory() as td:
            store=Store(Path(td)/'db.sqlite3')
            with patch('studio.krdict.get_keys',return_value=['synthetic-key']):
                result=KRDict(store,transport=lambda req:b'<channel><total>0</total></channel>').test_connection()
            self.assertTrue(result['connected'])
            self.assertFalse(result['has_vietnamese'])

    def test_network_errors_are_classified_without_disclosing_key(self):
        client=KRDict(Mock())
        errors=[(ssl.SSLCertVerificationError('secret URL'),'tls_error'),
                (socket.timeout('secret URL'),'timeout'),
                (OSError('secret URL'),'network_error')]
        for reason,code in errors:
            with self.subTest(code=code),patch('studio.krdict.urllib.request.urlopen',
                  side_effect=urllib.error.URLError(reason)):
                with self.assertRaises(KRDictError) as caught:client._request('물','DO_NOT_DISCLOSE')
                self.assertEqual(caught.exception.code,code)
                self.assertNotIn('DO_NOT_DISCLOSE',str(caught.exception))
                self.assertNotIn('secret URL',str(caught.exception))

    def test_import_quoted_opaque_key_and_preserve_other_providers(self):
        with tempfile.TemporaryDirectory() as td,patch.dict(os.environ,{
                'THSE_KEYS_FILE':str(Path(td)/'keys.local.json'),
                'KRDICT_API_KEY':'','KRDICT_API_KEYS':''}):
            import_txt('[groq]\nsynthetic-groq')
            result=import_txt('\ufeffKRDICT_API_KEY="synthetic-krdict-Z9"')
            self.assertEqual(get_keys('krdict'),['synthetic-krdict-Z9'])
            self.assertEqual(get_keys('groq')[0],'synthetic-groq')
            self.assertNotIn('synthetic',json.dumps(result))

    def test_replacing_stale_krdict_key_keeps_other_provider_pools(self):
        with tempfile.TemporaryDirectory() as td,patch.dict(os.environ,{
                'THSE_KEYS_FILE':str(Path(td)/'keys.local.json'),
                'KRDICT_API_KEY':'','KRDICT_API_KEYS':''}):
            import_txt('[krdict]\nstale-key\n[groq]\nsynthetic-groq')
            import_txt('[krdict]\nnew-key')
            self.assertEqual(get_keys('krdict')[0],'stale-key')
            result=import_txt('[krdict]\nnew-key',replace=True)
            self.assertEqual(get_keys('krdict'),['new-key'])
            self.assertEqual(get_keys('groq')[0],'synthetic-groq')
            self.assertEqual(result['mode'],'replace')

    def test_damaged_key_store_is_never_overwritten(self):
        for contents in ['not json','[]','{"encrypted":"broken"}',
                         '{"encrypted":123}','{"krdict":{"unexpected":"object"}}']:
            with self.subTest(contents=contents),tempfile.TemporaryDirectory() as td:
                path=Path(td)/'keys.local.json';path.write_text(contents)
                with patch.dict(os.environ,{'THSE_KEYS_FILE':str(path)}):
                    with self.assertRaises(ValueError):import_txt('[krdict]\nsynthetic-key')
                self.assertEqual(path.read_text(),contents)

    def test_dpapi_failure_has_key_specific_message_and_preserves_file(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'keys.json';path.write_text('{"encrypted":"dpapi:fake"}')
            with patch.dict(os.environ,{'THSE_KEYS_FILE':str(path)}),\
                 patch('studio.keys._unprotect',side_effect=PageError('wrong Windows user')):
                with self.assertRaisesRegex(ValueError,'tài khoản Windows'):get_keys('krdict')
            self.assertEqual(path.read_text(),'{"encrypted":"dpapi:fake"}')

    @unittest.skipUnless(os.name=='nt','Requires real Windows DPAPI')
    def test_real_windows_dpapi_unicode_roundtrip(self):
        text=json.dumps({'krdict':['synthetic-key'],'label':'Hàn–Việt 물'},ensure_ascii=False)
        encrypted=_protect(text)
        self.assertTrue(encrypted.startswith('dpapi:'))
        self.assertNotIn('synthetic-key',encrypted)
        self.assertEqual(_unprotect(encrypted),text)

    def test_misdecoded_utf16_is_rejected_with_useful_message(self):
        with self.assertRaisesRegex(ValueError,'giải mã'):parse_txt('k\x00e\x00y\x00','krdict')

    @unittest.skipUnless(shutil.which('node'),'Node.js required')
    def test_browser_decodes_windows_utf16_and_utf8_key_files(self):
        ui=(Path(__file__).resolve().parents[1]/'studio/ui.html').read_text('utf-8')
        fn=re.search(r'async function readKeyFile\(file\)\{[\s\S]*?\n\}',ui).group()
        script=fn+'''
(async()=>{
 const expected='KRDICT_API_KEY="synthetic-key"';
 for(const bytes of [Buffer.from(expected,'utf8'),
   Buffer.concat([Buffer.from([255,254]),Buffer.from(expected,'utf16le')]),
   Buffer.concat([Buffer.from([254,255]),Buffer.from(expected,'utf16le').swap16()])]){
   const decoded=await readKeyFile({arrayBuffer:async()=>bytes});
   if(decoded!==expected)throw Error('Key file decoded incorrectly');
 }
})().catch(err=>{console.error(err);process.exitCode=1});
'''
        result=subprocess.run(['node','-e',script],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)

class PipelineAuditTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required')
    def test_failed_mux_never_exposes_partial_mp4_and_intro_is_reused(self):
        from studio.renderer import render,draw_intro
        with tempfile.TemporaryDirectory() as td:
            output=Path(td)/'failed.mp4'
            def fail_mux(silent,events,target,duration):
                Path(target).write_bytes(b'partial-output')
                raise RuntimeError('injected mux failure')
            with patch('studio.renderer.mux_narration',side_effect=fail_mux),\
                 patch('studio.renderer.draw_intro',wraps=draw_intro) as intro:
                with self.assertRaisesRegex(RuntimeError,'injected mux'):
                    render([quiz()],output,width=540,fps=24,think=4,reveal=1.5,
                           transition=.3,intro=.5,sfx=True)
            self.assertFalse(output.exists())
            self.assertEqual(list(Path(td).iterdir()),[])
            intro.assert_called_once()

    def test_all_four_choices_require_hangul(self):
        with self.assertRaises(ValueError):replace(quiz(),wrong1='not Korean').validate()

    def test_strict_does_not_certify_unknown_distractor(self):
        dictionary=Mock()
        def lookup(word):
            return {'exact':word=='물','entries':[{'meanings':['Nước']}]} if word=='물' else {'exact':False,'entries':[]}
        dictionary.lookup.side_effect=lookup
        reviewer=Mock()
        report=verify_quiz(quiz(),Mock(),dictionary=dictionary,reviewer=reviewer)
        self.assertEqual(report['outcome'],'needs_review')
        reviewer.assert_not_called()
        self.assertTrue(any('đáp án nhiễu' in issue for issue in report['issues']))

    def test_dictionary_outage_makes_one_request_and_preserves_diagnostic(self):
        dictionary=Mock()
        dictionary.lookup.side_effect=KRDictError('network_error','Không kết nối được KRDict')
        report=verify_quiz(quiz(),Mock(),dictionary=dictionary)
        dictionary.lookup.assert_called_once_with('물')
        self.assertEqual(report['outcome'],'needs_review')
        self.assertIn('Không kết nối được KRDict',report['issues'])
        self.assertTrue(all(item['reason']=='network_error' for item in report['dictionary'].values()))

    def test_provider_fallback_propagates_budget_and_cancel(self):
        from studio.answer_enrichment import suggest
        from studio.seo import draft
        for failure in (BudgetExpired,RunCancelled):
            with self.subTest(failure=failure.__name__),patch('studio.review.get_keys',return_value=['fake']):
                with self.assertRaises(failure):
                    blind_review(quiz(),call=Mock(side_effect=failure('stop')))
            for function,module,arg in [(suggest,'answer_enrichment',quiz()),(draft,'seo',[quiz()])]:
                with self.subTest(module=module),patch('studio.'+module+'.next_key',side_effect=failure('stop')):
                    with self.assertRaises(failure):function(arg)

    def test_manual_queue_accepts_machine_approved_question(self):
        with tempfile.TemporaryDirectory() as td:
            service=Service(td);qid=service.store.insert_question(quiz())
            service.store.auto_approve([qid])
            queue=ExportQueue(service)
            with patch.object(service,'render_approved',return_value='synthetic.mp4'):
                result=queue.start([qid],per_video=1)
                # Join the real worker; no external renderer or API used.
                for thread in threading.enumerate():
                    if thread.name=='thse-export-'+result['job_id'][:8]:thread.join(5)
            self.assertEqual(queue.status(result['job_id'])['status'],'completed')

    def test_media_cleanup_cannot_race_a_writer(self):
        with tempfile.TemporaryDirectory() as td:
            service=Service(td);entered=threading.Event();release=threading.Event()
            def writer():
                with service._media_lock:entered.set();release.wait(5)
            thread=threading.Thread(target=writer);thread.start()
            try:
                self.assertTrue(entered.wait(2))
                with self.assertRaisesRegex(ValueError,'Đang xử lý'):service.clear_cache()
            finally:release.set();thread.join(5)

    def test_queue_history_is_bounded_and_elapsed_stops(self):
        queue=ExportQueue(Mock())
        with patch('studio.exports.time.monotonic',return_value=20):
            for number in range(40):
                jid=str(number);queue._jobs[jid]={'videos':[]}
                queue._started_at[jid]=10;queue._phase_at[jid]=11
                queue._finish(jid)
        self.assertEqual(len(queue._jobs),30)
        self.assertEqual(len(queue._started_at),30)
        self.assertEqual(len(queue._phase_at),30)
        self.assertEqual(len(queue._finished_at),30)
        with patch('studio.exports.time.monotonic',return_value=100):
            self.assertEqual(queue.status('39')['elapsed_seconds'],10)

    def test_pending_qa_search_is_not_limited_to_latest_hundred(self):
        with tempfile.TemporaryDirectory() as td:
            store=Store(Path(td)/'db.sqlite3')
            qid=store.insert_question(quiz(),provider='gemini')
            with store.db(immediate=True) as c:
                for n in range(110):
                    c.execute('''INSERT INTO questions(id,topic,question,correct,payload,
                        question_hash,answer_hash,status,provider,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?)''',
                        (str(n),'manual',str(n),str(n),'{}','q'+str(n),'a'+str(n),
                         'rendered','manual','9999'))
            self.assertEqual(store.pending_verification(),[{'id':qid}])

    def test_independent_stores_claim_each_scheduled_job_once(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'db.sqlite3';first=Store(path)
            video=Path(td)/'video.mp4';video.write_bytes(b'synthetic')
            with first.db(immediate=True) as c:
                for n in range(24):
                    c.execute('''INSERT INTO jobs(id,video_path,caption,scheduled_at,status,created_at,updated_at)
                        VALUES(?,?,?,'2000','scheduled','2000','2000')''',(str(n),str(video),''))
            stores=[Store(path) for _ in range(8)]
            barrier=threading.Barrier(8)
            def claim(store):barrier.wait(5);return store.claim_due()
            with ThreadPoolExecutor(max_workers=8) as pool:
                claimed=[row['id'] for batch in pool.map(claim,stores) for row in batch]
            self.assertEqual(len(claimed),24)
            self.assertEqual(len(set(claimed)),24)

class PublishingAuditTests(unittest.TestCase):
    def test_old_thumbnail_job_stops_after_eight_attempts_outside_ui_page(self):
        with tempfile.TemporaryDirectory() as td:
            store=Store(Path(td)/'db.sqlite3')
            with store.db(immediate=True) as c:
                for n in range(90):
                    c.execute('''INSERT INTO youtube_thumbnail_jobs(video_id,video_path,image_path,
                        status,attempts,next_attempt,updated_at) VALUES(?,?,?,'pending',7,'2000',?)''',
                        (str(n),'video.mp4','image.jpg',str(n).zfill(4)))
            self.assertNotIn('0',[row['video_id'] for row in store.list_youtube_thumbnails()])
            store.update_youtube_thumbnail('0',retry=True,error='processing')
            with store.db() as c:
                row=c.execute("SELECT status,attempts FROM youtube_thumbnail_jobs WHERE video_id='0'").fetchone()
            self.assertEqual(row['status'],'needs_review')
            self.assertEqual(row['attempts'],8)

    def test_missing_upload_confirmation_never_triggers_finalize(self):
        class Response(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
        with tempfile.TemporaryDirectory() as td:
            video=Path(td)/'video.mp4';video.write_bytes(b'fake')
            replies=[Response(json.dumps({'video_id':'123456','upload_url':'https://rupload.facebook.com/video'}).encode()),Response(b'{}')]
            with patch('studio.publisher.credentials',return_value=('12345','synthetic-token')),\
                 patch('studio.publisher.urllib.request.urlopen',side_effect=replies) as transport:
                with self.assertRaises(PublishingError):publish_reel(video,'test')
            self.assertEqual(transport.call_count,2)

    def test_youtube_requests_processing_read_permission(self):
        from importlib.resources import files
        spec=json.loads(files('googleapiclient.discovery_cache').joinpath('documents/youtube.v3.json').read_text())
        for resource,method in [('videos','list'),('videos','insert'),('thumbnails','set')]:
            self.assertTrue(set(SCOPES)&set(spec['resources'][resource]['methods'][method]['scopes']))

    def test_old_youtube_token_requires_explicit_reauthorization(self):
        with tempfile.TemporaryDirectory() as td:
            account=YouTubeAccount(td)
            with patch.object(account,'_load_private',return_value={'scopes':[SCOPES[0]]}):
                with self.assertRaisesRegex(ValueError,'Kết nối lại YouTube'):account.credentials()

    def test_thumbnail_queue_failure_does_not_relabel_uploaded_video(self):
        store=Mock();store.path=Path('/tmp/synthetic/db.sqlite3')
        store.claim_due.return_value=[]
        store.claim_outbound.return_value=[{'id':'j1','platform':'youtube','video_path':'fake.mp4',
                                           'title':'Title','caption':'Caption','privacy':'private'}]
        store.claim_youtube_thumbnails.return_value=[]
        store.enqueue_youtube_thumbnail.side_effect=OSError('disk full')
        scheduler=Scheduler(store)
        with patch.object(scheduler.youtube,'upload',return_value='remote12345'):
            scheduler.tick()
        store.finish_outbound.assert_called_once_with('j1','submitted',remote_id='remote12345')

class HTTPAuditTests(unittest.TestCase):
    def test_krdict_import_live_test_error_and_unicode_download(self):
        with tempfile.TemporaryDirectory() as td:
            service=Service(td);server=create_server(service,port=0)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base='http://127.0.0.1:'+str(server.server_address[1])
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def post(path,data):
                return opener.open(urllib.request.Request(base+path,data=json.dumps(data).encode(),
                    headers={'Content-Type':'application/json'}),timeout=5)
            try:
                with opener.open(base+'/health',timeout=5) as response:
                    self.assertEqual(json.load(response)['version'],'v1.2.2')
                with patch.dict(os.environ,{'KRDICT_API_KEY':'','KRDICT_API_KEYS':''}):
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        post('/api/krdict/test',{})
                    self.assertEqual(json.load(caught.exception)['code'],'key_missing')
                    with post('/api/keys/import',{'provider':'krdict','text':'KRDICT_API_KEY="synthetic-key"'}) as response:
                        self.assertEqual(json.load(response)['saved']['krdict'],1)
                    with patch('studio.krdict.KRDict._request',return_value=parse_search(XML,'물')) as request:
                        for _ in range(2):
                            with post('/api/krdict/test',{}) as response:
                                result=json.load(response)
                                self.assertTrue(result['has_vietnamese'])
                                self.assertFalse(result['cache_hit'])
                        self.assertEqual(request.call_count,2)
                    with patch('studio.krdict.KRDict._request',side_effect=KRDictError('tls_error','TLS verification failed')):
                        with self.assertRaises(urllib.error.HTTPError) as caught:post('/api/krdict/test',{})
                        self.assertEqual(json.load(caught.exception)['code'],'tls_error')
                folder=Path(service.production.settings()['export_dir']);folder.mkdir(exist_ok=True)
                video=folder/'Bài học 물.mp4';video.write_bytes(b'synthetic-video')
                with opener.open(base+'/api/videos/'+urllib.parse.quote(video.name),timeout=5) as response:
                    self.assertIn("filename*=UTF-8''",response.headers['Content-Disposition'])
                    self.assertEqual(response.read(),b'synthetic-video')
            finally:server.shutdown();server.server_close();thread.join(5)

if __name__=='__main__':unittest.main()
