from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.request_log_service import RequestLogStore, save_request_log_preview


class RequestLogStoreTests(unittest.TestCase):
    def test_append_keeps_only_recent_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RequestLogStore(Path(temp_dir) / "request_logs.jsonl", max_entries=100)

            for index in range(105):
                store.append(
                    {
                        "request_id": f"req-{index}",
                        "created_at": f"2026-04-24T00:00:{index:02d}Z",
                        "method": "POST",
                        "endpoint": "/v1/images/generations",
                        "model": "gpt-image-2",
                        "success": True,
                        "status_code": 200,
                        "duration_ms": 123,
                        "request": {"prompt_preview": f"prompt-{index}"},
                        "response": {"image_count": 1},
                    }
                )

            result = store.list(page=1, page_size=200)
            self.assertEqual(result.total, 100)
            self.assertEqual(len(result.items), 100)
            self.assertEqual(result.items[0]["request_id"], "req-104")
            self.assertEqual(result.items[-1]["request_id"], "req-5")

    def test_append_truncates_large_summary_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = RequestLogStore(Path(temp_dir) / "request_logs.jsonl", max_entries=100)
            large_text = "x" * 1200

            store.append(
                {
                    "request_id": "req-1",
                    "created_at": "2026-04-24T00:00:00Z",
                    "method": "POST",
                    "endpoint": "/v1/chat/completions",
                    "model": "gpt-image-2",
                    "success": False,
                    "status_code": 500,
                    "duration_ms": 321,
                    "error": large_text,
                    "request": {"prompt_preview": large_text},
                    "response": {"details": large_text},
                }
            )

            result = store.list(page=1, page_size=10)
            item = result.items[0]
            self.assertTrue(str(item["error"]).endswith("..."))
            self.assertLess(len(str(item["error"])), len(large_text))
            self.assertTrue(str(item["request"]["prompt_preview"]).endswith("..."))
            self.assertTrue(str(item["response"]["details"]).endswith("..."))

    def test_append_removes_preview_files_of_trimmed_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            preview_root = temp_root / "images" / "request-logs"
            preview_root.mkdir(parents=True, exist_ok=True)
            preview_file = preview_root / "2026" / "04" / "25" / "preview.webp"
            preview_file.parent.mkdir(parents=True, exist_ok=True)
            preview_file.write_bytes(b"preview")

            store = RequestLogStore(
                temp_root / "request_logs.jsonl",
                max_entries=100,
                preview_root=preview_root,
            )

            store.append(
                {
                    "request_id": "req-0",
                    "created_at": "2026-04-25T00:00:00Z",
                    "method": "POST",
                    "endpoint": "/v1/images/generations",
                    "model": "gpt-image-2",
                    "success": True,
                    "status_code": 200,
                    "duration_ms": 100,
                    "request": {"prompt_preview": "prompt-0"},
                    "response": {
                        "image_count": 1,
                        "preview_urls": ["/images/request-logs/2026/04/25/preview.webp"],
                    },
                }
            )

            for index in range(1, 101):
                store.append(
                    {
                        "request_id": f"req-{index}",
                        "created_at": f"2026-04-25T00:00:{index:02d}Z",
                        "method": "POST",
                        "endpoint": "/v1/images/generations",
                        "model": "gpt-image-2",
                        "success": True,
                        "status_code": 200,
                        "duration_ms": 100,
                        "request": {"prompt_preview": f"prompt-{index}"},
                        "response": {"image_count": 1},
                    }
                )

            self.assertFalse(preview_file.exists())

    def test_save_request_log_preview_creates_image_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            preview_root = Path(temp_dir) / "images" / "request-logs"
            preview_root.mkdir(parents=True, exist_ok=True)
            png_data = (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
                b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
                b"\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82"
            )

            preview_url = save_request_log_preview(
                png_data,
                base_url="http://example.com",
                preview_root=preview_root,
            )

            self.assertIsNotNone(preview_url)
            self.assertTrue(str(preview_url).startswith("http://example.com/images/request-logs/"))
            saved_files = list(preview_root.rglob("*"))
            self.assertTrue(any(path.is_file() for path in saved_files))


if __name__ == "__main__":
    unittest.main()
