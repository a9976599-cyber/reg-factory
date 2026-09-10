import unittest
from pathlib import Path

from webui import scripts as schema


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / ".env.example"


def _example_keys():
    keys = []
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        keys.append(stripped.split("=", 1)[0].strip())
    return keys


class EnvCatalogTests(unittest.TestCase):
    def test_template_has_no_duplicate_keys(self):
        keys = _example_keys()
        self.assertGreater(len(keys), 200, ".env.example 看起来被截断了")
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        self.assertEqual(
            duplicates,
            [],
            ".env.example 里出现重复键：后一处会静默覆盖前一处，用户改的值可能不生效",
        )

    def test_every_webui_editable_key_is_declared_in_the_template(self):
        """WebUI「环境配置」页能保存的键，模板里必须有一行默认值。

        反方向不做要求：任务脚本 / 代理面板专用的键（APPIUM_*、CUSTOM_MAIL_*、
        REG_FACTORY_PLUS_* 等）故意不进界面 schema，它们只供手改 .env 的用户使用。
        """
        declared = set(_example_keys())
        missing = sorted(set(schema.env_keys()) - declared)
        self.assertEqual(
            missing,
            [],
            "以下键在 WebUI 里可编辑，但 .env.example 没有定义（用户看不到也没有默认值）: %s"
            % missing,
        )
