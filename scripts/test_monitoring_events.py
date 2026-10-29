"""验证监测事件只在真实统计偏离时写入临时 SQLite。不调用 DeepSeek。"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TEMP_DIR = Path(tempfile.mkdtemp(prefix="monitoring-events-"))
TEMP_DB = TEMP_DIR / "events.sqlite3"
os.environ["MONITORING_EVENTS_DB"] = str(TEMP_DB)

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.monitoring_events import (
    DEFAULT_DATABASE,
    EVENT_NOTE,
    MonitoringEventError,
    database_path,
    deviation_metrics,
)

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
SESSION_A = "replay-test-a"
SESSION_B = "replay-test-b"
TARGET = "REC-MA-20260403-00346"
EARLY = "REC-MA-20260314-00001"
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


def metric(body: dict, field: str) -> dict | None:
    items = body.get("metrics")
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict) and item.get("field") == field:
            return item
    return None


def analysis_metric(body: dict, field: str) -> dict | None:
    deviation = body.get("statistical_deviation")
    if not isinstance(deviation, dict):
        return None
    items = deviation.get("analyzed_metrics")
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict) and item.get("field") == field:
            return item
    return None


def row_count() -> int:
    if not TEMP_DB.is_file():
        return 0
    conn = sqlite3.connect(TEMP_DB)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM monitoring_events").fetchone()[0])
    finally:
        conn.close()


def main() -> None:
    before = file_hash(EXCEL)
    default_existed = DEFAULT_DATABASE.exists()
    default_hash = file_hash(DEFAULT_DATABASE) if default_existed else ""
    check("J 测试前 Excel 哈希", before == EXPECTED_HASH, before)
    model_calls = {"count": 0}

    def blocked(*args, **kwargs):
        model_calls["count"] += 1
        raise RuntimeError("测试不应调用模型")

    main_module.ask_with_tools = blocked
    main_module.ask = blocked
    client = TestClient(app)

    no_deviation = deviation_metrics(
        {
            "statistical_deviation": {
                "detection_status": "未发现显著偏离",
                "analyzed_metrics": [{"field": "Y向振动(mm/s)", "metric_status": "偏离基线", "current_value": 1}],
            }
        }
    )
    short_baseline = deviation_metrics(
        {
            "statistical_deviation": {
                "detection_status": "基线不足",
                "analyzed_metrics": [{"field": "Y向振动(mm/s)", "metric_status": "基线不足"}],
            }
        }
    )
    unknown = deviation_metrics(
        {"statistical_deviation": {"detection_status": "无法判定", "analyzed_metrics": []}}
    )
    check(
        "本地规则不把非偏离状态当成事件",
        no_deviation == [] and short_baseline == [] and unknown == [],
    )

    os.environ["MONITORING_EVENTS_DB"] = str(ROOT / "data" / "official_dataset" / "monitoring_events.sqlite3")
    official_rejected = False
    official_message = ""
    try:
        database_path()
    except MonitoringEventError as exc:
        official_rejected = True
        official_message = exc.message
    os.environ["MONITORING_EVENTS_DB"] = str(TEMP_DB)
    check("数据库不能放进官方数据目录", official_rejected and "官方数据目录" in official_message, official_message)

    first = client.post("/api/monitoring/process-record", json={"session_id": SESSION_A, "record_id": TARGET})
    first_body = first.json()
    BODIES.append(first_body)
    analysis = first_body.get("analysis") if isinstance(first_body, dict) else None
    event = first_body.get("event") if isinstance(first_body, dict) else None
    observation = analysis.get("observation") if isinstance(analysis, dict) else None
    y_analysis = analysis_metric(analysis, "Y向振动(mm/s)") if isinstance(analysis, dict) else None
    y_event = metric(event, "Y向振动(mm/s)") if isinstance(event, dict) else None
    event_text = json.dumps(event, ensure_ascii=False) if isinstance(event, dict) else ""
    check(
        "A 保存 MM-01 统计偏离事件",
        first.status_code == 200
        and isinstance(observation, dict)
        and observation.get("device_id") == "MM-01"
        and observation.get("record_id") == TARGET
        and observation.get("collected_at") == "2026-04-03 11:05:00"
        and isinstance(y_analysis, dict)
        and y_analysis.get("current_value") == 3.017
        and y_analysis.get("robust_z") == 3.923797970479703
        and y_analysis.get("metric_status") == "偏离基线"
        and first_body.get("saved") is True
        and first_body.get("duplicate") is False
        and isinstance(event, dict)
        and event.get("event_type") == "统计偏离"
        and event.get("session_id") == SESSION_A
        and event.get("record_id") == TARGET
        and event.get("device_id") == "MM-01"
        and event.get("collected_at") == "2026-04-03 11:05:00"
        and event.get("processed_at") != event.get("collected_at")
        and isinstance(event.get("processed_at"), str)
        and "T" in event["processed_at"]
        and isinstance(y_event, dict)
        and y_event.get("current_value") == y_analysis.get("current_value")
        and y_event.get("median") == y_analysis.get("median")
        and y_event.get("robust_z") == y_analysis.get("robust_z")
        and event.get("diagnosis_level") == analysis.get("diagnosis_level")
        and analysis.get("diagnosis_level") == "diagnostic_clue"
        and analysis.get("candidate_faults") == []
        and event.get("candidate_faults") == []
        and analysis.get("confirmed") is False
        and "实验性统计偏离" in str(event.get("event_note"))
        and event.get("event_note") == EVENT_NOTE
        and "不是故障确诊" in str(event.get("event_note"))
        and "轴承磨损" not in event_text
        and "已确诊" not in event_text
        and row_count() == 1,
        f"status={first.status_code} saved={None if not isinstance(first_body, dict) else first_body.get('saved')} y={None if not isinstance(y_event, dict) else y_event.get('current_value')} z={None if not isinstance(y_event, dict) else y_event.get('robust_z')} rows={row_count()}",
    )

    again = client.post("/api/monitoring/process-record", json={"session_id": SESSION_A, "record_id": TARGET})
    again_body = again.json()
    BODIES.append(again_body)
    again_event = again_body.get("event") if isinstance(again_body, dict) else None
    check(
        "B 同会话重复提交不插入",
        again.status_code == 200
        and again_body.get("saved") is False
        and again_body.get("duplicate") is True
        and isinstance(again_event, dict)
        and again_event.get("event_id") == event.get("event_id")
        and again_event.get("processed_at") == event.get("processed_at")
        and row_count() == 1,
        f"status={again.status_code} saved={again_body.get('saved')} duplicate={again_body.get('duplicate')} rows={row_count()}",
    )

    other = client.post("/api/monitoring/process-record", json={"session_id": SESSION_B, "record_id": TARGET})
    other_body = other.json()
    BODIES.append(other_body)
    other_event = other_body.get("event") if isinstance(other_body, dict) else None
    check(
        "C 不同会话分别保存",
        other.status_code == 200
        and other_body.get("saved") is True
        and other_body.get("duplicate") is False
        and isinstance(other_event, dict)
        and other_event.get("session_id") == SESSION_B
        and other_event.get("record_id") == TARGET
        and other_event.get("event_id") != event.get("event_id")
        and row_count() == 2,
        f"status={other.status_code} id={None if not isinstance(other_event, dict) else other_event.get('event_id')} rows={row_count()}",
    )

    early_rows = row_count()
    early = client.post("/api/monitoring/process-record", json={"session_id": SESSION_A, "record_id": EARLY})
    early_body = early.json()
    BODIES.append(early_body)
    early_analysis = early_body.get("analysis") if isinstance(early_body, dict) else None
    early_deviation = early_analysis.get("statistical_deviation") if isinstance(early_analysis, dict) else None
    check(
        "D 基线不足不保存事件",
        early.status_code == 200
        and isinstance(early_deviation, dict)
        and early_deviation.get("detection_status") == "基线不足"
        and early_deviation.get("history_sample_count") == 0
        and early_body.get("event") is None
        and early_body.get("saved") is False
        and early_body.get("duplicate") is False
        and early_analysis.get("candidate_faults") == []
        and row_count() == early_rows,
        f"status={early.status_code} detection={None if not isinstance(early_deviation, dict) else early_deviation.get('detection_status')} rows={row_count()}",
    )

    missing_rows = row_count()
    missing = client.post("/api/monitoring/process-record", json={"session_id": SESSION_A, "record_id": "REC-NOT-EXIST-123"})
    missing_body = missing.json()
    BODIES.append(missing_body)
    check(
        "E 不存在的记录不生成事件",
        missing.status_code == 404
        and missing_body.get("success") is False
        and "没有找到记录编号" in str(missing_body.get("message"))
        and "event" not in missing_body
        and row_count() == missing_rows,
        f"status={missing.status_code} message={missing_body.get('message')} rows={row_count()}",
    )

    reopened = sqlite3.connect(TEMP_DB)
    try:
        stored = reopened.execute(
            "SELECT session_id, record_id, collected_at, processed_at, metrics_json FROM monitoring_events WHERE event_id = ?",
            (event.get("event_id"),),
        ).fetchone()
    finally:
        reopened.close()
    stored_metrics = json.loads(stored[4]) if stored is not None else []
    stored_y = next((item for item in stored_metrics if isinstance(item, dict) and item.get("field") == "Y向振动(mm/s)"), None)
    check(
        "F 重新打开数据库后事件仍在",
        stored is not None
        and stored[0] == SESSION_A
        and stored[1] == TARGET
        and stored[2] == "2026-04-03 11:05:00"
        and stored[3] == event.get("processed_at")
        and isinstance(stored_y, dict)
        and stored_y.get("current_value") == 3.017
        and stored_y.get("robust_z") == 3.923797970479703,
        f"found={stored is not None}",
    )

    by_session = client.get("/api/monitoring/events", params={"session_id": SESSION_A, "limit": 50})
    by_device = client.get("/api/monitoring/events", params={"device_id": "MM-01", "limit": 50})
    by_other_device = client.get("/api/monitoring/events", params={"device_id": "FF-01", "limit": 50})
    limited = client.get("/api/monitoring/events", params={"limit": 1})
    too_large = client.get("/api/monitoring/events", params={"limit": 101})
    session_body = by_session.json()
    device_body = by_device.json()
    other_device_body = by_other_device.json()
    limited_body = limited.json()
    BODIES.extend([session_body, device_body, other_device_body, limited_body, too_large.json()])
    session_events = session_body.get("events") if isinstance(session_body, dict) else None
    device_events = device_body.get("events") if isinstance(device_body, dict) else None
    limited_events = limited_body.get("events") if isinstance(limited_body, dict) else None
    check(
        "G 按会话、设备和条数查询",
        by_session.status_code == 200
        and isinstance(session_events, list)
        and len(session_events) == 1
        and session_events[0].get("session_id") == SESSION_A
        and by_device.status_code == 200
        and isinstance(device_events, list)
        and len(device_events) == 2
        and {item.get("session_id") for item in device_events} == {SESSION_A, SESSION_B}
        and by_other_device.status_code == 200
        and other_device_body.get("events") == []
        and limited.status_code == 200
        and limited_body.get("limit") == 1
        and isinstance(limited_events, list)
        and len(limited_events) == 1
        and too_large.status_code == 422,
        f"session={None if not isinstance(session_events, list) else len(session_events)} device={None if not isinstance(device_events, list) else len(device_events)} limit={limited.status_code} overflow={too_large.status_code}",
    )

    injected = client.post(
        "/api/monitoring/process-record",
        json={"session_id": "bad' OR 1=1 --", "record_id": TARGET},
    )
    readonly_rows = row_count()
    readonly = client.get(f"/api/dataset/analysis/{TARGET}")
    BODIES.append(readonly.json())
    BODIES.append(injected.json())
    leaked: list[str] = []
    for body in BODIES:
        label_keys(body, leaked)
    database_text = TEMP_DB.read_bytes().decode("utf-8", errors="ignore")
    leaked_in_db = [label for label in LABELS if label in database_text]
    check(
        "H 响应和数据库不含答案标签",
        not leaked
        and not leaked_in_db
        and injected.status_code == 422
        and readonly.status_code == 200
        and row_count() == readonly_rows
        and "api_key" not in database_text.casefold(),
        f"keys={','.join(leaked)} db={','.join(leaked_in_db)} rows={row_count()} analysis={readonly.status_code}",
    )

    summary = client.get("/api/dataset/summary")
    devices = client.get("/api/dataset/devices")
    records = client.get("/api/dataset/records", params={"device_id": "MM-01", "limit": 1})
    observed = client.get("/api/dataset/observations/REC-MA-20260403-00346")
    search = client.get("/api/knowledge/search", params={"q": "轴承", "top_k": 1})
    graph = client.get("/api/knowledge/graph")
    vibration = client.post(
        "/api/vibration/analyze",
        params={"sampling_rate": "1000", "column": "value"},
        content=("value\n" + "\n".join(["0", "1", "0", "-1", "0", "1", "0", "-1"])).encode("utf-8"),
        headers={"content-type": "text/csv"},
    )
    empty_chat = client.post("/api/ai/chat", json={})
    summary_body = summary.json()
    devices_body = devices.json()
    records_body = records.json()
    graph_body = graph.json()
    check(
        "I 原有接口仍可用且未调用模型",
        summary.status_code == 200
        and summary_body.get("device_count") == 46
        and summary_body.get("record_count") == 24000
        and devices.status_code == 200
        and devices_body.get("device_count") == 46
        and records.status_code == 200
        and records_body.get("returned_count") == 1
        and observed.status_code == 200
        and search.status_code == 200
        and search.json().get("count", 0) >= 1
        and graph.status_code == 200
        and len(graph_body.get("nodes", [])) == 252
        and len(graph_body.get("edges", [])) == 464
        and vibration.status_code == 200
        and vibration.json().get("analysis_note") == "这是信号分析结果，不是故障确诊。"
        and empty_chat.status_code == 422
        and model_calls["count"] == 0
        and row_count() == readonly_rows,
        f"summary={summary.status_code} graph={graph.status_code} vibration={vibration.status_code} model_calls={model_calls['count']}",
    )

    blocked_parent = TEMP_DIR / "not-a-directory"
    blocked_parent.write_text("x", encoding="utf-8")
    os.environ["MONITORING_EVENTS_DB"] = str(blocked_parent / "events.sqlite3")
    failed_save = client.post("/api/monitoring/process-record", json={"session_id": "replay-test-fail", "record_id": TARGET})
    failed_body = failed_save.json()
    os.environ["MONITORING_EVENTS_DB"] = str(TEMP_DB)
    check(
        "数据库打不开时不假装保存成功",
        failed_save.status_code == 500
        and failed_body.get("success") is False
        and "监测事件数据库无法打开" in str(failed_body.get("message"))
        and failed_body.get("saved") is not True
        and row_count() == readonly_rows,
        f"status={failed_save.status_code} message={failed_body.get('message')}",
    )

    race_results: list[dict] = []
    race_errors: list[str] = []

    def race_post() -> None:
        try:
            response = client.post(
                "/api/monitoring/process-record",
                json={"session_id": "replay-test-race", "record_id": TARGET},
            )
            race_results.append(response.json())
        except Exception as exc:
            race_errors.append(str(exc))

    first_thread = threading.Thread(target=race_post)
    second_thread = threading.Thread(target=race_post)
    first_thread.start()
    second_thread.start()
    first_thread.join()
    second_thread.join()
    saved_flags = [item.get("saved") is True for item in race_results if isinstance(item, dict)]
    duplicate_flags = [item.get("duplicate") is True for item in race_results if isinstance(item, dict)]
    race_count = row_count()
    check(
        "并发重复提交只保存一条",
        not race_errors
        and len(race_results) == 2
        and saved_flags.count(True) == 1
        and duplicate_flags.count(True) == 1
        and race_count == readonly_rows + 1,
        f"errors={race_errors} saved={saved_flags} duplicate={duplicate_flags} rows={race_count}",
    )

    import py_compile

    py_compile.compile(str(ROOT / "backend" / "app" / "main.py"), doraise=True)
    py_compile.compile(str(ROOT / "backend" / "app" / "services" / "monitoring_events.py"), doraise=True)
    after = file_hash(EXCEL)
    default_after = DEFAULT_DATABASE.exists()
    default_hash_after = file_hash(DEFAULT_DATABASE) if default_after else ""
    check(
        "J 编译通过、Excel 哈希不变且测试未写入默认库",
        after == EXPECTED_HASH
        and after == before
        and default_after == default_existed
        and default_hash_after == default_hash
        and "runtime" in DEFAULT_DATABASE.parts
        and "official" not in DEFAULT_DATABASE.parts
        and "official_dataset" not in DEFAULT_DATABASE.parts,
        f"excel={after} default_exists={default_after}",
    )

    failed = [name for name, passed, _detail in RESULTS if not passed]
    print(f"SUMMARY {len(RESULTS) - len(failed)}/{len(RESULTS)}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
