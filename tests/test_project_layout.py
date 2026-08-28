import unittest
from pathlib import Path

from webui.scripts import SCRIPTS


ROOT = Path(__file__).resolve().parents[1]


class ProjectLayoutTests(unittest.TestCase):
    def test_every_webui_task_points_to_an_existing_file(self):
        missing = [item["file"] for item in SCRIPTS if not (ROOT / item["file"]).is_file()]
        self.assertEqual(missing, [])

    def test_maintenance_commands_live_under_tools(self):
        names = {
            "export_accounts.py",
            "export_chatgpt2api.py",
            "extract_graph_tokens.py",
            "upload_tokens.py",
            "upgrade_claude_max.py",
            "validate_keys.py",
        }
        for name in names:
            self.assertTrue((ROOT / "tools" / name).is_file())
            self.assertFalse((ROOT / name).exists())


if __name__ == "__main__":
    unittest.main()
