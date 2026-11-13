"""TXT parsing tests use synthetic strings, never real credentials."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from studio.keys import get_keys, import_txt, parse_txt, MAX_TXT_BYTES

class ImportTxtTests(unittest.TestCase):
    def test_plain_file_uses_chosen_provider_and_deduplicates(self):
        parsed=parse_txt("\ufeff# My Gemini keys\nAIzaDEMO00001\nAIzaDEMO00002\nAIzaDEMO00001\n", "gemini")
        self.assertEqual(parsed, {"gemini":["AIzaDEMO00001","AIzaDEMO00002"]})

    def test_combined_sections_and_prefixed_lines(self):
        parsed=parse_txt("[gemini]\nAIzaDEMO00001\n[groq]\ngsk_DEMO00001\nopenrouter: sk-or-demo1234\n")
        self.assertEqual(parsed["gemini"],["AIzaDEMO00001"])
        self.assertEqual(parsed["groq"],["gsk_DEMO00001"])
        self.assertEqual(parsed["openrouter"],["sk-or-demo1234"])

    def test_invalid_input_rejected(self):
        with self.assertRaises(ValueError): parse_txt("[wrong_service]\nsecret123")
        with self.assertRaises(ValueError): parse_txt("GEMINI123 " * 8000)
        with self.assertRaises(ValueError): parse_txt("# no keys\n")

    def test_import_preserves_old_keys_and_makes_no_secret_response(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"keys.local.json"
            env={"THSE_KEYS_FILE":str(path),"GEMINI_API_KEYS":"",
                 "GEMINI_API_KEY":"","GROQ_API_KEYS":"","GROQ_API_KEY":"",
                 "OPENROUTER_API_KEYS":"","OPENROUTER_API_KEY":""}
            with patch.dict(os.environ,env):
                first=import_txt("AIzaDEMO00001\nAIzaDEMO00002","gemini")
                self.assertEqual(first["saved"]["gemini"],2)
                next_result=import_txt("[gemini]\nAIzaDEMO00001\n[groq]\ngsk_DEMO00001")
                self.assertEqual(get_keys("gemini"),["AIzaDEMO00001","AIzaDEMO00002"])
                self.assertEqual(get_keys("groq"),["gsk_DEMO00001"])
                self.assertEqual(next_result["saved"]["gemini"],2)
                self.assertNotIn("AIzaDEMO",json.dumps(next_result))
                self.assertTrue(path.is_file())
                # The file is local and excluded from Git.
                self.assertIn("gsk_DEMO00001",path.read_text(encoding="utf-8"))

if __name__ == "__main__": unittest.main()
