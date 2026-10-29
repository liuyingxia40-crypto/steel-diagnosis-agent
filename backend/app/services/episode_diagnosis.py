"""异常过程级后台诊断：每个过程最多调度一次，复用现有工具与 Supervisor，不经 HTTP 自调用。"""

from __future__ import annotations

import os
import threading
from typing import Callable

from app.services.fault_confirmation import (
    PHASE_DIAGNOSING,
    PHASE_FAILED,
    evaluate_fault_confirmation,
    public_confirmation,
)


def episode_diagnosis_enabled() -> bool:
    raw = os.environ.get("EPISODE_DIAGNOSIS_ENABLED", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}

_LOCK = threading.Lock()
_STARTED: set[str] = set()
_DONE: dict[str, dict] = {}
_AskWithTools = Callable[[str], dict]


def clear_episode_diagnosis_state(session_id: str | None = None) -> None:
    """测试或会话结束时清理去重状态。"""
    with _LOCK:
        if not session_id:
            _STARTED.clear()
            _DONE.clear()
            return
        prefix = f"{session_id}|"
        for key in [item for item in _STARTED if item.startswith(prefix)]:
            _STARTED.discard(key)
        for key in [item for item in _DONE if item.startswith(prefix)]:
            _DONE.pop(key, None)


def episode_diagnosis_key(session_id: str, device_id: str, event_type: str) -> str:
    return f"{session_id}|{device_id}|{event_type}"


def schedule_episode_diagnosis(
    *,
    session_id: str,
    device_id: str,
    record_id: str,
    event_type: str,
    event_id: int | None,
    ask_with_tools: _AskWithTools | None = None,
    background: bool = True,
) -> dict:
    """新异常过程触发一次诊断。已调度或已完成的过程直接返回，避免连续偏离重复诊断。"""
    if not episode_diagnosis_enabled():
        return {
            "ok": True,
            "scheduled": False,
            "duplicate": False,
            "disabled": True,
            "episode_key": episode_diagnosis_key(session_id, device_id, event_type),
            "fault_alert_required": False,
            "diagnosis_phase": PHASE_DIAGNOSING,
        }
    key = episode_diagnosis_key(session_id, device_id, event_type)
    with _LOCK:
        if key in _DONE:
            return {"ok": True, "scheduled": False, "duplicate": True, "episode_key": key, "result": _DONE[key]}
        if key in _STARTED:
            return {"ok": True, "scheduled": False, "duplicate": True, "episode_key": key, "result": None}
        _STARTED.add(key)

    payload = {
        "session_id": session_id,
        "device_id": device_id,
        "record_id": record_id,
        "event_type": event_type,
        "event_id": event_id,
        "episode_key": key,
    }

    if background:
        thread = threading.Thread(
            target=_run_safe,
            args=(payload, ask_with_tools),
            name=f"episode-diagnosis-{device_id}",
            daemon=True,
        )
        thread.start()
        return {
            "ok": True,
            "scheduled": True,
            "duplicate": False,
            "episode_key": key,
            "diagnosis_phase": PHASE_DIAGNOSING,
            "fault_alert_required": False,
        }

    result = _run_safe(payload, ask_with_tools)
    return {"ok": True, "scheduled": False, "duplicate": False, "episode_key": key, "result": result}


def _run_safe(payload: dict, ask_with_tools: _AskWithTools | None) -> dict:
    key = str(payload["episode_key"])
    try:
        result = run_episode_diagnosis(
            record_id=str(payload["record_id"]),
            session_id=str(payload["session_id"]),
            device_id=str(payload["device_id"]),
            event_id=payload.get("event_id") if isinstance(payload.get("event_id"), int) else None,
            ask_with_tools=ask_with_tools,
        )
    except Exception as exc:  # noqa: BLE001 — 后台线程必须落失败态，不能冒充成功
        result = {
            "ok": False,
            "record_id": payload.get("record_id"),
            "device_id": payload.get("device_id"),
            "session_id": payload.get("session_id"),
            "event_id": payload.get("event_id"),
            "episode_key": key,
            "diagnosis_error": f"异常过程诊断未完成：{exc}",
            "model_failed": True,
            "confirmation": public_confirmation(
                evaluate_fault_confirmation(diagnosis_error=f"异常过程诊断未完成：{exc}", model_failed=True)
            ),
            "fault_alert_required": False,
            "diagnosis_phase": PHASE_FAILED,
        }
        _persist_result(payload, result)
        with _LOCK:
            _DONE[key] = result
            _STARTED.discard(key)
        return result

    _persist_result(payload, result)
    with _LOCK:
        _DONE[key] = result
        _STARTED.discard(key)
    return result


