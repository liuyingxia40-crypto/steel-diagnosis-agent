"""按证据缺口补充已注册工具，并保留每一轮假设修正。

不调用模型改写假设，也不把假设写入正式知识图谱。
"""

from __future__ import annotations

import json
import re
import time

from app.services.agent_tools import execute_tool
from app.services.fault_hypothesis import (
    assess_fault_hypotheses,
    asks_for_fault_judgment,
    check_answer_consistency,
    render_fault_answer,
)

MAX_REVISION_ROUNDS = 3
_RECORD_ID = re.compile(r"REC-[A-Za-z0-9-]+")
_DIAGNOSIS_TOOLS = {"diagnose_fault", "analyze_equipment_record"}
_STOP_LIMIT = "达到最大推理轮数"
_STOP_EMPTY = "连续两轮没有新增有效证据"
_STOP_UNVERIFIABLE = "现有数据无法验证关键条件"
_STOP_ENOUGH = "已有足够证据且没有未解决的关键矛盾"
_STOP_FAILED = "工具或模型调用失败"


def run_closed_loop(
    question: str,
    tool_results: object,
    *,
    execute=execute_tool,
    physics=None,
    model_answer: str = "",
    max_rounds: int = MAX_REVISION_ROUNDS,
    model=None,
    prior_hypotheses: object = None,
    prior_physics: object = None,
) -> dict:
    """在已有工具结果上做有限轮修正。model 仅用于测试模型失败，不能改写假设。"""
    rows = [dict(item) for item in tool_results if isinstance(item, dict)] if isinstance(tool_results, list) else []
    limit = max(1, min(int(max_rounds), MAX_REVISION_ROUNDS))
    if not asks_for_fault_judgment(question):
        report = assess_fault_hypotheses(question, rows, model_answer=model_answer, physics=None)
        return _package(report, [], "", rows, [], False)
    rounds = []
    extra_steps = []
    done = _seen_calls(rows)
    previous = [dict(item) for item in prior_hypotheses if isinstance(item, dict)] if isinstance(prior_hypotheses, list) else []
    previous_physics = prior_physics if isinstance(prior_physics, dict) else None
    previous_fingerprint = None
    empty_streak = 0
    stop_reason = _STOP_LIMIT
    for round_no in range(1, limit + 1):
        report = assess_fault_hypotheses(question, rows, model_answer=model_answer, physics=_physics_for(rows, physics))
        revised, changes = _revise(previous, report, rows, previous_physics)
        report = _with_hypotheses(report, revised, model_answer)
        fingerprint = _fingerprint(report)
        if previous_fingerprint is not None and fingerprint == previous_fingerprint:
            empty_streak += 1
        elif previous_fingerprint is not None:
            empty_streak = 0
        previous_fingerprint = fingerprint
        pending = _plan(question, rows, done)
        failure = _failed_result(rows)
        record = {
            "round": round_no,
            "hypotheses": revised,
            "changes": changes,
            "physics": report.get("physics") or {},
            "pending_call": pending,
            "new_evidence": changes["added_evidence"],
            "removed_evidence": changes["removed_evidence"],
            "contradictions": changes["contradictions"],
            "stop_reason": "",
        }
        if model is not None:
            try:
                model({"round": round_no, "hypotheses": revised})
            except Exception as exc:
                record["model_error"] = f"模型调用失败：{exc}"
                record["stop_reason"] = _STOP_FAILED
                rounds.append(record)
                stop_reason = _STOP_FAILED
                break
        if failure:
            record["tool_error"] = failure
            record["stop_reason"] = _STOP_FAILED
            rounds.append(record)
            stop_reason = _STOP_FAILED
            break
        if empty_streak >= 2:
            record["stop_reason"] = _STOP_EMPTY
            rounds.append(record)
            stop_reason = _STOP_EMPTY
            break
        if not pending and _critical_gap(report, rows):
            record["stop_reason"] = _STOP_UNVERIFIABLE
            rounds.append(record)
            stop_reason = _STOP_UNVERIFIABLE
            break
        if not pending:
            record["stop_reason"] = _STOP_ENOUGH
            rounds.append(record)
            stop_reason = _STOP_ENOUGH
            break
        if round_no >= limit:
            record["stop_reason"] = _STOP_LIMIT
            rounds.append(record)
            stop_reason = _STOP_LIMIT
            break
        step = _execute_one(pending, round_no, execute)
        extra_steps.append(step)
        done.add((step["tool_name"], step["arguments"]))
        rows.append({"id": step["id"], "name": step["tool_name"], "result": step["result"], "arguments": step["arguments"]})
        record["tool_call"] = {
            "tool_name": step["tool_name"],
            "arguments": step["arguments"],
            "status": step["status"],
            "elapsed_ms": step["elapsed_ms"],
            "result_ok": isinstance(step["result"], dict) and step["result"].get("ok") is True,
        }
        rounds.append(record)
        previous = revised
        previous_physics = report.get("physics") or {}
        if step["status"] != "成功":
            stop_reason = _STOP_FAILED
            record["stop_reason"] = _STOP_FAILED
            record["tool_error"] = step["result"].get("error") if isinstance(step["result"], dict) else "工具执行失败。"
            break
    else:
        stop_reason = _STOP_LIMIT
    final = assess_fault_hypotheses(question, rows, model_answer=model_answer, physics=_physics_for(rows, physics))
    final_hypotheses, final_changes = _revise(previous, final, rows, previous_physics)
    final = _with_hypotheses(final, final_hypotheses, model_answer)
    final["public_answer"] = _public_answer(final, stop_reason)
    final["stop_reason"] = stop_reason
    final["written_to_graph"] = False
    final["causal_conclusion"] = False
    if rounds:
        rounds[-1]["final_changes"] = final_changes
    return _package(final, rounds, stop_reason, rows, extra_steps, True)


