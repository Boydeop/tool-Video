"""Multiple user-owned API keys with fair rotation and provider-level 429 cooldown."""
import json
import os
import re
import urllib.error
import urllib.request
from .quiz import Quiz
from .keys import next_key, rate_limited
from .models import get_model
from .run_limits import network_timeout, check_budget, budget_active

class ProviderError(RuntimeError):
    def __init__(self,message,*,retryable=True,retry_after=0):
        self.retryable=retryable
        self.retry_after=retry_after
        super().__init__(message)

def post(provider,url,payload,headers):
    req=urllib.request.Request(url,data=json.dumps(payload,ensure_ascii=False).encode(),
        headers={"Content-Type":"application/json",**headers},method="POST")
    try:
        with urllib.request.urlopen(req,timeout=network_timeout(20,70)) as r: raw=r.read(150001)
        if len(raw)>150000:raise ProviderError("Response too large")
        return json.loads(raw)
    except urllib.error.HTTPError as exc:
        if exc.code==429:
            rate_limited(provider,120)
            raise ProviderError(provider+" is rate-limited; provider-level cooldown active",retry_after=120) from None
        if exc.code==404:
            raise ProviderError(provider+" HTTP 404: model không khả dụng với key này. Vào API Key TXT → Chọn model để thay đổi.",retryable=False) from None
        if exc.code in (401,403):
            raise ProviderError(provider+" rejected API credentials or permissions",retryable=False) from None
        raise ProviderError(provider+" HTTP "+str(exc.code)+"; check model and service status",
                            retry_after=30 if exc.code>=500 else 0) from None
    except (urllib.error.URLError,TimeoutError,ValueError) as exc:
        raise ProviderError(provider+" connection or response error",retry_after=15) from None

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
      +"Không lặp lại đáp án hoặc câu đã dùng sau đây: "+json.dumps(recent[-50:],ensure_ascii=False))
    if provider=="gemini":
        try:key=next_key(provider)
        except RuntimeError as exc:raise ProviderError(str(exc),retry_after=120 if 'tạm nghỉ' in str(exc) else 0) from None
        model=get_model("gemini")
        if not re.fullmatch(r"[\w.-]+",model):raise ProviderError("Invalid Gemini model")
        data=post(provider,"https://generativelanguage.googleapis.com/v1beta/models/"+model+":generateContent",
            {"contents":[{"parts":[{"text":prompt}]}],
             "generationConfig":{"responseMimeType":"application/json"}},{"x-goog-api-key":key})
        try:content="".join(x.get("text","") for x in data["candidates"][0]["content"]["parts"])
        except (KeyError,IndexError,TypeError) as exc:raise ProviderError("Empty Gemini response") from exc
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
        try:content=data["choices"][0]["message"]["content"]
        except (KeyError,IndexError,TypeError) as exc:raise ProviderError("Empty provider response") from exc
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
        try:good.append(Quiz.parse(item,topic,level))
        except ValueError:pass
    if not good:raise ProviderError("No structurally valid questions")
    return good

def fallback(topic,level,count,recent,fmt="từ vựng",providers=None):
    errors=[]
    from .keys import get_keys
    candidate_list=(providers if providers is not None else
                    ["gemini","groq","openrouter","ollama"])
    if budget_active() and providers is None:
        # In a time-bounded Autopilot run, do not try three slow dead APIs
        # for each generation attempt. Honor whichever two are configured.
        candidate_list=[p for p in candidate_list if
                       (p=="ollama" and os.getenv("ENABLE_OLLAMA")=="1")
                       or (p!="ollama" and get_keys(p))][:2]
    if not candidate_list:
        raise ProviderError("Chưa có provider AI khả dụng. Kiểm tra key AI trong mục API & Model.",retryable=False)
    for provider in candidate_list:
        check_budget()
        try:return provider,generate(provider,topic,level,count,recent,fmt)
        except (ProviderError,ValueError) as exc:errors.append((provider,exc))
    raise ProviderError("All providers unavailable: "+" | ".join(p+': '+str(exc) for p,exc in errors),
        retryable=any(getattr(exc,'retryable',True) for _,exc in errors),
        retry_after=max((getattr(exc,'retry_after',0) for _,exc in errors),default=0))
