#!/usr/bin/env python3
"""生成报警管理系统的确定性合成样例，仅使用 Python 标准库。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


GENERATOR_VERSION = "1.0.0"
DEFAULT_SEED = 20260825
SMOKE_ROWS = 300
DEMO_ROWS = 20_000
SHANGHAI = timezone(timedelta(hours=8))
BASE_TIME = datetime(2026, 1, 15, 8, 0, tzinfo=SHANGHAI)
ROOT = Path(__file__).resolve().parents[1]

FIELDS = [
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

SCENARIO_PLAN = (
    ("ALARM_FLOOD", 60),
    ("DUPLICATE", 30),
    ("CHATTER", 40),
    ("SHORT_LIVED", 30),
    ("PERSISTENT", 30),
    ("INSTRUMENT_DRIFT", 30),
    ("EQUIPMENT_TRIP", 30),
    ("PROCESS_CASCADE", 30),
    ("MAINTENANCE_TEST", 20),
)
SCENARIO_CYCLE = tuple(
    scenario for scenario, count in SCENARIO_PLAN for _ in range(count)
)


def stable_number(seed: int, index: int, salt: str, modulo: int) -> int:
    payload = f"{GENERATOR_VERSION}:{seed}:{index}:{salt}".encode("ascii")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") % modulo


def iso(value: datetime | None) -> str:
    return value.isoformat(timespec="seconds") if value else ""


def scenario_time(scenario: str, occurrence: int, index: int) -> datetime:
    day_offset = index // 1_500
    start = BASE_TIME + timedelta(days=day_offset)
    if scenario == "ALARM_FLOOD":
        return start + timedelta(seconds=occurrence * 2)
    if scenario == "DUPLICATE":
        return start + timedelta(minutes=20, seconds=(occurrence // 2) * 20)
    if scenario == "CHATTER":
        return start + timedelta(minutes=40, seconds=occurrence * 3)
    return start + timedelta(hours=1, seconds=index * 11)


def make_record(index: int, scenario: str, occurrence: int, seed: int) -> dict[str, str]:
    event_time = scenario_time(scenario, occurrence, index)
    site_number = 1 + stable_number(seed, index, "site", 2)
    area_number = 1 + stable_number(seed, index, "area", 4)
    unit_number = 1 + stable_number(seed, index, "unit", 6)
    tag_suffix = 1 + stable_number(seed, occurrence, scenario, 24)
    value = 40 + stable_number(seed, index, "value", 5_000) / 100

    record = {
        "source_row": str(index + 2),
        "event_time": iso(event_time),
        "return_time": "",
        "ack_time": "",
        "site": f"SYNTHETIC_SITE_{site_number:02d}",
        "area": f"SYNTHETIC_AREA_{area_number:02d}",
        "unit": "" if index % 17 == 0 else f"SYNTHETIC_UNIT_{unit_number:02d}",
        "tag": f"SYNTHETIC-{scenario}-{tag_suffix:03d}",
        "description": f"[SYNTHETIC] {scenario} 合成报警场景",
        "priority": ("P1", "P2", "P3", "P4")[index % 4],
        "state": "ACTIVE",
        "value": f"{value:.2f}",
        "threshold": "75.00",
        "engineering_unit": "SYNTHETIC_UNIT_VALUE",
        "source_system": "SYNTHETIC_DCS",
        "operator": "" if index % 11 == 0 else f"SYNTHETIC_OPERATOR_{1 + index % 5:02d}",
    }

    if scenario == "DUPLICATE":
        pair = occurrence // 2
        record["tag"] = f"SYNTHETIC-DUPLICATE-{pair % 8 + 1:03d}"
        record["value"] = f"{50 + pair % 10:.2f}"
        record["priority"] = "P2"
        record["site"] = f"SYNTHETIC_SITE_{1 + stable_number(seed, pair, 'duplicate-site', 2):02d}"
        record["area"] = f"SYNTHETIC_AREA_{1 + stable_number(seed, pair, 'duplicate-area', 4):02d}"
        record["unit"] = f"SYNTHETIC_UNIT_{1 + stable_number(seed, pair, 'duplicate-unit', 6):02d}"
        record["operator"] = f"SYNTHETIC_OPERATOR_{1 + pair % 5:02d}"
    elif scenario == "CHATTER":
        record["tag"] = f"SYNTHETIC-CHATTER-{occurrence // 10 + 1:03d}"
        if occurrence % 2:
            record["state"] = "RETURNED"
            record["return_time"] = iso(event_time + timedelta(seconds=2))
    elif scenario == "SHORT_LIVED":
        record["state"] = "RETURNED"
        record["ack_time"] = iso(event_time + timedelta(seconds=1))
        record["return_time"] = iso(event_time + timedelta(seconds=4))
    elif scenario == "PERSISTENT":
        record["priority"] = "P1"
        record["ack_time"] = iso(event_time + timedelta(minutes=1))
    elif scenario == "INSTRUMENT_DRIFT":
        record["tag"] = f"SYNTHETIC-INSTRUMENT_DRIFT-{occurrence // 6 + 1:03d}"
        record["value"] = f"{60 + occurrence * 0.75:.2f}"
        record["description"] = "[SYNTHETIC] 仪表漂移合成场景"
    elif scenario == "EQUIPMENT_TRIP":
        step = occurrence % 5
        record["tag"] = f"SYNTHETIC-EQUIPMENT_TRIP-{step + 1:03d}"
        record["description"] = f"[SYNTHETIC] 设备跳停序列步骤 {step + 1}"
        record["priority"] = "P1" if step >= 3 else "P2"
    elif scenario == "PROCESS_CASCADE":
        step = occurrence % 5
        record["tag"] = f"SYNTHETIC-PROCESS_CASCADE-{step + 1:03d}"
        record["description"] = f"[SYNTHETIC] 工艺扰动级联步骤 {step + 1}"
    elif scenario == "MAINTENANCE_TEST":
        record["state"] = "ACKNOWLEDGED"
        record["ack_time"] = iso(event_time + timedelta(seconds=2))
        record["description"] = "[SYNTHETIC] 维护测试合成报警"

    return record


def build_records(count: int, seed: int = DEFAULT_SEED) -> list[dict[str, str]]:
    occurrences: Counter[str] = Counter()
    records: list[dict[str, str]] = []
    for index in range(count):
        scenario = SCENARIO_CYCLE[index % len(SCENARIO_CYCLE)]
        records.append(make_record(index, scenario, occurrences[scenario], seed))
        occurrences[scenario] += 1
    return records


def csv_bytes(records: list[dict[str, str]], encoding: str = "utf-8-sig") -> bytes:
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(records)
    return text.getvalue().encode(encoding)


def write_delimited(
    path: Path,
    records: list[dict[str, str]],
    *,
    delimiter: str,
    encoding: str,
    fields: list[str] = FIELDS,
    quoting: int = csv.QUOTE_MINIMAL,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=encoding, newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fields,
            delimiter=delimiter,
            lineterminator="\n",
            quoting=quoting,
        )
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in records)


def column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def worksheet_xml(rows: list[list[str]]) -> bytes:
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    ET.register_namespace("", namespace)
    worksheet = ET.Element(f"{{{namespace}}}worksheet")
    sheet_data = ET.SubElement(worksheet, f"{{{namespace}}}sheetData")
    for row_index, values in enumerate(rows, start=1):
        row = ET.SubElement(sheet_data, f"{{{namespace}}}row", {"r": str(row_index)})
        for column_index, value in enumerate(values, start=1):
            cell = ET.SubElement(
                row,
                f"{{{namespace}}}c",
                {"r": f"{column_name(column_index)}{row_index}", "t": "inlineStr"},
            )
            inline = ET.SubElement(cell, f"{{{namespace}}}is")
            text = ET.SubElement(inline, f"{{{namespace}}}t")
            text.text = value
    return ET.tostring(worksheet, encoding="utf-8", xml_declaration=True)


def zip_info(name: str) -> ZipInfo:
    info = ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    return info


def write_xlsx(path: Path, records: list[dict[str, str]], seed: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    visible_rows = [FIELDS] + [[record[field] for field in FIELDS] for record in records]
    files = {
        "[Content_Types].xml": b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>""",
        "_rels/.rels": b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""",
        "xl/workbook.xml": b"""<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="SYNTHETIC_METADATA" sheetId="1" state="hidden" r:id="rId1"/>
    <sheet name="SYNTHETIC_ALARMS" sheetId="2" r:id="rId2"/>
  </sheets>
