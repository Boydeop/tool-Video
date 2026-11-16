"""Facebook Login code flow; Page tokens remain on the local server."""
import json
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request
from .facebook import _version, connect, disconnect
from .local_storage import load_private,save_private

SCOPES='pages_show_list,pages_read_engagement,pages_manage_posts'


def graph(path, values, token='', method='GET'):
    url='https://graph.facebook.com/'+_version()+'/'+path
    data=None
    if method=='POST':data=urllib.parse.urlencode(values).encode()
    elif values:url+='?'+urllib.parse.urlencode(values)
    req=urllib.request.Request(url,data=data,method=method,
        headers={'Authorization':'Bearer '+token} if token else {})
    try:
        with urllib.request.urlopen(req,timeout=30) as response:return json.load(response)
    except Exception:
        # Never expose codes, client secrets, user tokens or provider responses.
        raise ValueError('Facebook OAuth không thành công. Kiểm tra cấu hình Meta App, quyền và thử lại.') from None


class FacebookOAuth:
    def __init__(self,root):
        self.path=root/'facebook_oauth_client.local.json'
        self.lock=threading.RLock()
        self.pending=None
        self.pages={}
        self.expires=0

    def configure(self,app_id,app_secret):
        if not re.fullmatch(r'\d{5,40}',app_id) or not 16<=len(app_secret)<=512:
            raise ValueError('Meta App ID / App Secret không hợp lệ.')
        with self.lock:
            save_private(self.path,{'app_id':app_id,'app_secret':app_secret})
            self.pending=None;self.pages={}
        return {'configured':True}

    def start(self,redirect):
        with self.lock:
            client=load_private(self.path)
            if not client:raise ValueError('Lưu Meta App ID và App Secret trước.')
            state=secrets.token_urlsafe(32)
            self.pending=(state,time.monotonic()+600,redirect)
            self.pages={}
            return {'authorization_url':'https://www.facebook.com/'+_version()+'/dialog/oauth?'+
                urllib.parse.urlencode({'client_id':client['app_id'],'redirect_uri':redirect,
                  'state':state,'scope':SCOPES,'response_type':'code'})}

    def callback(self,params):
        with self.lock:
            pending=self.pending
            if (not pending or time.monotonic()>pending[1] or
                not secrets.compare_digest(params.get('state',[''])[0],pending[0])):
                raise ValueError('Phiên Facebook OAuth không hợp lệ hoặc hết hạn. Kết nối lại.')
            self.pending=None  # One use, including denial and failed exchanges.
            code=params.get('code',[''])[0]
            if params.get('error') or not code or len(code)>4096:
                raise ValueError('Bạn chưa cấp quyền Facebook. Kết nối lại khi sẵn sàng.')
            client=load_private(self.path)
            response=graph('oauth/access_token',dict(client_id=client['app_id'],client_secret=client['app_secret'],code=code,redirect_uri=pending[2]),method='POST')
            token=response.get('access_token')
            if not token:raise ValueError('Meta chưa cấp access token.')
            # Request only the Page tokens needed for the explicit selection.
            pages={}
            after=None
            for _ in range(20):
                values={'fields':'id,name,access_token','limit':100}
                if after:values['after']=after
                result=graph('me/accounts',values,token)
                for page in result.get('data',[]):
                    if page.get('access_token') and page.get('id'):
                        pages[str(page['id'])]=page
                paging=result.get('paging',{})
                if not paging.get('next'):break
                after=paging.get('cursors',{}).get('after')
                if not after:raise ValueError('Không đọc được trang tiếp theo từ Meta.')
            else:raise ValueError('Danh sách Page quá lớn; dùng Page token thủ công.')
            self.pages=pages;self.expires=time.monotonic()+600
            return self.list_pages()

    def list_pages(self):
        with self.lock:
            if time.monotonic()>self.expires:self.pages={}
            return {'pages':[{'id':p['id'],'name':p.get('name',p['id'])} for p in self.pages.values()]}

    def select(self,page_id):
        with self.lock:
            self.list_pages()
            page=self.pages.get(page_id)
            if not page:raise ValueError('Chọn Page trong phiên OAuth còn hiệu lực.')
            result=connect(page_id,page['access_token'])
            self.pages={}
            return result

    def disconnect(self):
        with self.lock:
            self.pending=None;self.pages={}
            return disconnect()
