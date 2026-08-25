#!/usr/bin/env bash

source "$(dirname "$0")/common.sh"

M9_PORT=${M9_ALGORITHM_PORT:-18091}
M9_RUNTIME="$RUNTIME_DIR/m9-algorithm-v2"
M9_PID_FILE="$PID_DIR/m9-algorithm-v2.pid"
M9_LOG_FILE="$LOG_DIR/m9-algorithm-v2.log"
M9_BASE_URL="http://127.0.0.1:$M9_PORT"

cleanup() {
  local exit_code=$?
  if ! stop_pid_file "$M9_PID_FILE" "M9 Python 算法服务" "algorithm_service"; then
    echo "M9 算法服务未能正常停止，请检查 $M9_PID_FILE。" >&2
    exit_code=1
  fi
  if ((exit_code != 0)); then
    echo "M9 黑盒验收失败；算法日志：$M9_LOG_FILE" >&2
    if [[ -f "$M9_LOG_FILE" ]]; then
      tail -n 80 "$M9_LOG_FILE" >&2 || true
    fi
  fi
  exit "$exit_code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ ! "$M9_PORT" =~ ^[0-9]+$ ]] || ((M9_PORT < 1 || M9_PORT > 65535)); then
  echo "M9_ALGORITHM_PORT 必须是 1 到 65535 的整数，当前为：$M9_PORT" >&2
  exit 1
fi

if curl --noproxy '*' --connect-timeout 1 --max-time 2 --fail --silent \
    "$M9_BASE_URL/health" >/dev/null 2>&1; then
  echo "端口 $M9_PORT 已有服务响应；为避免误用或停止非本次进程，M9 验收拒绝启动。" >&2
  exit 1
fi

if [[ ! -x "$PYTHON_VENV/bin/python" ]] \
    || ! "$PYTHON_VENV/bin/python" -c 'import fastapi, uvicorn' >/dev/null 2>&1; then
  echo "准备项目锁定的 Python 运行环境。"
  "$REPOSITORY_ROOT/scripts/dev/bootstrap.sh"
fi

mkdir -p "$M9_RUNTIME"
rm -f "$M9_PID_FILE"
: >"$M9_LOG_FILE"

runtime_root="$REPOSITORY_ROOT/src/algorithm/algorithm_service"
forbidden_pattern='SYNTHETIC|synthetic[_ -]?(smoke|demo|scenario)|EQUIPMENT[_ -]?TRIP|PROCESS[_ -]?CASCADE|设备跳停|工艺扰动级联|步骤[[:space:]_-]*[1-5]|step[[:space:]_-]*[1-5]|samples[/\\]expected|analysis-smoke-expected|expected[/\\].*\.json'
if rg --line-number --ignore-case --glob '*.py' --glob '*.json' \
    "$forbidden_pattern" "$runtime_root" >"$M9_RUNTIME/forbidden-runtime-markers.txt"; then
  echo "算法运行时代码仍包含场景词、固定步骤识别或 expected 路径：" >&2
  cat "$M9_RUNTIME/forbidden-runtime-markers.txt" >&2
  exit 1
fi
if rg --line-number --glob '*.py' --glob '*.json' \
    '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}' \
    "$runtime_root" >"$M9_RUNTIME/fixed-runtime-uuids.txt"; then
  echo "算法运行时代码包含固定 UUID：" >&2
  cat "$M9_RUNTIME/fixed-runtime-uuids.txt" >&2
  exit 1
fi
echo "静态隔离检查通过：运行时无场景词、步骤识别、expected 读取路径或固定 UUID。"

(
  cd "$REPOSITORY_ROOT/src/algorithm"
  nohup env ALGORITHM_HOST=127.0.0.1 ALGORITHM_PORT="$M9_PORT" \
    "$PYTHON_VENV/bin/python" -m algorithm_service \
    </dev/null >"$M9_LOG_FILE" 2>&1 &
  echo $! >"$M9_PID_FILE"
)
wait_for_url "$M9_BASE_URL/health" "M9 Python 算法服务" 40

"$PYTHON_VENV/bin/python" - "$M9_BASE_URL" "$M9_RUNTIME/results.json" <<'PY'
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import random
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, ProxyHandler, Request
from uuid import NAMESPACE_URL, uuid5


BASE_URL = sys.argv[1]
RESULT_PATH = Path(sys.argv[2])
ANALYZE_URL = f"{BASE_URL}/api/v2/analyze"
CONTRACT_VERSION = "v2"
ALGORITHM_VERSION = "0.2.0"
RULE_VERSION = "hybrid-v2.0.0"
PARAMETERS = {
    "duplicate_window_seconds": 30,
    "chatter_window_seconds": 60,
    "chatter_min_count": 4,
    "chatter_min_transition_ratio": 0.8,
    "short_lived_seconds": 10,
    "persistent_requires_ack": True,
    "episode_gap_seconds": 60,
    "chain_window_seconds": 60,
    "chain_min_steps": 5,
    "min_episode_support": 3,
    "min_transition_probability": 0.6,
    "min_lift": 2.0,
    "expert_min_score": 0.35,
    "expert_min_margin": 0.10,
}
BASE_TIME = datetime(2027, 4, 18, 8, 0, tzinfo=timezone(timedelta(hours=8)))
results: dict[str, Any] = {}
HTTP = build_opener(ProxyHandler({}))


def fail(message: str, detail: Any | None = None) -> None:
    if detail is None:
        raise AssertionError(message)
    raise AssertionError(f"{message}\n{json.dumps(detail, ensure_ascii=False, indent=2, default=str)}")


def uid(label: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"m9-black-box:{label}"))


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def record(
    label: str,
    source_row: int,
    event_time: datetime,
    *,
    batch_id: str,
    site: str = "未见站点-甲",
    area: str = "未见区域-甲",
    unit: str | None = "未见单元-甲",
    tag: str,
    description: str,
    priority: str = "P3",
    state: str = "ACTIVE",
    return_time: datetime | None = None,
    ack_time: datetime | None = None,
    value: float | None = None,
    threshold: float | None = None,
    raw_payload: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "record_id": uid(label),
        "batch_id": batch_id,
        "source_row": source_row,
        "event_time": iso(event_time),
        "return_time": iso(return_time),
        "ack_time": iso(ack_time),
        "site": site,
        "area": area,
        "unit": unit,
        "tag": tag,
        "description": description,
        "priority": priority,
        "state": state,
        "value": value,
        "threshold": threshold,
        "engineering_unit": "u",
        "source_system": "M9_BLACK_BOX",
        "operator": None,
        "raw_payload": raw_payload or {"unmodeled_note": f"opaque-{source_row}"},
    }


