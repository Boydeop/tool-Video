"""Offline tests for model discovery, persistence and real generation routing."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from studio import ai
from studio.models import (
    ModelError, get_model, list_models, selected_models,
    set_model, test_model as verify_model, validate_model
)

class ModelsTests(unittest.TestCase):
    def test_saved_model_survives_reload_and_is_not_a_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"THSE_MODELS_FILE": str(Path(tmp)/"models.json"),
                                        "GEMINI_MODEL": "gemini-test-default"}):
                self.assertEqual(get_model("gemini"), "gemini-test-default")
                self.assertEqual(set_model("gemini", "gemini-3.5-flash-lite"),
                                 {"provider":"gemini","model":"gemini-3.5-flash-lite"})
                self.assertEqual(get_model("gemini"), "gemini-3.5-flash-lite")
                self.assertEqual(selected_models()["gemini"],"gemini-3.5-flash-lite")
                raw=(Path(tmp)/"models.json").read_text(encoding="utf-8")
                self.assertNotIn("api_key",raw)
                self.assertIn("gemini-3.5-flash-lite",raw)

    def test_reject_invalid_model_or_provider(self):
        for model in ("", "https://example.com", "../etc", "has space", "x"*200):
            with self.assertRaises(ValueError):validate_model("gemini",model)
        with self.assertRaises(ValueError):validate_model("fake","abc")

    def test_gemini_list_filters_non_generation_models(self):
        response={"models":[
            {"name":"models/gemini-3.5-flash-lite",
             "displayName":"Gemini Flash Lite",
             "supportedGenerationMethods":["generateContent"]},
            {"name":"models/gemini-tts","supportedGenerationMethods":["generateContent"]},
            {"name":"models/embedding-001","supportedGenerationMethods":["embedContent"]}
        ]}
        with patch("studio.models.get_keys",return_value=["fake-gemini-key"]), \
             patch("studio.models._get_json",return_value=response) as request:
            data=list_models("gemini")
            self.assertEqual([m["id"] for m in data["models"]],["gemini-3.5-flash-lite"])
            self.assertEqual(data["key_position"],1)
            self.assertIn("v1beta/models",request.call_args.args[1])
            self.assertNotIn("fake-gemini-key",json.dumps(data))

    def test_openrouter_prices_sort_free_first(self):
        response={"data":[
            {"id":"vendor/paid","name":"Paid","pricing":{"prompt":"0.1","completion":"0.2"}},
            {"id":"vendor/free:free","name":"Free","pricing":{"prompt":"0","completion":"0"},
             "architecture":{"input_modalities":["text"],"output_modalities":["text"]}}
        ]}
        with patch("studio.models.get_keys",return_value=["fake"]), \
             patch("studio.models._get_json",return_value=response):
            found=list_models("openrouter")["models"]
            self.assertEqual(found[0]["id"],"vendor/free:free")
            self.assertTrue(found[0]["free"])

    def test_can_try_next_key_for_401_but_not_for_429(self):
        from studio.models import ModelError
        error=ModelError("HTTP 401: no permission")
        response={"data":[{"id":"llama-3.1-8b-instant","active":True}]}
        with patch("studio.models.get_keys",return_value=["invalid","valid"]),\
             patch("studio.models._get_json",side_effect=[error,response]):
            self.assertEqual(list_models("groq")["key_position"],2)
        with patch("studio.models.get_keys",return_value=["first","second"]),\
             patch("studio.models._get_json",side_effect=ModelError("HTTP 429: cooldown")) as get:
            with self.assertRaises(ModelError):list_models("groq")
            self.assertEqual(get.call_count,1)

    def test_model_generation_uses_saved_id(self):
        row={"topic":"Gia đình","question":"Mẹ","correct":"어머니",
             "wrong1":"아버지","wrong2":"언니","wrong3":"동생","level":"TOPIK 1"}
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ,{"THSE_MODELS_FILE":str(Path(tmp)/"models.json")}):
                set_model("gemini","gemini-model-selected")
                with patch("studio.ai.next_key",return_value="test-owned-key"),\
                     patch("studio.ai.post",return_value={
                         "candidates":[{"content":{"parts":[{"text":json.dumps([row])}]}}]
                     }) as call:
                    result=ai.generate("gemini","Gia đình","TOPIK 1",1,[])
                    self.assertEqual(result[0].correct,"어머니")
                    self.assertIn("gemini-model-selected:generateContent",call.call_args.args[1])

    def test_model_test_checks_selected_key_position(self):
        with patch("studio.models.get_keys",return_value=["first","second"]),\
             patch("studio.models._read_json",return_value={"choices":[{"message":{"content":"OK"}}]}) as request:
            result=verify_model("groq","llama-3.1-8b-instant",2)
            self.assertEqual(result["key_position"],2)
            self.assertIn("second",str(request.call_args.args[1].headers))
            self.assertNotIn("second",json.dumps(result))
            with self.assertRaises(ValueError):
                verify_model("groq","llama-3.1-8b-instant",3)

if __name__=="__main__":
    unittest.main()
