from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.feishu_client import FeishuError, credentials_available, export_feishu_docx, parse_feishu_doc_url


class FeishuClientTests(unittest.TestCase):
    def test_parse_docx_url(self) -> None:
        ref = parse_feishu_doc_url("https://example.feishu.cn/docx/AbCd12345")
        self.assertEqual(ref.doc_type, "docx")
        self.assertEqual(ref.token, "AbCd12345")

    def test_parse_rejects_non_feishu_url(self) -> None:
        with self.assertRaises(FeishuError):
            parse_feishu_doc_url("https://example.com/docx/AbCd12345")

    def test_credentials_available(self) -> None:
        with patch.dict(os.environ, {"FEISHU_APP_ID": "id", "FEISHU_APP_SECRET": "secret"}, clear=False):
            self.assertTrue(credentials_available())

    def test_export_requires_credentials(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(FeishuError) as ctx:
                export_feishu_docx("https://example.feishu.cn/docx/AbCd12345")
            self.assertIn("FEISHU_APP_ID", str(ctx.exception))

    def test_missing_credentials_is_a_setup_state(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(credentials_available())


if __name__ == "__main__":
    unittest.main()
