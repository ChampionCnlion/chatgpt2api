from __future__ import annotations

import base64
import unittest
from unittest.mock import patch

from api.ai import _chat_response_summary, _collect_preview_urls_from_content, _image_response_summary


class AIPreviewSummaryTests(unittest.TestCase):
    def test_chat_response_summary_collects_markdown_data_url_preview(self):
        image_b64 = base64.b64encode(b"preview-image-bytes").decode("utf-8")
        result = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": f"![image](data:image/png;base64,{image_b64})",
                    }
                }
            ]
        }

        with patch("api.ai.save_request_log_preview", return_value="https://example.com/images/request-logs/a.webp") as mocked_save:
            summary = _chat_response_summary(result, base_url="https://example.com")

        self.assertEqual(summary.get("choice_count"), 1)
        self.assertEqual(summary.get("preview_urls"), ["https://example.com/images/request-logs/a.webp"])
        mocked_save.assert_called_once()

    def test_chat_response_summary_collects_response_output_preview(self):
        image_b64 = base64.b64encode(b"preview-image-bytes").decode("utf-8")
        result = {
            "output": [
                {
                    "type": "image_generation_call",
                    "result": image_b64,
                }
            ]
        }

        with patch("api.ai.save_request_log_preview", return_value="https://example.com/images/request-logs/b.webp") as mocked_save:
            summary = _chat_response_summary(result, base_url="https://example.com")

        self.assertEqual(summary.get("output_count"), 1)
        self.assertEqual(summary.get("preview_urls"), ["https://example.com/images/request-logs/b.webp"])
        mocked_save.assert_called_once()

    def test_image_response_summary_preserves_image_urls(self):
        summary = _image_response_summary(
            {
                "created": 123,
                "data": [
                    {"url": "https://example.com/images/a.png"},
                ],
            }
        )

        self.assertEqual(summary.get("image_count"), 1)
        self.assertEqual(summary.get("preview_urls"), ["https://example.com/images/a.png"])

    def test_chat_response_summary_normalizes_internal_preview_url_to_relative_path(self):
        image_b64 = base64.b64encode(b"preview-image-bytes").decode("utf-8")
        result = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": f"![image](data:image/png;base64,{image_b64})",
                    }
                }
            ]
        }

        with patch(
            "api.ai.save_request_log_preview",
            return_value="http://chatgpt2api/images/request-logs/2026/04/25/example.webp",
        ):
            summary = _chat_response_summary(result, base_url="http://chatgpt2api")

        self.assertEqual(
            summary.get("preview_urls"),
            ["/images/request-logs/2026/04/25/example.webp"],
        )

    def test_collect_preview_urls_from_stream_chat_chunk(self):
        image_b64 = base64.b64encode(b"preview-image-bytes").decode("utf-8")
        chunk = {
            "choices": [
                {
                    "delta": {
                        "content": f"![image](data:image/png;base64,{image_b64})",
                    }
                }
            ]
        }

        with patch("api.ai.save_request_log_preview", return_value="https://example.com/images/request-logs/c.webp") as mocked_save:
            preview_urls = _collect_preview_urls_from_content(chunk, base_url="https://example.com")

        self.assertEqual(preview_urls, ["https://example.com/images/request-logs/c.webp"])
        mocked_save.assert_called_once()

    def test_collect_preview_urls_from_stream_response_event(self):
        image_b64 = base64.b64encode(b"preview-image-bytes").decode("utf-8")
        event = {
            "type": "response.output_item.done",
            "item": {
                "type": "image_generation_call",
                "result": image_b64,
            },
        }

        with patch("api.ai.save_request_log_preview", return_value="https://example.com/images/request-logs/d.webp") as mocked_save:
            preview_urls = _collect_preview_urls_from_content(event, base_url="https://example.com")

        self.assertEqual(preview_urls, ["https://example.com/images/request-logs/d.webp"])
        mocked_save.assert_called_once()


if __name__ == "__main__":
    unittest.main()
