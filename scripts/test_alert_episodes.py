"""异常提醒过程生命周期：连续异常去重、多指标合并、恢复后再提醒、跨设备隔离。

不改动官方 Excel / 正式知识图谱，也不调用 DeepSeek。
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TEMP_DIR = Path(tempfile.mkdtemp(prefix="alert-episodes-"))
TEMP_DB = TEMP_DIR / "events.sqlite3"
os.environ["MONITORING_EVENTS_DB"] = str(TEMP_DB)
os.environ["EPISODE_DIAGNOSIS_ENABLED"] = "0"
os.environ["STARTUP_TOOL_SELFTEST"] = "0"

from app.services import monitoring_events as me

EXCEL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
EXPECTED_HASH = "1e9caeba1fe526f3ad1b5dd6445a9dbcc02fa22ec02273d9863ebc84e3e30f87"
RESULTS: list[tuple[str, bool, str]] = []


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((name, passed, detail))
    print(("PASS " if passed else "FAIL ") + name + ("" if not detail else " | " + detail))


def metric(field: str, value: float = 3.0) -> dict:
    return {
        "field": field,
        "unit": "mm/s",
        "current_value": value,
        "median": 1.0,
        "mad": 0.2,
        "robust_z": 4.0,
        "metric_status": "偏离基线",
    }


def analysis_for(
    record_id: str,
    device_id: str,
    fields: list[str] | None,
    *,
    collected_at: str = "2026-04-01 10:00:00",
) -> dict:
    metrics = [metric(field) for field in (fields or [])]
    status = "偏离基线" if metrics else "未发现显著偏离"
    return {
        "ok": True,
        "record_id": record_id,
        "data_source": "mock",
        "diagnosis_level": "statistical_deviation",
        "confirmed": False,
        "diagnostic_clues": [],
        "candidate_faults": [],
        "missing_data": [],
        "insufficient_reasons": [],
        "observation": {
            "record_id": record_id,
            "device_id": device_id,
            "collected_at": collected_at,
        },
        "statistical_deviation": {
            "record_id": record_id,
            "detection_status": status,
            "analyzed_metrics": metrics,
        },
    }


def process(session: str, record_id: str, device_id: str, fields: list[str] | None) -> dict:
    with patch.object(
        me,
        "analyze_record_for_diagnosis",
        return_value=analysis_for(record_id, device_id, fields),
    ):
        return me.process_monitoring_record(session, record_id)


def main() -> None:
    before = file_hash(EXCEL)
    check("G-pre 官方 Excel 哈希未改", before == EXPECTED_HASH, before)

    me.clear_alert_episodes("sess-a")
    me.clear_alert_episodes("sess-b")
    me.clear_alert_episodes("sess-dup")

    # A: 同一指标连续 10 条，只提醒一次；事件全部保留
    reminders_a = []
    events_a = []
    for index in range(10):
        result = process("sess-a", f"REC-A-{index:02d}", "MM-01", ["Y向振动(mm/s)"])
        reminders_a.append(result.get("reminder_required") is True)
        if isinstance(result.get("event"), dict):
            events_a.append(result["event"])
    listed_a = me.list_monitoring_events("sess-a", "MM-01", 100)
    check(
        "A 同一指标连续 10 条只提醒一次且事件全保留",
        reminders_a.count(True) == 1
        and reminders_a[0] is True
        and all(flag is False for flag in reminders_a[1:])
        and len(events_a) == 10
        and listed_a["count"] == 10
        and sum(1 for item in listed_a["events"] if item.get("reminder_required") is True) == 1,
        f"reminders={reminders_a} saved={len(events_a)} listed={listed_a['count']}",
    )

    # B: 同一条记录 3 个异常指标，只一个提醒，包含全部 3 项
    me.clear_alert_episodes("sess-b")
    multi = process(
        "sess-b",
        "REC-B-01",
        "MM-01",
        ["Y向振动(mm/s)", "X向振动(mm/s)", "Z向振动(mm/s)"],
    )
    multi_event = multi.get("event") if isinstance(multi.get("event"), dict) else {}
    multi_fields = [
        item.get("field")
        for item in (multi_event.get("metrics") or [])
        if isinstance(item, dict)
    ]
    check(
        "B 同记录 3 指标合并为一个提醒",
        multi.get("reminder_required") is True
        and multi.get("saved") is True
        and len(multi_fields) == 3
        and set(multi_fields) == {"Y向振动(mm/s)", "X向振动(mm/s)", "Z向振动(mm/s)"},
        f"reminder={multi.get('reminder_required')} fields={multi_fields}",
    )

    # C: 恢复正常后再异常，可重新提醒
    recovered = process("sess-b", "REC-B-02", "MM-01", None)
    again = process("sess-b", "REC-B-03", "MM-01", ["Y向振动(mm/s)"])
    check(
        "C 恢复后再异常可重新提醒",
        recovered.get("event") is None
        and recovered.get("reminder_required") is False
        and again.get("reminder_required") is True
        and again.get("saved") is True,
        f"recovered={recovered.get('event')} again={again.get('reminder_required')}",
    )

    # D: 两台设备分别异常，不错误合并
    me.clear_alert_episodes("sess-d")
    d1 = process("sess-d", "REC-D-01", "MM-01", ["Y向振动(mm/s)"])
    d2 = process("sess-d", "REC-D-02", "CC-01", ["Y向振动(mm/s)"])
    check(
        "D 不同设备独立提醒互不吞并",
        d1.get("reminder_required") is True
        and d2.get("reminder_required") is True
        and isinstance(d1.get("event"), dict)
        and isinstance(d2.get("event"), dict)
        and d1["event"]["device_id"] == "MM-01"
        and d2["event"]["device_id"] == "CC-01",
        f"d1={d1.get('reminder_required')} d2={d2.get('reminder_required')}",
    )

    # E: 重复处理同一条记录（暂停/继续/变速场景）不重复提醒
    me.clear_alert_episodes("sess-dup")
    first = process("sess-dup", "REC-E-01", "MM-01", ["Y向振动(mm/s)"])
    second = process("sess-dup", "REC-E-01", "MM-01", ["Y向振动(mm/s)"])
    third = process("sess-dup", "REC-E-01", "MM-01", ["Y向振动(mm/s)"])
    listed_e = me.list_monitoring_events("sess-dup", "MM-01", 100)
    check(
        "E 重复处理同一记录不重复提醒且不丢事件",
        first.get("reminder_required") is True
        and first.get("saved") is True
        and second.get("duplicate") is True
        and second.get("reminder_required") is False
        and third.get("duplicate") is True
        and third.get("reminder_required") is False
        and listed_e["count"] == 1,
        f"first={first.get('reminder_required')} second={second.get('reminder_required')} "
        f"dup={second.get('duplicate')} count={listed_e['count']}",
    )

    # F: 持续异常被抑制提醒的记录仍可查询
    listed_a_again = me.list_monitoring_events("sess-a", None, 100)
    detail_ids = {item["record_id"] for item in listed_a_again["events"]}
    check(
        "F 异常事件与历史记录可查询追溯",
        listed_a_again["count"] == 10
        and all(f"REC-A-{index:02d}" in detail_ids for index in range(10))
        and all(isinstance(item.get("metrics"), list) and item["metrics"] for item in listed_a_again["events"])
        and all(item.get("event_type") == "统计偏离" for item in listed_a_again["events"])
        and all(item.get("confirmed") is False for item in listed_a_again["events"]),
        f"count={listed_a_again['count']} ids={len(detail_ids)}",
    )

    after = file_hash(EXCEL)
    check(
        "G 官方 Excel 与检测结论口径未改",
        after == before == EXPECTED_HASH
        and all(item.get("event_type") == "统计偏离" for item in listed_a_again["events"])
        and all("不是故障确诊" in str(item.get("event_note") or "") for item in listed_a_again["events"]),
        after,
    )

    # 不同指标在同一设备上的新进入仍可提醒（不与 A/B 冲突）
    me.clear_alert_episodes("sess-new")
    base = process("sess-new", "REC-N-01", "MM-01", ["Y向振动(mm/s)"])
    cont = process("sess-new", "REC-N-02", "MM-01", ["Y向振动(mm/s)"])
    added = process("sess-new", "REC-N-03", "MM-01", ["Y向振动(mm/s)", "X向振动(mm/s)"])
    check(
        "附加 新指标进入时允许提醒、旧指标持续不单独再弹",
        base.get("reminder_required") is True
        and cont.get("reminder_required") is False
        and added.get("reminder_required") is True
        and len((added.get("event") or {}).get("metrics") or []) == 2,
        f"base={base.get('reminder_required')} cont={cont.get('reminder_required')} added={added.get('reminder_required')}",
    )

    failed = [name for name, passed, _ in RESULTS if not passed]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
