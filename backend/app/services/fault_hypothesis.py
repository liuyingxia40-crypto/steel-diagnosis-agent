"""根据已执行工具的返回值整理故障假设。

不读取模型原文来生成故障名称，也不写入正式知识图谱。
物理矛盾只复用现有 L2 校验结果。
"""

from __future__ import annotations

import re

_JUDGMENT_MARKERS = (
    "根因",
    "复合故障",
    "复合",
    "故障假设",
    "可能故障",
    "什么故障",
    "哪种故障",
    "故障原因",
    "原因是什么",
)
_COUNT_PATTERNS = (
    ("相邻关系", re.compile(r"相邻关系[^\d]{0,8}(\d+)\s*条")),
    ("部件", re.compile(r"部件[^\d]{0,8}(\d+)\s*个")),
    ("设备", re.compile(r"设备[^\d]{0,8}(\d+)\s*[个台]")),
    ("假设", re.compile(r"假设[^\d]{0,8}(\d+)\s*条")),
    ("证据", re.compile(r"证据[^\d]{0,8}(\d+)\s*条")),
)


def asks_for_fault_judgment(question: str) -> bool:
    text = question.strip() if isinstance(question, str) else ""
    if any(marker in text for marker in ("观测到", "观察到", "监测到")) and "故障" in text:
        return True
    return any(marker in text for marker in _JUDGMENT_MARKERS)


def assess_fault_hypotheses(question: str, tool_results: object, *, model_answer: str = "", physics=None) -> dict:
    """只根据工具结果建立假设。physics(entity_a, entity_b, record_id) 应调用现有 L2。"""
    applicable = asks_for_fault_judgment(question)
    rows = [item for item in tool_results if isinstance(item, dict)] if isinstance(tool_results, list) else []
    verified: list[str] = []
    insufficient: list[str] = []
    missing: list[str] = []
    supports: list[dict] = []
    opposes: list[dict] = []
    measurements: list[str] = []
    faults: list[dict] = []
    equipment: list[str] = []
    components: list[dict] = []
    relation_count = 0
    ambiguous = False
    record_ids: list[str] = []

    for item in rows:
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        name = str(item.get("name") or result.get("tool") or "")
        if result.get("ok") is not True:
            error = str(result.get("error") or "工具没有返回成功结果。")
            insufficient.append(f"{name or '工具'}：{error}")
            continue
        if name == "search_knowledge":
            _read_search(result, verified, supports, insufficient)
        elif name == "get_equipment_relations":
            relation_count, found_equipment, found_components, blocked = _read_relations(
                result, verified, supports, insufficient, missing
            )
            ambiguous = ambiguous or blocked
            equipment.extend(found_equipment)
            components.extend(found_components)
        elif name == "diagnose_fault":
            _read_diagnosis(result, faults, supports, insufficient, verified)
        elif name == "detect_anomaly":
            record_ids.extend(_read_anomaly(result, supports, opposes, insufficient, verified))
        elif name == "analyze_equipment_record":
            record_ids.extend(_read_analysis(result, faults, supports, opposes, insufficient, verified, measurements, missing))
        else:
            insufficient.append(f"{name or '工具'}：返回内容不能用来建立故障假设。")

    equipment = _unique(equipment)
    record_ids = _unique(record_ids)
    measurements = _unique(measurements)
    if ambiguous:
        equipment = []
        components = []
        missing.append("同名候选没有消歧，不能把其中一个当成设备或部件归属。")
    if not equipment:
        missing.append("未得到知识图谱确认的唯一设备，不能补造设备关系。")
    if not record_ids:
        missing.append("没有记录编号，不能执行 L2 物理校验，也没有生成测量值。")
    if not faults:
        missing.append("工具没有返回故障名称。不能把问题中的猜测写成假设。")
    missing.extend(f"缺少测量条件：{item}" for item in measurements)

    faults = _merge_faults(faults)
    hypotheses = _build_hypotheses(faults, equipment, components, supports, opposes, measurements, ambiguous)
    physics_caller = physics if applicable else None
    physics_notes = _apply_physics(hypotheses, equipment, record_ids, ambiguous, physics_caller, missing, opposes)
    counts = {
        "相邻关系": relation_count,
        "部件": len(components),
        "设备": len(equipment),
        "假设": len(hypotheses),
        "证据": len(supports) + len(opposes),
    }
    consistency = check_answer_consistency(model_answer, counts)
    report = {
        "applicable": applicable,
        "verified_facts": _unique(verified),
        "unverified_hypotheses": [item["hypothesis_id"] + " " + "、".join(item["faults"]) for item in hypotheses],
        "insufficient_evidence": _unique(insufficient),
        "missing_capabilities": _unique(missing),
        "hypotheses": hypotheses,
        "counts": counts,
        "consistency": consistency,
        "physics": physics_notes,
        "written_to_graph": False,
        "causal_conclusion": False,
    }
    report["public_answer"] = render_fault_answer(report) if applicable else ""
    return report