def request_payload(label: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "analysis_run_id": uid(f"run:{label}"),
        "contract_version": CONTRACT_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "parameters": deepcopy(PARAMETERS),
        "records": deepcopy(records),
    }


def http_json(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[bytes, dict[str, Any]]:
    data = None if body is None else json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    request = Request(
        f"{BASE_URL}{path}",
        method=method,
        data=data,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with HTTP.open(request, timeout=20) as response:
            raw = response.read()
            if response.status != 200:
                fail(f"{path} 返回 HTTP {response.status}", raw.decode(errors="replace"))
    except HTTPError as error:
        fail(f"{path} 返回 HTTP {error.code}", error.read().decode(errors="replace"))
    except URLError as error:
        fail(f"无法访问 {path}: {error.reason}")
    try:
        return raw, json.loads(raw)
    except json.JSONDecodeError as error:
        fail(f"{path} 未返回合法 JSON: {error}", raw.decode(errors="replace"))


def analyze(payload: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    original = deepcopy(payload)
    raw, response = http_json("POST", "/api/v2/analyze", payload)
    if payload != original:
        fail("HTTP 客户端调用意外修改请求对象")
    expected_top = {
        "analysis_run_id": payload["analysis_run_id"],
        "contract_version": CONTRACT_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "rule_version": RULE_VERSION,
        "parameters": PARAMETERS,
    }
    for key, expected in expected_top.items():
        if response.get(key) != expected:
            fail(f"响应字段 {key} 不符合 v2 契约", {"expected": expected, "actual": response.get(key)})
    if response.get("errors") != []:
        fail("合法 v2 请求不应返回逐项错误", response.get("errors"))
    summary = response.get("summary", {})
    count = len(payload["records"])
    if (summary.get("input_count"), summary.get("success_count"), summary.get("failure_count")) != (count, count, 0):
        fail("响应 summary 计数不一致", summary)
    if len(response.get("record_results", [])) != count:
        fail("逐记录结果没有唯一全覆盖", response)
    input_ids = {item["record_id"] for item in payload["records"]}
    output_ids = [item["record_id"] for item in response["record_results"]]
    if len(output_ids) != len(set(output_ids)) or set(output_ids) != input_ids:
        fail("逐记录结果 ID 不唯一或未全覆盖", output_ids)
    return raw, response


def response_indexes(payload: dict[str, Any], response: dict[str, Any]) -> tuple[dict[int, dict[str, Any]], list[tuple[int, ...]]]:
    source_by_id = {item["record_id"]: item["source_row"] for item in payload["records"]}
    by_source = {source_by_id[item["record_id"]]: item for item in response["record_results"]}
    chains = sorted(tuple(source_by_id[item] for item in chain["member_record_ids"])
                    for chain in response["event_chains"])
    return by_source, chains


def semantic(
    payload: dict[str, Any], response: dict[str, Any], *, include_evidence: bool = True
) -> dict[str, Any]:
    by_source, chains = response_indexes(payload, response)
    record_fields = ["noise_type", "alarm_class", "cause_category", "score"]
    if include_evidence:
        record_fields.append("evidence")
    return {
        "records": {
            source: {key: item[key] for key in record_fields}
            for source, item in sorted(by_source.items())
        },
        "chains": chains,
        "summary": response["summary"],
    }


health_raw, health = http_json("GET", "/health")
if health != {
    "status": "UP",
    "service": "algorithm-service",
    "version": ALGORITHM_VERSION,
    "contract_version": CONTRACT_VERSION,
}:
    fail("算法健康响应不是冻结的 v2 身份", health)
results["health"] = health

# 一个请求同时覆盖未见文本的数学化规则和三个全新 episode 的 Markov 链。
batch = uid("batch:base")
base_records: list[dict[str, Any]] = []
chain_tags = ["QZ-ALPHA-71", "QZ-BRAVO-83", "QZ-CYAN-29", "QZ-DELTA-64", "QZ-ECHO-52"]
source_row = 1
for episode in range(3):
    episode_start = BASE_TIME + timedelta(minutes=episode * 2)
    for step, tag in enumerate(chain_tags):
        base_records.append(record(
            f"base:chain:{episode}:{step}", source_row, episode_start + timedelta(seconds=step * 5),
            batch_id=batch, area="新关系区域", unit="新关系单元", tag=tag,
            description=f"Unseen neutral observation {chr(75 + step)}", value=100 + episode * 10 + step,
        ))
        source_row += 1

duplicate_rows = (source_row, source_row + 1)
duplicate_time = BASE_TIME + timedelta(hours=1)
for offset in (0, 5):
    base_records.append(record(
        f"base:duplicate:{offset}", source_row, duplicate_time + timedelta(seconds=offset), batch_id=batch,
        area="数学规则区域", unit="重复单元", tag="UV-DUP-991",
        description="Unseen duplicate neutral observation", value=17.25, threshold=20.0,
    ))
    source_row += 1

chatter_rows: list[int] = []
for index, state in enumerate(("ACTIVE", "RETURNED", "ACTIVE", "RETURNED")):
    chatter_rows.append(source_row)
    base_records.append(record(
        f"base:chatter:{index}", source_row, BASE_TIME + timedelta(hours=1, minutes=5, seconds=index * 8),
        batch_id=batch, area="数学规则区域", unit="抖动单元", tag="UV-CHAT-735",
        description="Unseen alternating neutral observation", state=state, value=30 + index,
    ))
    source_row += 1

short_row = source_row
short_event = BASE_TIME + timedelta(hours=1, minutes=10)
base_records.append(record(
    "base:short", source_row, short_event, batch_id=batch, area="数学规则区域", unit="短时单元",
    tag="UV-SHORT-417", description="Unseen brief neutral observation", state="RETURNED",
    return_time=short_event + timedelta(seconds=6), value=41,
))
source_row += 1
persistent_row = source_row
persistent_event = BASE_TIME + timedelta(hours=1, minutes=15)
base_records.append(record(
    "base:persistent", source_row, persistent_event, batch_id=batch, area="数学规则区域", unit="持续单元",
    tag="UV-PERSIST-608", description="Unseen lasting neutral observation", priority="P1", state="ACTIVE",
    ack_time=persistent_event + timedelta(seconds=3), value=51,
))

base_request = request_payload("base", base_records)
base_raw, base_response = analyze(base_request)
base_by_source, base_chains = response_indexes(base_request, base_response)
if len(base_chains) != 3 or any(len(chain) != 5 for chain in base_chains):
    fail("三个新 episode 未形成三条五成员 Markov 链", base_chains)
for chain in base_response["event_chains"]:
    if chain["association_rule"] != "MARKOV_TRANSITION_HYBRID_V2":
        fail("事件链未标识 hybrid-v2 Markov 规则", chain)
    explanation = chain["explanation"]
    for marker in ("P(v|u)", "P(v)", "lift", "median_lag", "不代表已确认根因"):
        if marker not in explanation:
            fail(f"事件链解释缺少 {marker}", chain)
for row in range(1, 16):
    if base_by_source[row]["cause_category"] != "UNKNOWN":
        fail("未见中性 tag/描述在证据不足时没有弃权", base_by_source[row])
for row in duplicate_rows:
    item = base_by_source[row]
    if item["noise_type"] != "DUPLICATE" or not math.isclose(item["score"], math.exp(-5 / 30), rel_tol=1e-6):
        fail("重复规则未按时间差指数公式计算", item)
for row in chatter_rows:
    item = base_by_source[row]
    if item["noise_type"] != "CHATTER" or not math.isclose(item["score"], 1.0):
        fail("抖动规则未按二值转换比计算", item)
if base_by_source[short_row]["noise_type"] != "SHORT_LIVED" \
        or not math.isclose(base_by_source[short_row]["score"], math.exp(-6 / 10), rel_tol=1e-6):
    fail("短时规则未按恢复时长指数公式计算", base_by_source[short_row])
if base_by_source[persistent_row]["noise_type"] != "PERSISTENT" \
        or not math.isclose(base_by_source[persistent_row]["score"], 1.0):
    fail("持续报警规则未命中或强度错误", base_by_source[persistent_row])
results["base"] = {"records": len(base_records), "chains": len(base_chains)}

# 相同请求必须产生完全相同的响应字节和 JSON 语义。
repeat_raw, repeat_response = analyze(deepcopy(base_request))
if repeat_raw != base_raw or repeat_response != base_response:
    fail("相同请求重复运行不确定")
results["deterministic"] = True

# 打乱输入数组不得改变按源记录对齐后的结果与链结构。
shuffled_request = deepcopy(base_request)
random.Random(20260826).shuffle(shuffled_request["records"])
_, shuffled_response = analyze(shuffled_request)
if semantic(shuffled_request, shuffled_response) != semantic(base_request, base_response):
    fail("乱序请求改变了语义结果")
results["shuffle_invariant"] = True

# UUID 全量重映射不得成为业务特征。
uuid_request = deepcopy(base_request)
uuid_request["analysis_run_id"] = uid("run:uuid-remap")
new_batch = uid("batch:uuid-remap")
for item in uuid_request["records"]:
    item["record_id"] = uid(f"remap:{item['source_row']}")
    item["batch_id"] = new_batch
_, uuid_response = analyze(uuid_request)
if semantic(uuid_request, uuid_response, include_evidence=False) \
        != semantic(base_request, base_response, include_evidence=False):
    fail("UUID 重映射改变了分类、分数、摘要或链结构")
results["uuid_invariant"] = True

# 全体时间平移不得改变分类、证据或链拓扑。
shift_request = deepcopy(base_request)
shift_request["analysis_run_id"] = uid("run:time-shift")
for item in shift_request["records"]:
    for field in ("event_time", "return_time", "ack_time"):
        if item[field] is not None:
            item[field] = iso(datetime.fromisoformat(item[field]) + timedelta(days=37, hours=3))
_, shift_response = analyze(shift_request)
if semantic(shift_request, shift_response, include_evidence=False) \
        != semantic(base_request, base_response, include_evidence=False):
    fail("整体时间平移改变了语义结果")
results["time_shift_invariant"] = True

# raw_payload 是追溯数据，不得成为模型特征。
raw_request = deepcopy(base_request)
raw_request["analysis_run_id"] = uid("run:raw-payload")
for item in raw_request["records"]:
    item["raw_payload"] = {"vendor_extension": f"changed-{item['source_row']}", "opaque": "不参与模型"}
_, raw_response = analyze(raw_request)
if semantic(raw_request, raw_response) != semantic(base_request, base_response):
    fail("无关 raw_payload 改变了语义结果")
results["raw_payload_invariant"] = True

# 两个 episode 支持不足，不能形成链；中性原因同样必须弃权。
support_records = [deepcopy(item) for item in base_records if item["source_row"] <= 10]
support_request = request_payload("support-less-than-three", support_records)
_, support_response = analyze(support_request)
if support_response["event_chains"]:
    fail("仅两个 episode 时错误形成事件链", support_response["event_chains"])
if any(item["cause_category"] != "UNKNOWN" for item in support_response["record_results"]):
    fail("支持不足的中性原因没有弃权", support_response["record_results"])
results["insufficient_support"] = True

# 同 tag 跨 site/area/unit 不能相互重复，也不能拼成事件链。
cross_batch = uid("batch:cross-relation")
cross_records: list[dict[str, Any]] = []
for index, unit in enumerate(("隔离单元-甲", "隔离单元-乙")):
    cross_records.append(record(
        f"cross:duplicate:{index}", index + 1, BASE_TIME + timedelta(seconds=index), batch_id=cross_batch,
        area="跨关系区域", unit=unit, tag="SHARED-TAG-901", description="Same neutral content",
        value=1, threshold=2,
    ))
row = 10
for episode, unit in enumerate(("隔离链-甲", "隔离链-乙", "隔离链-丙")):
    start = BASE_TIME + timedelta(hours=2, minutes=episode * 3)
    for step, tag in enumerate(chain_tags):
        cross_records.append(record(
            f"cross:chain:{episode}:{step}", row, start + timedelta(seconds=step * 4), batch_id=cross_batch,
            area="跨关系区域", unit=unit, tag=tag, description=f"Cross relation neutral {step}", value=row,
        ))
        row += 1
cross_request = request_payload("cross-relation", cross_records)
_, cross_response = analyze(cross_request)
cross_by_source, _ = response_indexes(cross_request, cross_response)
if cross_by_source[1]["noise_type"] == "DUPLICATE" or cross_by_source[2]["noise_type"] == "DUPLICATE":
    fail("相同 tag 跨关系范围被错误标为重复", [cross_by_source[1], cross_by_source[2]])
if cross_response["event_chains"]:
    fail("不同关系范围的相同序列被拼成事件链", cross_response["event_chains"])
results["relation_isolation"] = True

# 删除 return_time 后不得继续声称短时恢复。
no_return_request = request_payload("without-return-time", [deepcopy(base_records[short_row - 1])])
no_return_request["records"][0]["return_time"] = None
_, no_return_response = analyze(no_return_request)
no_return_item = no_return_response["record_results"][0]
affirmative_short_claim = ("命中短时恢复", "符合短时恢复", "SHORT_LIVED=命中")
if no_return_item["noise_type"] == "SHORT_LIVED" or any(
        marker in evidence for evidence in no_return_item["evidence"] for marker in affirmative_short_claim):
    fail("删除 return_time 后仍声称短时恢复", no_return_item)
results["return_time_required"] = True

# ACTIVE 与 ACKNOWLEDGED 都在报警侧，互换不得制造 chatter；值不同以排除重复规则干扰。
ack_batch = uid("batch:active-ack")
ack_records = [
    record(
        f"active-ack:{index}", index + 1, BASE_TIME + timedelta(hours=3, seconds=index * 8),
        batch_id=ack_batch, area="状态区域", unit="确认单元", tag="ACK-ACTIVE-552",
        description="Unseen acknowledgment neutral observation", state=state, value=70 + index,
    )
    for index, state in enumerate(("ACTIVE", "ACKNOWLEDGED", "ACTIVE", "ACKNOWLEDGED"))
]
ack_request = request_payload("active-ack", ack_records)
_, ack_response = analyze(ack_request)
if any(item["noise_type"] == "CHATTER" for item in ack_response["record_results"]):
    fail("ACTIVE/ACKNOWLEDGED 字符串互换错误制造 chatter", ack_response["record_results"])
results["active_ack_not_chatter"] = True

# 固定种子随机负控：公共 tag 被随机排列，但没有边达到三个 episode 支持。
rng = random.Random(20260826)
negative_tags = [f"RND-{letter}-88" for letter in "ABCDEFGH"]
negative_orders: list[list[str]] = []
edge_episodes: defaultdict[tuple[str, str], set[int]] = defaultdict(set)
for episode in range(5):
    order = negative_tags[:]
    rng.shuffle(order)
    negative_orders.append(order)
    for left, right in zip(order, order[1:]):
        edge_episodes[(left, right)].add(episode)
if max(map(len, edge_episodes.values()), default=0) >= PARAMETERS["min_episode_support"]:
    fail("随机负控夹具意外产生了达到支持度门槛的边", {
        f"{left}->{right}": sorted(episodes)
        for (left, right), episodes in edge_episodes.items()
    })
negative_batch = uid("batch:random-negative")
negative_records: list[dict[str, Any]] = []
row = 1
for episode, order in enumerate(negative_orders):
    start = BASE_TIME + timedelta(hours=4, minutes=episode * 2)
    for step, tag in enumerate(order):
        negative_records.append(record(
            f"random-negative:{episode}:{step}", row, start + timedelta(seconds=step * 3),
            batch_id=negative_batch, area="随机负控区域", unit="随机负控单元", tag=tag,
            description=f"Random neutral observation {step}", value=row,
        ))
        row += 1
negative_request = request_payload("random-negative", negative_records)
_, negative_response = analyze(negative_request)
if negative_response["event_chains"]:
    fail("固定种子随机负控错误形成事件链", negative_response["event_chains"])
results["random_negative"] = {"episodes": len(negative_orders), "records": len(negative_records)}

RESULT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(
    "M9 hybrid-v2 黑盒通过：未见 tag/描述、3 episode Markov、支持不足、乱序、UUID、时间平移、"
    "raw_payload、关系隔离、随机负控、return_time、ACTIVE/ACK 与确定性均符合契约。"
)
PY

echo "M9 算法 v2 黑盒验收通过；结果摘要：$M9_RUNTIME/results.json"
