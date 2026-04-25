from __future__ import annotations

import unittest

from fastapi import HTTPException

from utils.helper import (
    build_chat_image_markdown_content,
    extract_response_image_options,
    normalize_image_options,
)


class ImageOptionsTests(unittest.TestCase):
    def test_normalize_image_options_maps_legacy_size_alias(self):
        options = normalize_image_options({"size": "16:9"})

        self.assertEqual(options.size, "1536x1024")

    def test_normalize_image_options_rejects_transparent_jpeg(self):
        with self.assertRaises(HTTPException) as ctx:
            normalize_image_options({"background": "transparent", "output_format": "jpeg"})

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("transparent background", str(ctx.exception.detail))

    def test_build_chat_image_markdown_content_uses_item_mime_type(self):
        content = build_chat_image_markdown_content(
            {
                "data": [
                    {
                        "b64_json": "aGVsbG8=",
                        "mime_type": "image/webp",
                    }
                ]
            }
        )

        self.assertIn("data:image/webp;base64,aGVsbG8=", content)

    def test_extract_response_image_options_reads_image_generation_tool(self):
        options = extract_response_image_options(
            {
                "model": "gpt-image-2",
                "tools": [
                    {
                        "type": "image_generation",
                        "size": "9:16",
                        "quality": "high",
                        "output_format": "webp",
                        "output_compression": 80,
                    }
                ],
            }
        )

        self.assertEqual(options.size, "1024x1536")
        self.assertEqual(options.quality, "high")
        self.assertEqual(options.output_format, "webp")
        self.assertEqual(options.output_compression, 80)


if __name__ == "__main__":
    unittest.main()
