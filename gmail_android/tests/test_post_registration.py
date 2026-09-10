import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT))

import bluestacks
import gmail_register_local as gmail
import sms_provider


class StateTests(unittest.TestCase):
    def test_google_curly_apostrophes_are_normalized(self):
        text = gmail.normalized_text("Verify it\u2019s you; confirm you\u2019re not a robot")
        self.assertIn("verify it's you", text)
        self.assertIn("confirm you're not a robot", text)

    def test_resume_registration_prioritizes_phone_entry_over_security_handoff(self):
        texts = [
            "Confirm you're not a robot",
            "Get a verification code sent to your phone",
            "Phone number",
        ]
        with patch.object(gmail, "dump_state", return_value=texts), patch.object(
            gmail, "auto_phone_verification", return_value="gmail_opened"
        ) as phone_mock:
            result = gmail.resume_registration_flow(
                unittest.mock.Mock(),
                accept_terms=True,
                auto_phone=True,
                lease_holder=[],
            )

        self.assertEqual("gmail_opened", result)
        phone_mock.assert_called_once()

    def test_pending_account_round_trip_keeps_sms_cursor(self):
        account = gmail.Account("sample", "Pass1234!", "A", "B", "May", 5, 1990)
        phone = sms_provider.SmsNumber(
            phone="5551234",
            country_code="1",
            activation_id="smsman_abc",
            provider="smsman",
            can_receive_multiple=True,
            last_code="111111",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "pending.json"
            with patch.object(gmail, "STATE_PATH", state_path):
                gmail.save_account_state(account, "second_login", phone)
                restored_account, restored_phone, stage = gmail.load_account_state()

        self.assertEqual(account, restored_account)
        self.assertEqual("second_login", stage)
        self.assertEqual("smsman_abc", restored_phone.activation_id)
        self.assertEqual("111111", restored_phone.last_code)
        self.assertTrue(restored_phone.can_receive_multiple)


class SmsProviderTests(unittest.TestCase):
    @patch.object(sms_provider, "_request_smsman_number")
    @patch.object(sms_provider, "_request_firefox_number")
    def test_request_number_prefers_firefox(self, firefox_mock, smsman_mock):
        expected = sms_provider.SmsNumber("5551234", "1", "firefox-id", "firefox")
        firefox_mock.return_value = expected
        with patch.object(sms_provider, "SMS_TOKEN", "token"), patch.object(
            sms_provider, "SMS_PROJECT_ID_GMAIL", "project"
        ), patch.object(sms_provider, "SMSMAN_TOKEN", "smsman-token"), patch.object(
            sms_provider, "SMSMAN_APP_ID_GMAIL", "google"
        ):
            actual = sms_provider.request_number()

        self.assertIs(expected, actual)
        smsman_mock.assert_not_called()

    @patch.object(sms_provider.time, "sleep")
    @patch.object(sms_provider, "_smsman_get")
    def test_smsman_waits_for_code_different_from_previous(self, get_mock, _sleep_mock):
        get_mock.side_effect = [
            {"sms_code": "G-111111"},
            {"sms_code": "G-222222"},
        ]

        code = sms_provider._smsman_get_code(
            "smsman_request",
            max_wait=30,
            interval=0,
            since="111111",
        )

        self.assertEqual("222222", code)
        self.assertEqual(2, get_mock.call_count)

    def test_phone_full_number_joins_country_code_once(self):
        phone = sms_provider.SmsNumber("5551234", "1", "id", "firefox")
        joined = sms_provider.SmsNumber("+15551234", "", "id", "hero")
        self.assertEqual("+15551234", gmail.phone_full_number(phone))
        self.assertEqual("+15551234", gmail.phone_full_number(joined))

    def test_post_code_phone_rejection_includes_too_many(self):
        self.assertIn("too many", gmail.PHONE_REJECT_NEEDLES)
        self.assertIn("Privacy and Terms", gmail.POST_PHONE_SUCCESS_NEEDLES)


class BlueStacksTests(unittest.TestCase):
    def test_second_login_advances_phone_verification_intro(self):
        class Driver:
            current_package = "com.google.android.gms"
            current_activity = "MinuteMaidActivity"

        states = [
            ["Verify it's you"],
            ["Verify your phone number", "Continue"],
            ["Couldn't sign you in"],
        ]
        account = gmail.Account("sample", "Pass1234!", "A", "B", "May", 5, 1990)
        with patch.object(gmail.time, "sleep"), patch.object(
            gmail, "proceed_gmail_onboarding"
        ), patch.object(gmail, "dump_state", side_effect=states), patch.object(
            gmail, "click_first_text", return_value=True
        ) as click_mock, patch.object(bluestacks, "account_names", return_value=[]):
            result = gmail.second_login_flow(Driver(), account, 5735, phone=object())

        self.assertEqual("second_login_failed", result)
        self.assertTrue(
            any(call.args[1:] == (("Continue",),) for call in click_mock.call_args_list)
        )

    def test_second_login_keeps_in_progress_gms_activity(self):
        class Driver:
            current_package = "com.google.android.gms"
            current_activity = "MinuteMaidActivity"

            def activate_app(self, _package):
                raise AssertionError("Gmail must not be activated over an in-progress GMS challenge")

        with patch.object(gmail.time, "sleep"), patch.object(
            gmail, "proceed_gmail_onboarding"
        ), patch.object(gmail, "wait_until_any", return_value=[]), patch.object(
            gmail, "dump_state", return_value=[]
        ), patch.object(bluestacks, "account_names", return_value=[]):
            result = gmail.second_login_flow(
                Driver(),
                gmail.Account("sample", "Pass1234!", "A", "B", "May", 5, 1990),
                5735,
            )
        self.assertEqual("second_login_manual", result)

    def test_gmail_shell_without_android_account_is_not_success(self):
        class Driver:
            current_package = "com.google.android.gm"
            current_activity = "ConversationListActivityGmail"

            def activate_app(self, _package):
                return None

        account = gmail.Account("sample", "Pass1234!", "A", "B", "May", 5, 1990)
        with patch.object(gmail.time, "sleep"), patch.object(
            gmail, "proceed_gmail_onboarding"
        ), patch.object(gmail, "dump_state", return_value=["Compose", "Search in mail"]), patch.object(
            gmail, "wait_until_any", return_value=["Compose", "Search in mail"]
        ), patch.object(bluestacks, "account_names", return_value=[]):
            result = gmail.second_login_flow(Driver(), account, 5735)

        self.assertEqual("second_login_manual", result)

    def test_account_names_parse_dumpsys_rows(self):
        rows = [
            "Account {name=one@gmail.com, type=com.google}",
            "Account {name=two@gmail.com, type=com.google}",
            "Account {name=one@gmail.com, type=com.google}",
        ]
        with patch.object(bluestacks, "_list_accounts", return_value=rows):
            self.assertEqual(
                ["one@gmail.com", "two@gmail.com"],
                bluestacks.account_names(5675),
            )

    @patch.object(gmail, "adb_run", return_value=(True, "Starting: Intent"))
    def test_second_login_adb_launch_targets_gmail_activity(self, adb_mock):
        class Driver:
            current_package = "com.uncube.launcher3"
            current_activity = ""

            def activate_app(self, _package):
                return None

        with patch.object(gmail.time, "sleep"), patch.object(
            gmail, "proceed_gmail_onboarding"
        ), patch.object(gmail, "wait_until_any", return_value=[]), patch.object(
            gmail, "dump_state", return_value=[]
        ), patch.object(gmail, "visible_texts", return_value=[]), patch.object(
            gmail.bluestacks if hasattr(gmail, "bluestacks") else bluestacks,
            "account_names",
            return_value=[],
        ):
            result = gmail.second_login_flow(
                Driver(),
                gmail.Account("sample", "Pass1234!", "A", "B", "May", 5, 1990),
                5735,
            )
        self.assertEqual("second_login_manual", result)
        self.assertTrue(any("ConversationListActivityGmail" in str(call) for call in adb_mock.call_args_list))


if __name__ == "__main__":
    unittest.main()
