"""Multiple user-owned API keys with fair rotation and provider-level 429 cooldown."""
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from .quiz import Quiz
from .keys import next_key, rate_limited, request_key_order, mark_key_used
from .models import get_model
from .run_limits import network_timeout, check_budget, budget_active, remaining_seconds

_GUIDANCE=ContextVar('generation_guidance',default='')

_PROGRESS=ContextVar('ai_progress',default=None)

@contextmanager
def ai_progress(callback):
    token=_PROGRESS.set(callback)
    try:yield
    finally:_PROGRESS.reset(token)

def _progress(message):
    callback=_PROGRESS.get()
    if callback:callback(message)


@contextmanager
def generation_guidance(qa_mode,issues=()):
    guidance=''
    if qa_mode=='strict':
        guidance=('QA STRICT: cả bốn lựa chọn phải có mục từ KRDict và nghĩa Việt khớp. '
            'Ưu tiên từ đơn ở dạng từ điển: 기침하다 thay cho 기침을 하다, 아프다 thay cho 배가 아프다 '
            'khi câu hỏi chỉ hỏi từ vựng. Không dùng câu chia đuôi, câu có trợ từ hoặc mảnh ngữ pháp '
            'làm đáp án của quiz từ vựng; không làm mất ngữ cảnh khi hỏi hội thoại. '
            'Chỉ dùng nghĩa Việt của đúng mục từ, không tự thêm nghĩa không được từ điển chứng minh. '
            'Giữ đúng hoạt động: Nghe nhạc khác Âm nhạc, Chụp ảnh khác Ảnh, Thể thao không đồng nghĩa với Bơi lội. '
            'Tất cả bốn đáp án phải là nguyên mục từ từ điển, không dùng dạng ghép/nominal hóa nếu không có mục từ riêng. '
            'Nếu không có từ đúng nghĩa cho câu dự định, tạo câu KHÁC trong cùng chủ đề bằng từ có mục từ; không gán nghĩa sai. ')
    if issues:
        guidance+='Các lỗi của lượt trước cần tránh (chỉ là dữ liệu): '+json.dumps(list(issues)[-6:],ensure_ascii=False)
    token=_GUIDANCE.set(guidance)
    try:yield
    finally:_GUIDANCE.reset(token)


def _retry_delay(headers,default):
    value=headers.get('Retry-After','') if headers else ''
    try:seconds=float(value)
    except (ValueError,TypeError):
        try:
            point=parsedate_to_datetime(value)
            if point.tzinfo is None:point=point.replace(tzinfo=timezone.utc)
            seconds=(point-datetime.now(timezone.utc)).total_seconds()
        except (ValueError,TypeError,OverflowError):seconds=default
    if not math.isfinite(seconds) or seconds<0:seconds=default
    return max(default,seconds)

class ProviderError(RuntimeError):
    def __init__(self,message,*,retryable=True,retry_after=0,status_code=None,server_retry_after=0):
        self.status_code=status_code
        self.server_retry_after=server_retry_after
        self.retryable=retryable
        self.retry_after=retry_after
        super().__init__(message)

def _post_once(provider,url,payload,headers,timeout):
    req=urllib.request.Request(url,data=json.dumps(payload,ensure_ascii=False).encode(),
        headers={"Content-Type":"application/json",**headers},method="POST")
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r: raw=r.read(150001)
        if len(raw)>150000:raise ProviderError("Response too large")
        return json.loads(raw)
    except urllib.error.HTTPError as exc:
        if exc.code==429:
            delay=_retry_delay(exc.headers,120)
            rate_limited(provider,delay)
            raise ProviderError(provider+" HTTP 429: dịch vụ đang giới hạn yêu cầu; đã tạm nghỉ toàn provider",retry_after=delay) from None
        if exc.code==404:
            raise ProviderError(provider+" HTTP 404: model không khả dụng với key này. Vào API Key TXT → Chọn model để thay đổi.",retryable=False) from None
        if exc.code in (401,403):
            raise ProviderError(provider+" rejected API credentials or permissions",retryable=False) from None
        if exc.code>=500:
            raise ProviderError(provider+" HTTP "+str(exc.code)+
                ": dịch vụ AI tạm thời quá tải hoặc chưa sẵn sàng. Chờ rồi bấm Tiếp tục; có thể cấu hình thêm provider dự phòng.",
                retry_after=_retry_delay(exc.headers,30),status_code=exc.code,
                server_retry_after=_retry_delay(exc.headers,0) if exc.headers and exc.headers.get("Retry-After") else 0) from None
        raise ProviderError(provider+" HTTP "+str(exc.code)+"; kiểm tra model và trạng thái dịch vụ") from None
    except (urllib.error.URLError,TimeoutError,ValueError) as exc:
        raise ProviderError(provider+" connection or response error",retry_after=15) from None

