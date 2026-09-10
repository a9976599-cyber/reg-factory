import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common import asset_scanner
from common.outlook_recovery import load_scan_candidates, upsert_refresh_tokens


class OutlookRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(
            os.environ,
            {
                "REG_FACTORY_DATA_DIR": str(self.root),
                "REG_FACTORY_ENV_FILE": str(self.root / ".env"),
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def _write_cache(self, outcomes):
        items = []
        for index, (email, status, evidence) in enumerate(outcomes, start=1):
            items.append({
                "id": asset_scanner._stable_id("outlook", email, f"emails.txt:{index}"),
                "platform": "outlook",
                "kind": "mailbox",
                "email": email,
                "source": f"emails.txt:{index}",
                "status": status,
                "detail": status,
                "evidence": evidence,
            })
        path = self.root / "runtime" / "state" / "asset_pool_scan.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"items": items}), encoding="utf-8")

    def test_candidates_follow_scan_status_and_require_password(self):
        (self.root / "emails.txt").write_text(
            "expired@example.com----pw1----old-rt----client\n"
            "unlock@example.com----pw2----old-rt----client\n"
            "normal@example.com----pw3----good-rt----client\n"
            "missing@example.com----pw4\n"
            "unknown@example.com----pw5\n"
            "nopassword@example.com\n",
            encoding="utf-8",
        )
        self._write_cache([
            ("expired@example.com", "expired", "microsoft_oauth:invalid_grant"),
            ("unlock@example.com", "unlock", "microsoft_oauth:AADSTS50053"),
            ("normal@example.com", "normal", "microsoft_graph:200"),
            ("missing@example.com", "unknown", "local:missing_refresh_token"),
            ("unknown@example.com", "unknown", "network:timeout"),
            ("nopassword@example.com", "expired", "microsoft_oauth:invalid_grant"),
        ])

        unlock = load_scan_candidates(("unlock", "expired"))
        extraction = load_scan_candidates(("expired", "unknown"))

        self.assertEqual(
            {item["email"] for item in unlock},
            {"expired@example.com", "unlock@example.com"},
        )
        self.assertEqual(
            {item["email"] for item in extraction},
            {"expired@example.com", "missing@example.com"},
        )

    def test_recovered_token_updates_pool_errors_and_cached_status(self):
        (self.root / "emails.txt").write_text(
            "expired@example.com----old-password----old-rt----old-client----keep-extra\n",
            encoding="utf-8",
        )
        (self.root / "emails_error_claude.txt").write_text(
            "expired@example.com----old-password----service_abuse\n"
            "expired@example.com----old-password----captcha_failed\n",
            encoding="utf-8",
        )
        self._write_cache([
            ("expired@example.com", "expired", "microsoft_oauth:invalid_grant"),
        ])

        result = upsert_refresh_tokens([{
            "email": "expired@example.com",
            "password": "new-password",
            "refresh_token": "new-refresh-token",
            "client_id": "new-client-id",
        }])

        pool = (self.root / "emails.txt").read_text(encoding="utf-8")
        errors = (self.root / "emails_error_claude.txt").read_text(encoding="utf-8")
        report = asset_scanner.get_report()
        item = next(value for value in report["items"] if value["email"] == "expired@example.com")
        self.assertIn(
            "expired@example.com----new-password----new-refresh-token----new-client-id----keep-extra",
            pool,
        )
        self.assertNotIn("service_abuse", errors)
        self.assertIn("captcha_failed", errors)
        self.assertEqual(result, {"updated": 1, "appended": 0, "errors_cleared": 1})
        self.assertEqual(item["status"], "normal")
        self.assertEqual(item["evidence"], "recovery:refresh_token_updated")


if __name__ == "__main__":
    unittest.main()