def _package(report: dict, rounds: list[dict], stop_reason: str, rows: list[dict], extra_steps: list[dict], applicable: bool) -> dict:
    report["applicable"] = applicable and bool(report.get("applicable"))
    return {
        "applicable": report["applicable"],
        "fault_report": report,
        "rounds": rounds,
        "stop_reason": stop_reason,
        "tool_results": rows,
        "extra_steps": extra_steps,
        "written_to_graph": False,
        "causal_conclusion": False,
    }


def _physics_for(rows: list[dict], physics):
    if physics is None:
        return None
    record_ids, device_ids = _identities(rows)
    if len(record_ids) != 1 or len(device_ids) > 1:
        return None
    return physics


def _identities(rows: list[dict]) -> tuple[list[str], list[str]]:
    record_ids = []
    device_ids = []
    for item in rows:
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        if result.get("ok") is not True or item.get("name") not in {"detect_anomaly", "analyze_equipment_record"}:
            continue
        record_id = str(result.get("record_id") or "").strip()
        device_id = str(result.get("device_id") or "").strip()
        if record_id and record_id not in record_ids:
            record_ids.append(record_id)
        if device_id and device_id not in device_ids:
            device_ids.append(device_id)
    return record_ids, device_ids


def _critical_gap(report: dict, rows: list[dict]) -> bool:
    if any(item.get("ambiguous") is True for item in _results(rows)):
        return True
    if not _identities(rows)[0]:
        return True
    physics = report.get("physics") if isinstance(report.get("physics"), dict) else {}
    if physics.get("no_fault_hypothesis") is True:
        return True
    if not physics.get("called"):
        return True
    if any(item.get("opposing_evidence") for item in report.get("hypotheses") or []):
        return True
    missing = report.get("missing_capabilities") or []
    if any(str(item).startswith("缺少测量条件：") for item in missing):
        return True
    return False


def _plan(question: str, rows: list[dict], done: set[tuple[str, str]]) -> dict | None:
    if any(item.get("ambiguous") is True for item in _results(rows)):
        relation_blocked = True
    else:
        relation_blocked = False
    for fault in _fault_names(rows):
        call = {"name": "search_knowledge", "arguments": {"query": fault}}
        if _fresh(call, done) and not _searched(rows, fault):
            return call
    equipment = _equipment_name(rows)
    if equipment and not relation_blocked and not _has_tool(rows, "get_equipment_relations"):
        call = {"name": "get_equipment_relations", "arguments": {"name": equipment}}
        if _fresh(call, done):
            return call
    for record_id in _question_records(question) + _identities(rows)[0]:
        if _analyzed(rows, record_id):
            continue
        call = {"name": "analyze_equipment_record", "arguments": {"record_id": record_id}}
        if _fresh(call, done):
            return call
    supplement = _supplement_diagnosis(rows)
    if supplement and _fresh(supplement, done):
        return supplement
    failed = _failed_diagnosis_arguments(rows)
    if failed and _fresh(failed, done):
        return failed
    return None


def _supplement_diagnosis(rows: list[dict]) -> dict | None:
    """有诊断线索但没有候选时，只用已观测内容补调现有故障对照。"""
    for item in rows:
        if item.get("name") != "analyze_equipment_record":
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        if result.get("ok") is not True:
            continue
        record_id = str(result.get("record_id") or "").strip()
        if not _RECORD_ID.fullmatch(record_id):
            continue
        clues = result.get("diagnostic_clues") if isinstance(result.get("diagnostic_clues"), list) else []
        candidates = result.get("candidate_faults") if isinstance(result.get("candidate_faults"), list) else []
        if not clues or candidates:
            continue
        blocked = {str(label).strip() for label in result.get("missing_data") or [] if str(label).strip()}
        features = _clue_features(clues, blocked)
        if not features:
            continue
        arguments: dict = {"observed_features": features}
        equipment = _analysis_equipment(result)
        if equipment:
            arguments["equipment_name"] = equipment
        return {"name": "diagnose_fault", "arguments": arguments}
    return None


