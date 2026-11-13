import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from studio.keys import get_keys, next_key, write_keys, rate_limited, _BLOCKED_UNTIL

class KeyPoolTests(unittest.TestCase):
    def test_deduplicate_and_round_robin(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ,{"THSE_KEYS_FILE":str(Path(td)/"keys.json"),
                                      "GEMINI_API_KEY":"","GEMINI_API_KEYS":""}):
                self.assertEqual(write_keys("gemini","key_one, key_two,key_one"),2)
                self.assertEqual(get_keys("gemini"),["key_one","key_two"])
                self.assertEqual(next_key("gemini"),"key_one")
                self.assertEqual(next_key("gemini"),"key_two")
                self.assertEqual(next_key("gemini"),"key_one")
    def test_reject_unknown_provider(self):
        with self.assertRaises(ValueError):write_keys("invalid","ABC")
    def test_provider_wide_cooldown(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ,{"THSE_KEYS_FILE":str(Path(td)/"keys.json"),
                                      "GROQ_API_KEY":"","GROQ_API_KEYS":""}):
                write_keys("groq","key1,key2")
                rate_limited("groq",60)
                try:
                    with self.assertRaises(RuntimeError):next_key("groq")
                finally:
                    _BLOCKED_UNTIL.pop("groq",None)

if __name__=="__main__":unittest.main()
