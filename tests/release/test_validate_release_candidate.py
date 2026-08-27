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
        recorded_at = "2026-08-27T12:00:00+08:00"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "docs/verification/evidence/M14.md"
            evidence.parent.mkdir(parents=True)
            evidence.write_text(
                (
                    f"# M14\n\n项目负责人验收声明\nPASS\n{candidate}\n"
                    f"C:\\验收\\candidate.zip\n{archive_hash}\nproject_owner_current_session\n"
                    f"人工已验证不存在大问题，符合交付预期。\n{recorded_at}\nv1.0.0\n"
                ),
                encoding="utf-8",
            )
            acceptance = {
                "candidate_commit": candidate,
                "validated_archive_path": "C:\\验收\\candidate.zip",
                "validated_archive_sha256": archive_hash,
                "decision_source": "project_owner_current_session",
                "attestation_text": "人工已验证不存在大问题，符合交付预期。",
                "final_release_authorized": True,
                "result": "PASS",
                "recorded_at": recorded_at,
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
