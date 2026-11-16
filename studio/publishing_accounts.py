"""Non-secret account bindings for durable publishing jobs.

Bind Facebook/Instagram by asset ID and YouTube by its OAuth grant. Replacing
an account must not silently redirect a previously scheduled publication.
"""
import hashlib
from pathlib import Path
from .facebook import credentials
from .instagram import InstagramAccount
from .youtube import YouTubeAccount


def fingerprint_account(platform,value):
    return hashlib.sha256((platform+":"+str(value)).encode()).hexdigest()


def account_key(root,platform):
    root=Path(root)
    if platform=='facebook':
        page,token=credentials(root/'facebook.local.json')
        value=page if token else ''
    elif platform=='instagram':
        value=InstagramAccount(root).status().get('ig_id','')
    elif platform=='youtube':
        token=YouTubeAccount._load_private(root/'youtube_token.local.json') or {}
        value=token.get('refresh_token','')
    else:raise ValueError('Nền tảng không được hỗ trợ.')
    if not value:raise ValueError('Chưa kết nối '+platform+'.')
    return fingerprint_account(platform,value)


def check_account(root,platform,expected):
    if isinstance(expected,str) and expected and account_key(root,platform)!=expected:
        raise ValueError('Tài khoản '+platform+' đã đổi sau khi đặt lịch; không đăng sang tài khoản khác. Kiểm tra lịch và kết nối ban đầu.')