def _clue_features(clues: list, blocked: set[str]) -> list[str]:
    features = []
    for clue in clues:
        if not isinstance(clue, dict):
            continue
        if clue.get("partial_condition_met") is False and clue.get("complete_condition_met") is not True:
            continue
        for field in clue.get("observed_fields") or []:
            if not isinstance(field, dict):
                continue
            name = str(field.get("field") or "").strip()
            value = field.get("value")
            if not name or value is None or value == "" or _blocked_text(name, blocked):
                continue
            _append_feature(features, f"{name}={value}")
        if clue.get("complete_condition_met") is True:
            official = str(clue.get("official_condition") or "").strip()
            if official and not _blocked_text(official, blocked):
                _append_feature(features, official)
        statement = _observed_statement(clue, blocked)
        if statement:
            document = str(clue.get("document") or "").strip()
            line = clue.get("line_number")
            source = f"来源：{document} 第{line}行".strip() if document or line else ""
            _append_feature(features, f"{statement}。{source}".strip("。") if source else statement)
        if len(features) >= 20:
            break
    return features[:20]


def _observed_statement(clue: dict, blocked: set[str]) -> str:
    reason = str(clue.get("unverified_reason") or "").strip()
    kept = []
    for part in re.split(r"[。；]", reason):
        text = part.strip()
        if not text or _blocked_text(text, blocked):
            continue
        if any(word in text for word in ("不能", "缺少", "没有这项", "无法", "不是")):
            continue
        kept.append(text)
    return "。".join(kept)


def _blocked_text(text: str, blocked: set[str]) -> bool:
    return any(item in text for item in blocked)


def _append_feature(features: list[str], text: str) -> None:
    cleaned = " ".join(text.split())
    if cleaned and cleaned not in features and len(features) < 20:
        features.append(cleaned)


def _analysis_equipment(result: dict) -> str:
    observation = result.get("observation") if isinstance(result.get("observation"), dict) else {}
    category = str(observation.get("equipment_category") or "").strip()
    if category:
        return category
    equipment = str(result.get("equipment_name") or "").strip()
    return equipment


def _fresh(call: dict, done: set[tuple[str, str]]) -> bool:
    return (call["name"], json.dumps(call["arguments"], ensure_ascii=False, sort_keys=True)) not in done


def _execute_one(call: dict, round_no: int, execute) -> dict:
    arguments = json.dumps(call["arguments"], ensure_ascii=False, sort_keys=True)
    started = time.perf_counter()
    try:
        result = execute(call["name"], arguments)
    except Exception as exc:
        result = {"ok": False, "tool": call["name"], "error": f"工具执行失败：{exc}"}
    elapsed = int((time.perf_counter() - started) * 1000)
    if not isinstance(result, dict):
        result = {"ok": False, "tool": call["name"], "error": "工具没有返回结果对象。"}
    status = "成功" if result.get("ok") is True else "失败"
    return {
        "id": f"revision-{round_no}-{call['name']}",
        "round": round_no,
        "tool_name": call["name"],
        "arguments": arguments,
        "result": result,
        "elapsed_ms": elapsed,
        "status": status,
    }


def _revise(previous: list[dict], report: dict, rows: list[dict], previous_physics: dict | None = None) -> tuple[list[dict], dict]:
    """只在证据、L2 或验证状态实质变化时增加版本。进入新一轮本身不升版。"""
    current = [dict(item) for item in report.get("hypotheses") or [] if not item.get("compound")]
    by_name = {item["faults"][0]: item for item in current if item.get("faults")}
    revised = []
    added_evidence = []
    removed_evidence = []
    contradictions = []
    material_change = False
    physics = report.get("physics") if isinstance(report.get("physics"), dict) else {}
    previous_by_name = {item["faults"][0]: item for item in previous if item.get("faults") and not item.get("compound")}
    latest = _latest_named_faults(rows)
    for name, item in by_name.items():
        prior = previous_by_name.get(name)
        copied, changed, change = _revise_named(name, item, prior, previous, current, latest, physics, previous_physics)
        revised.append(copied)
        material_change = material_change or changed
        added_evidence.extend(change["added_evidence"])
        removed_evidence.extend(change["removed_evidence"])
        contradictions.extend(change["contradictions"])
    for name, prior in previous_by_name.items():
        if name in by_name or prior.get("verification_status") == "被证据否定":
            if name not in by_name and prior.get("verification_status") == "被证据否定":
                revised.append(_hold(prior))
            continue
        # 新请求开头还没有重新跑出故障名称时，不能把已保存假设当成被否定。
        # 只有后续诊断明确给出另一组名称，才淘汰旧名称并升版。
        if latest is None or name in latest:
            revised.append(_hold(prior))
            continue
        kept, change = _negate(prior, current, physics, previous_physics, name)
        revised.append(kept)
        material_change = True
        removed_evidence.extend(change["removed_evidence"])
        added_evidence.extend(change["added_evidence"])
    active = [item for item in revised if item.get("verification_status") != "被证据否定" and not item.get("compound")]
    compound = _revise_compound(previous, active)
    if compound is not None:
        prior_compound = next((item for item in previous if item.get("compound")), None)
        material_change = material_change or int(compound.get("version") or 1) != int((prior_compound or {}).get("version") or 0)
        revised.append(compound)
    return revised, {
        "added_evidence": added_evidence,
        "removed_evidence": removed_evidence,
        "contradictions": contradictions,
        "material_change": material_change,
    }


