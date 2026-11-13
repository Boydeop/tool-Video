"""Meta Page verification is mocked, no real token, publish or network request."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from studio import facebook,seo
from studio.quiz import Quiz

class FakeHTTPResponse:
    def __init__(self,payload):
        self.payload=json.dumps(payload).encode()
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,*args):return self.payload

def example():
    return Quiz(topic="Gia đình",question="Mẹ trong tiếng Hàn là gì?",
      correct="어머니",wrong1="아버지",wrong2="언니",wrong3="동생")

class FacebookSEOTests(unittest.TestCase):
    def test_connect_validates_page_and_never_returns_token(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ,{"THSE_FACEBOOK_PATH":str(Path(td)/"page.json")}):
                with patch("studio.facebook.urllib.request.urlopen",
                           return_value=FakeHTTPResponse({"id":"123456","name":"Tiếng Hàn So Easy"})):
                    result=facebook.connect("123456","FAKE_PAGE_ACCESS_TOKEN_LONG_ENOUGH")
                self.assertTrue(result["connected"])
                self.assertTrue(result["verified_read"])
                self.assertFalse(result["publishing_verified"])
                self.assertNotIn("FAKE_PAGE_ACCESS_TOKEN",json.dumps(result))
                self.assertEqual(facebook.credentials()[0],"123456")
                self.assertEqual(facebook.credentials()[1],"FAKE_PAGE_ACCESS_TOKEN_LONG_ENOUGH")
                self.assertFalse(facebook.disconnect()["connected"])

    def test_reject_page_mismatch(self):
        with patch("studio.facebook.urllib.request.urlopen",
                   return_value=FakeHTTPResponse({"id":"99999","name":"Other Page"})):
            with self.assertRaises(facebook.PageError):
                facebook.verify_page("12345","FAKE_PAGE_ACCESS_TOKEN_LONG_ENOUGH")

    def test_ai_seo_draft_is_editable_and_grounded(self):
        data={"title":"6 từ vựng tiếng Hàn về gia đình dễ nhớ",
              "description":"Bạn có biết từ mẹ trong tiếng Hàn nói thế nào? Cùng thử sức và học nhanh với Tiếng Hàn So Easy nhé!",
              "hashtags":["#TiengHanSoEasy","#TOPIK1"]}
        response={"candidates":[{"content":{"parts":[{"text":json.dumps(data,ensure_ascii=False)}]}}]}
        with patch("studio.seo.next_key",return_value="fake"),\
             patch("studio.seo.get_model",return_value="mock-model"),\
             patch("studio.seo.post",return_value=response) as call:
            result=seo.draft([example()])
        self.assertEqual(result["provider"],"gemini")
        self.assertIn("TOPIK1",result["caption"])
        self.assertTrue(result["requires_review"])
        self.assertNotIn("fake",json.dumps(result))
        self.assertIn("어머니",json.dumps(call.call_args.args[2],ensure_ascii=False))

if __name__=="__main__":unittest.main()
