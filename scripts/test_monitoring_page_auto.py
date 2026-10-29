"""验证监测页接入后端自动监测，并使用临时事件库。"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TEMP_DIR = Path(tempfile.mkdtemp(prefix="monitor-page-auto-"))
TEMP_DB = TEMP_DIR / "events.sqlite3"
os.environ["MONITORING_EVENTS_DB"] = str(TEMP_DB)
os.environ["STARTUP_TOOL_SELFTEST"] = "0"

from fastapi.testclient import TestClient

from app.main import app

PAGE = ROOT / "frontend" / "src" / "pages" / "EquipmentMonitoringPage.tsx"
GRAPH = ROOT / "frontend" / "src" / "pages" / "KnowledgeGraphPage.tsx"
APP = ROOT / "frontend" / "src" / "App.tsx"
ALGO = ROOT / "backend" / "app" / "services" / "anomaly_detection.py"
BRIDGE = ROOT / "backend" / "app" / "services" / "diagnostic_bridge.py"
EXCEL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
PRODUCTION_DB = ROOT / "data" / "runtime" / "monitoring_events.sqlite3"
EXCEL_HASH = "1e9caeba1fe526f3ad1b5dd6445a9dbcc02fa22ec02273d9863ebc84e3e30f87"
RESULTS: list[bool] = []


def file_hash(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append(passed)
    print(("PASS " if passed else "FAIL ") + name + ("" if not detail else " | " + detail))


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
    before = {path: file_hash(path) for path in (GRAPH, APP, ALGO, BRIDGE, EXCEL, PRODUCTION_DB)}
    page = PAGE.read_text(encoding="utf-8") + "\n" + (ROOT / "frontend" / "src" / "monitoring" / "autoMonitor.ts").read_text(encoding="utf-8")
    with TestClient(app) as client:
        started = client.post("/api/monitoring/auto/start", json={"device_id": "CC-01", "interval_seconds": 0})
        body = started.json()
        check(
            "A CC-01可以启动后端监测",
            started.status_code == 200 and body.get("device_id") == "CC-01" and body.get("status") == "运行中",
            body.get("message", body.get("status", "")),
        )
        progressed = wait_status(client, "CC-01", lambda item: item.get("processed_count", 0) >= 2 and item.get("current_record_id"), 30)
        calls = [route.path for route in app.routes]
        check(
            "B 不逐条调用process-record也会继续处理",
            progressed.get("processed_count", 0) >= 2 and "/api/monitoring/process-record" not in page,
            f"processed={progressed.get('processed_count')}",
        )
        check(
            "C 状态包含已处理数量和当前记录编号",
            isinstance(progressed.get("processed_count"), int)
            and isinstance(progressed.get("total_count"), int)
            and bool(progressed.get("current_record_id")),
            f"{progressed.get('processed_count')}/{progressed.get('total_count')} {progressed.get('current_record_id')}",
        )
        deviated = wait_status(client, "CC-01", lambda item: item.get("deviation_count", 0) >= 1 or item.get("status") in {"已结束", "已出错"}, 90)
        events = client.get("/api/monitoring/events", params={"session_id": body.get("task_id"), "device_id": "CC-01", "limit": 100})
        event_body = events.json()
        rows = event_body.get("events") if isinstance(event_body, dict) else []
        check(
            "D 统计偏离写入后可由事件接口读出",
            events.status_code == 200
            and deviated.get("deviation_count", 0) >= 1
            and isinstance(rows, list)
            and len(rows) >= 1
            and all(item.get("event_type") == "统计偏离" and item.get("device_id") == "CC-01" for item in rows)
            and all("不是故障确诊" in item.get("event_note", "") for item in rows),
            f"deviations={deviated.get('deviation_count')} listed={len(rows) if isinstance(rows, list) else 0}",
        )
        other = client.post("/api/monitoring/auto/start", json={"device_id": "CC-02", "interval_seconds": 0.3})
        duplicate = client.post("/api/monitoring/auto/start", json={"device_id": "CC-02", "interval_seconds": 0.3})
        check(
            "F 重复启动返回409",
            other.status_code == 200 and duplicate.status_code == 409 and "已有自动监测任务正在运行" in duplicate.json().get("message", ""),
            duplicate.json().get("message", ""),
        )
        stopped = client.post("/api/monitoring/auto/stop", json={"device_id": "CC-02", "task_id": other.json().get("task_id")})
        settled = wait_status(client, "CC-02", lambda item: item.get("status") == "已停止", 20)
        frozen = settled.get("processed_count")
        time.sleep(0.7)
        later = client.get("/api/monitoring/auto/status", params={"device_id": "CC-02"}).json()
        check(
            "E 停止后不再继续处理",
            stopped.status_code == 200 and settled.get("status") == "已停止" and later.get("processed_count") == frozen and frozen < later.get("total_count", 0),
            f"{later.get('status')} {later.get('processed_count')}/{later.get('total_count')}",
        )
        refreshed = client.get("/api/monitoring/auto/status", params={"device_id": "CC-01"}).json()
        check(
            "G 再次查询仍能读到原任务状态",
            refreshed.get("task_id") == body.get("task_id") and refreshed.get("device_id") == "CC-01" and refreshed.get("status") in {"运行中", "已结束", "正在停止"},
            f"{refreshed.get('status')} {refreshed.get('processed_count')}",
        )
        check(
            "H 页面不再通过旧回放写入事件",
            "/api/monitoring/auto/start" in page
            and "/api/monitoring/auto/stop" in page
            and "/api/monitoring/auto/status" in page
            and "process-record" not in page
            and "这是页面演示回放" in page
            and "离开本页不会停止" in page,
        )
        check(
            "I 页面标明历史模拟而不是设备在线",
            "官方历史数据模拟监测" in page
            and "实时传感器在线" not in page
            and "真实工厂实时报警" not in page
            and "已确诊故障" not in page
            and "当前没有连接实时传感器" in page
            and "这是页面演示回放" in page,
        )
        routes = {getattr(route, "path", "") for route in app.routes}
        check(
            "J 知识图谱、问答和振动接口仍在",
            "/api/ai/chat" in routes and "/api/knowledge/graph" in routes and "/api/vibration/analyze" in routes and "/api/monitoring/process-record" in calls,
        )
        client.post("/api/monitoring/auto/stop", json={"device_id": "CC-01"})
    after = {path: file_hash(path) for path in (GRAPH, APP, ALGO, BRIDGE, EXCEL, PRODUCTION_DB)}
    check("正式库、算法和Excel没有被改写", before == after and file_hash(EXCEL) == EXCEL_HASH)
    print(f"{sum(RESULTS)}/{len(RESULTS)} passed")
    if not all(RESULTS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