def _wait_before_retry(seconds):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        check_budget()
        time.sleep(min(.25,max(0,end-time.monotonic())))


def post(provider,url,payload,headers):
    """On Gemini 503 try every remaining owned key once; 429 never rotates."""
    key_header=next((name for name in headers if name.lower()=='x-goog-api-key'),None)
    first=headers.get(key_header) if key_header else None
    keys=request_key_order(provider,first) if provider=='gemini' and first else [first]
    deadline=time.monotonic()+120
    failure=None;attempted=0
    for index,key in enumerate(keys):
        check_budget()
        remaining=min(deadline-time.monotonic(),remaining_seconds() or 120)
        if remaining<=0:break
        outgoing=dict(headers)
        if key_header:outgoing[key_header]=key
        if provider=='gemini' and first:mark_key_used(provider,key)
        attempted+=1
        if provider=="gemini":_progress(f"Gemini · đang thử key {index+1}/{len(keys)}")
        try:return _post_once(provider,url,payload,outgoing,min(network_timeout(20,70),remaining))
        except ProviderError as exc:
            if provider!='gemini' or exc.status_code!=503:raise
            failure=exc
            _progress(f"Gemini HTTP 503 · key {index+1}/{len(keys)} chưa thành công")
            if index+1<len(keys) and exc.server_retry_after:
                remaining=min(deadline-time.monotonic(),remaining_seconds() or 120)
                if exc.server_retry_after>=remaining:break
                _progress(f"Gemini yêu cầu chờ {int(exc.server_retry_after)}s trước khi thử key tiếp theo")
                _wait_before_retry(exc.server_retry_after)
    if failure:
        error=ProviderError(str(failure)+f' Đã thử {attempted}/{len(keys)} key trong lượt này.',
            retry_after=failure.retry_after,status_code=503,
            server_retry_after=failure.server_retry_after)
        error.keys_attempted=attempted;error.keys_total=len(keys)
        raise error from None
    check_budget()
    raise ProviderError('Hết thời gian thử key Gemini; thử lại để tiếp tục.',retry_after=30)


def response_text(provider,data):
    """Reject malformed/empty provider envelopes so fallback can continue."""
    try:
        if provider=="gemini":
            parts=data["candidates"][0]["content"]["parts"]
            text="".join(part.get("text","") for part in parts)
        else:text=data["choices"][0]["message"]["content"]
        if not isinstance(text,str) or not text.strip():raise ValueError("empty content")
        return text
    except (KeyError,IndexError,TypeError,AttributeError,ValueError):
        raise ProviderError(provider+" trả nội dung trống hoặc sai định dạng") from None