def _revise_named(name, item, prior, previous, current, latest, physics, previous_physics) -> tuple[dict, bool, dict]:
    copied = dict(item)
    copied["written_to_graph"] = False
    copied["causal_conclusion"] = False
    copied["prior_status"] = "" if prior is None else prior.get("verification_status") or ""
    new_support = _new_items(item.get("supporting_evidence") or [], [] if prior is None else prior.get("supporting_evidence") or [])
    new_oppose = _new_items(item.get("opposing_evidence") or [], [] if prior is None else prior.get("opposing_evidence") or [])
    l2_changed = prior is not None and _fault_l2(previous_physics, name) != _fault_l2(physics, name)
    l2_note = _l2_note(previous_physics, physics, name) if l2_changed else ""
    negated = latest is not None and name not in latest and prior is not None and prior.get("verification_status") != "被证据否定"
    change = {"added_evidence": [], "removed_evidence": [], "contradictions": []}
    if prior is None:
        copied["version"] = 1
        copied["version_history"] = []
        if item.get("opposing_evidence"):
            copied["verification_status"] = "被削弱"
            copied["credibility"] = "降低"
            copied["revision_reason"] = _oppose_reason(item.get("opposing_evidence") or [], "")
            change["contradictions"].append(item["opposing_evidence"][-1].get("detail") or "出现反对证据。")
        elif previous:
            copied["revision_reason"] = _candidate_reason(item.get("supporting_evidence") or [])
            change["added_evidence"].append(name)
        else:
            copied["revision_reason"] = "根据已有工具结果生成，尚未验证。"
        return copied, bool(previous), change
    if negated:
        copied, change = _negate(prior, current, physics, previous_physics, name)
        return copied, True, change
    material = bool(new_support or new_oppose or l2_changed)
    if not material:
        return _hold_with_gaps(prior, item), False, change
    history = [dict(entry) for entry in (prior.get("version_history") or [])]
    history.append(_snapshot(prior))
    copied["version"] = int(prior.get("version") or 1) + 1
    copied["version_history"] = history
    if new_oppose:
        copied["verification_status"] = "被削弱"
        copied["credibility"] = "降低"
        copied["revision_reason"] = _oppose_reason(new_oppose, l2_note)
        change["contradictions"].append(new_oppose[-1].get("detail") or "出现反对证据。")
        change["added_evidence"].extend(_id_labels(new_oppose))
    elif new_support:
        copied["verification_status"] = _keep_unverified(prior.get("verification_status"))
        copied["credibility"] = prior.get("credibility") or "未评估"
        copied["revision_reason"] = _support_reason(new_support, l2_note)
        change["added_evidence"].extend(_id_labels(new_support))
    else:
        copied["verification_status"] = _keep_unverified(prior.get("verification_status"))
        copied["credibility"] = prior.get("credibility") or "未评估"
        copied["revision_reason"] = f"{l2_note}。假设仍然未验证。"
    return copied, True, change


def _hold(prior: dict) -> dict:
    """保留当前版本。重复证据和空转轮次只改写说明，不制造新版本。"""
    held = dict(prior)
    held["supporting_evidence"] = list(prior.get("supporting_evidence") or [])
    held["opposing_evidence"] = list(prior.get("opposing_evidence") or [])
    held["version"] = int(prior.get("version") or 1)
    held["version_history"] = [dict(entry) for entry in (prior.get("version_history") or [])]
    held["verification_status"] = prior.get("verification_status") or "未验证"
    held["credibility"] = prior.get("credibility") or "未评估"
    held["revision_reason"] = _stable_reason(prior)
    held["written_to_graph"] = False
    held["causal_conclusion"] = False
    held["prior_status"] = prior.get("verification_status") or ""
    return held


def _negate(prior: dict, current: list[dict], physics: dict, previous_physics: dict | None, name: str) -> tuple[dict, dict]:
    kept = dict(prior)
    evidence = _other_fault_evidence(current, name)
    history = [dict(entry) for entry in (prior.get("version_history") or [])]
    history.append(_snapshot(prior))
    kept["version"] = int(prior.get("version") or 1) + 1
    kept["version_history"] = history
    kept["prior_status"] = prior.get("verification_status") or ""
    kept["verification_status"] = "被证据否定"
    kept["credibility"] = "降低"
    kept["revision_reason"] = _negation_reason(evidence, _l2_note(previous_physics, physics, name))
    kept["written_to_graph"] = False
    kept["causal_conclusion"] = False
    return kept, {"added_evidence": _id_labels(evidence), "removed_evidence": [name], "contradictions": []}


