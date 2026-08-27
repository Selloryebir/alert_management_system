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

GENERATOR_VERSION = "3.0.0"
RANDOM_STREAM_VERSION = "1.0.0"
DEFAULT_SEED = 20260825
SMOKE_ROWS = 300
DEMO_ROWS = 20_000
FORMAL_ROWS = 144
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

FORMAL_SCENARIO_PLAN = (
    ("ALARM_FLOOD", 12),
    ("DUPLICATE", 12),
    ("CHATTER", 12),
    ("SHORT_LIVED", 12),
    ("PERSISTENT", 12),
    ("EQUIPMENT_TRIP", 12),
    ("INSTRUMENT_DRIFT", 12),
    ("PROCESS_DISTURBANCE", 12),
    ("MAINTENANCE_TEST", 12),
    ("NORMAL", 12),
    ("FALSE_POSITIVE_BOUNDARY", 12),
    ("MIXED_PRIORITY_STATE", 12),
)


def stable_number(seed: int, index: int, salt: str, modulo: int) -> int:
    payload = f"{RANDOM_STREAM_VERSION}:{seed}:{index}:{salt}".encode("ascii")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") % modulo


def iso(value: datetime | None) -> str:
    return value.isoformat(timespec="seconds") if value else ""


def scenario_time(scenario: str, occurrence: int, index: int) -> datetime:
    day_offset = index // 1_500
    start = BASE_TIME + timedelta(days=day_offset)
    if scenario == "ALARM_FLOOD":
        return start + timedelta(seconds=occurrence * 2)
    if scenario == "DUPLICATE":
        return start + timedelta(
            minutes=20,
            seconds=(occurrence // 2) * 20 + (occurrence % 2) * 5,
        )
    if scenario == "CHATTER":
        return start + timedelta(minutes=40, seconds=occurrence * 3)
    if scenario == "EQUIPMENT_TRIP":
        return start + timedelta(
            hours=2,
            seconds=(occurrence // 5) * 120 + (occurrence % 5) * 11,
        )
    if scenario == "PROCESS_CASCADE":
        return start + timedelta(
            hours=4,
            seconds=(occurrence // 5) * 120 + (occurrence % 5) * 11,
        )
    return start + timedelta(hours=1, seconds=index * 11)


def make_record(
    index: int, scenario: str, occurrence: int, seed: int
) -> dict[str, str]:
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
        "operator": ""
        if index % 11 == 0
        else f"SYNTHETIC_OPERATOR_{1 + index % 5:02d}",
    }

    if scenario == "DUPLICATE":
        pair = occurrence // 2
        record["tag"] = f"SYNTHETIC-DUPLICATE-{pair % 8 + 1:03d}"
        record["value"] = f"{50 + pair % 10:.2f}"
        record["priority"] = "P2"
        record["site"] = (
            f"SYNTHETIC_SITE_{1 + stable_number(seed, pair, 'duplicate-site', 2):02d}"
        )
        record["area"] = (
            f"SYNTHETIC_AREA_{1 + stable_number(seed, pair, 'duplicate-area', 4):02d}"
        )
        record["unit"] = (
            f"SYNTHETIC_UNIT_{1 + stable_number(seed, pair, 'duplicate-unit', 6):02d}"
        )
        record["operator"] = f"SYNTHETIC_OPERATOR_{1 + pair % 5:02d}"
    elif scenario == "CHATTER":
        group = occurrence // 10
        record["site"] = "SYNTHETIC_SITE_01"
        record["area"] = "SYNTHETIC_AREA_02"
        record["unit"] = f"SYNTHETIC_UNIT_{group + 1:02d}"
        record["tag"] = f"SYNTHETIC-CHATTER-{group + 1:03d}"
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
        record["site"] = "SYNTHETIC_SITE_01"
        record["area"] = "SYNTHETIC_AREA_03"
        record["unit"] = "SYNTHETIC_UNIT_05"
        record["tag"] = f"SYNTHETIC-EQUIPMENT_TRIP-{step + 1:03d}"
        record["description"] = f"[SYNTHETIC] 设备跳停序列步骤 {step + 1}"
        record["priority"] = "P1" if step >= 3 else "P2"
    elif scenario == "PROCESS_CASCADE":
        step = occurrence % 5
        record["site"] = "SYNTHETIC_SITE_02"
        record["area"] = "SYNTHETIC_AREA_04"
        record["unit"] = "SYNTHETIC_UNIT_06"
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


def build_formal_records(seed: int = DEFAULT_SEED) -> list[dict[str, str]]:
    """构造业务演示短集；场景标签只存在于生成器，不写入运行时字段。"""
    records: list[dict[str, str]] = []
    descriptions = (
        "合成报警：入口压力偏高",
        "[SYNTHETIC] Outlet temperature deviation observed",
        "合成报警：循环水流量波动，已请求现场核对",
        "[SYNTHETIC] 中文与 English 混合描述，用于验证较长文本在页面和报告中的显示一致性",
    )
    for scenario_index, (scenario, count) in enumerate(FORMAL_SCENARIO_PLAN):
        for occurrence in range(count):
            index = len(records)
            base_scenario = {
                "PROCESS_DISTURBANCE": "PROCESS_CASCADE",
                "NORMAL": "SHORT_LIVED",
                "FALSE_POSITIVE_BOUNDARY": "MAINTENANCE_TEST",
                "MIXED_PRIORITY_STATE": "ALARM_FLOOD",
            }.get(scenario, scenario)
            record = make_record(index, base_scenario, occurrence, seed)
            record["tag"] = f"SYN-{scenario_index + 1:02d}-{occurrence // 3 + 1:03d}"
            record["description"] = descriptions[
                (scenario_index + occurrence) % len(descriptions)
            ]
            record["source_system"] = ("SYNTHETIC_DCS", "SYNTHETIC_SCADA")[index % 2]
            record["operator"] = (
                "" if index % 5 == 0 else f"合成操作员{index % 7 + 1:02d}"
            )

            if scenario == "DUPLICATE":
                record["tag"] = (
                    f"SYN-{scenario_index + 1:02d}-{occurrence // 2 + 1:03d}"
                )
                record["description"] = "合成报警：同一信号重复上送"
            elif scenario == "CHATTER":
                record["tag"] = (
                    f"SYN-{scenario_index + 1:02d}-{occurrence // 6 + 1:03d}"
                )
                record["description"] = "合成报警：开关信号在短窗口内往返"
            elif scenario == "NORMAL":
                event = datetime.fromisoformat(record["event_time"])
                record.update(
                    priority="P4",
                    state="RETURNED",
                    ack_time=iso(event + timedelta(seconds=2)),
                    return_time=iso(event + timedelta(seconds=8)),
                    description="合成记录：参数短暂越限后正常恢复",
                )
            elif scenario == "FALSE_POSITIVE_BOUNDARY":
                event = datetime.fromisoformat(record["event_time"])
                record.update(
                    priority="P3",
                    state="ACKNOWLEDGED",
                    ack_time=iso(event + timedelta(seconds=2)),
                    return_time="",
                    description=(
                        "合成边界：非设备故障，检修旁路测试；NOT an equipment trip，"
                        "不应仅凭描述确认根因"
                    ),
                )
            elif scenario == "MIXED_PRIORITY_STATE":
                event = datetime.fromisoformat(record["event_time"])
                priority = ("P1", "P2", "P3", "P4")[occurrence % 4]
                state = ("ACTIVE", "ACKNOWLEDGED", "RETURNED")[occurrence % 3]
                record.update(priority=priority, state=state)
                if state == "ACKNOWLEDGED":
                    record["ack_time"] = iso(event + timedelta(seconds=3))
                elif state == "RETURNED":
                    record["return_time"] = iso(event + timedelta(seconds=12))
            records.append(record)
    assert len(records) == FORMAL_ROWS
    return records


def normalized_digest(records: list[dict[str, str]]) -> str:
    payload = json.dumps(
        records, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_formal_long_records(
    count: int, seed: int = DEFAULT_SEED
) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    batch_index = 0
    while len(records) < count:
        batch = build_formal_records(seed + batch_index)
        for source in batch:
            if len(records) == count:
                break
            record = source.copy()
            record["source_row"] = str(len(records) + 2)
            for field in ("event_time", "return_time", "ack_time"):
                if record[field]:
                    record[field] = iso(
                        datetime.fromisoformat(record[field])
                        + timedelta(days=batch_index)
                    )
            records.append(record)
        batch_index += 1
    return records


def formal_scenario_counts(count: int) -> dict[str, int]:
    counts: Counter[str] = Counter()
    remaining = count
    while remaining:
        for scenario, planned in FORMAL_SCENARIO_PLAN:
            consumed = min(planned, remaining)
            counts[scenario] += consumed
            remaining -= consumed
            if remaining == 0:
                break
    return dict(counts)


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
        writer.writerows(
            {field: row.get(field, "") for field in fields} for row in records
        )


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
    visible_rows = [FIELDS] + [
        [record[field] for field in FIELDS] for record in records
    ]
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
            [
                ["SYNTHETIC_ONLY", "true"],
                ["generator_version", GENERATOR_VERSION],
                ["seed", str(seed)],
            ]
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
        scenario = next(
            name for name, _ in SCENARIO_PLAN if tag.startswith(f"SYNTHETIC-{name}-")
        )
        counts[scenario] += 1
    return dict(sorted(counts.items()))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def generate_smoke(seed: int) -> None:
    records = build_records(SMOKE_ROWS, seed)
    smoke_dir = ROOT / "samples" / "smoke"
    write_delimited(
        smoke_dir / "synthetic_smoke_utf8.csv",
        records,
        delimiter=",",
        encoding="utf-8-sig",
    )
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


def generate_formal(seed: int) -> None:
    records = build_formal_records(seed)
    formal_dir = ROOT / "samples" / "demo"
    write_delimited(
        formal_dir / "alarm_demo_utf8.csv", records, delimiter=",", encoding="utf-8-sig"
    )
    write_delimited(
        formal_dir / "alarm_demo_utf8.txt",
        records,
        delimiter="\t",
        encoding="utf-8",
        quoting=csv.QUOTE_ALL,
    )
    write_xlsx(formal_dir / "alarm_demo.xlsx", records, seed)
    write_json(
        ROOT / "samples" / "expected" / "formal-demo-summary.json",
        {
            "schema_version": 1,
            "synthetic": True,
            "generator_version": GENERATOR_VERSION,
            "seed": seed,
            "row_count": len(records),
            "scenario_counts": dict(FORMAL_SCENARIO_PLAN),
            "normalized_sha256": normalized_digest(records),
            "runtime_fields_contain_scenario_names": False,
        },
    )


def valid_invalid_base(index: int, seed: int) -> dict[str, str]:
    return make_record(index, "ALARM_FLOOD", index, seed)


def generate_invalid(seed: int) -> None:
    invalid_dir = ROOT / "samples" / "invalid"
    invalid_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, list[dict[str, str]]] = {}
    row_errors: dict[str, list[dict[str, object]]] = {}

    empty_path = invalid_dir / "empty.csv"
    empty_path.write_bytes(b"")
    unsupported_path = invalid_dir / "unsupported_format.json"
    unsupported_path.write_text('{"synthetic": true}\n', encoding="utf-8")

    long_rows = [valid_invalid_base(0, seed)]
    long_rows[0]["description"] = "[SYNTHETIC]" + "长" * 4_096
    files["field_too_long.csv"] = long_rows
    row_errors["field_too_long.csv"] = []

    missing_header = [valid_invalid_base(index, seed) for index in range(6)]
    write_delimited(
        invalid_dir / "missing_header.csv",
        missing_header,
        delimiter=",",
        encoding="utf-8-sig",
        fields=[field for field in FIELDS if field != "description"],
    )
    files["missing_header.csv"] = missing_header
    row_errors["missing_header.csv"] = []

    missing_rows = [valid_invalid_base(index, seed) for index in range(8)]
    for row, field in zip(
        missing_rows,
        (
            "event_time",
            "site",
            "area",
            "tag",
            "description",
            "priority",
            "state",
            "source_system",
        ),
        strict=True,
    ):
        row[field] = ""
    files["required_value_missing.csv"] = missing_rows
    row_errors["required_value_missing.csv"] = [
        {
            "source_row": int(row["source_row"]),
            "field": field,
            "code": "REQUIRED_VALUE_MISSING",
        }
        for row, field in zip(
            missing_rows,
            (
                "event_time",
                "site",
                "area",
                "tag",
                "description",
                "priority",
                "state",
                "source_system",
            ),
            strict=True,
        )
    ]

    time_rows = [valid_invalid_base(index, seed) for index in range(7)]
    time_mutations = (
        ("event_time", "NOT_A_TIMESTAMP"),
        ("event_time", "2026-02-30T08:00:00+08:00"),
        ("event_time", "2026-01-15T08:00:00+25:00"),
        ("return_time", "NOT_A_TIMESTAMP"),
        ("return_time", "2026-02-30T08:00:00+08:00"),
        ("ack_time", "NOT_A_TIMESTAMP"),
        ("ack_time", "2026-01-15T08:00:00+25:00"),
    )
    for row, (field, value) in zip(time_rows, time_mutations, strict=True):
        row[field] = value
    files["invalid_time.csv"] = time_rows
    row_errors["invalid_time.csv"] = [
        {"source_row": int(row["source_row"]), "field": field, "code": "INVALID_TIME"}
        for row, (field, _) in zip(time_rows, time_mutations, strict=True)
    ]

    enum_rows = [valid_invalid_base(index, seed) for index in range(7)]
    enum_mutations = (
        ("priority", "INVALID_PRIORITY"),
        ("priority", "P0"),
        ("priority", "P5"),
        ("priority", "PRIORITY_1"),
        ("state", "INVALID_STATE"),
        ("state", "OPEN"),
        ("state", "RETURNED_WRONG"),
    )
    for row, (field, value) in zip(enum_rows, enum_mutations, strict=True):
        row[field] = value
    files["invalid_enum.csv"] = enum_rows
    row_errors["invalid_enum.csv"] = [
        {"source_row": int(row["source_row"]), "field": field, "code": "INVALID_ENUM"}
        for row, (field, _) in zip(enum_rows, enum_mutations, strict=True)
    ]

    number_rows = [valid_invalid_base(index, seed) for index in range(7)]
    number_mutations = (
        ("value", "NOT_A_NUMBER"),
        ("value", "--1"),
        ("value", "1.2.3"),
        ("value", "12x"),
        ("threshold", "NOT_A_NUMBER"),
        ("threshold", "++2"),
        ("threshold", "1e+"),
    )
    for row, (field, value) in zip(number_rows, number_mutations, strict=True):
        row[field] = value
    files["invalid_number.csv"] = number_rows
    row_errors["invalid_number.csv"] = [
        {"source_row": int(row["source_row"]), "field": field, "code": "INVALID_NUMBER"}
        for row, (field, _) in zip(number_rows, number_mutations, strict=True)
    ]

    order_rows = [valid_invalid_base(index, seed) for index in range(7)]
    for index, row in enumerate(order_rows):
        event_time = datetime.fromisoformat(row["event_time"])
        if index % 2 == 0:
            row["return_time"] = iso(event_time - timedelta(seconds=index + 1))
        else:
            row["ack_time"] = iso(event_time - timedelta(seconds=index + 1))
    files["time_order_invalid.csv"] = order_rows
    row_errors["time_order_invalid.csv"] = [
        {
            "source_row": int(row["source_row"]),
            "field": "return_time" if index % 2 == 0 else "ack_time",
            "code": "TIME_ORDER_INVALID",
        }
        for index, row in enumerate(order_rows)
    ]

    for name, rows in files.items():
        if name == "missing_header.csv":
            continue
        write_delimited(invalid_dir / name, rows, delimiter=",", encoding="utf-8-sig")

    expected_codes = {
        "empty.csv": "EMPTY_FILE",
        "unsupported_format.json": "UNSUPPORTED_FORMAT",
        "field_too_long.csv": "IMPORT_CELL_LIMIT",
        "missing_header.csv": "MISSING_HEADER",
        "required_value_missing.csv": "REQUIRED_VALUE_MISSING",
        "invalid_time.csv": "INVALID_TIME",
        "invalid_enum.csv": "INVALID_ENUM",
        "invalid_number.csv": "INVALID_NUMBER",
        "time_order_invalid.csv": "TIME_ORDER_INVALID",
    }
    file_expectations = {}
    for name, code in expected_codes.items():
        path = invalid_dir / name
        if name in {"empty.csv", "unsupported_format.json"}:
            file_expectations[name] = {
                "data_rows": 0,
                "total_rows": 0,
                "valid_rows": 0,
                "expected_error_code": code,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "file_errors": [{"source_row": 1, "field": "_file", "code": code}],
                "row_errors": [],
            }
            continue
        file_expectations[name] = {
            "data_rows": len(files[name]),
            "total_rows": len(files[name]),
            "valid_rows": 0,
            "expected_error_code": code,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "file_errors": (
                [
                    {
                        "source_row": 1,
                        "field": "description",
                        "code": "MISSING_HEADER",
                    }
                ]
                if name == "missing_header.csv"
                else (
                    [
                        {
                            "source_row": 2,
                            "field": "description",
                            "code": "IMPORT_CELL_LIMIT",
                        }
                    ]
                    if name == "field_too_long.csv"
                    else []
                )
            ),
            "row_errors": row_errors[name],
        }

    write_json(
        ROOT / "samples" / "expected" / "invalid-summary.json",
        {
            "schema_version": 1,
            "synthetic": True,
            "generator_version": GENERATOR_VERSION,
            "seed": seed,
            "valid_rows_definition": "通过文件级与逐行校验、可进入 READY 的数据行数；文件级阻断时为 0",
            "row_validation_skipped_on_file_error": True,
            "total_data_rows": sum(len(rows) for rows in files.values()),
            "total_valid_rows": 0,
            "total_file_errors": 4,
            "total_row_errors": sum(len(errors) for errors in row_errors.values()),
            "files": file_expectations,
        },
    )


def generate_demo_summary(seed: int) -> None:
    records = build_formal_long_records(DEMO_ROWS, seed)
    write_json(
        ROOT / "samples" / "expected" / "demo-summary.json",
        {
            "synthetic": True,
            "generated_file_committed": False,
            "generator_version": GENERATOR_VERSION,
            "seed": seed,
            "row_count": len(records),
            "scenario_counts": formal_scenario_counts(len(records)),
            "utf8_csv_sha256": hashlib.sha256(csv_bytes(records)).hexdigest(),
        },
    )


def generate_large(path: Path, rows: int, seed: int) -> None:
    records = build_formal_long_records(rows, seed)
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
        generate_formal(args.seed)
        generate_demo_summary(args.seed)
    if args.dataset in {"demo", "generated"}:
        rows = DEMO_ROWS if args.dataset == "demo" else args.rows
        if args.output is None:
            raise SystemExit(
                "--dataset demo/generated 必须提供 --output，避免误提交大文件"
            )
        if rows is None or rows <= 0:
            raise SystemExit("--dataset generated 必须提供正整数 --rows")
        generate_large(args.output.resolve(), rows, args.seed)


if __name__ == "__main__":
    main()