def check_answer_consistency(answer: str, counts: dict) -> dict:
    """只比较回答里的明确数量和工具统计。不一致时不采用回答中的数字。"""
    text = answer if isinstance(answer, str) else ""
    issues = []
    for label, pattern in _COUNT_PATTERNS:
        expected = counts.get(label)
        if not isinstance(expected, int):
            continue
        for found in pattern.findall(text):
            if int(found) != expected:
                issues.append(f"{label}的工具结果是 {expected}，回答草稿写成了 {found}。")
    return {"ok": not issues, "issues": issues}


def render_fault_answer(report: dict) -> str:
    counts = report.get("counts") if isinstance(report.get("counts"), dict) else {}
    lines = []
    issues = (report.get("consistency") or {}).get("issues") or []
    if issues:
        lines.append("回答草稿里的数量与工具结果不一致，正式回答只保留工具返回的数量。")
    hypotheses = report.get("hypotheses") if isinstance(report.get("hypotheses"), list) else []
    if not hypotheses:
        lines.append("无法判断具体故障。现有工具没有返回可以形成假设的故障名称。")
    else:
        lines.append("以下为未验证假设，不是已验证事实，没有写入知识图谱，也不是故障确诊。")
    lines.append(f"未验证假设：{int(counts.get('假设') or 0)} 条")
    lines.append(f"相邻关系：{int(counts.get('相邻关系') or 0)} 条")
    lines.append(f"部件：{int(counts.get('部件') or 0)} 个")
    lines.append(f"设备：{int(counts.get('设备') or 0)} 个")
    lines.append(f"证据：{int(counts.get('证据') or 0)} 条")
    for item in hypotheses:
        lines.extend(_hypothesis_lines(item))
    facts = report.get("verified_facts") if isinstance(report.get("verified_facts"), list) else []
    if facts:
        lines.append("已验证事实：")
        lines.extend(f"- {item}" for item in facts)
    gaps = report.get("insufficient_evidence") if isinstance(report.get("insufficient_evidence"), list) else []
    if gaps:
        lines.append("证据不足：")
        lines.extend(f"- {item}" for item in gaps)
    missing = report.get("missing_capabilities") if isinstance(report.get("missing_capabilities"), list) else []
    if missing:
        lines.append("缺失能力：")
        lines.extend(f"- {item}" for item in missing)
    lines.extend(_record_physics_lines(report.get("physics") if isinstance(report.get("physics"), dict) else {}))
    return "\n".join(lines)


def _record_physics_lines(physics: dict) -> list[str]:
    if physics.get("level") != "record" or physics.get("called") is not True:
        return []
    result = next((item for item in physics.get("results") or [] if isinstance(item, dict)), {})
    lines = ["记录级 L2：已根据记录编号执行。没有可供验证的故障假设。"]
    lines.append("数值条件一致：" + _id_text(result.get("consistent_checks")))
    lines.append("数值条件矛盾：" + _id_text(result.get("contradicted_checks")))
    lines.append("数据不足：" + _id_text(result.get("insufficient_checks")))
    lines.append("不适用：" + _id_text(result.get("not_applicable_checks")))
    sources = result.get("supplementary_measurements") if isinstance(result.get("supplementary_measurements"), list) else []
    if sources:
        rendered = [
            f"{item.get('measurement_id')} SHA-256 {item.get('content_sha256')} {item.get('origin_note')}"
            for item in sources
            if isinstance(item, dict)
        ]
        lines.append("补充测量来源：" + "；".join(rendered))
        lines.append("补充测量没有写入知识图谱，也不能把数值条件升级成故障确诊。")
    lines.append("记录级数值结果不能生成故障名称，也不是故障确诊或因果关系。")
    return lines