def _revise_compound(previous: list[dict], active: list[dict]) -> dict | None:
    if len(active) < 2:
        return None
    evidence = [entry for item in active for entry in item.get("supporting_evidence") or []]
    oppose = [entry for item in active for entry in item.get("opposing_evidence") or []]
    compound = {
        "hypothesis_id": "C1",
        "compound": True,
        "faults": [item["faults"][0] for item in active],
        "equipment": active[0].get("equipment") or [],
        "components": active[0].get("components") or [],
        "basis": "多个故障名称同时出现，只形成复合故障假设，不能据此认定因果关系。",
        "supporting_evidence": evidence,
        "opposing_evidence": oppose,
        "missing_measurements": active[0].get("missing_measurements") or [],
        "verification_method": "分别核对每个故障。不能把名称并列写成因果链。",
        "verification_status": "未验证" if not oppose else "被削弱",
        "credibility": "降低" if any(item.get("credibility") == "降低" for item in active) else "未评估",
        "source_locators": [locator for item in active for locator in item.get("source_locators") or []],
        "written_to_graph": False,
        "causal_conclusion": False,
    }
    prior = next((item for item in previous if item.get("compound")), None)
    same = prior is not None and _compound_same(prior, compound)
    if prior is None or not same:
        compound["version"] = 1 if prior is None else int(prior.get("version") or 1) + 1
        compound["version_history"] = [] if prior is None else [dict(entry) for entry in (prior.get("version_history") or [])] + [_snapshot(prior)]
        compound["revision_reason"] = "复合假设只表示多个未验证故障并列，不是因果关系。"
    else:
        compound["version"] = int(prior.get("version") or 1)
        compound["version_history"] = [dict(entry) for entry in (prior.get("version_history") or [])]
        compound["revision_reason"] = _stable_reason(prior)
    return compound


def _compound_same(prior: dict, compound: dict) -> bool:
    return (
        tuple(prior.get("faults") or []) == tuple(compound.get("faults") or [])
        and (prior.get("verification_status") or "") == compound["verification_status"]
        and _keys(prior.get("supporting_evidence") or []) == _keys(compound.get("supporting_evidence") or [])
        and _keys(prior.get("opposing_evidence") or []) == _keys(compound.get("opposing_evidence") or [])
    )


def _hold_with_gaps(prior: dict, fresh: dict) -> dict:
    """版本保持不变，但工具新列出的缺失测量必须留下，不能借此改故障名称。"""
    held = _hold(prior)
    fresh_missing = _labels(fresh.get("missing_measurements") or [])
    prior_missing = _labels(prior.get("missing_measurements") or [])
    if fresh_missing == prior_missing:
        return held
    held["missing_measurements"] = fresh_missing
    held["verification_method"] = "补充缺失测量后再验证：" + "、".join(fresh_missing) + "。测量不足时不能改成另一个故障名称。"
    held["revision_reason"] = "没有新的支持或反对证据，版本保持不变。缺失测量：" + "、".join(fresh_missing) + "。现有资料不足以改成其他故障名称。"
    return held


def _labels(items: list) -> list[str]:
    found = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in found:
            found.append(text)
    return found


def _stable_reason(prior: dict) -> str:
    reason = str(prior.get("revision_reason") or "")
    if _is_material_reason(reason):
        return reason
    return "本轮没有新增证据，假设内容、验证状态和评估结果没有实质变化。"


def _is_material_reason(reason: str) -> bool:
    return (
        reason.startswith("新证据")
        or reason.startswith("L2")
        or reason.startswith("由新增")
        or "被否定" in reason
        or reason.startswith("复合假设只表示")
        or "缺失测量" in reason
    )


def _support_reason(items: list[dict], l2_note: str) -> str:
    parts = ["新证据支持原假设，假设仍然未验证"]
    labels = _format_ids(items)
    if labels:
        parts.append(f"证据编号：{labels}")
    basis = _evidence_basis(items)
    if basis:
        parts.append(f"具体依据：{basis}")
    parts.append("该依据只说明工具返回了这些内容，不是确诊")
    if l2_note:
        parts.append(l2_note)
    return "。".join(parts) + "。"


def _evidence_basis(items: list[dict]) -> str:
    parts = []
    for item in items:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        detail = str(item.get("detail") or "").strip()
        text = "：".join(part for part in (source, detail) if part)
        if text and text not in parts:
            parts.append(text)
    return "；".join(parts)


def _oppose_reason(items: list[dict], l2_note: str) -> str:
    detail = items[-1].get("detail") or "出现反对证据。" if items else "出现反对证据。"
    parts = [f"新证据与原假设矛盾：{detail}"]
    labels = _format_ids(items)
    if labels:
        parts.append(f"证据编号：{labels}")
    if l2_note:
        parts.append(l2_note)
    return "。".join(parts) + "。"


def _candidate_reason(items: list[dict]) -> str:
    reason = "由新增工具结果形成候选假设，不是已验证事实。"
    labels = _format_ids(items)
    if labels:
        reason += f"证据编号：{labels}。"
    return reason


def _negation_reason(items: list[dict], l2_note: str) -> str:
    parts = ["后续工具结果不再包含该故障名称，原假设保留但被否定"]
    labels = _format_ids(items)
    if labels:
        parts.append(f"依据证据编号：{labels}")
    if l2_note:
        parts.append(l2_note)
    return "。".join(parts) + "。"


