import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

curl_cffi_module = types.ModuleType("curl_cffi")
curl_cffi_requests_module = types.ModuleType("curl_cffi.requests")
curl_cffi_requests_module.Session = object
curl_cffi_module.requests = curl_cffi_requests_module
sys.modules.setdefault("curl_cffi", curl_cffi_module)
sys.modules.setdefault("curl_cffi.requests", curl_cffi_requests_module)

account_service_module = types.ModuleType("services.account_service")


class _DummyAccountService:
    def add_accounts(self, tokens):
        return {"added": len(tokens), "skipped": 0}

    def refresh_accounts(self, tokens):
        return {"refreshed": len(tokens)}


account_service_module.account_service = _DummyAccountService()
sys.modules.setdefault("services.account_service", account_service_module)

proxy_service_module = types.ModuleType("services.proxy_service")


class _DummyProxySettings:
    def build_session_kwargs(self, verify=True):
        return {}


proxy_service_module.proxy_settings = _DummyProxySettings()
sys.modules.setdefault("services.proxy_service", proxy_service_module)

from services.cpa_service import CPAConfig, CPAImportService, _is_remote_quota_exhausted_file


class TestCPAService(unittest.TestCase):
    def test_detects_usage_limit_reached_accounts(self) -> None:
        item = {
            "status": "error",
            "unavailable": True,
            "status_message": json.dumps(
                {
                    "error": {
                        "type": "usage_limit_reached",
                        "message": "The usage limit has been reached",
                    }
                }
            ),
        }

        self.assertTrue(_is_remote_quota_exhausted_file(item))

    def test_does_not_treat_401_as_quota_exhausted(self) -> None:
        item = {
            "status": "error",
            "unavailable": True,
            "status_code": 401,
            "status_message": "HTTP 401 unauthorized",
        }

        self.assertFalse(_is_remote_quota_exhausted_file(item))

    def test_start_recover_exhausted_uses_quota_matches_without_remote_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = CPAConfig(Path(temp_dir) / "cpa.json")
            service = CPAImportService(config)
            pool = config.add_pool(name="test", base_url="http://example.com", secret_key="secret")
            files = [
                {
                    "name": "quota-a.json",
                    "status": "error",
                    "unavailable": True,
                    "status_message": '{"error":{"type":"usage_limit_reached","message":"The usage limit has been reached"}}',
                },
                {
                    "name": "401-b.json",
                    "status": "error",
                    "unavailable": True,
                    "status_code": 401,
                    "status_message": "HTTP 401 unauthorized",
                },
            ]

            with (
                patch("services.cpa_service.list_remote_files", return_value=files),
                patch.object(service, "_start_job", return_value={"job_id": "job-1"}) as mocked_start_job,
            ):
                result = service.start_recover_exhausted(pool)

            self.assertEqual(result, {"job_id": "job-1"})
            mocked_start_job.assert_called_once_with(
                pool,
                ["quota-a.json"],
                job_field="recover_job",
                delete_remote_after_import=False,
            )

    def test_start_recover_exhausted_applies_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = CPAConfig(Path(temp_dir) / "cpa.json")
            service = CPAImportService(config)
            pool = config.add_pool(name="test", base_url="http://example.com", secret_key="secret")
            files = [
                {
                    "name": "quota-a.json",
                    "status": "error",
                    "unavailable": True,
                    "status_message": '{"error":{"type":"usage_limit_reached","message":"The usage limit has been reached"}}',
                },
                {
                    "name": "quota-b.json",
                    "status": "error",
                    "unavailable": True,
                    "status_message": '{"error":{"type":"usage_limit_reached","message":"The usage limit has been reached"}}',
                },
            ]

            with (
                patch("services.cpa_service.list_remote_files", return_value=files),
                patch.object(service, "_start_job", return_value={"job_id": "job-2"}) as mocked_start_job,
            ):
                result = service.start_recover_exhausted(pool, limit=1)

            self.assertEqual(result, {"job_id": "job-2"})
            mocked_start_job.assert_called_once_with(
                pool,
                ["quota-a.json"],
                job_field="recover_job",
                delete_remote_after_import=False,
            )

    def test_start_recover_exhausted_returns_empty_completed_job_when_no_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = CPAConfig(Path(temp_dir) / "cpa.json")
            service = CPAImportService(config)
            pool = config.add_pool(name="test", base_url="http://example.com", secret_key="secret")
            files = [
                {
                    "name": "ok-a.json",
                    "status": "disabled",
                    "unavailable": False,
                    "status_message": "",
                }
            ]

            with patch("services.cpa_service.list_remote_files", return_value=files):
                job = service.start_recover_exhausted(pool)

            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["total"], 0)
            self.assertEqual(job["deleted"], 0)
            saved_pool = config.get_pool(pool["id"])
            self.assertEqual(saved_pool["recover_job"]["total"], 0)


if __name__ == "__main__":
    unittest.main()
