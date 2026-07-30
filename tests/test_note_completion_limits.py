import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.note_builder import (
    DEEPSEEK_V4_CHAPTER_MAX_TOKENS,
    DEEPSEEK_V4_OVERVIEW_MAX_TOKENS,
    _call_chat,
    _chapter_text,
    _completion_truncation,
    _note_is_complete,
    _partial_note,
    _provider_generation_limits,
)


DEEPSEEK = {
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "test-key",
    "model": "deepseek-v4-pro",
}


class _FakeResponse:
    status_code = 200
    headers = {}

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "model": "deepseek-v4-pro",
            "choices": [{
                "message": {"content": "partial answer"},
                "finish_reason": "length",
            }],
            "usage": {
                "prompt_tokens": 12000,
                "completion_tokens": 32768,
                "total_tokens": 44768,
            },
        }

    def close(self):
        return None


class NoteCompletionLimitTests(unittest.TestCase):
    def test_deepseek_v4_uses_larger_safe_limits(self):
        limits = _provider_generation_limits(DEEPSEEK)
        self.assertEqual(
            limits["chapter_max_tokens"], DEEPSEEK_V4_CHAPTER_MAX_TOKENS,
        )
        self.assertEqual(
            limits["overview_max_tokens"], DEEPSEEK_V4_OVERVIEW_MAX_TOKENS,
        )
        self.assertEqual(limits["timeout"], 600)
        self.assertEqual(limits["concurrency"], 4)

    @patch("src.note_builder.requests.post", return_value=_FakeResponse())
    def test_chat_response_keeps_finish_reason_and_usage(self, post):
        content, metadata = _call_chat(
            DEEPSEEK,
            [{"role": "user", "content": "test"}],
            max_tokens=32768,
            include_metadata=True,
        )

        self.assertEqual(content, "partial answer")
        self.assertEqual(metadata["finish_reason"], "length")
        self.assertEqual(metadata["usage"]["completion_tokens"], 32768)
        self.assertEqual(post.call_args.kwargs["json"]["max_tokens"], 32768)
        self.assertTrue(_completion_truncation(metadata)[0])

    def test_partial_note_is_not_a_complete_cache(self):
        metadata = {
            "finish_reason": "length",
            "max_tokens": 32768,
            "usage": {"completion_tokens": 32768},
        }
        with tempfile.TemporaryDirectory() as temp:
            note = Path(temp) / "chapter.md"
            note.write_text(
                _partial_note("unfinished", "finish_reason=length", metadata),
                encoding="utf-8",
            )
            self.assertFalse(_note_is_complete(note))

    def test_input_truncation_reports_exact_omitted_characters(self):
        with tempfile.TemporaryDirectory() as temp:
            text_path = Path(temp) / "chapter.md"
            text_path.write_text("字" * 20, encoding="utf-8")
            warnings = []
            text = _chapter_text(
                {"title": "Long chapter", "text_path": str(text_path)},
                max_chars=12,
                truncation_warnings=warnings,
            )
            self.assertIn("已截取前部分", text)
            self.assertIn("省略 8 字符", warnings[0])


if __name__ == "__main__":
    unittest.main()
