"""核对旧监测事件不补诊断，振动分析接口仍在。使用临时数据库。"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TEMP_DIR = Path(tempfile.mkdtemp(prefix="monitor-diagnosis-view-"))
TEMP_DB = TEMP_DIR / "events.sqlite3"
os.environ["MONITORING_EVENTS_DB"] = str(TEMP_DB)
os.environ["STARTUP_TOOL_SELFTEST"] = "0"

import app.services.monitoring_events as monitoring_events
from app.services.agent_tools import TOOLS

TARGET = "REC-MA-20260403-00346"
PROTECTED = [
    ROOT / "backend" / "app" / "services" / "anomaly_detection.py",
    ROOT / "backend" / "app" / "services" / "diagnostic_bridge.py",
    ROOT / "backend" / "app" / "services" / "vibration_analysis.py",
    ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx",
    ROOT / "data" / "processed" / "graph" / "graph.json",
    ROOT / "data" / "runtime" / "monitoring_events.sqlite3",
]
EXCEL_HASH = "1e9caeba1fe526f3ad1b5dd6445a9dbcc02fa22ec02273d9863ebc84e3e30f87"
RESULTS: list[bool] = []


def file_hash(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append(bool(passed))
    print(("PASS " if passed else "FAIL ") + name + ("" if not detail else " | " + detail))


def main() -> None:
    before = {path: file_hash(path) for path in PROTECTED}
    saved = monitoring_events.process_monitoring_record("diag-view", TARGET)
    event = saved.get("event") if isinstance(saved, dict) else None
    conn = sqlite3.connect(TEMP_DB)
    try:
        conn.execute(
            """
            INSERT INTO monitoring_events (
                session_id, record_id, device_id, collected_at, processed_at,
                event_type, diagnosis_level, detection_status, data_source,
                event_scope, event_note, metrics_json, clues_json, candidate_faults_json, diagnosis_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-view",
                "REC-OLD-1",
                "MM-01",
                "2026-04-01 00:00:00",
                "2026-04-01T00:00:00+08:00",
                "统计偏离",
                "statistical_deviation",
                "偏离基线",
                "钢铁设备数据集_开发集.xlsx",
                "official_development_set_history_replay",
                monitoring_events.EVENT_NOTE,
                "[]",
                "[]",
                "[]",
                "",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    legacy = monitoring_events.list_monitoring_events("legacy-view", "MM-01", 10)
    legacy_event = legacy["events"][0]
    routes = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    names = [item["function"]["name"] for item in TOOLS]
    check(
        "新事件带有已保存的诊断痕迹",
        isinstance(event, dict)
        and event.get("diagnosis_available") is True
        and event.get("confirmed") is False
        and event.get("candidate_faults") == []
        and isinstance(event.get("evidence_sources"), list)
        and event.get("evidence_sources")
        and isinstance(event.get("missing_conditions"), list)
        and "证据不足，无法确定具体故障原因" in str(event.get("diagnosis_status")),
        str(event.get("diagnosis_status") if isinstance(event, dict) else ""),
    )
    check(
        "空 diagnosis_json 不补诊断",
        legacy_event.get("diagnosis_available") is False
        and legacy_event.get("diagnosis_status") == ""
        and legacy_event.get("evidence_sources") == []
        and legacy_event.get("missing_conditions") == []
        and legacy_event.get("confirmed") is False,
    )
    check(
        "振动分析、问答、图谱和五个工具仍在",
        '@app.post("/api/vibration/analyze")' in routes
        and '@app.post("/api/ai/chat"' in routes
        and '@app.get("/api/knowledge/graph")' in routes
        and names
        == [
            "search_knowledge",
            "get_equipment_relations",
            "diagnose_fault",
            "detect_anomaly",
            "analyze_equipment_record",
        ],
    )
    after = {path: file_hash(path) for path in PROTECTED}
    check("算法、图谱、Excel 和正式监测库没有被改写", before == after and file_hash(PROTECTED[3]) == EXCEL_HASH)
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    if not all(RESULTS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