</workbook>""",
        "xl/_rels/workbook.xml.rels": b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>
</Relationships>""",
        "xl/worksheets/sheet1.xml": worksheet_xml(
            [["SYNTHETIC_ONLY", "true"], ["generator_version", GENERATOR_VERSION], ["seed", str(seed)]]
        ),
        "xl/worksheets/sheet2.xml": worksheet_xml(visible_rows),
    }
    with ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(zip_info(name), content)


def scenario_counts(records: list[dict[str, str]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for record in records:
        tag = record["tag"]
        scenario = next(name for name, _ in SCENARIO_PLAN if tag.startswith(f"SYNTHETIC-{name}-"))
        counts[scenario] += 1
    return dict(sorted(counts.items()))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def generate_smoke(seed: int) -> None:
    records = build_records(SMOKE_ROWS, seed)
    smoke_dir = ROOT / "samples" / "smoke"
    write_delimited(smoke_dir / "synthetic_smoke_utf8.csv", records, delimiter=",", encoding="utf-8-sig")
    write_delimited(
        smoke_dir / "synthetic_smoke_utf8.txt",
        records,
        delimiter="\t",
        encoding="utf-8",
        quoting=csv.QUOTE_ALL,
    )
    write_xlsx(smoke_dir / "synthetic_smoke.xlsx", records, seed)
    write_delimited(
        smoke_dir / "synthetic_smoke_gb18030.csv",
        records[:12],
        delimiter=",",
        encoding="gb18030",
    )
    write_json(
        ROOT / "samples" / "expected" / "smoke-summary.json",
        {
            "synthetic": True,
            "generator_version": GENERATOR_VERSION,
            "seed": seed,
            "row_count": len(records),
            "scenario_counts": scenario_counts(records),
            "utf8_csv_sha256": hashlib.sha256(csv_bytes(records)).hexdigest(),
        },
    )


def valid_invalid_base(index: int, seed: int) -> dict[str, str]:
    return make_record(index, "ALARM_FLOOD", index, seed)


def generate_invalid(seed: int) -> None:
    invalid_dir = ROOT / "samples" / "invalid"
    invalid_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, list[dict[str, str]]] = {}

    missing_header = [valid_invalid_base(index, seed) for index in range(6)]
    write_delimited(
        invalid_dir / "missing_header.csv",
        missing_header,
        delimiter=",",
        encoding="utf-8-sig",
        fields=[field for field in FIELDS if field != "description"],
    )
    files["missing_header.csv"] = missing_header

    missing_rows = [valid_invalid_base(index, seed) for index in range(8)]
    for row, field in zip(
        missing_rows,
        ("event_time", "site", "area", "tag", "description", "priority", "state", "source_system"),
        strict=True,
    ):
        row[field] = ""
    files["required_value_missing.csv"] = missing_rows

    time_rows = [valid_invalid_base(index, seed) for index in range(7)]
    time_mutations = (
        ("event_time", "not-a-time"),
        ("event_time", "2026-13-40T25:61:00+08:00"),
        ("event_time", "2026/01/15 08:00"),
        ("return_time", "yesterday"),
        ("return_time", "2026-02-30T08:00:00+08:00"),
        ("ack_time", "08:00:00"),
        ("ack_time", "2026-01-15T08:00:00+99:00"),
    )
    for row, (field, value) in zip(time_rows, time_mutations, strict=True):
        row[field] = value
    files["invalid_time.csv"] = time_rows

    enum_rows = [valid_invalid_base(index, seed) for index in range(7)]
    enum_mutations = (
        ("priority", "P0"),
        ("priority", "P5"),
        ("priority", "HIGH"),
        ("priority", "p1"),
        ("state", "OPEN"),
        ("state", "active"),
        ("state", "CLEARED"),
    )
    for row, (field, value) in zip(enum_rows, enum_mutations, strict=True):
        row[field] = value
    files["invalid_enum.csv"] = enum_rows

    number_rows = [valid_invalid_base(index, seed) for index in range(7)]
    number_mutations = (
        ("value", "NaN"),
        ("value", "Infinity"),
        ("value", "12,34"),
        ("value", "十"),
        ("threshold", "75%"),
        ("threshold", "--1"),
        ("threshold", "1e+"),
    )
    for row, (field, value) in zip(number_rows, number_mutations, strict=True):
        row[field] = value
    files["invalid_number.csv"] = number_rows

    order_rows = [valid_invalid_base(index, seed) for index in range(7)]
    for index, row in enumerate(order_rows):
        event_time = datetime.fromisoformat(row["event_time"])
        if index % 2 == 0:
            row["return_time"] = iso(event_time - timedelta(seconds=index + 1))
        else:
            row["ack_time"] = iso(event_time - timedelta(seconds=index + 1))
    files["time_order_invalid.csv"] = order_rows

    for name, rows in files.items():
        if name == "missing_header.csv":
            continue
        write_delimited(invalid_dir / name, rows, delimiter=",", encoding="utf-8-sig")

    expected_codes = {
        "missing_header.csv": "MISSING_HEADER",
        "required_value_missing.csv": "REQUIRED_VALUE_MISSING",
        "invalid_time.csv": "INVALID_TIME",
        "invalid_enum.csv": "INVALID_ENUM",
        "invalid_number.csv": "INVALID_NUMBER",
        "time_order_invalid.csv": "TIME_ORDER_INVALID",
    }
    write_json(
        ROOT / "samples" / "expected" / "invalid-summary.json",
        {
            "synthetic": True,
            "generator_version": GENERATOR_VERSION,
            "seed": seed,
            "total_data_rows": sum(len(rows) for rows in files.values()),
            "files": {
                name: {"data_rows": len(files[name]), "expected_error_code": code}
                for name, code in expected_codes.items()
            },
        },
    )


def generate_demo_summary(seed: int) -> None:
    records = build_records(DEMO_ROWS, seed)
    write_json(
        ROOT / "samples" / "expected" / "demo-summary.json",
        {
            "synthetic": True,
            "generated_file_committed": False,
            "generator_version": GENERATOR_VERSION,
            "seed": seed,
            "row_count": len(records),
            "scenario_counts": scenario_counts(records),
            "utf8_csv_sha256": hashlib.sha256(csv_bytes(records)).hexdigest(),
        },
    )


def generate_large(path: Path, rows: int, seed: int) -> None:
    records = build_records(rows, seed)
    write_delimited(path, records, delimiter=",", encoding="utf-8-sig")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        choices=("committed", "smoke", "invalid", "demo", "generated"),
        default="committed",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--rows", type=int)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.dataset in {"committed", "smoke"}:
        generate_smoke(args.seed)
    if args.dataset in {"committed", "invalid"}:
        generate_invalid(args.seed)
    if args.dataset == "committed":
        generate_demo_summary(args.seed)
    if args.dataset in {"demo", "generated"}:
        rows = DEMO_ROWS if args.dataset == "demo" else args.rows
        if args.output is None:
            raise SystemExit("--dataset demo/generated 必须提供 --output，避免误提交大文件")
        if rows is None or rows <= 0:
            raise SystemExit("--dataset generated 必须提供正整数 --rows")
        generate_large(args.output.resolve(), rows, args.seed)


if __name__ == "__main__":
    main()
