import unittest
from unittest.mock import patch

from tools import upload_tokens


class UploadGrokTokensTests(unittest.TestCase):
    def _run_upload(self, force_sub2api):
        config = {
            "SUB2API_URL": "https://sub.example.com",
            "SUB2API_EMAIL": "admin@example.com",
            "SUB2API_PASSWORD": "secret",
            "SUB2API_GROK_GROUP": "grok",
            "SUB2API_GROK_PROXY_ID": 12,
            "WEBCHAT2API_URL": "",
            "WEBCHAT2API_KEY": "",
        }
        with patch.multiple(upload_tokens, **config), patch.object(
            upload_tokens.glob,
            "glob",
            return_value=["user.sso.json"],
        ), patch.object(
            upload_tokens,
            "_read_json",
            return_value={"email": "user@example.com", "sso": "sso-token"},
        ), patch.object(
            upload_tokens,
            "uploaded_set",
            return_value={"user@example.com"},
        ), patch.object(
            upload_tokens.uploaders,
            "upload_sub2api_grok",
            return_value=(True, "fixed"),
        ) as upload, patch.object(upload_tokens, "mark_uploaded") as mark:
            upload_tokens.upload_grok(force_sub2api=force_sub2api)
        return upload, mark

    def test_skips_account_already_marked_uploaded(self):
        upload, mark = self._run_upload(force_sub2api=False)

        upload.assert_not_called()
        mark.assert_not_called()

    def test_force_reimports_account_already_marked_uploaded(self):
        upload, mark = self._run_upload(force_sub2api=True)

        upload.assert_called_once()
        self.assertEqual(upload.call_args.kwargs["account_email"], "user@example.com")
        self.assertEqual(upload.call_args.kwargs["proxy_id"], 12)
        mark.assert_called_once_with("grok", "sub2api", "user@example.com")


if __name__ == "__main__":
    unittest.main()
