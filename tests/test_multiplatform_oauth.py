"""Offline OAuth and durable cross-platform dispatch regression tests."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit,parse_qs
from studio.facebook_oauth import FacebookOAuth
from studio.database import Store
from studio.scheduler import Scheduler

class FacebookOAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.oauth=FacebookOAuth(Path(self.tmp.name))
        self.oauth.configure('123456789','s'*32)
        self.redirect='http://127.0.0.1:8765/api/facebook/oauth/callback'

    def begin(self):
        result=self.oauth.start(self.redirect)
        return parse_qs(urlsplit(result['authorization_url']).query)['state'][0]

    def test_invalid_state_does_not_exchange(self):
        self.begin()
        with patch('studio.facebook_oauth.graph') as graph:
            with self.assertRaises(ValueError):self.oauth.callback({'state':['forged'],'code':['secret-code']})
            graph.assert_not_called()

    def test_expired_and_denied_callbacks(self):
        state=self.begin()
        with patch('studio.facebook_oauth.time.monotonic',return_value=10**15):
            with self.assertRaises(ValueError):self.oauth.callback({'state':[state],'code':['code']})
        state=self.begin()
        with self.assertRaises(ValueError):self.oauth.callback({'state':[state],'error':['access_denied']})
        self.assertIsNone(self.oauth.pending)

    def test_exchange_selection_and_replay(self):
        state=self.begin()
        with patch('studio.facebook_oauth.graph',side_effect=[{'access_token':'user-secret'},
                {'data':[{'id':'123456','name':'My Page','access_token':'page-secret'}]}]) as graph:
            result=self.oauth.callback({'state':[state],'code':['secret-code']})
            self.assertEqual(result,{'pages':[{'id':'123456','name':'My Page'}]})
            self.assertEqual(graph.call_args_list[0].args[1]['client_id'],'123456789')
            self.assertEqual(graph.call_args_list[0].kwargs['method'],'POST')
            with self.assertRaises(ValueError):self.oauth.callback({'state':[state],'code':['secret-code']})
        with patch('studio.facebook_oauth.connect',return_value={'connected':True}) as connect:
            self.assertTrue(self.oauth.select('123456')['connected'])
            connect.assert_called_once_with('123456','page-secret')
            self.assertEqual(self.oauth.list_pages(),{'pages':[]})

    def test_new_attempt_clears_old_pages_and_disconnect_invalidates_state(self):
        self.oauth.pages={'x':{'id':'x','name':'X','access_token':'secret'}}
        self.begin();self.assertEqual(self.oauth.pages,{})
        with patch('studio.facebook_oauth.disconnect'):
            self.oauth.disconnect()
        self.assertIsNone(self.oauth.pending)

class MultiPlatformTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=Store(self.root/'studio.sqlite3')
        self.video=self.root/'video.mp4';self.video.write_bytes(b'test')
        self.when=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()

    def schedule(self,platforms):
        return self.store.schedule_outbound_batch(self.video,platforms,'Title','Caption',self.when)

    def test_atomic_batch_rolls_back_when_later_platform_duplicates(self):
        self.schedule(['youtube'])
        with self.assertRaises(ValueError):self.schedule(['facebook','youtube'])
        self.assertEqual([r['platform'] for r in self.store.list_outbound()],['youtube'])

    def test_rejects_empty_unknown_duplicate_and_naive_time(self):
        for platforms in [[],['tiktok'],['facebook','facebook']]:
            with self.assertRaises(ValueError):self.schedule(platforms)
        with self.assertRaises(ValueError):
            self.store.schedule_outbound_batch(self.video,['facebook'],'','',datetime.now().isoformat())
        self.assertEqual(self.store.list_outbound(),[])

    def test_legacy_and_unified_facebook_cannot_double_schedule(self):
        self.store.add_job(self.video,'',self.when)
        with self.assertRaises(ValueError):self.schedule(['youtube','facebook'])
        self.assertEqual(self.store.list_outbound(),[])
        other=self.root/'other.mp4';other.write_bytes(b'test')
        self.store.schedule_outbound(other,'facebook','','',self.when)
        with self.assertRaises(ValueError):self.store.add_job(other,'',self.when)

    def test_parallel_dispatch_failure_isolated_and_no_reupload(self):
        self.schedule(['facebook','youtube','instagram'])
        with self.store.db() as c:c.execute("UPDATE outbound_posts SET publish_at='2000-01-01T00:00:00+00:00'")
        barrier=threading.Barrier(3)
        def facebook(*a,**kw):barrier.wait(timeout=5);raise RuntimeError('uncertain upload')
        def youtube(*a,**kw):barrier.wait(timeout=5);return 'youtube-id'
        def instagram(*a,**kw):barrier.wait(timeout=5);return 'instagram-id'
        scheduler=Scheduler(self.store)
        with patch('studio.scheduler.publish_reel',side_effect=facebook) as fb, \
             patch.object(scheduler.youtube,'upload',side_effect=youtube) as yt, \
             patch.object(scheduler.instagram,'publish',side_effect=instagram) as ig:
            scheduler.tick();scheduler.tick()
            self.assertEqual([fb.call_count,yt.call_count,ig.call_count],[1,1,1])
        rows={r['platform']:r for r in self.store.list_outbound()}
        self.assertEqual(rows['facebook']['status'],'needs_review')
        self.assertEqual(rows['youtube']['status'],'submitted')
        self.assertEqual(rows['instagram']['status'],'submitted')
        self.assertEqual(self.store.claim_outbound(),[])

class PublishingHTTPTests(unittest.TestCase):
    def setUp(self):
        import urllib.request
        from studio.service import Service
        from studio.webapp import create_server
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.service=Service(self.tmp.name)
        self.server=create_server(self.service,port=0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close)
        self.base='http://127.0.0.1:'+str(self.server.server_address[1])

    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=5)

    def request(self,path,body=None):
        import json,urllib.request,urllib.error
        req=urllib.request.Request(self.base+path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type':'application/json'})
        try:response=urllib.request.urlopen(req,timeout=5)
        except urllib.error.HTTPError as exc:response=exc
        return response.status,response.read().decode()

    def test_oauth_api_full_flow_keeps_tokens_out_of_responses(self):
        import json
        self.assertEqual(self.request('/api/facebook/oauth/configure',{'app_id':'123456789','app_secret':'s'*32})[0],200)
        status,result=self.request('/api/facebook/oauth/start',{})
        self.assertEqual(status,200)
        query=parse_qs(urlsplit(json.loads(result)['authorization_url']).query)
        self.assertEqual(query['redirect_uri'],[self.base+'/api/facebook/oauth/callback'])
        with patch('studio.facebook_oauth.graph',side_effect=[{'access_token':'USER-SECRET'},
             {'data':[{'id':'123456','name':'Page','access_token':'PAGE-SECRET'}]}]):
            self.assertEqual(self.request('/api/facebook/oauth/callback?state='+query['state'][0]+'&code=CODE-SECRET')[0],200)
        status,pages=self.request('/api/facebook/oauth/pages')
        self.assertEqual(json.loads(pages),{'pages':[{'id':'123456','name':'Page'}]})
        self.assertNotIn('SECRET',pages)
        with patch('studio.facebook_oauth.connect',return_value={'connected':True}):
            self.assertEqual(self.request('/api/facebook/oauth/select',{'page_id':'123456'})[0],200)

    def test_schedule_now_creates_all_selected_jobs_and_requires_accounts(self):
        import json
        video=self.service.root/'videos'/'video.mp4';video.parent.mkdir(exist_ok=True);video.write_bytes(b'test')
        payload={'video_name':'video.mp4','platforms':['facebook','youtube','instagram'],
                 'title':'Title','caption':'Caption','publish_now':True,'public_url':'https://media.example.com/video.mp4'}
        self.assertEqual(self.request('/api/platforms/schedule',payload)[0],400)
        with patch('studio.service.credentials',return_value=('123456','TOKEN')), \
             patch('studio.youtube.YouTubeAccount.status',return_value={'connected':True}), \
             patch('studio.instagram.InstagramAccount.status',return_value={'connected':True}), \
             patch('studio.publishing_accounts.account_key',return_value='bound-account'):
            status,response=self.request('/api/platforms/schedule',payload)
        self.assertEqual(status,200,response)
        self.assertEqual(set(json.loads(response)['jobs']),set(payload['platforms']))
        self.assertEqual(len(self.service.store.list_outbound()),3)
