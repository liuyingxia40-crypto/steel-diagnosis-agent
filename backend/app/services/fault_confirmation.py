"""故障告警资格的结构化确认判定。

不把统计偏离、诊断线索、possible_match、未验证假设或模型自然语言当成确诊。
也不把 diagnose_fault / analyze 返回里的 confirmed 字段改写成 true。
"""

from __future__ import annotations

from typing import Any

PHASE_STATISTICAL = "statistical_deviation"
PHASE_DIAGNOSING = "diagnosing"
PHASE_INSUFFICIENT = "insufficient_evidence"
PHASE_FAILED = "diagnosis_failed"
PHASE_POSSIBLE = "possible_match_unconfirmed"
PHASE_CONFIRMED = "confirmed_fault"

_ALERT_NOTE = "仅当结构化规则确认故障时才发出故障告警；统计偏离与可能匹配不会告警。"


def evaluate_fault_confirmation(
    *,
    analysis: dict | None = None,
    fault_report: dict | None = None,
    physics: dict | None = None,
    tool_results: list | None = None,
    diagnosis_error: str | None = None,
    model_failed: bool = False,
) -> dict:
    """根据结构化证据判定 diagnosis_phase 与 fault_alert_required。"""
    error_text = (diagnosis_error or "").strip()
    if model_failed or error_text:
        return _result(
            PHASE_FAILED,
            False,
            reasons=[error_text or "诊断失败或模型不可用。"],
            confirmed_faults=[],
        )

    analysis = analysis if isinstance(analysis, dict) else {}
    if analysis.get("ok") is not True:
        message = analysis.get("error") if isinstance(analysis.get("error"), str) else ""
        return _result(
            PHASE_FAILED,
            False,
            reasons=[message.strip() or "记录分析没有成功。"],
            confirmed_faults=[],
        )

    detection = ""
    deviation = analysis.get("statistical_deviation")
    if isinstance(deviation, dict):
        detection = str(deviation.get("detection_status") or "")
    detection = detection or str(analysis.get("detection_status") or "")

    clues = analysis.get("diagnostic_clues") if isinstance(analysis.get("diagnostic_clues"), list) else []
    candidates = analysis.get("candidate_faults") if isinstance(analysis.get("candidate_faults"), list) else []
    checks = analysis.get("feature_checks") if isinstance(analysis.get("feature_checks"), list) else []
    level = str(analysis.get("diagnosis_level") or "")

    if detection == "偏离基线" and not clues and not candidates:
        return _result(
            PHASE_STATISTICAL,
            False,
            reasons=["只有统计偏离，没有诊断结论，不告警。"],
            confirmed_faults=[],
        )

    if clues and not candidates:
        return _result(
            PHASE_INSUFFICIENT,
            False,
            reasons=["只有诊断线索，没有故障候选，不告警。"],
            confirmed_faults=[],
        )

    if _has_unverified_hypotheses_only(fault_report) and not _complete_features(checks):
        return _result(
            PHASE_POSSIBLE,
            False,
            reasons=["仅有未验证故障假设，不告警。"],
            confirmed_faults=[],
        )

    if _physics_ok_without_fault_evidence(physics, candidates, clues):
        return _result(
            PHASE_INSUFFICIENT,
            False,
            reasons=["L2 数值一致但没有故障证据，不告警。"],
            confirmed_faults=[],
        )

    if _tool_failure(tool_results):
        return _result(
            PHASE_FAILED,
            False,
            reasons=["诊断工具执行失败，不告警。"],
            confirmed_faults=[],
        )

    if _physics_contradicted(physics):
        return _result(
            PHASE_INSUFFICIENT,
            False,
            reasons=["L2 物理校验存在矛盾证据，不告警。"],
            confirmed_faults=[],
        )

    complete = _complete_features(checks)
    if not complete:
        if candidates or level == "possible_match":
            return _result(
                PHASE_POSSIBLE,
                False,
                reasons=["存在可能匹配或候选故障，但缺少完整条件证据，不告警。"],
                confirmed_faults=[],
            )
        if detection == "偏离基线":
            return _result(
                PHASE_STATISTICAL,
                False,
                reasons=["只有统计偏离，没有完整条件确认，不告警。"],
                confirmed_faults=[],
            )
        return _result(
            PHASE_INSUFFICIENT,
            False,
            reasons=["证据不足，不能确认故障，不告警。"],
            confirmed_faults=[],
        )

    if not candidates:
        return _result(
            PHASE_INSUFFICIENT,
            False,
            reasons=["已有完整条件核对，但没有故障候选，不告警。"],
            confirmed_faults=[],
        )

    confirmed_faults = _structurally_confirmed_faults(candidates, complete)
    if not confirmed_faults:
        return _result(
            PHASE_POSSIBLE,
            False,
            reasons=["候选故障未能被完整条件结构化确认，不告警。"],
            confirmed_faults=[],
        )

    return _result(
        PHASE_CONFIRMED,
        True,
        reasons=[
            "完整条件特征已核对，且 diagnose_fault 候选与完整条件匹配；结构化规则确认故障告警。",
            _ALERT_NOTE,
        ],
        confirmed_faults=confirmed_faults,
    )


