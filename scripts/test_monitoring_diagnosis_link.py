"""验证统计偏离事件带上同一次分析里的诊断线索。使用临时数据库。"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TEMP_DIR = Path(tempfile.mkdtemp(prefix="monitoring-diagnosis-"))
TEMP_DB = TEMP_DIR / "events.sqlite3"
os.environ["MONITORING_EVENTS_DB"] = str(TEMP_DB)
os.environ["STARTUP_TOOL_SELFTEST"] = "0"

from fastapi.testclient import TestClient

import app.services.deepseek as deepseek
import app.services.diagnostic_bridge as diagnostic_bridge
import app.services.monitoring_events as monitoring_events
from app.main import app
from app.services.agent_tools import TOOLS
from app.services.official_dataset import list_device_record_ids

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
TARGET = "REC-MA-20260403-00346"
PROTECTED = [
    ROOT / "backend" / "app" / "services" / "anomaly_detection.py",
    ROOT / "backend" / "app" / "services" / "diagnostic_bridge.py",
    ROOT / "backend" / "app" / "services" / "agent_tools.py",
    ROOT / "frontend" / "src" / "pages" / "EquipmentMonitoringPage.tsx",
    ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx",
    ROOT / "data" / "processed" / "graph" / "graph.json",
]
PRODUCTION_DB = ROOT / "data" / "runtime" / "monitoring_events.sqlite3"
EXCEL_HASH = "1e9caeba1fe526f3ad1b5dd6445a9dbcc02fa22ec02273d9863ebc84e3e30f87"
RESULTS: list[bool] = []
DEEPSEEK_CALLS: list[str] = []
DIAGNOSIS_CALLS: list[str] = []


def file_hash(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append(passed)
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
        last = body if isinstance(body, dict) else {}
        if response.status_code == 200 and predicate(last):
            return last
        time.sleep(0.05)
    return last


def main() -> None:
    before = {path: file_hash(path) for path in PROTECTED}
    production_before = file_hash(PRODUCTION_DB)
    original_complete = deepseek.complete_chat
    original_analysis = monitoring_events.analyze_record_for_diagnosis

    def reject_model(*_args, **_kwargs):
        DEEPSEEK_CALLS.append("complete_chat")
        raise AssertionError("监测诊断不能调用 DeepSeek")

    def counted_analysis(record_id: str):
        DIAGNOSIS_CALLS.append(record_id)
        return original_analysis(record_id)

    deepseek.complete_chat = reject_model
    monitoring_events.analyze_record_for_diagnosis = counted_analysis
    first_ids = list_device_record_ids("CC-01")["record_ids"]
    try:
        with TestClient(app) as client:
            started = client.post("/api/monitoring/auto/start", json={"device_id": "CC-01", "interval_seconds": 0})
            task = started.json()
            progressed = wait_status(
                client,
                "CC-01",
                lambda item: item.get("deviation_count", 0) >= 1 or item.get("status") in {"已结束", "已出错"},
                90,
            )
            client.post("/api/monitoring/auto/stop", json={"device_id": "CC-01", "task_id": task.get("task_id")})
            settled = wait_status(client, "CC-01", lambda item: item.get("status") in {"已停止", "已结束", "已中断"}, 20)
            events = client.get(
                "/api/monitoring/events",
                params={"session_id": task.get("task_id"), "device_id": "CC-01", "limit": 100},
            )
            event_body = events.json()
            rows = event_body.get("events") if isinstance(event_body, dict) else []
            sample = rows[0] if isinstance(rows, list) and rows else {}
            found_labels: list[str] = []
            label_keys(rows, found_labels)
            check(
                "1 自动监测的统计偏离带有同一次诊断线索",
                started.status_code == 200
                and progressed.get("deviation_count", 0) >= 1
                and events.status_code == 200
                and isinstance(sample, dict)
                and sample.get("device_id") == "CC-01"
                and sample.get("record_id")
                and isinstance(sample.get("metrics"), list)
                and len(sample.get("metrics") or []) > 0
                and isinstance(sample.get("diagnostic_clues"), list)
                and isinstance(sample.get("evidence_sources"), list)
                and isinstance(sample.get("missing_conditions"), list)
                and "证据不足，无法确定具体故障原因" in str(sample.get("diagnosis_status"))
                and sample.get("confirmed") is False
                and all(item.get("confirmed") is not True for item in sample.get("candidate_faults") or []),
                f"status={sample.get('diagnosis_status')} clues={len(sample.get('diagnostic_clues') or [])}",
            )
            direct = client.post("/api/monitoring/process-record", json={"session_id": "diag-link", "record_id": TARGET})
            direct_body = direct.json()
            event = direct_body.get("event") if isinstance(direct_body, dict) else None
            before_calls = len(DIAGNOSIS_CALLS)
            again = client.post("/api/monitoring/process-record", json={"session_id": "diag-link", "record_id": TARGET})
            again_body = again.json()
            normal = client.post("/api/monitoring/process-record", json={"session_id": "diag-link", "record_id": first_ids[0]})
            conn = sqlite3.connect(TEMP_DB)
            conn.row_factory = sqlite3.Row
            try:
                stored = conn.execute(
                    "SELECT diagnosis_json, record_id FROM monitoring_events WHERE session_id = ? AND record_id = ?",
                    ("diag-link", TARGET),
                ).fetchone()
                normal_row = conn.execute(
                    "SELECT event_id FROM monitoring_events WHERE session_id = ? AND record_id = ?",
                    ("diag-link", first_ids[0]),
                ).fetchone()
                duplicates = conn.execute(
                    "SELECT record_id, COUNT(*) AS n FROM monitoring_events WHERE session_id = ? GROUP BY record_id, event_type HAVING n > 1",
                    ("diag-link",),
                ).fetchall()
            finally:
                conn.close()
            reopened = monitoring_events.list_monitoring_events("diag-link", "MM-01", 10)
            reopened_event = next(item for item in reopened["events"] if item["record_id"] == TARGET)
            trace = json.loads(stored["diagnosis_json"]) if stored is not None else {}
            event_text = json.dumps(event, ensure_ascii=False) if isinstance(event, dict) else ""
            label_keys(event, found_labels)
            check(
                "2 证据不足时不写虚假故障名称",
                isinstance(event, dict)
                and event.get("candidate_faults") == []
                and event.get("confirmed") is False
                and "轴承磨损" not in event_text
                and "已确诊" not in event_text
                and "概率" not in str(event.get("diagnosis_status")),
                event.get("diagnosis_status") if isinstance(event, dict) else "",
            )
            check(
                "3 正常记录不生成诊断事件",
                normal.status_code == 200 and normal.json().get("saved") is False and normal.json().get("event") is None and normal_row is None,
                first_ids[0],
            )
            check(
                "4 同一任务重复处理不新增诊断记录",
                again.status_code == 200
                and again_body.get("duplicate") is True
                and again_body.get("saved") is False
                and again_body.get("event", {}).get("event_id") == event.get("event_id")
                and len(DIAGNOSIS_CALLS) >= before_calls + 1
                and not duplicates,
                f"duplicate={again_body.get('duplicate')} rows={len(duplicates)}",
            )
            check(
                "5 重新打开数据库仍能读到诊断线索",
                stored is not None
                and trace.get("diagnosis_status") == reopened_event.get("diagnosis_status")
                and reopened_event.get("diagnostic_clues") == event.get("diagnostic_clues")
                and reopened_event.get("evidence_sources") == event.get("evidence_sources")
                and bool(reopened_event.get("missing_conditions")),
                reopened_event.get("diagnosis_status", ""),
            )
            check("6 不调用 DeepSeek", not DEEPSEEK_CALLS)
            check("7 不使用 Excel 答案标签", not found_labels, ",".join(found_labels))
            status = client.get("/api/monitoring/auto/status", params={"device_id": "CC-01"})
            check(
                "8 自动监测启停和进度查询仍可用",
                settled.get("status") in {"已停止", "已结束", "已中断"}
                and settled.get("processed_count", 0) >= 1
                and status.status_code == 200
                and status.json().get("device_id") == "CC-01",
                f"{settled.get('status')} {settled.get('processed_count')}/{settled.get('total_count')}",
            )
            routes = {getattr(route, "path", "") for route in app.routes}
            check(
                "9 五个工具和知识图谱接口仍在",
                [item["function"]["name"] for item in TOOLS]
                == [
                    "search_knowledge",
                    "get_equipment_relations",
                    "diagnose_fault",
                    "detect_anomaly",
                    "analyze_equipment_record",
                ]
                and "/api/knowledge/graph" in routes
                and diagnostic_bridge.analyze_record_for_diagnosis is original_analysis,
            )
            print("SAMPLE")
            print(
                json.dumps(
                    {
                        "record_id": event.get("record_id") if isinstance(event, dict) else "",
                        "device_id": event.get("device_id") if isinstance(event, dict) else "",
                        "diagnosis_level": event.get("diagnosis_level") if isinstance(event, dict) else "",
                        "diagnosis_status": event.get("diagnosis_status") if isinstance(event, dict) else "",
                        "metrics": [item.get("field") for item in (event.get("metrics") or [])] if isinstance(event, dict) else [],
                        "clues": [item.get("feature_code") for item in (event.get("diagnostic_clues") or [])] if isinstance(event, dict) else [],
                        "sources": event.get("evidence_sources") if isinstance(event, dict) else [],
                        "candidate_faults": event.get("candidate_faults") if isinstance(event, dict) else [],
                        "missing_count": len(event.get("missing_conditions") or []) if isinstance(event, dict) else 0,
                    },
                    ensure_ascii=False,
                )
            )
    finally:
        deepseek.complete_chat = original_complete
        monitoring_events.analyze_record_for_diagnosis = original_analysis
    after = {path: file_hash(path) for path in PROTECTED}
    check(
        "10 临时库之外的文件没有被改写",
        before == after and file_hash(PROTECTED[4]) == EXCEL_HASH and file_hash(PRODUCTION_DB) == production_before,
    )
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    if not all(RESULTS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
