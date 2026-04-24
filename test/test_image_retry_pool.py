from __future__ import annotations

import unittest

from services.chatgpt_service import ChatGPTService, ImageGenerationError


class FakeAccountService:
    def __init__(self):
        self.tokens = ["token-1", "token-2"]
        self.marked: list[tuple[str, bool]] = []
        self.removed: list[str] = []

    def get_available_access_token(self, excluded_tokens: set[str] | None = None) -> str:
        excluded = excluded_tokens or set()
        for token in self.tokens:
            if token not in excluded:
                return token
        raise RuntimeError("no available image quota")

    def mark_image_result(self, access_token: str, success: bool) -> dict[str, object]:
        self.marked.append((access_token, success))
        return {"quota": 1, "status": "正常"}

    def remove_token(self, access_token: str) -> bool:
        self.removed.append(access_token)
        return True

    def list_tokens(self) -> list[str]:
        return list(self.tokens)


class RetryBackend:
    def __init__(self, access_token: str):
        self.access_token = access_token

    def images_generations(self, prompt: str, model: str, response_format: str = "b64_json"):
        if self.access_token == "token-1":
            raise RuntimeError("no downloadable image result found; conversation_id=test")
        return {
            "created": 1,
            "data": [{"b64_json": "aGVsbG8=", "revised_prompt": prompt}],
        }


class RetryStreamBackend:
    def __init__(self, access_token: str):
        self.access_token = access_token

    def stream_image_chat_completions(self, prompt: str, model: str, images=None):
        if self.access_token == "token-1":
            raise RuntimeError("no downloadable image result found; conversation_id=test")
        yield {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {"role": "assistant", "content": "![img](data:image/png;base64,aGVsbG8=)"},
                "finish_reason": None,
            }],
        }
        yield {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }],
        }


class AlwaysFailBackend:
    def __init__(self, access_token: str):
        self.access_token = access_token

    def images_generations(self, prompt: str, model: str, response_format: str = "b64_json"):
        raise RuntimeError("no downloadable image result found; conversation_id=test")


class ImageRetryPoolTests(unittest.TestCase):
    def test_generate_with_pool_retries_next_token(self):
        account_service = FakeAccountService()
        service = ChatGPTService(account_service)
        service._new_backend = staticmethod(lambda access_token="": RetryBackend(access_token))

        result = service.generate_with_pool("画一只猫", "gpt-image-2", 1)

        self.assertEqual(len(result["data"]), 1)
        self.assertEqual(account_service.marked, [("token-1", False), ("token-2", True)])

    def test_stream_image_chat_completion_retries_before_emit(self):
        account_service = FakeAccountService()
        service = ChatGPTService(account_service)
        service._new_backend = staticmethod(lambda access_token="": RetryStreamBackend(access_token))

        chunks = list(service._stream_image_chat_completion({
            "model": "gpt-image-2",
            "messages": [{"role": "user", "content": "画一只猫"}],
        }))

        self.assertEqual(len(chunks), 2)
        self.assertEqual(account_service.marked, [("token-1", False), ("token-2", True)])

    def test_generate_with_pool_raises_last_error_when_all_tokens_fail(self):
        account_service = FakeAccountService()
        service = ChatGPTService(account_service)
        service._new_backend = staticmethod(lambda access_token="": AlwaysFailBackend(access_token))

        with self.assertRaises(ImageGenerationError) as ctx:
            service.generate_with_pool("画一只猫", "gpt-image-2", 1)

        self.assertIn("no downloadable image result found", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
