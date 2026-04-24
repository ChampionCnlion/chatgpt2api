from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.request_log_service import RequestLogStore


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


if __name__ == "__main__":
    unittest.main()