def _other_fault_evidence(current: list[dict], name: str) -> list[dict]:
    found = []
    seen = set()
    for item in current:
        if not item.get("faults") or item["faults"][0] == name:
            continue
        for evidence in item.get("supporting_evidence") or []:
            if evidence.get("fault") and evidence.get("fault") != name and _evidence_key(evidence) not in seen:
                seen.add(_evidence_key(evidence))
                found.append(evidence)
    return found


def _snapshot(item: dict) -> dict:
    return {
        "version": int(item.get("version") or 1),
        "verification_status": item.get("verification_status") or "",
        "revision_reason": item.get("revision_reason") or "",
        "credibility": item.get("credibility") or "",
        "supporting_evidence_ids": sorted(_evidence_ids(item.get("supporting_evidence") or [])),
        "opposing_evidence_ids": sorted(_evidence_ids(item.get("opposing_evidence") or [])),
    }


def _latest_named_faults(rows: list[dict]) -> set[str] | None:
    """只在后续诊断工具明确给出另一组故障名称时，才用它淘汰旧名称。"""
    latest = None
    for item in rows:
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        if result.get("ok") is not True:
            continue
        names = []
        if item.get("name") == "diagnose_fault" and result.get("status") == "possible_match":
            names = [str(match.get("fault") or "") for match in result.get("matches") or [] if isinstance(match, dict)]
        if item.get("name") == "analyze_equipment_record" and result.get("diagnosis_level") == "possible_match":
            names = [str(match.get("fault") or "") for match in result.get("candidate_faults") or [] if isinstance(match, dict)]
        names = [name for name in names if name]
        if names:
            latest = set(names)
    return latest


def _with_hypotheses(report: dict, hypotheses: list[dict], model_answer: str) -> dict:
    copied = dict(report)
    copied["hypotheses"] = hypotheses
    counts = dict(report.get("counts") or {})
    counts["假设"] = len(hypotheses)
    copied["counts"] = counts
    copied["consistency"] = check_answer_consistency(model_answer, counts)
    copied["unverified_hypotheses"] = [
        item["hypothesis_id"] + " " + "、".join(item.get("faults") or [])
        for item in hypotheses
        if item.get("verification_status") != "被证据否定"
    ]
    copied["written_to_graph"] = False
    copied["causal_conclusion"] = False
    return copied


def _public_answer(report: dict, stop_reason: str) -> str:
    body = render_fault_answer(report)
    lines = [f"停止原因：{stop_reason}", body]
    if stop_reason == _STOP_LIMIT:
        lines.append("达到最大推理轮数不等于诊断成功。")
    lines.append("停止后的假设仍然不是已验证事实。")
    return "\n".join(lines)


def _fingerprint(report: dict) -> tuple:
    """相同资料位置和相同 L2 状态视为同一证据，重复检索不会被当成新增。"""
    keys = set()
    for item in report.get("hypotheses") or []:
        for evidence in list(item.get("supporting_evidence") or []) + list(item.get("opposing_evidence") or []):
            if isinstance(evidence, dict):
                keys.add(_evidence_key(evidence))
        for label in _labels(item.get("missing_measurements") or []):
            keys.add(("missing", label))
    physics = report.get("physics") if isinstance(report.get("physics"), dict) else {}
    l2_rows = []
    for item in physics.get("results") or []:
        if isinstance(item, dict):
            l2_rows.append(_fault_l2(physics, str(item.get("fault") or "")) or ())
    l2 = tuple(sorted(l2_rows))
    return tuple(sorted(keys)) + (bool(physics.get("called")), str(physics.get("error") or ""), l2)


def _evidence_ids(items: list[dict]) -> set[str]:
    return {str(item.get("evidence_id") or "") for item in items if isinstance(item, dict) and item.get("evidence_id")}


def _evidence_key(item: dict) -> tuple:
    return (
        str(item.get("source") or ""),
        str(item.get("locator") or ""),
        str(item.get("detail") or ""),
        str(item.get("fault") or ""),
    )


def _keys(items: list[dict]) -> set[tuple]:
    return {_evidence_key(item) for item in items if isinstance(item, dict)}


def _new_items(current: list[dict], prior: list[dict]) -> list[dict]:
    known = _keys(prior)
    found = []
    for item in current:
        if not isinstance(item, dict):
            continue
        key = _evidence_key(item)
        if key in known:
            continue
        known.add(key)
        found.append(item)
    return found


def _format_ids(items: list[dict]) -> str:
    return "、".join(_id_labels(items))


def _id_labels(items: list[dict]) -> list[str]:
    labels = []
    for item in items:
        if not isinstance(item, dict):
            continue
        evidence_id = str(item.get("evidence_id") or "").strip()
        if not evidence_id:
            continue
        locator = str(item.get("locator") or "").strip()
        label = f"{evidence_id}（{locator}）" if locator else evidence_id
        if label not in labels:
            labels.append(label)
    return labels