def _id_text(items: object) -> str:
    if not isinstance(items, list) or not items:
        return "无"
    return "、".join(str(item) for item in items)


def _merge_faults(faults: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for item in faults:
        name = item["fault"]
        current = merged.setdefault(name, {"fault": name, "basis": item.get("basis") or "", "locators": []})
        current["locators"].extend(item.get("locators") or [])
        if item.get("basis") and item["basis"] not in current["basis"]:
            current["basis"] = current["basis"] + " " + item["basis"]
    return list(merged.values())


def _build_hypotheses(faults, equipment, components, supports, opposes, measurements, ambiguous: bool) -> list[dict]:
    component_names = [item["name"] for item in components]
    singles = []
    for index, fault in enumerate(faults, start=1):
        own_supports = [item for item in supports if item.get("fault") in {None, fault["fault"]}]
        status = "待核查" if ambiguous or opposes else "未验证"
        credibility = "降低" if opposes else "未评估"
        if measurements and not own_supports:
            status = "证据不足"
        singles.append(
            _hypothesis(
                f"H{index}",
                [fault["fault"]],
                equipment,
                component_names,
                fault.get("basis") or "工具返回了可能匹配，不是确诊。",
                own_supports,
                opposes,
                measurements,
                "对照该故障在工具结果中的来源定位；有记录编号时复用 L2 物理校验。",
                status,
                credibility,
                fault.get("locators") or [],
                False,
            )
        )
    if len(singles) < 2:
        return singles
    combined_faults = [item["faults"][0] for item in singles]
    compound = _hypothesis(
        "C1",
        combined_faults,
        equipment,
        component_names,
        "多个工具结果分别给出了不同故障名称。把它们并列只表示复合故障假设，不是因果链，也尚未证实。",
        supports,
        opposes,
        measurements,
        "分别核对每个故障的证据。有记录编号时对每个故障复用 L2 物理校验，不把组合本身写成已验证关系。",
        "待核查" if ambiguous or opposes else "未验证",
        "降低" if opposes else "未评估",
        _unique([locator for item in singles for locator in item["source_locators"]]),
        True,
    )
    return [*singles, compound]


def _apply_physics(hypotheses, equipment, record_ids, ambiguous: bool, physics, missing: list[str], opposes: list[dict]) -> dict:
    notes = {"called": False, "level": "", "no_fault_hypothesis": False, "results": [], "error": ""}
    if ambiguous or not record_ids or physics is None:
        return notes
    record_id = record_ids[0]
    if not hypotheses:
        _apply_record_physics(notes, physics, record_id, missing)
        return notes
    if not equipment:
        return notes
    notes["level"] = "hypothesis"
    for item in hypotheses:
        if item.get("compound"):
            continue
        fault = item["faults"][0]
        try:
            result = physics(equipment[0], fault, record_id)
        except Exception as exc:
            message = f"L2 物理校验没有完成：{exc}"
            missing.append(message)
            notes["error"] = message
            item["verification_status"] = "待核查"
            item["credibility"] = "降低"
            continue
        if not isinstance(result, dict):
            missing.append("L2 物理校验没有返回结果对象，不能把它视为通过。")
            item["verification_status"] = "待核查"
            item["credibility"] = "降低"
            continue
        notes["called"] = True
        notes["results"].append(
            {
                "fault": fault,
                "overall_status": result.get("overall_status"),
                "written_to_graph": False,
                "causal_conclusion": False,
            }
        )
        for label in result.get("missing_measurements") or []:
            if isinstance(label, str) and label.strip():
                item["missing_measurements"] = _unique([*item["missing_measurements"], label.strip()])
        contradicted = result.get("contradicted_checks") if isinstance(result.get("contradicted_checks"), list) else []
        checks = result.get("physical_checks") if isinstance(result.get("physical_checks"), list) else []
        titles = {check.get("check_id"): check for check in checks if isinstance(check, dict)}
        for check_id in contradicted:
            matched = titles.get(check_id) if isinstance(titles.get(check_id), dict) else {}
            title = matched.get("title") or check_id
            detail = f"{title} 与现有数据矛盾。"
            source = matched.get("evidence_source") if isinstance(matched.get("evidence_source"), dict) else {}
            if source.get("kind") == "supplementary_measurement":
                detail += "来源是补充测量 " + "、".join(str(item) for item in source.get("measurement_ids") or []) + "，不是官方 Excel，也不是故障确诊。"
            oppose = {"evidence_id": f"L2-{check_id}", "source": "validate_research_physics", "detail": detail, "fault": fault}
            opposes.append(oppose)
            item["opposing_evidence"].append(oppose)
            item["verification_status"] = "待核查"
            item["credibility"] = "降低"
        if result.get("written_to_graph") is True or result.get("causal_conclusion") is True:
            item["verification_status"] = "待核查"
            item["credibility"] = "降低"
            missing.append("L2 结果声称已写图或已证明因果，本流程拒绝采用该结论。")
    for item in hypotheses:
        if item.get("compound") and any(single["credibility"] == "降低" for single in hypotheses if not single.get("compound")):
            item["verification_status"] = "待核查"
            item["credibility"] = "降低"
            existing = item["opposing_evidence"]
            item["opposing_evidence"] = existing + [entry for entry in opposes if entry not in existing]
    return notes


def _apply_record_physics(notes: dict, physics, record_id: str, missing: list[str]) -> None:
    """记录级校验只保留数值结果。数值一致也不能写出故障名称。"""
    notes["level"] = "record"
    notes["no_fault_hypothesis"] = True
    try:
        result = physics("", "", record_id)
    except Exception as exc:
        notes["error"] = f"L2 物理校验没有完成：{exc}"
        missing.append(notes["error"])
        return
    if not isinstance(result, dict):
        notes["error"] = "L2 物理校验没有返回结果对象，不能把它视为通过。"
        missing.append(notes["error"])
        return
    notes["called"] = True
    notes["results"].append(
        {
            "fault": "",
            "level": "record",
            "overall_status": result.get("overall_status"),
            "consistent_checks": list(result.get("consistent_checks") or []),
            "contradicted_checks": list(result.get("contradicted_checks") or []),
            "insufficient_checks": list(result.get("insufficient_checks") or []),
            "not_applicable_checks": list(result.get("not_applicable_checks") or []),
            "supplementary_measurements": list(result.get("supplementary_measurements") or []),
            "written_to_graph": False,
            "causal_conclusion": False,
        }
    )
    for label in result.get("missing_measurements") or []:
        if isinstance(label, str) and label.strip():
            text = f"缺少测量条件：{label.strip()}"
            if text not in missing:
                missing.append(text)
    if result.get("written_to_graph") is True or result.get("causal_conclusion") is True:
        missing.append("L2 结果声称已写图或已证明因果，本流程拒绝采用该结论。")


def _hypothesis(hypothesis_id, faults, equipment, components, basis, supports, opposes, measurements, method, status, credibility, locators, compound: bool) -> dict:
    return {
        "hypothesis_id": hypothesis_id,
        "compound": compound,
        "faults": faults,
        "equipment": list(equipment),
        "components": list(components),
        "basis": basis,
        "supporting_evidence": list(supports),
        "opposing_evidence": list(opposes),
        "missing_measurements": list(measurements),
        "verification_method": method,
        "verification_status": status,
        "credibility": credibility,
        "source_locators": _unique([str(item) for item in locators if str(item).strip()]),
        "written_to_graph": False,
        "causal_conclusion": False,
    }


def _hypothesis_lines(item: dict) -> list[str]:
    lines = [
        f"{item['hypothesis_id']}",
        "故障：" + "、".join(item["faults"]),
        "复合故障：" + ("是" if item["compound"] else "否"),
        "涉及设备：" + ("、".join(item["equipment"]) if item["equipment"] else "图谱未确认"),
        "涉及部件：" + ("、".join(item["components"]) if item["components"] else "图谱未确认"),
        f"验证状态：{item['verification_status']}",
        f"假设版本：{item.get('version') or 1}",
        f"修正理由：{item.get('revision_reason') or '尚无新证据。'}",
        f"可信程度：{item['credibility']}",
        f"推理依据：{item['basis']}",
        "支持证据：",
    ]
    lines.extend(_evidence_lines(item["supporting_evidence"]))
    lines.append("反对证据：")
    lines.extend(_evidence_lines(item["opposing_evidence"]))
    missing = "、".join(item["missing_measurements"]) if item["missing_measurements"] else "无"
    lines.append(f"缺失测量条件：{missing}")
    lines.append(f"验证方法：{item['verification_method']}")
    locators = "；".join(item["source_locators"]) if item["source_locators"] else "无"
    lines.append(f"来源定位：{locators}")
    return lines


def _evidence_lines(items: list[dict]) -> list[str]:
    if not items:
        return ["- 无"]
    lines = []
    for item in items:
        locator = item.get("locator") or ""
        detail = item.get("detail") or ""
        lines.append(f"- {item.get('evidence_id') or ''} {item.get('source') or ''} {locator} {detail}".strip())
    return lines


def _read_search(result: dict, verified: list[str], supports: list[dict], insufficient: list[str]) -> None:
    rows = result.get("results") if isinstance(result.get("results"), list) else []
    if not rows:
        insufficient.append("知识检索没有返回资料。")
        return
    kept = False
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        document = str(row.get("document") or "").strip()
        locator = str(row.get("locator") or "").strip()
        if not document:
            continue
        kept = True
        label = f"{document} {locator}".strip()
        verified.append(label)
        supports.append({"evidence_id": f"S{index}", "source": "search_knowledge", "locator": label, "detail": "检索返回了该资料位置。", "fault": None})
    if not kept:
        insufficient.append("知识检索没有返回可核对的资料来源。")


def _read_relations(result, verified, supports, insufficient, missing):
    if result.get("ambiguous") is True:
        count = len(result.get("candidates") or [])
        insufficient.append(f"设备关系返回 {count} 个同名候选，归属不明确。")
        missing.append("需要使用候选节点的完整名称或节点编号再次查询。")
        return 0, [], [], True
    relations = result.get("relations") if isinstance(result.get("relations"), list) else []
    node = result.get("node") if isinstance(result.get("node"), dict) else {}
    equipment = [str(node.get("name")).strip()] if str(node.get("name") or "").strip() else []
    if not relations:
        insufficient.append(str(result.get("message") or "知识图谱没有返回可用关系。"))
        return 0, equipment, [], False
    components = []
    for index, item in enumerate(relations, start=1):
        if not isinstance(item, dict):
            continue
        fact = f"{item.get('source_name') or ''} {item.get('relation') or ''} {item.get('target_name') or ''}".strip()
        verified.append(fact)
        supports.append({"evidence_id": f"R{index}", "source": "get_equipment_relations", "locator": fact, "detail": "图谱中的相邻关系。", "fault": None})
        if item.get("relation") == "HAS_COMPONENT" and item.get("target_name"):
            components.append({"name": str(item.get("target_name"))})
    count = result.get("count")
    relation_count = count if isinstance(count, int) and not isinstance(count, bool) else len(relations)
    return relation_count, equipment, components, False


def _read_diagnosis(result, faults, supports, insufficient, verified) -> None:
    if result.get("status") != "possible_match" or result.get("confirmed") is True:
        insufficient.append(str(result.get("message") or "故障特征对照证据不足。"))
        return
    matches = result.get("matches") if isinstance(result.get("matches"), list) else []
    if not matches:
        insufficient.append("故障特征对照没有返回可核对的匹配。")
        return
    for match in matches:
        if not isinstance(match, dict) or match.get("confirmed") is True:
            continue
        fault = str(match.get("fault") or "").strip()
        if not fault:
            continue
        locators = []
        features = match.get("matched_features") if isinstance(match.get("matched_features"), list) else []
        for feature in features:
            if not isinstance(feature, dict):
                continue
            locator = " ".join(part for part in (str(feature.get("document") or ""), str(feature.get("locator") or "")) if part).strip()
            if locator:
                locators.append(locator)
                verified.append(locator)
        faults.append({"fault": fault, "basis": "diagnose_fault 返回 possible_match，confirmed 为 false。", "locators": locators})
        supports.append(
            {
                "evidence_id": f"D-{fault}",
                "source": "diagnose_fault",
                "locator": "；".join(locators),
                "detail": f"{fault} 只是可能匹配，不是确诊。",
                "fault": fault,
            }
        )


def _read_anomaly(result, supports, opposes, insufficient, verified) -> list[str]:
    status = str(result.get("detection_status") or "")
    record_id = str(result.get("record_id") or "").strip()
    if status in {"基线不足", "无法判定"} or not status:
        insufficient.append(f"{record_id} 的统计检测状态为{status or '空'}。".strip())
        return [record_id] if record_id else []
    label = f"{record_id} 的统计检测状态为{status}。这是统计结果，不是故障确诊。"
    verified.append(label)
    entry = {"evidence_id": f"A-{record_id or 'unknown'}", "source": "detect_anomaly", "locator": record_id, "detail": label, "fault": None}
    if status == "未发现显著偏离":
        opposes.append(entry)
    else:
        supports.append(entry)
    return [record_id] if record_id else []


def _read_analysis(result, faults, supports, opposes, insufficient, verified, measurements, missing) -> list[str]:
    record_id = str(result.get("record_id") or "").strip()
    level = str(result.get("diagnosis_level") or "")
    detection = str(result.get("detection_status") or "")
    deviation = result.get("statistical_deviation") if isinstance(result.get("statistical_deviation"), dict) else {}
    detection = detection or str(deviation.get("detection_status") or "")
    if detection:
        verified.append(f"{record_id} 的统计检测状态为{detection}。这是统计结果，不是故障确诊。")
    for label in result.get("missing_data") or []:
        if isinstance(label, str) and label.strip():
            measurements.append(label.strip())
    if level == "possible_match":
        candidates = result.get("candidate_faults") if isinstance(result.get("candidate_faults"), list) else []
        named = False
        for candidate in candidates:
            if not isinstance(candidate, dict) or candidate.get("confirmed") is True:
                continue
            fault = str(candidate.get("fault") or "").strip()
            if not fault:
                continue
            named = True
            faults.append({"fault": fault, "basis": "analyze_equipment_record 返回 possible_match，confirmed 为 false。", "locators": [record_id] if record_id else []})
            supports.append({"evidence_id": f"AN-{fault}", "source": "analyze_equipment_record", "locator": record_id, "detail": f"{fault} 只是可能匹配。", "fault": fault})
        if not named:
            insufficient.append(f"{record_id} 的诊断级别为 possible_match，但没有返回故障名称。")
    elif level == "diagnostic_clue":
        insufficient.append(f"{record_id} 只有部分诊断线索，不能据此确定故障名称。")
        missing.append("诊断线索缺少完整条件，不能升级为故障假设。")
    elif level:
        insufficient.append(f"{record_id} 的诊断级别为{level}，证据不足以确定具体故障。")
    else:
        insufficient.append("记录分析没有返回可核对的诊断级别。")
    if detection == "未发现显著偏离":
        opposes.append({"evidence_id": f"AN-STATUS-{record_id or 'unknown'}", "source": "analyze_equipment_record", "locator": record_id, "detail": "统计检测未发现显著偏离。", "fault": None})
    return [record_id] if record_id else []


def _unique(items: list[str]) -> list[str]:
    found = []
    for item in items:
        if item and item not in found:
            found.append(item)
    return found