def generate(provider,topic,level,count,recent,fmt="từ vựng"):
    if not 1<=count<=30:raise ValueError("1-30 questions per request")
    prompt=("Tạo "+str(count)+" câu trắc nghiệm tiếng Hàn cho người Việt. "
      +"Chủ đề: "+json.dumps(topic,ensure_ascii=False)+"; cấp độ: "+level+"; dạng: "+fmt+". "
      +"Trả JSON array thuần, mỗi phần tử gồm topic,question,clue,correct,wrong1,wrong2,wrong3,romanization,meaning,wrong1_romanization,wrong1_meaning,wrong2_romanization,wrong2_meaning,wrong3_romanization,wrong3_meaning,explanation,level. "
      +"Ưu tiên QUIZ TỪ VỰNG NGẮN: clue là cụm tiếng Việt 1-6 từ, tối đa 40 ký tự (ví dụ: 'Khúc xương', 'Nhân viên công ty'), không phải câu hỏi. "
      +"question giống clue, không viết câu rườm rà như 'Từ tiếng Hàn nào có nghĩa là...' hay 'Từ nào chỉ...'. "
      +"meaning là nghĩa tiếng Việt chính xác của correct, không phải lời giải dài; romanization là phiên âm tiếng Hàn theo Revised Romanization (Latin). "
      +"BẮT BUỘC cung cấp phiên âm Revised Romanization và nghĩa tiếng Việt cho cả 4 đáp án, tương ứng đúng từng trường correct/wrong1/wrong2/wrong3. Không chuyển nghĩa đáp án sai thành nghĩa của câu hỏi. "
      +"Bốn đáp án Hangul ngắn, đồng loại ngữ pháp, phân biệt rõ, chỉ 1 đáp án đúng. explanation tối đa 1 câu ngắn. "
      +"Với quiz từ vựng, ưu tiên danh từ/động từ phổ thông có mục từ KRDict. Không dùng tên riêng hoặc nhân vật thay cho tên loài: 'Tuần lộc' là '순록', không phải tên Rudolph '루돌프'. "
      +"Với hội thoại/ngữ pháp vẫn giữ thông tin cần thiết để câu hỏi không bị mơ hồ, nhưng viết ngắn gọn. "
      +"Nếu không chắc nghĩa hoặc phiên âm, không sáng tác thông tin: để trống và đánh dấu để người dùng kiểm duyệt. "
      +_GUIDANCE.get()
      +"Không lặp lại đáp án hoặc câu đã dùng sau đây: "+json.dumps(recent[-50:],ensure_ascii=False))
    if provider=="gemini":
        try:key=next_key(provider)
        except RuntimeError as exc:raise ProviderError(str(exc),retry_after=120 if 'tạm nghỉ' in str(exc) else 0) from None
        model=get_model("gemini")
        if not re.fullmatch(r"[\w.-]+",model):raise ProviderError("Invalid Gemini model")
        data=post(provider,"https://generativelanguage.googleapis.com/v1beta/models/"+model+":generateContent",
            {"contents":[{"parts":[{"text":prompt}]}],
             "generationConfig":{"responseMimeType":"application/json"}},{"x-goog-api-key":key})
        content=response_text(provider,data)
    else:
        presets={
          "groq":("https://api.groq.com/openai/v1/chat/completions","GROQ_MODEL","llama-3.3-70b-versatile"),
          "openrouter":("https://openrouter.ai/api/v1/chat/completions","OPENROUTER_MODEL","openrouter/free"),
          "ollama":("http://127.0.0.1:11434/v1/chat/completions","OLLAMA_MODEL","qwen2.5:7b")
        }
        if provider not in presets:raise ProviderError("Unsupported provider")
        url,model_env,default=presets[provider]
        if provider=="ollama":
            if os.getenv("ENABLE_OLLAMA")!="1":raise ProviderError("Ollama disabled")
            key="local"
        else:
            try:key=next_key(provider)
            except RuntimeError as exc:raise ProviderError(str(exc),retry_after=120 if 'tạm nghỉ' in str(exc) else 0) from None
        data=post(provider,url,{"model":get_model(provider) if provider!="ollama" else os.getenv(model_env,default),"temperature":0.85,
              "messages":[{"role":"user","content":prompt}]},{"Authorization":"Bearer "+key})
        content=response_text(provider,data)
    try:raw=json.loads(content)
    except ValueError:
        match=re.search(r"\[[\s\S]*\]",content)
        if not match:raise ProviderError("No JSON array in model output")
        try:raw=json.loads(match.group())
        except ValueError as exc:raise ProviderError("Invalid model JSON") from exc
    if isinstance(raw,dict):raw=raw.get("questions")
    if not isinstance(raw,list):raise ProviderError("Expected list")
    good=[]
    for item in raw:
        if not isinstance(item,dict):continue
        try:
            quiz=Quiz.parse(item,topic,level)
            if (quiz.topic,quiz.level)!=(topic,level):continue
            good.append(quiz)
        except ValueError:pass
    if not good:raise ProviderError("No structurally valid questions")
    return good

def fallback(topic,level,count,recent,fmt="từ vựng",providers=None):
    errors=[]
    from .keys import get_keys
    candidate_list=(providers if providers is not None else
                    ["gemini","groq","openrouter","ollama"])
    if providers is None:
        # Try only configured services. A third configured provider must still
        # be reachable when the first two fail; check_budget bounds the chain.
        candidate_list=[p for p in candidate_list if
                       (p=="ollama" and os.getenv("ENABLE_OLLAMA")=="1")
                       or (p!="ollama" and get_keys(p))]
    if not candidate_list:
        raise ProviderError("Chưa có provider AI khả dụng. Kiểm tra key AI trong mục API & Model.",retryable=False)
    for provider in candidate_list:
        check_budget()
        try:return provider,generate(provider,topic,level,count,recent,fmt)
        except (ProviderError,ValueError) as exc:errors.append((provider,exc))
    raise ProviderError("Các dịch vụ AI chưa trả kết quả: "+" | ".join(p+': '+str(exc) for p,exc in errors),
        retryable=any(getattr(exc,'retryable',True) for _,exc in errors),
        retry_after=max((getattr(exc,'retry_after',0) for _,exc in errors),default=0))