def _keep_unverified(status: object) -> str:
    text = str(status or "未验证")
    if text == "已验证":
        return "未验证"
    return text


def _fault_l2(physics: dict | None, fault: str) -> tuple | None:
    if not isinstance(physics, dict) or not physics.get("called"):
        return None
    for item in physics.get("results") or []:
        if isinstance(item, dict) and item.get("fault") == fault:
            return (str(item.get("overall_status") or ""),)
    return ("called",)


def _l2_note(previous_physics: dict | None, physics: dict | None, fault: str) -> str:
    old = _fault_l2(previous_physics, fault)
    new = _fault_l2(physics, fault)
    old_label = "未执行" if old is None else old[0]
    new_label = "未执行" if new is None else new[0]
    return f"L2 校验结果由{old_label}变为{new_label}"


def _results(rows: list[dict]) -> list[dict]:
    found = []
    for item in rows:
        result = item.get("result")
        if isinstance(result, dict) and result.get("ok") is True:
            found.append(result)
    return found


def _fault_names(rows: list[dict]) -> list[str]:
    names = []
    for result in _results(rows):
        for match in result.get("matches") or []:
            if isinstance(match, dict) and match.get("fault") and match["fault"] not in names:
                names.append(match["fault"])
        for candidate in result.get("candidate_faults") or []:
            if isinstance(candidate, dict) and candidate.get("fault") and candidate["fault"] not in names:
                names.append(candidate["fault"])
    return names


def _equipment_name(rows: list[dict]) -> str:
    for result in _results(rows):
        if result.get("ambiguous") is True:
            continue
        node = result.get("node") if isinstance(result.get("node"), dict) else {}
        if node.get("name"):
            return str(node["name"])
        if result.get("equipment_name"):
            return str(result["equipment_name"])
    return ""


def _question_records(question: str) -> list[str]:
    return list(dict.fromkeys(_RECORD_ID.findall(question or "")))


def _searched(rows: list[dict], fault: str) -> bool:
    for item in rows:
        if item.get("name") != "search_knowledge":
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        arguments = _arguments(item)
        if result.get("query") == fault or arguments.get("query") == fault:
            return True
    return False


def _analyzed(rows: list[dict], record_id: str) -> bool:
    for item in rows:
        if item.get("name") != "analyze_equipment_record":
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        arguments = _arguments(item)
        if result.get("record_id") == record_id or arguments.get("record_id") == record_id:
            return True
    return False


def _has_tool(rows: list[dict], name: str) -> bool:
    return any(item.get("name") == name for item in rows)


def _failed_diagnosis_arguments(rows: list[dict]) -> dict | None:
    for item in rows:
        if item.get("name") not in _DIAGNOSIS_TOOLS:
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        if result.get("ok") is True:
            return None
        arguments = _arguments(item)
        if arguments:
            return {"name": item["name"], "arguments": arguments}
    return None


def _failed_result(rows: list[dict]) -> str:
    for item in rows:
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        if result.get("ok") is False and item.get("name") in {"search_knowledge", "get_equipment_relations", "diagnose_fault", "analyze_equipment_record", "detect_anomaly"}:
            return str(result.get("error") or "工具执行失败。")
    return ""


def _arguments(item: dict) -> dict:
    raw = item.get("arguments")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def reassess_record(record_id: str) -> dict:
    """用本地工具和已关联的补充测量重新评估。不调用外部模型，也不写正式图谱。"""
    from app.services.agent_tools import execute_tool

    identity = record_id.strip() if isinstance(record_id, str) else ""
    if _RECORD_ID.fullmatch(identity) is None:
        return _reassess_failure("记录编号格式不正确。")
    analysis = execute_tool("analyze_equipment_record", {"record_id": identity})
    if not isinstance(analysis, dict) or analysis.get("ok") is not True:
        message = analysis.get("error") if isinstance(analysis, dict) else ""
        return _reassess_failure(str(message or "记录分析没有成功，没有开始重新评估。"))
    device_id = analysis.get("device_id")
    if not isinstance(device_id, str) or not device_id.strip():
        return _reassess_failure("记录没有设备编号，没有保存评估。")
    return assess_saved_record(
        identity,
        device_id.strip(),
        [{"id": "reassess-analysis", "name": "analyze_equipment_record", "result": analysis, "arguments": {"record_id": identity}}],
        execute=execute_tool,
        physics=_local_reassess_physics,
        diagnosis_level=str(analysis.get("diagnosis_level") or ""),
    )


