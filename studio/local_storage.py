"""Atomic, private JSON credentials shared by local OAuth integrations."""
import json
import os
from pathlib import Path
import tempfile


def load_private(path):
    from .facebook import _unprotect
    path=Path(path)
    if not path.is_file():return None
    try:
        wrapper=json.loads(path.read_text('utf-8'))
        data=json.loads(_unprotect(wrapper['encrypted']))
        if not isinstance(data,dict):raise ValueError('Invalid object')
        return data
    except (ValueError,KeyError,TypeError):
        raise ValueError('Không đọc được cấu hình OAuth riêng tư. Sao lưu file trước khi kết nối lại.') from None


def save_private(path,content):
    from .facebook import _protect
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    payload=json.dumps({'encrypted':_protect(json.dumps(content,ensure_ascii=False))})
    # mkstemp creates a unique 0600 file before any secret bytes are written.
    fd,name=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)
