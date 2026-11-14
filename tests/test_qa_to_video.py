"""Regression: eight auto-approved questions could not be manually approved.
Exercise actual UI functions in Node and the HTTP approval/export pipeline.
"""
from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest.mock import patch
from studio.quiz import Quiz
from studio.service import Service
from studio.webapp import create_server

ROOT=Path(__file__).resolve().parents[1]

class SelectionUITests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node.js required')
    def test_actual_ui_allows_eight_machine_checked_questions_to_approve_and_export(self):
        ui=(ROOT/'studio/ui.html').read_text('utf-8')
        blocks=[]
        for begin,end in [('function selectedApproved(){','function selectedForApproval(){'),
                          ('function selectedForApproval(){','function updateSelection(){'),
                          ('function updateSelection(){','function filteredQuestions(){'),
                          ('function filteredQuestions(){','function renderBatches(){'),
                          ('function selectReadyQuestions(){','$("selectApproved").addEventListener'),
                          ('async function approveSelectedQuestions(){','$("approve").addEventListener'),
                          ('async function startExport(batch){','// Manual video action')]:
            blocks.append(ui[ui.index(begin):ui.index(end,ui.index(begin))])
        harness=r'''
const assert=require('node:assert/strict');
const nodes={};const $=id=>nodes[id]??=(
 {value:'',disabled:false,textContent:'',style:{},open:false,scrolled:0,
  scrollIntoView(){this.scrolled++}});
const document={querySelector:()=>({value:'hanji'})};
const state={selection:new Set(),questions:[],trash:[],busy:false,activeRender:false,questionPage:1};
const calls=[];
function notice(){} function showPanel(){} function renderQuestions(){}
async function saveProduction(){return true} async function loadStatus(){}
async function run(fn){return await fn()}
const setTimeout=fn=>{fn();return 0};
async function api(url,body){
 calls.push({url,body});
 if(url==='/api/approve'){
  for(const q of state.questions)if(body.ids.includes(q.id))q.status='approved';
  return {updated:body.ids.length};
 }
 if(url==='/api/render/start')return {job_id:'job-eight'};
 if(url.startsWith('/api/render/progress'))return {
  status:'completed',percent:100,total_videos:2,videos:['one.mp4','two.mp4']};
 throw Error('Unexpected request: '+url);
}
'''
        assertions=r'''
(async()=>{
 for(let n=0;n<8;n++)state.questions.push({id:'q'+n,status:'auto_approved',batch_id:'batch',qa_report:{marker:'evidence'}});
 $('questionFilter').value='auto_approved';$('batchFilter').value='all';
 state.selection=new Set(state.questions.map(q=>q.id));
 updateSelection();
 assert.equal($('approve').disabled,false,'QA-passed questions must allow human approval');
 assert.equal($('makeSelectedVideo').disabled,false);
 await approveSelectedQuestions();
 assert.equal(calls[0].url,'/api/approve');assert.equal(calls[0].body.ids.length,8);
 assert.equal(state.selection.size,8);
 assert.ok(state.questions.every(q=>q.status==='approved'&&q.qa_report.marker==='evidence'));
 $('perVideo').value='6';$('voiceEngine').value='auto';$('voice').checked=false;
 await startExport(true);
 const request=calls.find(c=>c.url==='/api/render/start');
 assert.equal(request.body.ids.length,8);assert.equal(request.body.per_video,6);
 assert.equal($('manualExportDetails').open,true);
 assert.ok($('renderProgressPanel').scrolled>0);
 assert.equal(state.activeRender,false);
 // Ready selector includes human AND machine approvals, retaining batch filtering.
 state.questions=[{id:'human',status:'approved',batch_id:'A'},
  {id:'machine',status:'auto_approved',batch_id:'A'},
  {id:'draft',status:'draft',batch_id:'A'},
  {id:'old',status:'rendered',batch_id:'A'},
  {id:'elsewhere',status:'auto_approved',batch_id:'B'}];
 $('batchFilter').value='A';selectReadyQuestions();
 assert.deepEqual([...state.selection],['human','machine']);
 state.selection=new Set(['human','machine','draft']);
 await approveSelectedQuestions();assert.equal(state.selection.size,3,'mixed selections must not lose already-approved rows');
 state.selection=new Set(['old']);updateSelection();
 assert.equal($('approve').disabled,true);assert.equal($('makeSelectedVideo').disabled,true);
 state.selection=new Set(['human']);state.activeRender=true;updateSelection();
 assert.equal($('approve').disabled,true);assert.equal($('makeSelectedVideo').disabled,true);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result=subprocess.run(['node','-e',harness+'\n'.join(blocks)+assertions],
                              capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)

class ApprovalExportHTTPTests(unittest.TestCase):
    def test_eight_auto_approved_rows_can_be_human_approved_and_exported_in_two_batches(self):
        words=['물','차','학교','사람','사과','책','바다','지하철']
        with tempfile.TemporaryDirectory() as td:
            service=Service(td);ids=[]
            for n,word in enumerate(words):
                q=Quiz(topic='Từ vựng',question='Bài '+str(n),correct=word,
                       wrong1='봄',wrong2='여름',wrong3='가을')
                ids.append(service.store.insert_question(q,provider='gemini'))
                service.store.set_question_qa(ids[-1],'verified',{'tier':'dictionary+blind_ai','marker':'evidence'})
            server=create_server(service,port=0)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base='http://127.0.0.1:'+str(server.server_address[1])
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def request(path,data=None):
                req=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,
                    headers={'Content-Type':'application/json'})
                with opener.open(req,timeout=5) as response:return json.load(response)
            groups=[]
            def render(subset,**kwargs):
                groups.append(list(subset));path=str(Path(td)/f'video-{len(groups)}.mp4')
                service.store.mark_rendered(subset,path)
                return path
            try:
                response=request('/api/approve',{'ids':ids})
                self.assertEqual(response['updated'],8)
                self.assertTrue(all(row['status']=='approved' and row['qa_report']['marker']=='evidence'
                                    for row in service.store.list_questions()))
                with patch.object(service,'render_approved',side_effect=render):
                    job=request('/api/render/start',{'ids':ids,'per_video':6,'theme':'hanji','with_voice':False})
                    for _ in range(300):
                        status=request('/api/render/progress?job_id='+job['job_id'])
                        if status['status'] in ('completed','failed'):break
                        time.sleep(.01)
                self.assertEqual(status['status'],'completed',status)
                self.assertEqual([len(group) for group in groups],[6,2])
                self.assertEqual([qid for group in groups for qid in group],ids)
                self.assertEqual(len(status['videos']),2)
                self.assertTrue(all(row['status']=='rendered' for row in service.store.list_questions()))
            finally:server.shutdown();server.server_close();thread.join(5)

if __name__=='__main__':unittest.main()
