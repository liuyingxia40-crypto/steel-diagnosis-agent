"""自动监测 → 过程诊断 → 故障告警 端到端联调（隔离库 + 模拟工具）。

不改正式知识图谱 / 官方 Excel / 正式监测事件库 / 正式诊断历史 / .env。
不把合成测量写入正式库。默认不调用真实 DeepSeek。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

TEMP = Path(tempfile.mkdtemp(prefix="e2e-fault-alert-", dir=str(ROOT / "data" / "runtime")))
os.environ["MONITORING_EVENTS_DB"] = str(TEMP / "events.sqlite3")
os.environ["DIAGNOSTIC_EVIDENCE_DB"] = str(TEMP / "evidence.sqlite3")
os.environ["DIAGNOSIS_HISTORY_DB"] = str(TEMP / "history.sqlite3")
os.environ["SUPPLEMENTARY_MEASUREMENT_DB"] = str(TEMP / "supplements.sqlite3")
os.environ["FOLLOWUP_TASK_DB"] = str(TEMP / "followup.sqlite3")
os.environ["SUPERVISOR_TRACE_DB"] = str(TEMP / "traces.sqlite3")
os.environ["EPISODE_DIAGNOSIS_ENABLED"] = "1"
os.environ["STARTUP_TOOL_SELFTEST"] = "0"

import app.services.deepseek as deepseek
from app.services import episode_diagnosis as ed
from app.services import monitoring_events as me
from app.services.agent_tools import execute_tool
from app.services.diagnostic_bridge import analyze_record_for_diagnosis
from app.services.diagnostic_evidence import register_evidence
from app.services.official_dataset import get_observation

EXCEL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
PROD_EVENTS = ROOT / "data" / "runtime" / "monitoring_events.sqlite3"
PROD_EVIDENCE = ROOT / "data" / "runtime" / "diagnostic_evidence.sqlite3"
EXPECTED_HASH = "1e9caeba1fe526f3ad1b5dd6445a9dbcc02fa22ec02273d9863ebc84e3e30f87"

# 官方真实记录（合成证据仅挂到隔离 evidence DB）
STATS_ONLY = "REC-MA-20260401-00319"  # 统计偏离，无完整条件
AXIAL_HOST = "REC-MA-20260403-00346"  # 偏离 + X>Y，可挂合成轴向

RESULTS: list[tuple[str, bool, str]] = []
CHAIN_LOG: list[dict[str, Any]] = []
EXPORT: dict[str, Any] = {"cases": {}, "temp_dir": str(TEMP)}


def check(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((name, passed, detail))
    print(("PASS " if passed else "FAIL ") + name + ("" if not detail else " | " + detail))


def file_hash(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def log_stage(stage: str, **payload: Any) -> None:
    entry = {"stage": stage, **payload}
    CHAIN_LOG.append(entry)
    print(f"  [{stage}] " + json.dumps(payload, ensure_ascii=False, default=str)[:500])


def reject_deepseek(*_args: Any, **_kwargs: Any) -> None:
    raise AssertionError("本联调禁止调用真实 DeepSeek")


def mock_ask_from_tools(record_id: str) -> Callable[[str], dict]:
    """模拟 Supervisor：真实调用 analyze_equipment_record，不走 DeepSeek。"""

    def _ask(question: str) -> dict:
        analysis = execute_tool("analyze_equipment_record", {"record_id": record_id})
        return {
            "answer": "MOCK_NOT_DEEPSEEK：仅返回真实工具结果。",
            "tool_results": [
                {
                    "id": "mock-analyze",
                    "name": "analyze_equipment_record",
                    "arguments": {"record_id": record_id},
                    "result": analysis,
                }
            ],
            "fault_report": {"hypotheses": [], "physics": {"ok": True, "contradicted_checks": []}},
            "trace_saved": False,
        }

    return _ask


def mock_ask_tool_failure(_question: str) -> dict:
    return {
        "answer": "MOCK_NOT_DEEPSEEK：工具失败。",
        "tool_results": [
            {
                "id": "mock-fail",
                "name": "analyze_equipment_record",
                "arguments": {"record_id": "x"},
                "result": {"ok": False, "error": "模拟工具失败"},
            }
        ],
        "fault_report": {"hypotheses": [], "physics": {"ok": True, "contradicted_checks": []}},
        "trace_saved": False,
    }


def mock_ask_feature_mismatch(record_id: str) -> Callable[[str], dict]:
    """完整条件存在，但候选 matched_feature_codes 与之不匹配。"""

    def _ask(question: str) -> dict:
        analysis = execute_tool("analyze_equipment_record", {"record_id": record_id})
        if isinstance(analysis, dict) and analysis.get("ok") is True:
            analysis = dict(analysis)
            analysis["candidate_faults"] = [
                {
                    "fault": "故意错码故障",
                    "matched_feature_codes": ["temperature"],
                    "confirmed": False,
                }
            ]
            # 保留完整条件检查，但候选码不在 complete 集合中
            checks = list(analysis.get("feature_checks") or [])
            analysis["feature_checks"] = checks
        return {
            "answer": "MOCK_NOT_DEEPSEEK：特征码不匹配。",
            "tool_results": [
                {
                    "id": "mock-mismatch",
                    "name": "analyze_equipment_record",
                    "arguments": {"record_id": record_id},
                    "result": analysis,
                }
            ],
            "fault_report": {"hypotheses": [], "physics": {"ok": True, "contradicted_checks": []}},
            "trace_saved": False,
        }

    return _ask


def install_sync_scheduler(
    ask: Callable[[str], dict],
    *,
    counter: dict | None = None,
) -> Callable:
    real = ed.schedule_episode_diagnosis

    def wrapped(**kwargs: Any) -> dict:
        if counter is not None:
            counter["schedule_calls"] = int(counter.get("schedule_calls") or 0) + 1
        kwargs["background"] = False
        kwargs["ask_with_tools"] = ask
        out = real(**kwargs)
        if counter is not None:
            if out.get("duplicate") is True:
                counter["duplicate"] = int(counter.get("duplicate") or 0) + 1
            elif out.get("disabled") is True:
                counter["disabled"] = int(counter.get("disabled") or 0) + 1
            elif out.get("scheduled") is True or isinstance(out.get("result"), dict):
                # 异步：scheduled=True；同步 background=False：带 result
                counter["diagnosis_ran"] = int(counter.get("diagnosis_ran") or 0) + 1
        return out

    return wrapped


def register_synthetic_axial(record_id: str) -> dict:
    obs = get_observation(record_id)["record"]
    return register_evidence(
        record_id,
        {
            "device_id": obs["device_id"],
            "measured_at": obs["collected_at"],
            "sensor_position": "e2e-synthetic-axial",
            "data_origin": "synthetic_test_signal",
            "evidence_kind": "axial_eccentricity",
            "value": 15.0,
            "unit": "um",
            "method": "runout_measurement",
            "reference_basis": "shaft_axis",
            "note": "E2E 合成轴向偏心，仅隔离库",
        },
    )


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
    complete: bool = False,
    candidates: list | None = None,
    clues: list | None = None,
    diagnosis_level: str = "statistical_deviation",
) -> dict:
    metrics = [metric(field) for field in (fields or [])]
    status = "偏离基线" if metrics else "未发现显著偏离"
    feature_checks: list[dict] = []
    if complete:
        feature_checks.append(
            {
                "feature_code": "amplitude",
                "complete_condition_met": True,
                "partial_condition_met": True,
                "official_condition": "振幅 X>Y 且轴向偏心",
            }
        )
    return {
        "ok": True,
        "record_id": record_id,
        "device_id": device_id,
        "data_source": "mock_e2e",
        "diagnosis_level": diagnosis_level,
        "confirmed": False,
        "diagnostic_clues": clues or [],
        "candidate_faults": candidates or [],
        "feature_checks": feature_checks,
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


def listed_alerts(session: str, device_id: str | None = None) -> list[dict]:
    # API limit 上限 100；超过时按页拼装（仅联调读隔离库）。
    collected: list[dict] = []
    body = me.list_monitoring_events(session, device_id, 100)
    for item in body.get("events") or []:
        if isinstance(item, dict):
            collected.append(item)
    # list_monitoring_events 通常按新到旧；若刚好 100 条用 SQL 核对总数
    db = os.environ["MONITORING_EVENTS_DB"]
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        if device_id:
            total = conn.execute(
                "SELECT COUNT(*) AS c FROM monitoring_events WHERE session_id = ? AND device_id = ?",
                (session, device_id),
            ).fetchone()["c"]
        else:
            total = conn.execute(
                "SELECT COUNT(*) AS c FROM monitoring_events WHERE session_id = ?",
                (session,),
            ).fetchone()["c"]
    if int(total) <= 100:
        return collected
    # 直接读隔离库补齐（不改正式库）
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        if device_id:
            rows = conn.execute(
                "SELECT * FROM monitoring_events WHERE session_id = ? AND device_id = ? ORDER BY event_id ASC",
                (session, device_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM monitoring_events WHERE session_id = ? ORDER BY event_id ASC",
                (session,),
            ).fetchall()
    return [me._row_to_event(row) for row in rows]  # noqa: SLF001 — 联调复用序列化


def count_fault_alerts(events: list[dict]) -> int:
    return sum(1 for e in events if e.get("fault_alert_required") is True)


def case_success_full_chain() -> None:
    """开始监测语义：process → schedule → 工具 → 完整条件 → 确认 → 写回 → 轮询。"""
    session = "e2e-success"
    rid = AXIAL_HOST
    me.clear_alert_episodes(session)
    ed.clear_episode_diagnosis_state(session)

    log_stage("analyze_before", record_id=rid)
    before = analyze_record_for_diagnosis(rid)
    log_stage(
        "analyze_before_result",
        detection=(before.get("statistical_deviation") or {}).get("detection_status"),
        complete=any(
            c.get("complete_condition_met") is True
            for c in (before.get("feature_checks") or [])
            if isinstance(c, dict)
        ),
        level=before.get("diagnosis_level"),
    )

    reg = register_synthetic_axial(rid)
    log_stage("register_synthetic_axial", stored=reg.get("stored"), evidence_db=os.environ["DIAGNOSTIC_EVIDENCE_DB"])

    mid = analyze_record_for_diagnosis(rid)
    complete_ok = any(
        c.get("complete_condition_met") is True for c in (mid.get("feature_checks") or []) if isinstance(c, dict)
    )
    cands = mid.get("candidate_faults") or []
    log_stage(
        "analyze_after_axial",
        complete=complete_ok,
        candidates=[c.get("matched_feature_codes") for c in cands if isinstance(c, dict)],
        level=mid.get("diagnosis_level"),
    )

    counter: dict[str, int] = {}
    ask = mock_ask_from_tools(rid)
    with patch.object(ed, "schedule_episode_diagnosis", install_sync_scheduler(ask, counter=counter)):
        out = me.process_monitoring_record(session, rid)
    log_stage(
        "process_monitoring_record",
        saved=out.get("saved"),
        reminder=out.get("reminder_required"),
        process_return_alert=out.get("fault_alert_required"),
        schedule_calls=counter.get("schedule_calls"),
        diagnosis_ran=counter.get("diagnosis_ran"),
        ask_note="MOCK_NOT_DEEPSEEK",
    )

    events = listed_alerts(session)
    lead = next((e for e in events if e.get("record_id") == rid), None)
    log_stage(
        "list_monitoring_events",
        count=len(events),
        fault_alert=None if not lead else lead.get("fault_alert_required"),
        phase=None if not lead else lead.get("diagnosis_phase"),
        event_id=None if not lead else lead.get("event_id"),
        device_id=None if not lead else lead.get("device_id"),
    )

    # 过程返回值是诊断前快照；告警资格以轮询列表为准（与前端一致）
    ok = (
        before.get("ok") is True
        and reg.get("stored") is True
        and complete_ok is True
        and out.get("saved") is True
        and out.get("reminder_required") is True
        and int(counter.get("schedule_calls") or 0) == 1
        and int(counter.get("diagnosis_ran") or 0) == 1
        and isinstance(lead, dict)
        and lead.get("fault_alert_required") is True
        and lead.get("diagnosis_phase") == "confirmed_fault"
        and lead.get("device_id")
        and lead.get("record_id") == rid
    )
    check(
        "S1 完整成功链路（合成轴向+模拟工具）",
        ok,
        f"alert={None if not lead else lead.get('fault_alert_required')} phase={None if not lead else lead.get('diagnosis_phase')}",
    )
    EXPORT["cases"]["success"] = {
        "session_id": session,
        "events": events,
        "process_return_fault_alert_required": out.get("fault_alert_required"),
        "note": "process 返回诊断前快照；列表接口为写回后真相",
    }


def case_no_false_alarms() -> None:
    # A: 只有统计偏离
    session = "e2e-stats"
    me.clear_alert_episodes(session)
    ed.clear_episode_diagnosis_state(session)
    counter: dict[str, int] = {}
    ask = mock_ask_from_tools(STATS_ONLY)
    with patch.object(ed, "schedule_episode_diagnosis", install_sync_scheduler(ask, counter=counter)):
        out = me.process_monitoring_record(session, STATS_ONLY)
    events = listed_alerts(session)
    log_stage(
        "stats_only",
        saved=out.get("saved"),
        reminder=out.get("reminder_required"),
        alerts=count_fault_alerts(events),
        phases=[e.get("diagnosis_phase") for e in events],
    )
    check(
        "N1 只有统计偏离不告警",
        out.get("saved") is True
        and out.get("reminder_required") is True
        and count_fault_alerts(events) == 0
        and all(e.get("fault_alert_required") is not True for e in events)
        and all(e.get("diagnosis_phase") != "confirmed_fault" for e in events),
        f"phases={[e.get('diagnosis_phase') for e in events]}",
    )
    EXPORT["cases"]["stats_only"] = {"events": events}

    # B: X>Y 无轴向（AXIAL_HOST 未再注册新证据时已有成功用例证据——用新 session 但同一隔离库会仍有证据）
    # 因此对该记录在「无完整条件」场景使用 mock analysis：X/Y 部分成立，无 complete
    session_b = "e2e-xy-no-axial"
    me.clear_alert_episodes(session_b)
    ed.clear_episode_diagnosis_state(session_b)
    device_b = get_observation(AXIAL_HOST)["record"]["device_id"]
    partial_analysis = analysis_for(
        "REC-E2E-XY-01",
        device_b,
        ["X向振动(mm/s)", "Y向振动(mm/s)"],
        diagnosis_level="possible_match",
        clues=[{"feature_code": "amplitude", "partial_condition_met": True}],
        candidates=[{"fault": "转子不平衡", "matched_feature_codes": ["amplitude"], "confirmed": False}],
    )
    partial_analysis["feature_checks"] = [
        {
            "feature_code": "amplitude",
            "complete_condition_met": False,
            "partial_condition_met": True,
            "official_condition": "振幅 X>Y 且轴向偏心",
            "unverified_reason": "缺少轴向偏心测量",
        }
    ]
    counter_b: dict[str, int] = {}

    def ask_partial(q: str) -> dict:
        return {
            "answer": "MOCK_NOT_DEEPSEEK",
            "tool_results": [
                {
                    "id": "1",
                    "name": "analyze_equipment_record",
                    "arguments": {"record_id": "REC-E2E-XY-01"},
                    "result": partial_analysis,
                }
            ],
            "fault_report": {"hypotheses": [], "physics": {"ok": True, "contradicted_checks": []}},
            "trace_saved": False,
        }

    with (
        patch.object(me, "analyze_record_for_diagnosis", return_value=partial_analysis),
        patch("app.services.diagnostic_bridge.analyze_record_for_diagnosis", return_value=partial_analysis),
        patch.object(ed, "schedule_episode_diagnosis", install_sync_scheduler(ask_partial, counter=counter_b)),
    ):
        out_b = me.process_monitoring_record(session_b, "REC-E2E-XY-01")
    events_b = listed_alerts(session_b)
    log_stage("xy_no_axial", alerts=count_fault_alerts(events_b), phases=[e.get("diagnosis_phase") for e in events_b])
    check(
        "N2 仅 X/Y 无轴向偏心证据不告警",
        out_b.get("saved") is True
        and count_fault_alerts(events_b) == 0
        and bool(events_b)
        and events_b[0].get("diagnosis_phase") == "possible_match_unconfirmed",
        f"phases={[e.get('diagnosis_phase') for e in events_b]}",
    )

    # C: 有候选但完整条件不满足（同上 N2 已覆盖；再加线索不足）
    session_c = "e2e-candidate-incomplete"
    me.clear_alert_episodes(session_c)
    ed.clear_episode_diagnosis_state(session_c)
    incomplete = analysis_for(
        "REC-E2E-CAND-01",
        "MM-01",
        ["Y向振动(mm/s)"],
        diagnosis_level="possible_match",
        candidates=[{"fault": "轴承磨损", "matched_feature_codes": ["amplitude"], "confirmed": False}],
        clues=[{"note": "线索"}],
    )
    incomplete["feature_checks"] = [
        {"feature_code": "amplitude", "complete_condition_met": False, "partial_condition_met": False}
    ]

    def ask_incomplete(q: str) -> dict:
        return {
            "answer": "MOCK",
            "tool_results": [
                {
                    "id": "1",
                    "name": "analyze_equipment_record",
                    "result": incomplete,
                    "arguments": {"record_id": "REC-E2E-CAND-01"},
                }
            ],
            "fault_report": {"hypotheses": [], "physics": {"ok": True, "contradicted_checks": []}},
            "trace_saved": False,
        }

    with (
        patch.object(me, "analyze_record_for_diagnosis", return_value=incomplete),
        patch("app.services.diagnostic_bridge.analyze_record_for_diagnosis", return_value=incomplete),
        patch.object(ed, "schedule_episode_diagnosis", install_sync_scheduler(ask_incomplete)),
    ):
        out_c = me.process_monitoring_record(session_c, "REC-E2E-CAND-01")
    events_c = listed_alerts(session_c)
    check(
        "N3 有候选但完整条件不满足不告警",
        out_c.get("saved") is True
        and count_fault_alerts(events_c) == 0
        and bool(events_c)
        and events_c[0].get("diagnosis_phase") == "possible_match_unconfirmed",
        f"phase={None if not events_c else events_c[0].get('diagnosis_phase')}",
    )

    # D: 合法补充测量 → 进入确认判定（复用成功会话已验证；此处再验 analyze 阶段 complete）
    # 使用尚无证据的新记录路径：在隔离库对 STATS 类设备另挂？AXIAL_HOST 已有证据。
    # 验证：analyze 返回 complete + candidates 交集非空后，确认阶段可告警（S1 已走通）。
    check(
        "N4 合法合成轴向使完整条件满足并进入确认",
        complete_ok_from_export(),
        "见 S1 analyze_after_axial",
    )

    # E: 工具失败不告警
    session_e = "e2e-tool-fail"
    me.clear_alert_episodes(session_e)
    ed.clear_episode_diagnosis_state(session_e)
    # 带候选以避免「仅统计偏离」提前返回，从而走到工具失败分支。
    fail_base = analysis_for(
        "REC-E2E-FAIL-01",
        "MM-01",
        ["Y向振动(mm/s)"],
        candidates=[{"fault": "待查", "matched_feature_codes": ["amplitude"], "confirmed": False}],
        clues=[{"note": "线索"}],
        diagnosis_level="possible_match",
    )
    with (
        patch.object(me, "analyze_record_for_diagnosis", return_value=fail_base),
        patch("app.services.diagnostic_bridge.analyze_record_for_diagnosis", return_value=fail_base),
        patch.object(ed, "schedule_episode_diagnosis", install_sync_scheduler(mock_ask_tool_failure)),
    ):
        out_e = me.process_monitoring_record(session_e, "REC-E2E-FAIL-01")
    events_e = listed_alerts(session_e)
    check(
        "N5 工具调用失败不告警",
        out_e.get("saved") is True
        and count_fault_alerts(events_e) == 0
        and bool(events_e)
        and events_e[0].get("diagnosis_phase") == "diagnosis_failed",
        f"phase={None if not events_e else events_e[0].get('diagnosis_phase')}",
    )

    # F: 完整条件与候选特征码不匹配
    session_f = "e2e-code-mismatch"
    me.clear_alert_episodes(session_f)
    ed.clear_episode_diagnosis_state(session_f)
    # 需要真实 complete：用 AXIAL_HOST（隔离库已有合成轴向）
    ask_f = mock_ask_feature_mismatch(AXIAL_HOST)
    with patch.object(ed, "schedule_episode_diagnosis", install_sync_scheduler(ask_f)):
        out_f = me.process_monitoring_record(session_f, AXIAL_HOST)
    events_f = listed_alerts(session_f)
    log_stage(
        "feature_mismatch",
        saved=out_f.get("saved"),
        alerts=count_fault_alerts(events_f),
        phases=[e.get("diagnosis_phase") for e in events_f],
        confirmed_names=[
            ((e.get("diagnosis") or {}).get("confirmed_fault_names") if isinstance(e.get("diagnosis"), dict) else None)
            for e in events_f
        ],
    )
    check(
        "N6 完整条件与候选特征码不匹配不告警",
        out_f.get("saved") is True and count_fault_alerts(events_f) == 0,
        f"phases={[e.get('diagnosis_phase') for e in events_f]}",
    )


def complete_ok_from_export() -> bool:
    success = EXPORT.get("cases", {}).get("success")
    if not isinstance(success, dict):
        return False
    events = success.get("events") or []
    return any(e.get("fault_alert_required") is True for e in events if isinstance(e, dict))


def case_dedupe_100() -> None:
    session = "e2e-dedupe-100"
    me.clear_alert_episodes(session)
    ed.clear_episode_diagnosis_state(session)
    counter: dict[str, int] = {"schedule_calls": 0, "scheduled_true": 0, "duplicate": 0}
    reminders: list[bool] = []
    ask_hits = {"n": 0}

    def ask_stats(q: str) -> dict:
        ask_hits["n"] += 1
        analysis = analysis_for("REC-DEDUP-00", "MM-01", ["Y向振动(mm/s)"])
        return {
            "answer": "MOCK",
            "tool_results": [
                {
                    "id": "1",
                    "name": "analyze_equipment_record",
                    "result": analysis,
                    "arguments": {"record_id": "REC-DEDUP-00"},
                }
            ],
            "fault_report": {"hypotheses": [], "physics": {"ok": True, "contradicted_checks": []}},
            "trace_saved": False,
        }

    scheduler = install_sync_scheduler(ask_stats, counter=counter)
    with patch.object(ed, "schedule_episode_diagnosis", scheduler):
        for i in range(100):
            rid = f"REC-DEDUP-{i:03d}"
            mocked = analysis_for(rid, "MM-01", ["Y向振动(mm/s)"], collected_at=f"2026-04-01 10:{i // 60:02d}:{i % 60:02d}")
            with patch.object(me, "analyze_record_for_diagnosis", return_value=mocked):
                out = me.process_monitoring_record(session, rid)
            reminders.append(out.get("reminder_required") is True)

    events = listed_alerts(session, "MM-01")
    # 只有过程起点会 schedule；后续 99 条 reminder=False 不调用 schedule
    log_stage(
        "dedupe_100",
        reminders=reminders.count(True),
        schedule_calls=counter.get("schedule_calls"),
        diagnosis_ran=counter.get("diagnosis_ran"),
        ask_hits=ask_hits["n"],
        events=len(events),
        alerts=count_fault_alerts(events),
    )
    check(
        "D1 同过程 100 条偏离只提醒/诊断一次且不误告警",
        reminders.count(True) == 1
        and reminders[0] is True
        and all(not x for x in reminders[1:])
        and int(counter.get("schedule_calls") or 0) == 1
        and int(counter.get("diagnosis_ran") or 0) == 1
        and ask_hits["n"] == 1
        and len(events) == 100
        and count_fault_alerts(events) == 0
        and sum(1 for e in events if e.get("reminder_required") is True) == 1,
        f"reminders={reminders.count(True)} sched={counter} asks={ask_hits['n']} events={len(events)}",
    )
    EXPORT["cases"]["dedupe_100"] = {
        "events_sample": events[:3] + events[-1:],
        "reminder_true_count": reminders.count(True),
        "schedule_calls": counter.get("schedule_calls"),
        "ask_hits": ask_hits["n"],
        "fault_alert_count": count_fault_alerts(events),
        "all_events": events,
    }

    # 不同设备独立过程
    session_d = "e2e-multi-device"
    me.clear_alert_episodes(session_d)
    ed.clear_episode_diagnosis_state(session_d)
    counter_d: dict[str, int] = {}
    asks_d = {"n": 0}

    def ask_d(q: str) -> dict:
        asks_d["n"] += 1
        return {
            "answer": "MOCK",
            "tool_results": [
                {
                    "id": "1",
                    "name": "analyze_equipment_record",
                    "result": {"ok": True, "diagnosis_level": "statistical_deviation", "statistical_deviation": {"detection_status": "偏离基线"}, "candidate_faults": [], "diagnostic_clues": [], "feature_checks": []},
                    "arguments": {},
                }
            ],
            "fault_report": {"hypotheses": [], "physics": {"ok": True, "contradicted_checks": []}},
            "trace_saved": False,
        }

    with patch.object(ed, "schedule_episode_diagnosis", install_sync_scheduler(ask_d, counter=counter_d)):
        for device in ("MM-01", "CC-01"):
            mocked = analysis_for(f"REC-{device}-01", device, ["Y向振动(mm/s)"])
            with patch.object(me, "analyze_record_for_diagnosis", return_value=mocked):
                me.process_monitoring_record(session_d, f"REC-{device}-01")
            # 同设备第二笔不诊断
            mocked2 = analysis_for(f"REC-{device}-02", device, ["Y向振动(mm/s)"])
            with patch.object(me, "analyze_record_for_diagnosis", return_value=mocked2):
                me.process_monitoring_record(session_d, f"REC-{device}-02")

    events_d = listed_alerts(session_d)
    check(
        "D2 不同设备独立诊断互不覆盖",
        int(counter_d.get("diagnosis_ran") or 0) == 2
        and asks_d["n"] == 2
        and sum(1 for e in events_d if e.get("reminder_required") is True) == 2
        and {e.get("device_id") for e in events_d if e.get("reminder_required")} == {"MM-01", "CC-01"},
        f"sched={counter_d} asks={asks_d['n']}",
    )


def case_api_and_frontend_contract() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    success_events = EXPORT["cases"]["success"]["events"]
    lead = next(e for e in success_events if e.get("fault_alert_required") is True)
    session = "e2e-success"

    with TestClient(app) as client:
        resp = client.get(
            "/api/monitoring/events",
            params={"session_id": session, "device_id": lead["device_id"], "limit": 50},
        )
        body = resp.json()
        api_events = body.get("events") if isinstance(body, dict) else None
        api_lead = None
        if isinstance(api_events, list):
            for item in api_events:
                if isinstance(item, dict) and item.get("event_id") == lead["event_id"]:
                    api_lead = item
                    break
        log_stage(
            "api_list_events",
            status=resp.status_code,
            api_alert=None if not api_lead else api_lead.get("fault_alert_required"),
            api_phase=None if not api_lead else api_lead.get("diagnosis_phase"),
        )
        check(
            "A1 后端列表 fault_alert_required 与写回一致",
            resp.status_code == 200
            and isinstance(api_lead, dict)
            and api_lead.get("fault_alert_required") is True
            and api_lead.get("diagnosis_phase") == "confirmed_fault"
            and api_lead.get("record_id") == lead.get("record_id")
            and api_lead.get("device_id") == lead.get("device_id"),
            f"api={None if not api_lead else api_lead.get('fault_alert_required')}",
        )

        # 统计偏离事件仍可查
        stats_session = "e2e-stats"
        stats_resp = client.get("/api/monitoring/events", params={"session_id": stats_session, "limit": 50})
        stats_body = stats_resp.json()
        stats_events = stats_body.get("events") if isinstance(stats_body, dict) else []
        check(
            "A2 统计偏离事件可在异常中心查询且不带故障告警",
            stats_resp.status_code == 200
            and isinstance(stats_events, list)
            and len(stats_events) >= 1
            and all(e.get("fault_alert_required") is not True for e in stats_events if isinstance(e, dict))
            and all(e.get("detection_status") == "偏离基线" for e in stats_events if isinstance(e, dict)),
            f"count={len(stats_events) if isinstance(stats_events, list) else 0}",
        )

    # 导出给前端 TS 契约测试
    export_path = TEMP / "fe_contract_events.json"
    payload = {
        "success_events": success_events,
        "stats_events": EXPORT["cases"]["stats_only"]["events"],
        "dedupe_events": EXPORT["cases"]["dedupe_100"]["all_events"],
        "device_id": lead["device_id"],
        "alert_event_id": lead["event_id"],
    }
    export_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    EXPORT["fe_contract_path"] = str(export_path)
    check("A3 已导出前端契约事件 JSON", export_path.is_file(), str(export_path))


def case_isolation_guard() -> None:
    excel_after = file_hash(EXCEL)
    check("G1 官方 Excel 未被修改", excel_after == EXPECTED_HASH, excel_after)
    check(
        "G2 使用隔离监测库",
        Path(os.environ["MONITORING_EVENTS_DB"]).resolve() != PROD_EVENTS.resolve()
        and str(TEMP) in os.environ["MONITORING_EVENTS_DB"],
        os.environ["MONITORING_EVENTS_DB"],
    )
    check(
        "G3 使用隔离证据库",
        Path(os.environ["DIAGNOSTIC_EVIDENCE_DB"]).resolve() != PROD_EVIDENCE.resolve(),
        os.environ["DIAGNOSTIC_EVIDENCE_DB"],
    )
    # 正式库不应出现本轮合成传感器位
    if PROD_EVIDENCE.is_file():
        with sqlite3.connect(str(PROD_EVIDENCE)) as conn:
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM diagnostic_evidence WHERE sensor_position = ?",
                    ("e2e-synthetic-axial",),
                ).fetchone()
                prod_hits = int(row[0]) if row else 0
            except sqlite3.Error:
                prod_hits = 0
        check("G4 正式证据库无合成轴向写入", prod_hits == 0, str(prod_hits))
    else:
        check("G4 正式证据库不存在或未写入", True, "missing_prod_db")


def main() -> None:
    print(f"TEMP={TEMP}")
    excel_before = file_hash(EXCEL)
    check("G0 预检 Excel 哈希", excel_before == EXPECTED_HASH, excel_before)

    deepseek.complete_chat = reject_deepseek
    deepseek.ask_with_tools = reject_deepseek  # type: ignore[assignment]

    case_success_full_chain()
    case_no_false_alarms()
    case_dedupe_100()
    case_api_and_frontend_contract()
    case_isolation_guard()

    report = TEMP / "e2e_report.json"
    report.write_text(
        json.dumps(
            {
                "temp_dir": str(TEMP),
                "results": [{"name": n, "passed": p, "detail": d} for n, p, d in RESULTS],
                "chain_log": CHAIN_LOG,
                "export_keys": list(EXPORT.get("cases", {}).keys()),
                "fe_contract_path": EXPORT.get("fe_contract_path"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"REPORT={report}")

    failed = [n for n, p, _ in RESULTS if not p]
    print(f"TOTAL={len(RESULTS)} PASS={len(RESULTS) - len(failed)} FAIL={len(failed)}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
