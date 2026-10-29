"""监测启动 → 偏离事件 → reminder_required → 前端弹窗过滤 的链路验收。

使用临时监测库，不改正式库与官方 Excel，不调用 DeepSeek，不改统计阈值。
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TEMP_DIR = Path(tempfile.mkdtemp(prefix="monitor-alert-pipeline-"))
TEMP_DB = TEMP_DIR / "events.sqlite3"
os.environ["MONITORING_EVENTS_DB"] = str(TEMP_DB)
os.environ["EPISODE_DIAGNOSIS_ENABLED"] = "0"
os.environ["STARTUP_TOOL_SELFTEST"] = "0"

from fastapi.testclient import TestClient

import app.services.automatic_monitoring as automatic_monitoring
import app.services.deepseek as deepseek
from app.main import app
from app.services.monitoring_events import clear_alert_episodes, process_monitoring_record

EXCEL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
PRODUCTION_DB = ROOT / "data" / "runtime" / "monitoring_events.sqlite3"
EXCEL_HASH = "1e9caeba1fe526f3ad1b5dd6445a9dbcc02fa22ec02273d9863ebc84e3e30f87"
SAMPLE = "REC-MA-20260401-00319"
RESULTS: list[tuple[str, bool, str]] = []


def file_hash(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((name, passed, detail))
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
    excel_before = file_hash(EXCEL)
    production_before = file_hash(PRODUCTION_DB)
    check("预检 Excel 哈希", excel_before == EXCEL_HASH, excel_before)

    def reject_model(*_args, **_kwargs):
        raise AssertionError("本测试不能调用 DeepSeek")

    deepseek.complete_chat = reject_model

    with TestClient(app) as client:
        missing = client.post("/api/monitoring/auto/start", json={"device_id": "NO-DEVICE", "interval_seconds": 0})
        check(
            "1 监测启动失败有明确错误",
            missing.status_code in {404, 422} and isinstance(missing.json().get("message"), str) and missing.json().get("message"),
            f"status={missing.status_code} body={missing.json()}",
        )

        started = client.post("/api/monitoring/auto/start", json={"device_id": "MM-01", "interval_seconds": 0})
        body = started.json()
        task_id = str(body.get("task_id") or "")
        running = wait_status(client, "MM-01", lambda item: int(item.get("processed_count") or 0) >= 3, 30)
        check(
            "2 任务正常运行并推进进度",
            started.status_code == 200
            and body.get("status") == "运行中"
            and running.get("status") == "运行中"
            and int(running.get("processed_count") or 0) >= 3,
            f"start={body.get('status')} processed={running.get('processed_count')}",
        )
        client.post("/api/monitoring/auto/stop", json={"device_id": "MM-01", "task_id": task_id})
        wait_status(client, "MM-01", lambda item: item.get("status") in {"已停止", "已中断"}, 20)

        clear_alert_episodes("sess-sample")
        first = process_monitoring_record("sess-sample", SAMPLE)
        again = process_monitoring_record("sess-sample", SAMPLE)
        listed = client.get(
            "/api/monitoring/events",
            params={"session_id": "sess-sample", "device_id": "MM-01", "limit": 20},
        )
        listed_body = listed.json()
        events = listed_body.get("events") if isinstance(listed_body, dict) else None
        sample_event = None
        if isinstance(events, list):
            for item in events:
                if isinstance(item, dict) and item.get("record_id") == SAMPLE:
                    sample_event = item
                    break
        check(
            "3 样本记录可检测到偏离并生成事件",
            first.get("ok") is True
            and first.get("saved") is True
            and first.get("reminder_required") is True
            and isinstance(first.get("event"), dict)
            and first["event"].get("reminder_required") is True
            and first["event"].get("detection_status") == "偏离基线"
            and first["event"].get("confirmed") is False
            and "不是故障确诊" in str(first["event"].get("event_note"))
            and listed.status_code == 200
            and isinstance(sample_event, dict)
            and sample_event.get("reminder_required") is True,
            f"saved={first.get('saved')} reminder={first.get('reminder_required')} listed={sample_event}",
        )
        check(
            "4 重复处理同一记录不重复提醒",
            again.get("duplicate") is True and again.get("reminder_required") is False and again.get("saved") is False,
            f"dup={again.get('duplicate')} reminder={again.get('reminder_required')}",
        )

        # 前端弹窗规则：reminder_required 过程起点触发每会话一次汇总弹窗，不再连锁队列弹窗。
        from pathlib import Path as _P

        alert_src = (_P(ROOT) / "frontend" / "src" / "monitoring" / "anomalyAlert.ts").read_text(encoding="utf-8")
        page_src = (_P(ROOT) / "frontend" / "src" / "pages" / "EquipmentMonitoringPage.tsx").read_text(encoding="utf-8")
        check(
            "5 前端仅对 fault_alert_required 故障告警弹窗",
            "needsFaultAlert" in alert_src
            and "fault_alert_required === true" in alert_src
            and "ingestSessionEvents" in alert_src
            and "autoSummaryShown" in alert_src
            and "createMonitorAlertSession" in page_src
            and "ingestSessionEvents" in page_src
            and "确认故障告警" in page_src
            and "异常中心" in page_src
            and "setAlertQueue(" not in page_src
            and "关闭后继续查看" not in page_src,
        )

        early = process_monitoring_record("sess-early", "REC-MA-20260314-00001")
        check(
            "6 无偏离时不生成提醒事件",
            early.get("ok") is True
            and early.get("saved") is False
            and early.get("event") is None
            and early.get("reminder_required") is False,
            f"saved={early.get('saved')} detection={(early.get('analysis') or {}).get('statistical_deviation', {}).get('detection_status')}",
        )

        # 模拟启动失败路径（处理函数抛错）
        original = automatic_monitoring.process_monitoring_record

        def boom(_session: str, _record: str) -> dict:
            raise RuntimeError("模拟失败")

        automatic_monitoring.process_monitoring_record = boom
        try:
            failed = client.post("/api/monitoring/auto/start", json={"device_id": "CC-03", "interval_seconds": 0})
            failed_status = wait_status(client, "CC-03", lambda item: item.get("status") != "运行中", 15)
        finally:
            automatic_monitoring.process_monitoring_record = original
        check(
            "7 运行中分析失败会落到已出错且不假写偏离",
            failed.status_code == 200
            and failed_status.get("status") == "已出错"
            and int(failed_status.get("deviation_count") or 0) == 0
            and "没有把未完成的记录写成异常事件" in str(failed_status.get("error_message") or ""),
            failed_status.get("status"),
        )

    # 确认临时库存在 reminder_required，正式库未被本测试改写
    conn = sqlite3.connect(TEMP_DB)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(monitoring_events)")}
    finally:
        conn.close()
    check("8 临时事件库含 reminder_required 列", "reminder_required" in cols, str(sorted(cols)))
    check(
        "9 正式监测库与 Excel 未被本测试改写",
        file_hash(EXCEL) == excel_before == EXCEL_HASH and file_hash(PRODUCTION_DB) == production_before,
    )

    failed = [name for name, passed, _ in RESULTS if not passed]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
