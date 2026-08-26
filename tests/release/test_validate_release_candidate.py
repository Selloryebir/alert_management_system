from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


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

    def test_candidate_rejects_prefilled_human_acceptance(self) -> None:
        state = {"stages": {"M14": {"human_acceptance": {"result": "PASS"}}}}
        with self.assertRaisesRegex(ValueError, "不得预填"):
            validator.validate_human_acceptance("candidate", state, "a" * 40)

    def test_post_acceptance_product_change_is_rejected(self) -> None:
        result = SimpleNamespace(
            returncode=0,
            stdout="services/core-api/src/main/java/com/example/AlarmService.java\n",
        )
        with patch.object(validator, "run", return_value=result):
            with self.assertRaisesRegex(ValueError, "必须重建候选"):
                validator.validate_post_acceptance_changes("a" * 40, "b" * 40)

    def test_post_acceptance_governance_closure_is_allowed(self) -> None:
        result = SimpleNamespace(
            returncode=0,
            stdout="automation/state.json\ndocs/verification/evidence/M14.md\n",
        )
        with patch.object(validator, "run", return_value=result):
            validator.validate_post_acceptance_changes("a" * 40, "b" * 40)

    def test_approved_acceptance_is_bound_to_evidence(self) -> None:
        candidate = "a" * 40
        archive_hash = "b" * 64
        signed_at = "2026-08-27T12:00:00+08:00"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "docs/verification/evidence/M14.md"
            evidence.parent.mkdir(parents=True)
            evidence.write_text(
                (
                    f"# M14\n\nAC-022 PASS\n{candidate}\nC:\\验收\\candidate.zip\n{archive_hash}\n"
                    f"业务验收员\n业务验收员签名\n2026-08-27T11:00:00+08:00\n{signed_at}\n"
                    + "\n".join(sorted(validator.HUMAN_ACCEPTANCE_STEPS))
                ),
                encoding="utf-8",
            )
            acceptance = {
                "candidate_commit": candidate,
                "archive_path": "C:\\验收\\candidate.zip",
                "archive_sha256": archive_hash,
                "windows_version": "Windows 11 x64 24H2",
                "browser": "Microsoft Edge 140",
                "pdf_reader": "Microsoft Edge 140",
                "xlsx_reader": "WPS Office 12",
                "acceptor": "业务验收员",
                "business_role": "报警分析业务人员",
                "signature": "业务验收员签名",
                "independent_from_development": True,
                "no_oral_supplement": True,
                "step_results": {step: "PASS" for step in validator.HUMAN_ACCEPTANCE_STEPS},
                "blocker_count": 0,
                "severe_count": 0,
                "result": "PASS",
                "started_at": "2026-08-27T11:00:00+08:00",
                "signed_at": signed_at,
                "record_file": "docs/verification/evidence/M14.md",
            }
            state = {
                "stages": {
                    "M14": {
                        "human_acceptance": acceptance,
                        "evidence_files": ["docs/verification/evidence/M14.md"],
                    }
                }
            }
            with patch.object(validator, "ROOT", root), patch.object(
                validator, "run", return_value=SimpleNamespace(returncode=0, stdout="")
            ):
                validator.validate_human_acceptance("approved", state, candidate)


if __name__ == "__main__":
    unittest.main()
