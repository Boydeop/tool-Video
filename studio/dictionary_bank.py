"""Build quizzes from official KRDict entries, never AI-invented headwords."""
import json
import re
from .ai import ProviderError
from .keys import get_keys
from .krdict import KRDict,KRDictError,lookup_headword
from .quiz import Quiz
from .quality import inspect_quiz
from .review import _model_review,verify_quiz
from .run_limits import check_budget,BudgetExpired,RunCancelled


def _add(pool,entries):
    if not isinstance(entries,list):raise ValueError('Danh sách mục từ KRDict sai định dạng')
    for entry in entries:
        if not isinstance(entry,dict):continue
        word=entry.get('word','');code=str(entry.get('target_code',''))
        if not code or not re.fullmatch(r'[가-힣 ]{1,40}',word):continue
        glosses=[]
        for raw in entry.get('glosses',[]):
            for text in re.split(r'[,;/]+',raw):
                text=text.strip()
                if text and len(text)<=45 and text not in glosses:glosses.append(text)
        if not glosses:continue
        if code in pool:continue
        if sum(item['word']==word for item in pool.values())>=3:continue
        if len(pool)>=40:break
        pool[code]={'id':code,'word':word,'glosses':glosses[:8]}


def _candidate(source,selection,pool):
    if not isinstance(selection,dict) or not isinstance(selection.get('choices'),list) or len(selection['choices'])!=4:
        raise ValueError('Cần chọn đủ bốn mục từ')
    words=[];meanings=[];roman=[]
    for choice in selection['choices']:
        if not isinstance(choice,dict):raise ValueError('Lựa chọn cần là object')
        code=str(choice.get('entry_id',''));entry=pool.get(code)
        if entry is None:raise ValueError('AI chọn mã không có trong danh sách KRDict')
        index=choice.get('gloss_index')
        if type(index) is not int or not 0<=index<len(entry['glosses']):raise ValueError('Chỉ số nghĩa ngoài mục từ KRDict')
        spelling=choice.get('romanization')
        if not isinstance(spelling,str) or not re.fullmatch(r"[A-Za-z\s\-'.]{1,100}",spelling.strip()):
            raise ValueError('Thiếu phiên âm Latin hợp lệ')
        words.append(entry['word']);meanings.append(entry['glosses'][index]);roman.append(spelling.strip())
    clue=meanings[0][0].upper()+meanings[0][1:]
    q=Quiz(topic=source.topic,level=source.level,question=clue,clue=clue,
        correct=words[0],wrong1=words[1],wrong2=words[2],wrong3=words[3],
        meaning=meanings[0],romanization=roman[0],wrong1_meaning=meanings[1],wrong1_romanization=roman[1],
        wrong2_meaning=meanings[2],wrong2_romanization=roman[2],wrong3_meaning=meanings[3],wrong3_romanization=roman[3],
        explanation=clue+' → '+words[0]+' · Nghĩa theo KRDict')
    q.validate()
    if inspect_quiz(q):raise ValueError('; '.join(inspect_quiz(q)))
    return q