def assess_saved_record(
    record_id: str,
    device_id: str,
    tool_results: list,
    *,
    execute=execute_tool,
    physics=None,
    diagnosis_level: str = "",
    trace_id: str = "",
    max_rounds: int = MAX_REVISION_ROUNDS,
    measurements: list | None = None,
) -> dict:
    """读取同一记录的上一版假设，跑闭环，再追加一条评估。不覆盖旧版本。"""
    from app.services.diagnosis_history import latest_state, save_assessment
    from app.services.supplementary_measurement import SupplementaryMeasurementError, list_for_record

    identity = record_id.strip() if isinstance(record_id, str) else ""
    if _RECORD_ID.fullmatch(identity) is None:
        return _reassess_failure("记录编号格式不正确。")
    prior = latest_state(identity) or {}
    loop = run_closed_loop(
        f"记录 {identity} 可能是什么故障？",
        tool_results,
        execute=execute,
        physics=physics,
        prior_hypotheses=prior.get("hypotheses"),
        prior_physics=prior.get("physics"),
        max_rounds=max_rounds,
    )
    report = loop["fault_report"] if isinstance(loop.get("fault_report"), dict) else {}
    hypotheses = [item for item in report.get("hypotheses") or [] if isinstance(item, dict)]
    physics_notes = report.get("physics") if isinstance(report.get("physics"), dict) else {}
    if measurements is None:
        try:
            measurements = list_for_record(identity)
        except SupplementaryMeasurementError:
            measurements = []
    missing = _missing_labels(report, hypotheses)
    saved = save_assessment(
        identity,
        device_id,
        trace_id=trace_id,
        diagnosis_level=diagnosis_level,
        stop_reason=str(loop.get("stop_reason") or ""),
        model_called=False,
        prior_hypotheses=prior.get("hypotheses") if isinstance(prior.get("hypotheses"), list) else [],
        hypotheses=hypotheses,
        physics=physics_notes,
        measurements=measurements,
        missing_measurements=missing,
        public_answer=str(report.get("public_answer") or ""),
    )
    return {
        "ok": True,
        "record_id": identity,
        "device_id": device_id,
        "assessment_id": saved.get("assessment_id") or "",
        "assessment_no": saved.get("assessment_no"),
        "confirmed": False,
        "written_to_graph": False,
        "causal_conclusion": False,
        "official_excel_modified": False,
        "model_called": False,
        "trace_id": saved.get("trace_id") or "",
        "stop_reason": loop.get("stop_reason") or "",
        "change_lines": list(saved.get("change_lines") or []),
        "public_answer": report.get("public_answer") or "",
        "hypotheses": [_public_hypothesis(item) for item in hypotheses],
        "supplementary_measurements": measurements,
        "rounds": [
            {
                "round": item.get("round"),
                "stop_reason": item.get("stop_reason") or "",
                "material_change": bool((item.get("changes") or {}).get("material_change")) if isinstance(item.get("changes"), dict) else False,
            }
            for item in loop.get("rounds") or []
            if isinstance(item, dict)
        ],
    }


def _missing_labels(report: dict, hypotheses: list[dict]) -> list[str]:
    labels = []
    for item in report.get("missing_capabilities") or []:
        if isinstance(item, str) and item.startswith("缺少测量条件："):
            label = item.removeprefix("缺少测量条件：").strip()
            if label and label not in labels:
                labels.append(label)
    for hypothesis in hypotheses:
        for label in hypothesis.get("missing_measurements") or []:
            if isinstance(label, str) and label.strip() and label.strip() not in labels:
                labels.append(label.strip())
    return labels


def _local_reassess_physics(entity_a: str, entity_b: str, record_id: str) -> dict:
    from app.services.knowledge_physical_validator import validate_record_physics, validate_research_physics

    if not str(entity_a or "").strip() and not str(entity_b or "").strip():
        return validate_record_physics(record_id)
    return validate_research_physics(entity_a, entity_b, record_id, allow_external_model=False)


def _public_hypothesis(item: dict) -> dict:
    return {
        "hypothesis_id": item.get("hypothesis_id"),
        "faults": list(item.get("faults") or []),
        "version": item.get("version"),
        "verification_status": item.get("verification_status"),
        "revision_reason": item.get("revision_reason"),
        "version_history": [
            {
                "version": entry.get("version"),
                "verification_status": entry.get("verification_status"),
                "revision_reason": entry.get("revision_reason"),
            }
            for entry in item.get("version_history") or []
            if isinstance(entry, dict)
        ],
        "supporting_evidence": _public_evidence(item.get("supporting_evidence")),
        "opposing_evidence": _public_evidence(item.get("opposing_evidence")),
        "missing_measurements": list(item.get("missing_measurements") or []),
        "causal_conclusion": False,
        "written_to_graph": False,
    }


def _public_evidence(items: object) -> list[dict]:
    rows = []
    if not isinstance(items, list):
        return rows
    for item in items:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "evidence_id": item.get("evidence_id"),
                "source": item.get("source"),
                "locator": item.get("locator"),
                "detail": item.get("detail"),
            }
        )
    return rows


def _reassess_failure(message: str) -> dict:
    return {
        "ok": False,
        "error": message,
        "confirmed": False,
        "written_to_graph": False,
        "causal_conclusion": False,
        "official_excel_modified": False,
        "model_called": False,
        "hypotheses": [],
    }


def _seen_calls(rows: list[dict]) -> set[tuple[str, str]]:
    found = set()
    for item in rows:
        arguments = _arguments(item)
        if item.get("name") and arguments:
            found.add((str(item["name"]), json.dumps(arguments, ensure_ascii=False, sort_keys=True)))
    return found