def run_episode_diagnosis(
    *,
    record_id: str,
    session_id: str = "",
    device_id: str = "",
    event_id: int | None = None,
    ask_with_tools: _AskWithTools | None = None,
) -> dict:
    """同步执行一次过程诊断：优先 Supervisor/DeepSeek 工具链，失败时记录失败态。"""
    from app.services.agent_tools import execute_tool
    from app.services.deepseek import DeepSeekError, ask_with_tools as default_ask
    from app.services.diagnostic_bridge import analyze_record_for_diagnosis
    from app.services.fault_revision import run_closed_loop

    identity = record_id.strip() if isinstance(record_id, str) else ""
    ask = ask_with_tools or default_ask
    analysis = analyze_record_for_diagnosis(identity)
    tool_results: list = []
    fault_report = None
    physics = None
    model_failed = False
    diagnosis_error = ""
    supervisor_outcome = None

    question = (
        f"请基于官方开发集记录编号 {identity} 调用已有工具分析统计偏离与可能故障。"
        "不能编造测量值，不能把统计偏离说成已确诊故障。"
    )

    try:
        supervisor_outcome = ask(question)
        if not isinstance(supervisor_outcome, dict):
            model_failed = True
            diagnosis_error = "DeepSeek/Supervisor 没有返回有效结果。"
        else:
            raw_tools = supervisor_outcome.get("tool_results")
            tool_results = list(raw_tools) if isinstance(raw_tools, list) else []
            fault_report = supervisor_outcome.get("fault_report") if isinstance(supervisor_outcome.get("fault_report"), dict) else None
            if isinstance(fault_report, dict):
                physics = fault_report.get("physics") if isinstance(fault_report.get("physics"), dict) else None
    except DeepSeekError as exc:
        model_failed = True
        diagnosis_error = exc.message
        # 模型不可用时仍用本地工具补一次闭环，但确认规则不会因本地 possible_match 告警。
        local_analysis = execute_tool("analyze_equipment_record", {"record_id": identity})
        tool_results = [
            {
                "id": "local-analysis",
                "name": "analyze_equipment_record",
                "result": local_analysis,
                "arguments": {"record_id": identity},
            }
        ]
        try:
            loop = run_closed_loop(
                question,
                tool_results,
                execute=execute_tool,
                physics=_local_physics,
                max_rounds=2,
            )
            tool_results = list(loop.get("tool_results") or tool_results)
            fault_report = loop.get("fault_report") if isinstance(loop.get("fault_report"), dict) else None
            if isinstance(fault_report, dict):
                physics = fault_report.get("physics") if isinstance(fault_report.get("physics"), dict) else None
        except Exception as loop_exc:  # noqa: BLE001
            diagnosis_error = f"{diagnosis_error}；本地闭环也失败：{loop_exc}"

    if isinstance(analysis, dict) and analysis.get("ok") is True:
        # 用工具结果中的 analyze 覆盖，保证与轨迹一致。
        for item in tool_results:
            if not isinstance(item, dict) or item.get("name") != "analyze_equipment_record":
                continue
            result = item.get("result")
            if isinstance(result, dict) and result.get("ok") is True:
                analysis = {**analysis, **{k: result[k] for k in result if k not in {"tool", "error"}}}
                break

    confirmation = evaluate_fault_confirmation(
        analysis=analysis if isinstance(analysis, dict) else None,
        fault_report=fault_report if isinstance(fault_report, dict) else None,
        physics=physics if isinstance(physics, dict) else None,
        tool_results=tool_results,
        diagnosis_error=diagnosis_error or None,
        model_failed=model_failed,
    )
    public = public_confirmation(confirmation)

    assessment_id = ""
    if not model_failed and isinstance(analysis, dict) and analysis.get("ok") is True:
        try:
            from app.services.fault_revision import assess_saved_record

            device = device_id or str(analysis.get("observation", {}).get("device_id") or analysis.get("device_id") or "")
            if device:
                saved = assess_saved_record(
                    identity,
                    device,
                    tool_results
                    or [
                        {
                            "id": "episode-analysis",
                            "name": "analyze_equipment_record",
                            "result": analysis,
                            "arguments": {"record_id": identity},
                        }
                    ],
                    execute=execute_tool,
                    physics=_local_physics,
                    diagnosis_level=str(analysis.get("diagnosis_level") or ""),
                    max_rounds=2,
                )
                assessment_id = str(saved.get("assessment_id") or "")
        except Exception:  # noqa: BLE001 — 历史保存失败不影响告警判定，但记入错误说明
            if not diagnosis_error:
                diagnosis_error = "诊断历史保存失败。"

    return {
        "ok": not model_failed,
        "record_id": identity,
        "device_id": device_id,
        "session_id": session_id,
        "event_id": event_id,
        "episode_key": episode_diagnosis_key(session_id, device_id, "统计偏离") if session_id and device_id else "",
        "analysis": analysis,
        "tool_results": tool_results,
        "fault_report": fault_report,
        "supervisor_outcome": {
            "trace_saved": bool((supervisor_outcome or {}).get("trace_saved")) if isinstance(supervisor_outcome, dict) else False,
            "answer": str((supervisor_outcome or {}).get("answer") or "")[:500] if isinstance(supervisor_outcome, dict) else "",
        },
        "assessment_id": assessment_id,
        "diagnosis_error": diagnosis_error,
        "model_failed": model_failed,
        "confirmation": public,
        "fault_alert_required": public.get("fault_alert_required") is True,
        "diagnosis_phase": public.get("diagnosis_phase") or PHASE_FAILED,
    }


def _local_physics(entity_a: str, entity_b: str, record_id: str) -> dict:
    from app.services.knowledge_physical_validator import validate_record_physics, validate_research_physics

    if not str(entity_a or "").strip() and not str(entity_b or "").strip():
        return validate_record_physics(record_id)
    return validate_research_physics(entity_a, entity_b, record_id, allow_external_model=False)


def _persist_result(payload: dict, result: dict) -> None:
    event_id = payload.get("event_id")
    if not isinstance(event_id, int) or event_id <= 0:
        return
    try:
        from app.services.monitoring_events import apply_episode_diagnosis_result

        apply_episode_diagnosis_result(event_id, result)
    except Exception:  # noqa: BLE001 — 持久化失败已在结果中保留，避免线程崩溃
        result["persist_error"] = "诊断结果未能写回监测事件。"
