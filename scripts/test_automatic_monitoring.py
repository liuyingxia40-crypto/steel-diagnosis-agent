"""验证后端自动历史回放。使用临时监测库，不调用 DeepSeek。"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TEMP_DIR = Path(tempfile.mkdtemp(prefix="automatic-monitoring-"))
TEMP_DB = TEMP_DIR / "events.sqlite3"
os.environ["MONITORING_EVENTS_DB"] = str(TEMP_DB)
os.environ["STARTUP_TOOL_SELFTEST"] = "0"

from fastapi.testclient import TestClient

import app.services.automatic_monitoring as automatic_monitoring
import app.services.deepseek as deepseek
from app.main import app
from app.services.agent_tools import TOOLS
from app.services.automatic_monitoring import recover_abandoned_tasks
from app.services.official_dataset import MAX_LIMIT, get_observation, list_device_record_ids

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
PROTECTED = [
    ROOT / "backend" / "app" / "services" / "anomaly_detection.py",
    ROOT / "backend" / "app" / "services" / "diagnostic_bridge.py",
    ROOT / "backend" / "app" / "services" / "agent_tools.py",
    ROOT / "frontend" / "src" / "pages" / "EquipmentMonitoringPage.tsx",
    ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx",
]
PRODUCTION_DB = ROOT / "data" / "runtime" / "monitoring_events.sqlite3"
EXCEL_HASH = "1e9caeba1fe526f3ad1b5dd6445a9dbcc02fa22ec02273d9863ebc84e3e30f87"
RESULTS: list[tuple[str, bool, str]] = []
DEEPSEEK_CALLS: list[str] = []


def file_hash(path: Path) -> str:
    if not path.is_file():
        return ""
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


def wait_status(client: TestClient, device_id: str, predicate, timeout: float) -> dict:
    started = time.monotonic()
    last: dict = {}
    while time.monotonic() - started < timeout:
        response = client.get("/api/monitoring/auto/status", params={"device_id": device_id})
        body = response.json()
        last = body if isinstance(body, dict) else {"message": body}
        if response.status_code == 200 and predicate(last):
            return last
        time.sleep(0.05)
    return last


def event_rows(task_id: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(TEMP_DB)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT record_id, event_type, detection_status, diagnosis_level, event_note, metrics_json, candidate_faults_json FROM monitoring_events WHERE session_id = ?",
            (task_id,),
        ).fetchall()
    finally:
        conn.close()


def main() -> None:
    before = {path: file_hash(path) for path in PROTECTED}
    production_before = file_hash(PRODUCTION_DB)
    original_complete = deepseek.complete_chat

    def reject_model(*_args, **_kwargs):
        DEEPSEEK_CALLS.append("complete_chat")
        raise AssertionError("自动监测不能调用 DeepSeek")

    deepseek.complete_chat = reject_model
    sequence = list_device_record_ids("CC-01")
    record_ids = sequence["record_ids"]
    times = [get_observation(record_id)["record"]["collected_at"] for record_id in record_ids]
    routes = {getattr(route, "path", "") for route in app.routes}
    tool_names = [item["function"]["name"] for item in TOOLS]
    try:
        with TestClient(app) as client:
            started = client.post("/api/monitoring/auto/start", json={"device_id": "CC-02", "interval_seconds": 0.25})
            started_body = started.json()
            check(
                "A 启动一台设备的后端自动监测",
                started.status_code == 200
                and started_body.get("device_id") == "CC-02"
                and started_body.get("status") == "运行中"
                and str(started_body.get("task_id", "")).startswith("auto-")
                and "没有连接实时传感器" in started_body.get("monitoring_note", ""),
                started_body.get("message", started_body.get("status", "")),
            )
            running = wait_status(client, "CC-02", lambda item: item.get("processed_count", 0) >= 2, 20)
            check(
                "B 不调用 process-record 也会连续处理多条记录",
                running.get("processed_count", 0) >= 2 and running.get("status") == "运行中",
                f"processed={running.get('processed_count')} status={running.get('status')}",
            )
            duplicate = client.post("/api/monitoring/auto/start", json={"device_id": "CC-02", "interval_seconds": 0})
            duplicate_body = duplicate.json()
            check(
                "E 同一设备重复启动不会创建两个任务",
                duplicate.status_code == 409 and duplicate_body.get("message") == "这台设备已有自动监测任务正在运行。",
                duplicate_body.get("message", ""),
            )
            stopped = client.post("/api/monitoring/auto/stop", json={"device_id": "CC-02"})
            settled = wait_status(client, "CC-02", lambda item: item.get("status") in {"已停止", "已中断"}, 20)
            frozen = settled.get("processed_count")
            time.sleep(0.8)
            later = client.get("/api/monitoring/auto/status", params={"device_id": "CC-02"}).json()
            check(
                "F 停止后不再继续处理记录",
                stopped.status_code == 200
                and settled.get("status") == "已停止"
                and later.get("processed_count") == frozen
                and isinstance(frozen, int)
                and frozen < later.get("total_count", 0),
                f"status={later.get('status')} processed={later.get('processed_count')}/{later.get('total_count')}",
            )
            full = client.post("/api/monitoring/auto/start", json={"device_id": "CC-01", "interval_seconds": 0})
            full_body = full.json()
            finished = wait_status(
                client,
                "CC-01",
                lambda item: item.get("status") in {"已结束", "已出错", "已中断", "已停止"},
                90,
            )
            rows = event_rows(full_body.get("task_id", ""))
            found_labels: list[str] = []
            label_keys(finished, found_labels)
            for row in rows:
                label_keys(json.loads(row["metrics_json"]), found_labels)
                label_keys(json.loads(row["candidate_faults_json"]), found_labels)
            first_saved = any(row["record_id"] == record_ids[0] for row in rows)
            duplicate_events = len(rows) != len({row["record_id"] for row in rows})
            check(
                "C 统计偏离写入已有 SQLite 事件",
                finished.get("status") == "已结束"
                and finished.get("deviation_count", 0) >= 1
                and len(rows) == finished.get("deviation_count")
                and all(row["event_type"] == "统计偏离" and row["detection_status"] == "偏离基线" for row in rows)
                and all("不是故障确诊" in row["event_note"] for row in rows)
                and all("确诊" not in row["diagnosis_level"] for row in rows),
                f"deviations={finished.get('deviation_count')} rows={len(rows)}",
            )
            check(
                "D 正常记录不会被写成异常事件",
                finished.get("processed_count") == 400
                and finished.get("deviation_count", 0) < finished.get("processed_count", 0)
                and not first_saved,
                f"processed={finished.get('processed_count')} deviations={finished.get('deviation_count')} first_saved={first_saved}",
            )
            check(
                "G 处理到最后一条记录后结束",
                finished.get("status") == "已结束"
                and finished.get("processed_count") == len(record_ids) == 400
                and finished.get("processed_count") > MAX_LIMIT
                and finished.get("current_record_id") == record_ids[-1]
                and not duplicate_events
                and len(record_ids) == len(set(record_ids))
                and any(count > 1 for count in Counter(times).values())
                and times == sorted(times),
                f"current={finished.get('current_record_id')} total={len(record_ids)}",
            )
            check(
                "I 不读取官方 Excel 答案标签",
                not found_labels and EXCEL_HASH == before[PROTECTED[-1]],
                ",".join(found_labels),
            )
            original_process = automatic_monitoring.process_monitoring_record

            def fail_process(_session_id: str, _record_id: str) -> dict:
                raise RuntimeError("模拟分析失败")

            automatic_monitoring.process_monitoring_record = fail_process
            try:
                failed = client.post("/api/monitoring/auto/start", json={"device_id": "CC-03", "interval_seconds": 0})
                failed_body = failed.json()
                failed_status = wait_status(client, "CC-03", lambda item: item.get("status") != "运行中", 15)
            finally:
                automatic_monitoring.process_monitoring_record = original_process
            failed_rows = event_rows(failed_body.get("task_id", ""))
            check(
                "H 任务异常和重启残留都不会继续显示运行中",
                failed.status_code == 200
                and failed_status.get("status") == "已出错"
                and failed_status.get("status") != "运行中"
                and failed_rows == []
                and "没有把未完成的记录写成异常事件" in failed_status.get("error_message", ""),
                failed_status.get("status", ""),
            )
            conn = sqlite3.connect(TEMP_DB)
            try:
                conn.execute(
                    """
                    INSERT INTO automatic_monitoring_tasks (
                        task_id, device_id, status, current_record_id, processed_count,
                        deviation_count, total_count, interval_seconds, last_processed_at,
                        error_message, started_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "auto-orphan01",
                        "CC-09",
                        "运行中",
                        "",
                        0,
                        0,
                        400,
                        1,
                        "",
                        "",
                        "2026-01-01T00:00:00+08:00",
                        "2026-01-01T00:00:00+08:00",
                    ),
                )
                conn.commit()
            finally:
                conn.close()
            recovered = recover_abandoned_tasks()
            orphan = client.get("/api/monitoring/auto/status", params={"task_id": "auto-orphan01"}).json()
            check(
                "H2 重启后清除虚假运行中任务",
                recovered >= 1 and orphan.get("status") == "已中断" and "没有继续处理记录" in orphan.get("error_message", ""),
                f"recovered={recovered} status={orphan.get('status', orphan.get('message', ''))}",
            )
            manual = client.post(
                "/api/monitoring/process-record",
                json={"session_id": "manual-check", "record_id": record_ids[0]},
            )
            listed = client.get("/api/monitoring/events", params={"session_id": "manual-check", "limit": 10})
            check(
                "K 原有监测接口和五个智能体工具仍在",
                manual.status_code == 200
                and manual.json().get("saved") is False
                and listed.status_code == 200
                and "/api/vibration/analyze" in routes
                and "/api/monitoring/process-record" in routes
                and tool_names == [
                    "search_knowledge",
                    "get_equipment_relations",
                    "diagnose_fault",
                    "detect_anomaly",
                    "analyze_equipment_record",
                ],
                f"manual={manual.status_code}",
            )
            closing = client.post("/api/monitoring/auto/start", json={"device_id": "FF-01", "interval_seconds": 1})
            automatic_monitoring.shutdown_automatic_monitoring()
            time.sleep(0.3)
            closed = client.get("/api/monitoring/auto/status", params={"device_id": "FF-01"}).json()
            refused = client.post("/api/monitoring/auto/start", json={"device_id": "CC-04", "interval_seconds": 0})
            check(
                "H3 服务关闭后没有遗留运行中任务",
                closing.status_code == 200
                and closed.get("status") != "运行中"
                and refused.status_code == 503,
                f"closed={closed.get('status')} refused={refused.status_code}",
            )
    finally:
        deepseek.complete_chat = original_complete
    after = {path: file_hash(path) for path in PROTECTED}
    check(
        "J 不修改原有诊断算法、智能体工具和前端",
        before == after and not DEEPSEEK_CALLS,
        ",".join(path.name for path in PROTECTED if before[path] != after[path]),
    )
    check(
        "L 正式监测事件库没有被写入",
        file_hash(PRODUCTION_DB) == production_before,
        str(TEMP_DB),
    )
    print(f"DEVICE CC-01 RECORDS {len(record_ids)} DEVIATIONS {finished.get('deviation_count') if 'finished' in locals() else 'unknown'}")
    print(f"{sum(item[1] for item in RESULTS)}/{len(RESULTS)} passed")
    if not all(item[1] for item in RESULTS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