def _result(phase: str, alert: bool, *, reasons: list[str], confirmed_faults: list[str]) -> dict:
    return {
        "ok": True,
        "diagnosis_phase": phase,
        "fault_alert_required": bool(alert) and phase == PHASE_CONFIRMED,
        "confirmation_status": phase,
        "reasons": list(reasons),
        "confirmed_fault_names": list(confirmed_faults),
        # 工具层 confirmed 仍为 false；告警资格单独字段表达。
        "tool_confirmed": False,
        "note": _ALERT_NOTE,
    }


def _complete_features(checks: list) -> list[dict]:
    complete = []
    for item in checks:
        if not isinstance(item, dict):
            continue
        if item.get("complete_condition_met") is True:
            complete.append(item)
    return complete


def _structurally_confirmed_faults(candidates: list, complete: list[dict]) -> list[str]:
    complete_codes = {
        str(item.get("feature_code") or "").strip()
        for item in complete
        if str(item.get("feature_code") or "").strip()
    }
    faults: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        # 不得把工具里误标的 confirmed 当真；这里只看结构化匹配。
        fault = str(candidate.get("fault") or "").strip()
        if not fault:
            continue
        codes = candidate.get("matched_feature_codes")
        code_list = [str(code).strip() for code in codes] if isinstance(codes, list) else []
        code_list = [code for code in code_list if code]
        if not code_list:
            continue
        # 候选匹配到的特征码必须全部来自完整条件核对，缺一不可。
        if all(code in complete_codes for code in code_list) and fault not in faults:
            faults.append(fault)
    return faults


def _has_unverified_hypotheses_only(fault_report: dict | None) -> bool:
    if not isinstance(fault_report, dict):
        return False
    hypotheses = fault_report.get("hypotheses")
    if not isinstance(hypotheses, list) or not hypotheses:
        return False
    for item in hypotheses:
        if not isinstance(item, dict):
            continue
        status = str(item.get("verification_status") or "").strip()
        if status and status not in {"unverified", "未验证", "possible", "possible_match"}:
            # 非未验证状态仍不足以单独告警；这里只用于“仅有未验证假设”分支识别。
            return False
    return True


def _physics_ok_without_fault_evidence(physics: dict | None, candidates: list, clues: list) -> bool:
    if not isinstance(physics, dict) or physics.get("ok") is not True:
        return False
    contradicted = physics.get("contradicted_checks")
    if isinstance(contradicted, list) and contradicted:
        return False
    return not candidates and not clues


def _physics_contradicted(physics: dict | None) -> bool:
    if not isinstance(physics, dict):
        return False
    contradicted = physics.get("contradicted_checks")
    return isinstance(contradicted, list) and len(contradicted) > 0


def _tool_failure(tool_results: list | None) -> bool:
    if not isinstance(tool_results, list):
        return False
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else item
        if not isinstance(result, dict):
            continue
        if result.get("ok") is False:
            return True
        if isinstance(result.get("error"), str) and result.get("error").strip():
            # analyze/diagnose 失败对象通常 ok=False；兼容仅有 error 的结构。
            if result.get("ok") is False or item.get("name") in {
                "analyze_equipment_record",
                "diagnose_fault",
                "search_knowledge",
                "get_equipment_relations",
                "validate_research_physics",
            }:
                if result.get("ok") is False:
                    return True
    return False


def public_confirmation(result: dict[str, Any]) -> dict:
    return {
        "diagnosis_phase": result.get("diagnosis_phase") or PHASE_STATISTICAL,
        "fault_alert_required": result.get("fault_alert_required") is True,
        "confirmation_status": result.get("confirmation_status") or "",
        "reasons": list(result.get("reasons") or []),
        "confirmed_fault_names": list(result.get("confirmed_fault_names") or []),
        "tool_confirmed": False,
        "note": result.get("note") or _ALERT_NOTE,
    }
