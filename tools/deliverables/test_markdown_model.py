from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

from markdown_model import SourceError, _validate_sensitive_content


class SensitiveContentTest(unittest.TestCase):
    def test_rejects_qualifier_from_another_clause(self) -> None:
        with self.assertRaisesRegex(SourceError, "限定词必须与指标"):
            _validate_sensitive_content(Path("docs/guides/test.md"), "历史材料未经验证；当前系统准确率达到 98%。")

    def test_accepts_qualified_risk_in_same_clause(self) -> None:
        _validate_sensitive_content(Path("docs/guides/test.md"), "当前准确率 98% 未验证。")

    def test_allows_scoped_capability_name_but_not_current_claim(self) -> None:
        _validate_sensitive_content(Path("docs/guides/test.md"), "| SC-050 | 7×24 小时稳定运行 | 外部条件后重启 | 未验证 |")
        with self.assertRaisesRegex(SourceError, "限定词必须与指标"):
            _validate_sensitive_content(
                Path("docs/guides/test.md"),
                "| SC-050 | 7×24 小时稳定运行 | 外部条件后重启 | 当前系统已达到 7×24 小时稳定运行 |",
            )

    def test_rejects_literal_credential_assignment(self) -> None:
        with self.assertRaisesRegex(SourceError, "疑似字面凭据赋值"):
            _validate_sensitive_content(Path("docs/guides/test.md"), "DB_PASSWORD=AlertProd-2026-DoNotShare!")

    def test_accepts_credential_file_and_variable_references(self) -> None:
        _validate_sensitive_content(
            Path("docs/guides/test.md"),
            "DB_PASSWORD_FILE=/run/secrets/database-password\nDB_PASSWORD=${DATABASE_PASSWORD}\nAPP_SECRETS_DIR=/srv/secrets",
        )


if __name__ == "__main__":
    unittest.main()
