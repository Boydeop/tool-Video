"""Regressions reproduced by the v1.5 audit; no live credentials."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from studio.service import Service
from studio.production import Production
from studio.quiz import Quiz

class AuditRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.service=Service(self.tmp.name)

    def test_ambiguous_video_name_must_not_resolve_to_wrong_content(self):
        folders=[self.service.root/'old',self.service.root/'new']
        for folder in folders:
            folder.mkdir();(folder/'same.mp4').write_bytes(folder.name.encode())
        with patch.object(self.service,'output_folders',return_value=folders):
            with self.assertRaisesRegex(ValueError,'trùng tên'):
                self.service.video_path('same.mp4')

    def test_busy_verification_does_not_overwrite_successful_qa(self):
        q=Quiz(topic='Gia đình',question='Nước uống',correct='물',wrong1='차',wrong2='집',wrong3='책')
        qid=self.service.store.insert_question(q,'gemini')
        report={'version':4,'outcome':'verified'}
        self.service.store.set_question_qa(qid,'verified',report)
        with patch.object(self.service.store,'pending_verification',return_value=[{'id':qid}]), \
             patch.object(self.service,'verify_generated',side_effect=ValueError('Đang xử lý video')):
            self.service.verify_pending_questions()
        self.assertEqual(self.service.store.get_question_qa(qid)['outcome'],'verified')

    def test_oversized_logo_rejected_before_decode(self):
        import base64,io
        from PIL import Image
        out=io.BytesIO();Image.new('RGB',(3001,2)).save(out,'PNG')
        with patch.object(Image.Image,'convert',side_effect=AssertionError('decoded too early')):
            with self.assertRaises(ValueError):self.service.production.set_logo(base64.b64encode(out.getvalue()).decode())

    def test_explicit_full_video_path_selects_exact_export_folder(self):
        a=self.service.root/'videos';a.mkdir();(a/'same.mp4').write_bytes(b'old')
        b=self.service.root/'custom';b.mkdir();(b/'same.mp4').write_bytes(b'new')
        self.service.production.update({'export_dir':str(b)})
        self.assertEqual(self.service.video_path(str(a/'same.mp4')),a/'same.mp4')

class ProviderEnvelopeTests(unittest.TestCase):
    def test_malformed_envelopes_become_provider_errors(self):
        from studio.ai import response_text,ProviderError
        for provider,data in [('groq',{'choices':[{'message':{'content':None}}]}),
            ('gemini',{'candidates':[]}),('groq',[]),('gemini',{'candidates':[None]})]:
            with self.subTest(provider=provider,data=data):
                with self.assertRaises(ProviderError):response_text(provider,data)

    def test_empty_provider_does_not_prevent_fallback(self):
        from studio.ai import fallback
        quiz=Quiz(topic='Gia đình',question='Nước uống',correct='물',wrong1='차',wrong2='집',wrong3='책')
        import json
        bad={'choices':[{'message':{'content':None}}]}
        good={'choices':[{'message':{'content':json.dumps([quiz.to_dict()])}}]}
        with patch('studio.ai.next_key',return_value='fake'),patch('studio.ai.get_model',return_value='model'), \
             patch('studio.ai.post',side_effect=[bad,good]):
            provider,rows=fallback('Gia đình','TOPIK 1',1,[],providers=['groq','openrouter'])
        self.assertEqual(provider,'openrouter');self.assertEqual(len(rows),1)

class BoundPublishingTests(unittest.TestCase):
    def test_changed_account_blocks_dispatch_without_upload(self):
        from datetime import datetime,timedelta,timezone
        from studio.database import Store
        from studio.scheduler import Scheduler
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);video=root/'video.mp4';video.write_bytes(b'test')
            store=Store(root/'studio.sqlite3')
            store.schedule_outbound_batch(video,['youtube'],'Title','Caption',
                (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),targets={'youtube':'original'})
            with store.db() as c:c.execute("UPDATE outbound_posts SET publish_at='2000-01-01T00:00:00+00:00'")
            scheduler=Scheduler(store)
            with patch('studio.publishing_accounts.account_key',return_value='different'), \
                 patch.object(scheduler.youtube,'upload') as upload:
                scheduler.tick();upload.assert_not_called()
            self.assertEqual(store.list_outbound()[0]['status'],'needs_review')

    def test_account_binding_survives_database_reopen(self):
        from studio.database import Store
        from datetime import datetime,timedelta,timezone
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);video=root/'v.mp4';video.write_bytes(b'test')
            store=Store(root/'studio.sqlite3')
            store.schedule_outbound_batch(video,['facebook'],'','',
                (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),targets={'facebook':'page-hash'})
            self.assertEqual(Store(store.path).list_outbound()[0]['target_key'],'page-hash')

    def test_unbound_legacy_queue_is_held_during_migration(self):
        from studio.database import Store
        from datetime import datetime,timedelta,timezone
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);video=root/'v.mp4';video.write_bytes(b'test')
            store=Store(root/'studio.sqlite3')
            store.add_job(video,'',(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())
            with store.db() as c:c.execute('ALTER TABLE jobs DROP COLUMN target_key')
            restored=Store(store.path)
            self.assertEqual(restored.list_jobs()[0]['status'],'needs_review')

class StaticFrameTests(unittest.TestCase):
    def test_cached_regions_have_identical_pixels_and_exclude_motion(self):
        from studio.renderer import draw_frame,_steady_frame_key
        q=Quiz(topic='Gia đình',question='Nước uống',correct='물',wrong1='차',wrong2='집',wrong3='책')
        self.assertIsNone(_steady_frame_key(.2,1.35,4,1.5))
        self.assertIsNone(_steady_frame_key(3,1.35,4,1.5))
        self.assertIsNone(_steady_frame_key(7,1.35,4,1.5))
        for a,b in [(.6,1.2),(5.5,6.7)]:
            self.assertEqual(_steady_frame_key(a,1.35,4,1.5),_steady_frame_key(b,1.35,4,1.5))
            self.assertEqual(draw_frame(q,1,1,'hanji',a,4,reveal=1.5).tobytes(),
                             draw_frame(q,1,1,'hanji',b,4,reveal=1.5).tobytes())

class CredentialSnapshotTests(unittest.TestCase):
    def test_youtube_rechecks_the_loaded_grant_before_network_io(self):
        from studio.youtube import YouTubeAccount,SCOPES
        from studio.publishing_accounts import fingerprint_account
        with tempfile.TemporaryDirectory() as tmp:
            account=YouTubeAccount(tmp)
            with patch.object(account,'_load_private',return_value={'refresh_token':'new-grant','scopes':SCOPES}):
                with self.assertRaisesRegex(ValueError,'đổi tài khoản'):
                    account.credentials(fingerprint_account('youtube','old-grant'))

    def test_meta_accounts_rechecked_on_exact_upload_snapshot(self):
        from studio.publisher import publish_reel,PublishingError
        from studio.instagram import InstagramAccount
        from studio.publishing_accounts import fingerprint_account
        with patch('studio.publisher.urllib.request.urlopen') as network:
            with self.assertRaises(PublishingError):
                publish_reel('unused.mp4','',account=('222222','new-token'),
                             target_key=fingerprint_account('facebook','111111'))
            network.assert_not_called()
        with tempfile.TemporaryDirectory() as tmp:
            account=InstagramAccount(tmp)
            with patch.object(account,'_read',return_value={'ig_id':'222222','token':'token'}), \
                 patch('studio.instagram._graph') as network:
                with self.assertRaisesRegex(ValueError,'đổi tài khoản'):
                    account.publish('https://media.example.com/video.mp4','',
                                    fingerprint_account('instagram','111111'))
                network.assert_not_called()

    def test_failed_private_write_preserves_previous_credentials(self):
        from studio.local_storage import save_private,load_private
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'credentials.json';save_private(path,{'grant':'previous'})
            with patch('studio.facebook._protect',side_effect=RuntimeError('protect failed')):
                with self.assertRaises(RuntimeError):save_private(path,{'grant':'replacement'})
            self.assertEqual(load_private(path),{'grant':'previous'})
            self.assertEqual(list(Path(tmp).glob('*.tmp')),[])
