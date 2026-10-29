"""验证官方数据集的四个只读 HTTP 接口。不调用 DeepSeek。"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app

EXCEL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
EXPECTED_HASH = "1e9caeba1fe526f3ad1b5dd6445a9dbcc02fa22ec02273d9863ebc84e3e30f87"
LABELS = {
    "设备状态",
    "故障类型",
    "故障严重度",
    "故障位置",
    "诊断置信度",
    "频谱描述",
    "根因分析",
    "建议措施",
    "优先级",
    "影响程度",
}
RESULTS: list[tuple[str, bool, str]] = []
BODIES: list[object] = []


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((name, passed, detail))
    print(("PASS " if passed else "FAIL ") + name + ("" if not detail else " | " + detail))


def label_keys(value: object, found: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in LABELS:
                found.append(str(key))
            label_keys(item, found)
    elif isinstance(value, list):
        for item in value:
            label_keys(item, found)


def main() -> None:
    before = file_hash(EXCEL)
    check("H 测试前 Excel 哈希", before == EXPECTED_HASH, before)

    model_calls = {"count": 0}

    def blocked(*args, **kwargs):
        model_calls["count"] += 1
        raise RuntimeError("测试不应调用模型")

    main_module.ask_with_tools = blocked
    main_module.ask = blocked
    client = TestClient(app)

    summary_response = client.get("/api/dataset/summary")
    summary = summary_response.json()
    BODIES.append(summary)
    categories = {sheet.get("equipment_category") for sheet in summary.get("sheets", [])}
    check(
        "A 概况接口",
        summary_response.status_code == 200
        and summary.get("source_name") == EXCEL.name
        and summary.get("sheet_count") == 6
        and summary.get("device_count") == 46
        and summary.get("record_count") == 24000
        and len(categories) == 6
        and all(sheet.get("time_start") and sheet.get("time_end") for sheet in summary.get("sheets", [])),
        f"status={summary_response.status_code} devices={summary.get('device_count')} records={summary.get('record_count')} categories={sorted(str(item) for item in categories)}",
    )

    devices_response = client.get("/api/dataset/devices")
    devices = devices_response.json()
    BODIES.append(devices)
    device_ids = {item.get("device_id") for item in devices.get("devices", [])}
    expected_devices = {"MM-01", "GB-01", "CP-01", "FF-01", "DP-01", "CC-01"}
    check(
        "B 设备清单",
        devices_response.status_code == 200
        and devices.get("device_count") == 46
        and len(devices.get("devices", [])) == 46
        and expected_devices <= device_ids
        and all(item.get("record_count", 0) > 0 and item.get("time_start") and item.get("time_end") for item in devices.get("devices", [])),
        f"status={devices_response.status_code} count={devices.get('device_count')}",
    )

    records_response = client.get("/api/dataset/records", params={"device_id": "MM-01", "limit": 10})
    records = records_response.json()
    BODIES.append(records)
    rows = records.get("records", [])
    ordered = all(
        (left.get("collected_at"), left.get("excel_row"), left.get("record_id"))
        <= (right.get("collected_at"), right.get("excel_row"), right.get("record_id"))
        for left, right in zip(rows, rows[1:])
    )
    check(
        "C MM-01 时间排序",
        records_response.status_code == 200
        and records.get("found") is True
        and records.get("device_id") == "MM-01"
        and records.get("returned_count") == 10
        and records.get("matched_count", 0) > 10
        and records.get("truncated") is True
        and records.get("limit") == 10
        and len(rows) == 10
        and ordered
        and all(row.get("device_id") == "MM-01" and row.get("collected_at") and isinstance(row.get("values"), dict) for row in rows),
        f"status={records_response.status_code} returned={records.get('returned_count')} matched={records.get('matched_count')} first={rows[0].get('collected_at') if rows else None} last={rows[-1].get('collected_at') if rows else None}",
    )

    observation_response = client.get("/api/dataset/observations/REC-MA-20260403-00346")
    observation = observation_response.json()
    BODIES.append(observation)
    record = observation.get("record") if isinstance(observation, dict) else None
    values = record.get("values") if isinstance(record, dict) else {}
    y_vibration = values.get("Y向振动(mm/s)") if isinstance(values, dict) else None
    check(
        "D 指定观测记录",
        observation_response.status_code == 200
        and observation.get("found") is True
        and isinstance(record, dict)
        and record.get("record_id") == "REC-MA-20260403-00346"
        and record.get("device_id") == "MM-01"
        and record.get("collected_at") == "2026-04-03 11:05:00"
        and "duplicate_timestamp" in record
        and "same_timestamp_count" in record
        and isinstance(y_vibration, (int, float))
        and abs(float(y_vibration) - 3.017) < 1e-9
        and isinstance(values.get("X向振动(mm/s)"), (int, float))
        and isinstance(values.get("转速(rpm)"), (int, float))
        and "sampling_rate" not in record
        and "sampling_rate" not in values,
        f"status={observation_response.status_code} device={None if not isinstance(record, dict) else record.get('device_id')} time={None if not isinstance(record, dict) else record.get('collected_at')} y={y_vibration}",
    )

    fan_response = client.get("/api/dataset/records", params={"device_id": "FF-01", "limit": 100})
    fan = fan_response.json()
    BODIES.append(fan)
    marked = next((item for item in fan.get("records", []) if item.get("duplicate_timestamp") is True), None)
    same_time = {}
    same_response = None
    if isinstance(marked, dict):
        stamp = marked.get("collected_at")
        same_response = client.get(
            "/api/dataset/records",
            params={"device_id": "FF-01", "start_time": stamp, "end_time": stamp, "limit": 100},
        )
        same_time = same_response.json()
        BODIES.append(same_time)
    same_rows = same_time.get("records", []) if isinstance(same_time, dict) else []
    same_ids = [item.get("record_id") for item in same_rows]
    value_sets = [tuple(sorted((item.get("values") or {}).items())) for item in same_rows]
    check(
        "E FF-01 重复时刻全部保留",
        same_response is not None
        and same_response.status_code == 200
        and same_time.get("found") is True
        and len(same_rows) >= 2
        and len(same_ids) == len(set(same_ids))
        and len(same_rows) == same_rows[0].get("same_timestamp_count")
        and all(item.get("collected_at") == marked.get("collected_at") and item.get("duplicate_timestamp") is True for item in same_rows)
        and [item.get("excel_row") for item in same_rows] == sorted(item.get("excel_row") for item in same_rows)
        and len(set(value_sets)) >= 1,
        f"time={None if marked is None else marked.get('collected_at')} count={len(same_rows)} distinct_values={len(set(value_sets))}",
    )

    missing_record = client.get("/api/dataset/observations/REC-NOT-EXIST-123")
    missing_body = missing_record.json()
    BODIES.append(missing_body)
    check(
        "E2 不存在的记录",
        missing_record.status_code == 404
        and missing_body.get("success") is False
        and "没有找到记录编号" in str(missing_body.get("message"))
        and "REC-NOT-EXIST-123" in str(missing_body.get("message"))
        and "record" not in missing_body,
        f"status={missing_record.status_code} message={missing_body.get('message')}",
    )

    missing_device = client.get("/api/dataset/records", params={"device_id": "NO-SUCH-DEVICE", "limit": 10})
    missing_device_body = missing_device.json()
    BODIES.append(missing_device_body)
    check(
        "E3 不存在的设备",
        missing_device.status_code == 200
        and missing_device_body.get("found") is False
        and missing_device_body.get("records") == []
        and missing_device_body.get("matched_count") == 0,
        f"status={missing_device.status_code} found={missing_device_body.get('found')}",
    )

    bad_limit = client.get("/api/dataset/records", params={"device_id": "MM-01", "limit": 101})
    bad_time = client.get("/api/dataset/records", params={"device_id": "MM-01", "start_time": "yesterday"})
    reversed_time = client.get(
        "/api/dataset/records",
        params={"device_id": "MM-01", "start_time": "2026-04-10 00:00:00", "end_time": "2026-04-01 00:00:00"},
    )
    missing_id = client.get("/api/dataset/records")
    traversal = client.get("/api/dataset/records", params={"device_id": "../.env"})
    for response in (bad_limit, bad_time, reversed_time, missing_id, traversal):
        BODIES.append(response.json())
    check(
        "安全 非法参数与路径",
        bad_limit.status_code == 422
        and "limit" in str(bad_limit.json().get("message"))
        and bad_time.status_code == 422
        and "YYYY-MM-DD" in str(bad_time.json().get("message"))
        and reversed_time.status_code == 422
        and "start_time" in str(reversed_time.json().get("message"))
        and missing_id.status_code == 422
        and "device_id" in str(missing_id.json().get("message"))
        and traversal.status_code == 422
        and "不能用于读取服务器文件" in str(traversal.json().get("message"))
        and "DEEPSEEK" not in traversal.text
        and "api_key" not in traversal.text.lower()
        and not traversal.content.startswith(b"PK"),
        f"limit={bad_limit.status_code} time={bad_time.status_code} range={reversed_time.status_code} missing={missing_id.status_code} path={traversal.status_code}",
    )

    leaked: list[str] = []
    for body in BODIES:
        label_keys(body, leaked)
    check("F 响应不含答案标签", not leaked, ",".join(leaked))

    search_response = client.get("/api/knowledge/search", params={"q": "轴承", "top_k": 1})
    search_body = search_response.json()
    graph_response = client.get("/api/knowledge/graph")
    graph_body = graph_response.json()
    csv_body = "value\n" + "\n".join(["0", "1", "0", "-1", "0", "1", "0", "-1"])
    vibration_response = client.post(
        "/api/vibration/analyze",
        params={"sampling_rate": "1000", "column": "value"},
        content=csv_body.encode("utf-8"),
        headers={"content-type": "text/csv"},
    )
    vibration_body = vibration_response.json()
    empty_chat = client.post("/api/ai/chat", json={})
    blank_chat = client.post("/api/ai/chat", json={"question": " "})
    check(
        "G 原有接口未调用模型",
        search_response.status_code == 200
        and search_body.get("count", 0) >= 1
        and isinstance(search_body.get("results"), list)
        and graph_response.status_code == 200
        and len(graph_body.get("nodes", [])) == 252
        and len(graph_body.get("edges", [])) == 464
        and vibration_response.status_code == 200
        and vibration_body.get("ok") is True
        and vibration_body.get("analysis_note") == "这是信号分析结果，不是故障确诊。"
        and empty_chat.status_code == 422
        and empty_chat.json().get("message") == "请输入问题"
        and blank_chat.status_code == 422
        and blank_chat.json().get("message") == "请输入问题"
        and model_calls["count"] == 0,
        f"search={search_response.status_code}/{search_body.get('count')} graph={graph_response.status_code} vibration={vibration_response.status_code} chat={empty_chat.status_code}/{blank_chat.status_code} model_calls={model_calls['count']}",
    )

    import py_compile

    py_compile.compile(str(ROOT / "backend" / "app" / "main.py"), doraise=True)
    after = file_hash(EXCEL)
    check("H 编译通过且 Excel 哈希不变", after == EXPECTED_HASH and after == before, after)

    failed = [name for name, passed, _detail in RESULTS if not passed]
    print(f"SUMMARY {len(RESULTS) - len(failed)}/{len(RESULTS)}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
