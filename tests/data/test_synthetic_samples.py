from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "samples"
GENERATOR_PATH = SAMPLES / "generate_samples.py"
EXPECTED_FIELDS = [
    "source_row",
    "event_time",
    "return_time",
    "ack_time",
    "site",
    "area",
    "unit",
    "tag",
    "description",
    "priority",
    "state",
    "value",
    "threshold",
    "engineering_unit",
    "source_system",
    "operator",
]
EXPECTED_SMOKE_SHA256 = "476f4c7cded5709cd1b6a4e91f0f24f9efdb1499715494a9f0ea34e2b9bf76c3"
EXPECTED_DEMO_SHA256 = "808b819fef1929f3ed78f0334a4369ea34d1596fa83802e09810c5ddf8377b1a"


def load_generator():
    specification = importlib.util.spec_from_file_location("synthetic_generator", GENERATOR_PATH)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def read_delimited(path: Path, *, encoding: str, delimiter: str) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding=encoding, newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        return list(reader.fieldnames or []), list(reader)


def xlsx_first_visible(path: Path) -> tuple[str, list[list[str]]]:
    spreadsheet = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    relationships = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package_relationships = "http://schemas.openxmlformats.org/package/2006/relationships"
    with ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheets = workbook.find(f"{{{spreadsheet}}}sheets")
        assert sheets is not None
        visible = next(sheet for sheet in sheets if sheet.attrib.get("state", "visible") == "visible")
        relation_id = visible.attrib[f"{{{relationships}}}id"]
        relations = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = next(
            relation.attrib["Target"]
            for relation in relations.findall(f"{{{package_relationships}}}Relationship")
            if relation.attrib["Id"] == relation_id
        )
        worksheet = ET.fromstring(archive.read(f"xl/{target}"))
        rows: list[list[str]] = []
        for row in worksheet.findall(f".//{{{spreadsheet}}}row"):
            values = []
            for cell in row.findall(f"{{{spreadsheet}}}c"):
                text = cell.find(f"{{{spreadsheet}}}is/{{{spreadsheet}}}t")
                values.append(text.text if text is not None and text.text is not None else "")
            rows.append(values)
        return visible.attrib["name"], rows


def test_smoke_formats_are_equivalent_and_synthetic() -> None:
    csv_fields, csv_rows = read_delimited(
        SAMPLES / "smoke" / "synthetic_smoke_utf8.csv",
        encoding="utf-8-sig",
        delimiter=",",
    )
    txt_fields, txt_rows = read_delimited(
        SAMPLES / "smoke" / "synthetic_smoke_utf8.txt",
        encoding="utf-8",
        delimiter="\t",
    )
    sheet_name, sheet_rows = xlsx_first_visible(SAMPLES / "smoke" / "synthetic_smoke.xlsx")
    xlsx_fields = sheet_rows[0]
    xlsx_rows = [dict(zip(xlsx_fields, values, strict=True)) for values in sheet_rows[1:]]

    assert csv_fields == txt_fields == xlsx_fields == EXPECTED_FIELDS
    assert len(csv_rows) == 300
    assert csv_rows == txt_rows == xlsx_rows
    assert sheet_name == "SYNTHETIC_ALARMS"
    assert all(row["source_system"] == "SYNTHETIC_DCS" for row in csv_rows)
    assert all(row["site"].startswith("SYNTHETIC_SITE_") for row in csv_rows)
    assert all(row["tag"].startswith("SYNTHETIC-") for row in csv_rows)
    assert all("SYNTHETIC" in row["description"] for row in csv_rows)
    duplicate_rows = [row for row in csv_rows if row["tag"].startswith("SYNTHETIC-DUPLICATE-")]
    for first, second in zip(duplicate_rows[::2], duplicate_rows[1::2], strict=True):
        assert {key: value for key, value in first.items() if key != "source_row"} == {
            key: value for key, value in second.items() if key != "source_row"
        }


def test_committed_summaries_and_gb18030_sample_are_fixed() -> None:
    smoke_path = SAMPLES / "smoke" / "synthetic_smoke_utf8.csv"
    smoke_summary = json.loads((SAMPLES / "expected" / "smoke-summary.json").read_text("utf-8"))
    invalid_summary = json.loads((SAMPLES / "expected" / "invalid-summary.json").read_text("utf-8"))
    gb_fields, gb_rows = read_delimited(
        SAMPLES / "smoke" / "synthetic_smoke_gb18030.csv",
        encoding="gb18030",
        delimiter=",",
    )

    assert hashlib.sha256(smoke_path.read_bytes()).hexdigest() == EXPECTED_SMOKE_SHA256
    assert smoke_summary["utf8_csv_sha256"] == EXPECTED_SMOKE_SHA256
    assert smoke_summary["row_count"] == 300
    assert sum(smoke_summary["scenario_counts"].values()) == 300
    assert invalid_summary["total_data_rows"] == 42
    assert 30 <= invalid_summary["total_data_rows"] <= 50
    assert gb_fields == EXPECTED_FIELDS
    assert len(gb_rows) == 12
    assert all("SYNTHETIC" in " ".join(row.values()) for row in gb_rows)


def test_invalid_files_match_declared_reachable_scenarios() -> None:
    summary = json.loads((SAMPLES / "expected" / "invalid-summary.json").read_text("utf-8"))
    expected_codes = {
        "MISSING_HEADER",
        "REQUIRED_VALUE_MISSING",
        "INVALID_TIME",
        "INVALID_ENUM",
        "INVALID_NUMBER",
        "TIME_ORDER_INVALID",
    }
    observed_codes = set()
    total_rows = 0
    for name, declaration in summary["files"].items():
        fields, rows = read_delimited(SAMPLES / "invalid" / name, encoding="utf-8-sig", delimiter=",")
        assert len(rows) == declaration["data_rows"]
        assert all("SYNTHETIC" in " ".join(row.values()) for row in rows)
        if declaration["expected_error_code"] == "MISSING_HEADER":
            assert "description" not in fields
        else:
            assert fields == EXPECTED_FIELDS
        observed_codes.add(declaration["expected_error_code"])
        total_rows += len(rows)
    assert observed_codes == expected_codes
    assert total_rows == 42


def test_demo_20000_is_rebuildable_with_fixed_digest(tmp_path: Path) -> None:
    generator = load_generator()
    first = tmp_path / "synthetic_demo_first.csv"
    second = tmp_path / "synthetic_demo_second.csv"
    generator.generate_large(first, generator.DEMO_ROWS, generator.DEFAULT_SEED)
    generator.generate_large(second, generator.DEMO_ROWS, generator.DEFAULT_SEED)

    first_digest = hashlib.sha256(first.read_bytes()).hexdigest()
    assert first_digest == hashlib.sha256(second.read_bytes()).hexdigest()
    assert first_digest == EXPECTED_DEMO_SHA256
    fields, rows = read_delimited(first, encoding="utf-8-sig", delimiter=",")
    assert fields == EXPECTED_FIELDS
    assert len(rows) == 20_000
    assert rows[0]["source_row"] == "2"
    assert rows[-1]["source_row"] == "20001"
    assert all(row["source_system"] == "SYNTHETIC_DCS" for row in rows)
