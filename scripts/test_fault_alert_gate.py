"""故障告警门槛与异常过程诊断去重（模拟工具 + 临时库，不改正式数据，不调用真实 DeepSeek）。"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TEMP_DIR = Path(tempfile.mkdtemp(prefix="fault-alert-gate-", dir=str(ROOT / "data" / "runtime")))
TEMP_DB = TEMP_DIR / "events.sqlite3"
os.environ["MONITORING_EVENTS_DB"] = str(TEMP_DB)
os.environ["EPISODE_DIAGNOSIS_ENABLED"] = "1"
os.environ["STARTUP_TOOL_SELFTEST"] = "0"

from app.services import episode_diagnosis as ed
from app.services import monitoring_events as me
from app.services.deepseek import DeepSeekError
from app.services.fault_confirmation import evaluate_fault_confirmation

EXCEL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
EXPECTED_HASH = "1e9caeba1fe526f3ad1b5dd6445a9dbcc02fa22ec02273d9863ebc84e3e30f87"
RESULTS: list[tuple[str, bool, str]] = []


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((name, passed, detail))
    print(("PASS " if passed else "FAIL ") + name + ("" if not detail else " | " + detail))


def analysis_shell(
    *,
    detection: str = "偏离基线",
    clues: list | None = None,
    candidates: list | None = None,
    checks: list | None = None,
    level: str = "statistical_deviation",
) -> dict:
    return {
        "ok": True,
        "record_id": "REC-TEST-001",
        "diagnosis_level": level,
        "detection_status": detection,
        "statistical_deviation": {"detection_status": detection},
        "diagnostic_clues": clues or [],
        "candidate_faults": candidates or [],
        "feature_checks": checks or [],
        "confirmed": False,
    }


def main() -> None:
    before = file_hash(EXCEL)
    check("预检 Excel 未改", before == EXPECTED_HASH, before)

    only_stats = evaluate_fault_confirmation(analysis=analysis_shell())
    check(
        "1 仅统计偏离不告警",
        only_stats["fault_alert_required"] is False and only_stats["diagnosis_phase"] == "statistical_deviation",
        only_stats["diagnosis_phase"],
    )

    clue_only = evaluate_fault_confirmation(
        analysis=analysis_shell(
            clues=[{"partial_condition_met": True, "complete_condition_met": False, "feature_code": "amplitude"}],
            level="diagnostic_clue",
        )
    )
    check(
        "2 有线索无候选不告警",
        clue_only["fault_alert_required"] is False and clue_only["diagnosis_phase"] == "insufficient_evidence",
        clue_only["diagnosis_phase"],
    )

    unverified = evaluate_fault_confirmation(
        analysis=analysis_shell(
            candidates=[{"fault": "转子不平衡", "confirmed": False, "matched_feature_codes": ["amplitude"], "source": "diagnose_fault"}],
            level="possible_match",
            checks=[{"feature_code": "amplitude", "complete_condition_met": False, "partial_condition_met": True}],
        ),
        fault_report={"hypotheses": [{"verification_status": "unverified", "faults": ["转子不平衡"]}]},
    )
    check(
        "3 未验证假设不告警",
        unverified["fault_alert_required"] is False,
        unverified["diagnosis_phase"],
    )

    failed = evaluate_fault_confirmation(model_failed=True, diagnosis_error="DeepSeek 不可用")
    check(
        "4 模型失败不告警且可查失败态",
        failed["fault_alert_required"] is False and failed["diagnosis_phase"] == "diagnosis_failed",
        str(failed["reasons"]),
    )

    confirmed = evaluate_fault_confirmation(
        analysis=analysis_shell(
            level="possible_match",
            candidates=[
                {
                    "fault": "转子不平衡",
                    "confirmed": False,
                    "matched_feature_codes": ["amplitude"],
                    "source": "diagnose_fault",
                }
            ],
            checks=[
                {
                    "feature_code": "amplitude",
                    "complete_condition_met": True,
                    "partial_condition_met": True,
                    "related_fault_section": "转子不平衡 / 频谱特征",
                    "official_condition": "X向振动大于Y向振动，并有轴向侧偏心",
                    "passed_to_diagnose_fault": True,
                }
            ],
            clues=[{"feature_code": "amplitude", "complete_condition_met": True}],
        ),
        physics={"ok": True, "contradicted_checks": []},
    )
    check(
        "5 完整条件结构化确认才告警",
        confirmed["fault_alert_required"] is True and confirmed["diagnosis_phase"] == "confirmed_fault",
        str(confirmed["confirmed_fault_names"]),
    )

    ed.clear_episode_diagnosis_state()
    calls = {"n": 0}

    def fake_ask(_question: str) -> dict:
        calls["n"] += 1
        raise DeepSeekError("模拟 DeepSeek 失败", status_code=503)

    # 直接测调度去重：100 次同一过程只 schedule 一次执行入口
    first = ed.schedule_episode_diagnosis(
        session_id="sess-burst",
        device_id="MM-01",
        record_id="REC-MA-20260401-00319",
        event_type="统计偏离",
        event_id=1,
        ask_with_tools=fake_ask,
        background=False,
    )
    dupes = [
        ed.schedule_episode_diagnosis(
            session_id="sess-burst",
            device_id="MM-01",
            record_id=f"REC-{i}",
            event_type="统计偏离",
            event_id=i,
            ask_with_tools=fake_ask,
            background=False,
        )
        for i in range(2, 102)
    ]
    check(
        "6 同一异常过程连续 100 条偏离不重复诊断",
        first.get("duplicate") is False
        and all(item.get("duplicate") is True for item in dupes)
        and calls["n"] == 1
        and (first.get("result") or {}).get("fault_alert_required") is False,
        f"calls={calls['n']} first_dup={first.get('duplicate')}",
    )

    # 持久化：直接插入最小事件再写回诊断结果
    os.environ["EPISODE_DIAGNOSIS_ENABLED"] = "0"
    ed.clear_episode_diagnosis_state()
    conn = me._connect(me.database_path())
    try:
        cur = conn.execute(
            me._INSERT_EVENT,
            (
                "sess-persist",
                "REC-FAKE-1",
                "CC-01",
                "2026-04-01 10:00:00",
                "2026-04-01T10:00:00+08:00",
                "统计偏离",
                "statistical_deviation",
                "偏离基线",
                "mock",
                me.EVENT_SCOPE,
                me.EVENT_NOTE,
                "[]",
                "[]",
                "[]",
                "{}",
                1,
                0,
                "diagnosing",
                "",
                "sess-persist|CC-01|统计偏离",
            ),
        )
        event_id = int(cur.lastrowid)
    finally:
        conn.close()
    listed = me.list_monitoring_events(session_id="sess-persist", device_id="CC-01", limit=10)
    seed = next((item for item in listed.get("events") or [] if item.get("event_id") == event_id), None)
    check(
        "7 统计事件默认不带故障告警",
        isinstance(seed, dict) and seed.get("fault_alert_required") is False and seed.get("reminder_required") is True,
        f"alert={None if not seed else seed.get('fault_alert_required')} phase={None if not seed else seed.get('diagnosis_phase')}",
    )

    applied = me.apply_episode_diagnosis_result(
        event_id,
        {
            "ok": False,
            "diagnosis_error": "模拟失败",
            "model_failed": True,
            "assessment_id": "",
            "episode_key": "sess-persist|CC-01|统计偏离",
            "confirmation": evaluate_fault_confirmation(diagnosis_error="模拟失败", model_failed=True),
            "fault_alert_required": False,
            "diagnosis_phase": "diagnosis_failed",
            "analysis": analysis_shell(),
        },
    )
    check(
        "8 失败态可查询且仍不告警",
        isinstance(applied, dict)
        and applied.get("fault_alert_required") is False
        and applied.get("diagnosis_phase") == "diagnosis_failed",
        str(applied.get("diagnosis_status") if isinstance(applied, dict) else applied),
    )

    confirmed_event = me.apply_episode_diagnosis_result(
        event_id,
        {
            "ok": True,
            "assessment_id": "assess-1",
            "episode_key": "sess-persist|CC-01|统计偏离",
            "confirmation": confirmed,
            "fault_alert_required": True,
            "diagnosis_phase": "confirmed_fault",
            "analysis": analysis_shell(
                level="possible_match",
                candidates=[{"fault": "转子不平衡", "confirmed": False, "matched_feature_codes": ["amplitude"], "source": "diagnose_fault"}],
                checks=[{"feature_code": "amplitude", "complete_condition_met": True}],
                clues=[{"feature_code": "amplitude", "complete_condition_met": True}],
            ),
        },
    )
    check(
        "9 确认规则通过后写回 fault_alert_required",
        isinstance(confirmed_event, dict) and confirmed_event.get("fault_alert_required") is True,
        str(confirmed_event.get("diagnosis_phase") if isinstance(confirmed_event, dict) else None),
    )

    again = me.apply_episode_diagnosis_result(
        event_id,
        {
            "ok": True,
            "assessment_id": "assess-2",
            "episode_key": "sess-persist|CC-01|统计偏离",
            "confirmation": confirmed,
            "fault_alert_required": True,
            "diagnosis_phase": "confirmed_fault",
            "analysis": analysis_shell(
                level="possible_match",
                candidates=[{"fault": "转子不平衡", "confirmed": False, "matched_feature_codes": ["amplitude"], "source": "diagnose_fault"}],
                checks=[{"feature_code": "amplitude", "complete_condition_met": True}],
            ),
        },
    )
    check(
        "10 同一事件重复写回不取消已有告警资格",
        isinstance(again, dict) and again.get("fault_alert_required") is True,
    )

    other = evaluate_fault_confirmation(
        analysis=analysis_shell(
            level="possible_match",
            candidates=[{"fault": "轴承磨损", "confirmed": False, "matched_feature_codes": ["envelope_spectrum"], "source": "diagnose_fault"}],
            checks=[{"feature_code": "amplitude", "complete_condition_met": True}],
        )
    )
    check(
        "11 不同特征码/过程不会误确认",
        other["fault_alert_required"] is False,
        other["diagnosis_phase"],
    )

    check("12 正式 Excel 未被改写", file_hash(EXCEL) == before == EXPECTED_HASH)

    failed_names = [name for name, passed, _ in RESULTS if not passed]
    print(f"{len(RESULTS) - len(failed_names)}/{len(RESULTS)} passed")
    if failed_names:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