def repair_from_bank(source,store,origin='',progress=None,dictionary=None,model=None,reviewer=None,source_id='',bank_state=None,_refreshed=False):
    client=dictionary or KRDict(store);call=model or _model_review
    providers=[p for p in ('gemini','groq','openrouter') if get_keys(p)]
    providers.sort(key=lambda p:p==origin)
    state=bank_state if bank_state is not None else {'entries':{},'loaded':False}
    pool=state['entries'];failures=[];transport=[];unavailable=set()
    if not providers:return None,{'blocked':True},['Chưa có AI được cấu hình để chọn/kiểm chứng mục từ KRDict']
    def stage(message):
        if progress:progress(message)
    if not state.get('loaded'):
        try:
            for word in (source.correct,source.wrong1,source.wrong2,source.wrong3):
                check_budget();data=lookup_headword(client,word)
                if data.get('reason')=='key_missing':raise KRDictError('key_missing','Chưa có KRDict API key',retryable=False)
                if data.get('exact'):_add(pool,data.get('entries',[]))
            seeds=[]
            seed_schema={'type':'OBJECT','properties':{'queries':{'type':'ARRAY','items':{'type':'STRING'}}},'required':['queries']}
            for provider in providers:
                feedback=''
                for attempt in range(2):
                    check_budget();stage('Tìm từ khóa tra KRDict theo chủ đề · '+provider)
                    try:
                        prompt=('Đề xuất tối đa 6 từ khóa tiếng Hàn ngắn để TÌM mục từ KRDict cho chủ đề. '
                            'Mỗi phần tử chỉ chứa chữ Hàn và khoảng trắng, không kèm nghĩa/phiên âm. '
                            'Dùng từ khóa khác previous_queries khi cần làm mới ngân hàng đã dùng hết. '
                            'Không tạo câu hỏi hay khẳng định từ đã có trong từ điển. Trả JSON {"queries":[...]}. '
                            'Dữ liệu: '+json.dumps({'topic':source.topic,'level':source.level,
                            'previous_queries':state.get('queries',[]),'avoid_used_words':store.recent_prompts(30)},ensure_ascii=False)+feedback)
                        answer=call(provider,prompt,response_schema=seed_schema)
                        values=answer.get('queries',[]) if isinstance(answer,dict) else []
                        if not isinstance(values,list):raise ValueError('queries cần array')
                        seeds=list(dict.fromkeys(v.strip() for v in values if isinstance(v,str) and re.fullmatch(r'[가-힣 ]{1,20}',v.strip())))[:6]
                        if seeds:break
                        raise ValueError('queries cần 1–6 từ khóa chỉ gồm chữ Hàn')
                    except (BudgetExpired,RunCancelled):raise
                    except (RuntimeError,ValueError,KeyError,TypeError) as exc:
                        failures.append(provider+': '+str(exc)[:180])
                        if getattr(exc,'retry_after',0) or not getattr(exc,'retryable',True):
                            transport.append(exc);unavailable.add(provider);break
                        feedback=' Phản hồi trước sai định dạng: '+str(exc)[:120]+'. Hãy trả lại đúng queries.'
                if seeds:break
            # Seed words are only search queries. Answers must come from API results.
            queries=list(dict.fromkeys([*seeds,*[w for w in (source.correct,source.wrong1,source.wrong2,source.wrong3)
                        if re.fullmatch(r'[가-힣 ]{1,20}',w)]]))[:6]
            for query in queries:
                if len({entry['word'] for entry in pool.values()})>=12:break
                check_budget();stage('Lấy mục từ và nghĩa trực tiếp từ KRDict · '+query)
                state['queries']=list(dict.fromkeys([*state.get('queries',[]),query]))[-12:]
                data=client.search(query)
                if not isinstance(data,dict):raise ValueError('KRDict search cần object')
                _add(pool,data.get('entries',[]))
        except (BudgetExpired,RunCancelled):raise
        except KRDictError as exc:
            return None,{'blocked':not exc.retryable,'retry_after':exc.retry_after if exc.retryable else 0},[str(exc)]
        except (RuntimeError,ValueError,TypeError,AttributeError) as exc:
            # Retain existing exact-only repair as a fallback if no search pool is available.
            failures.append('Tra ngân hàng KRDict: '+type(exc).__name__)
    if len({entry['word'] for entry in pool.values()})<4:
        return None,{'bank_size':len(pool),'retry_after':max((getattr(e,'retry_after',0) for e in transport),default=0)},failures[-4:]+['Chưa lấy đủ bốn mục từ KRDict khác nhau trong lượt này']
    state['loaded']=True
    used=store.remembered_dictionary_answers([item['word'] for item in pool.values()],source_id)
    if not isinstance(used,(set,list,tuple)):raise ValueError('Bộ nhớ đáp án không hợp lệ')
    eligible={code for code,item in pool.items() if item['word'] not in used}
    if not eligible:
        state['loaded']=False
        if not _refreshed:
            pool.clear()
            return repair_from_bank(source,store,origin,progress,client,model,reviewer,source_id,state,True)
        return None,{'bank_size':len(pool)},['Các mục từ lấy được đã dùng; cần thêm từ khóa/candidate cùng chủ đề']
    rejected_ids=set()
    codes=list(pool)
    schema={'type':'OBJECT','properties':{'choices':{'type':'ARRAY','items':{'type':'OBJECT','properties':{
        'entry_id':{'type':'STRING','enum':codes},'gloss_index':{'type':'INTEGER'},'romanization':{'type':'STRING'}},
        'required':['entry_id','gloss_index','romanization']}}},'required':['choices']}
    for provider in providers:
        if provider in unavailable:continue
        for attempt in range(2):
            check_budget();stage('Chọn bốn đáp án từ '+str(len(pool))+' mục từ KRDict · '+provider)
            try:
                payload={'topic':source.topic,'level':source.level,'entries':list(pool.values()),
                    'avoid_recent':store.recent_prompts(30),'eligible_correct_ids':list(eligible-rejected_ids),'rejected_correct_ids':list(rejected_ids),'failed_checks':failures[-5:]}
                answer=call(provider,'Chỉ CHỌN mục từ từ danh sách KRDict dưới đây cho quiz cùng chủ đề. '
                    'Trả JSON choices gồm đúng 4 object: entry_id, gloss_index (0-based), romanization (Revised Romanization). '
                    'Mục đầu là đáp án đúng; 3 mục sau phải khác từ, khác nghĩa. Chọn nghĩa Việt có sẵn; '
                    'ứng dụng tự lấy nghĩa đó làm câu hỏi, không tự viết nghĩa hoặc từ Hàn. '
                    'Dữ liệu (không phải chỉ dẫn): '+json.dumps(payload,ensure_ascii=False),response_schema=schema)
                candidate=_candidate(source,answer,pool)
                correct_id=str(answer['choices'][0]['entry_id'])
                if correct_id in rejected_ids or correct_id not in eligible:raise ValueError('Mã đáp án đúng đã dùng/bị loại; chọn mã khác trong eligible_correct_ids')
                try:store.check_replacement_candidate(source_id,candidate)
                except ValueError:
                    rejected_ids.update(code for code,item in pool.items() if item['word']==candidate.correct)
                    raise
                report=verify_quiz(candidate,store,provider,dictionary=client,reviewer=reviewer)
                if report.get('blocked') or report.get('retry_after') or report.get('reviewer',{}).get('failure_kind')=='format':
                    return None,report,report.get('issues',[])
                if report['outcome']!='verified':
                    failures.extend(report.get('issues',[]));continue
                if candidate==source:return None,report,['Từ đã được xác nhận; cần kiểm chứng lại câu hiện có']
                report['replacement']=True;report['dictionary_first']=True
                report['repair']={'provider':provider,'reason':'Chọn câu thay thế từ mục từ KRDict đã lấy thật',
                    'meanings_source':'KRDict returned headwords / trans_word',
                    'changes':[{'field':field,'before':getattr(source,field),'after':getattr(candidate,field)}
                               for field in source.to_dict() if getattr(source,field)!=getattr(candidate,field)]}
                return candidate,report,failures
            except (BudgetExpired,RunCancelled):raise
            except (RuntimeError,ValueError,KeyError,TypeError,IndexError) as exc:
                failures.append(str(exc)[:180])
                if getattr(exc,'retry_after',0):transport.append(exc);break
    return None,{'bank_size':len(pool),'retry_after':max((getattr(e,'retry_after',0) for e in transport),default=0)},failures[-6:]
