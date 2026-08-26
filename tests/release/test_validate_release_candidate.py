from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "validate_release_candidate.py"
SPEC = importlib.util.spec_from_file_location("validate_release_candidate", SCRIPT)
assert SPEC and SPEC.loader
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


class ReleaseCandidateValidatorTests(unittest.TestCase):
    def write_matrix(self, rows: list[str]) -> Path:
        temporary = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False)
        self.addCleanup(Path(temporary.name).unlink, missing_ok=True)
        temporary.write("\n".join(rows))
        temporary.close()
        return Path(temporary.name)

    def test_disposition_normalization(self) -> None:
        self.assertEqual("已实现", validator.normalize_disposition("已实现（调整）"))
        self.assertEqual("外部条件后重启", validator.normalize_disposition("外部输入后才可重启"))
        self.assertEqual("M14", validator.normalize_disposition("M14"))

    def test_unknown_disposition_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "未知来源处置"):
            validator.normalize_disposition("延期")

    def test_duplicate_matrix_id_is_rejected(self) -> None:
        path = self.write_matrix([
            "| SC-001 | A | 已实现 | X |",
            "| SC-001 | B | 已实现 | X |",
        ])
        with self.assertRaisesRegex(ValueError, "ID 重复"):
            validator.parse_matrix(path, 2)

    def test_unsafe_repository_path_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "路径不安全"):
            validator.safe_repository_file("../outside.pdf")


if __name__ == "__main__":
    unittest.main()
