"""核对研究假设能够计算的数值条件。

只复用已有观测、统计偏离和 KB-02 原文。不调用模型，不把数值一致写成因果证明。
"""

from __future__ import annotations

import re

from app.services.diagnostic_bridge import analyze_record_for_diagnosis
from app.services.knowledge_graph import load_graph
from app.services.knowledge_graph_import import merge_imported_graph
from app.services.knowledge_research_reasoner import research_knowledge_relationship
from app.services.official_dataset import MAX_LIMIT, MEASUREMENT_FIELDS, DatasetError, get_device_records, get_observation

VALIDATION_NOTE = "L2仅验证已有数据能够检查的物理或数值条件，不构成故障确诊，也不自动证明因果关系。"
SPEED_FIELD = "转速(rpm)"
FREQUENCY_FIELD = "转频(Hz)"
X_FIELD = "X向振动(mm/s)"
Y_FIELD = "Y向振动(mm/s)"
TEMPERATURE_FIELDS = ("驱动端轴承温度(°C)", "非驱动端轴承温度(°C)")
FIXED_FREQUENCY_FIELDS = ("BPFO频率(Hz)", "BPFI频率(Hz)", "BSF频率(Hz)", "啮合频率(Hz)")
COMPARED_FIELDS = (
    SPEED_FIELD,
    FREQUENCY_FIELD,
    X_FIELD,
    Y_FIELD,
    *TEMPERATURE_FIELDS,
    "BPFO幅值(g)",
    "1X转频幅值(mm/s)",
)


def validate_record_physics(record_id: str | None = None) -> dict:
    """只核对一条观测记录自身能够计算的数值条件。不生成故障名称，也不调用模型。"""
    identity = record_id.strip() if isinstance(record_id, str) and record_id.strip() else None
    loaded = _load_record(identity, "", "")
    return _physics_report(
        {},
        "",
        "",
        identity,
        loaded,
        [],
        loaded["checks"],
        "record",
        VALIDATION_NOTE + "没有可供验证的故障假设。",
    )


def validate_research_physics(
    entity_a: str,
    entity_b: str,
    record_id: str | None = None,
    *,
    entity_a_id: str | None = None,
    entity_b_id: str | None = None,
    entity_a_scope: str | None = None,
    entity_b_scope: str | None = None,
    evaluations: list[dict] | None = None,
    allow_external_model: bool = True,
) -> dict:
    """从服务器已有证据重算物理检查。evaluations 只供带有来源的测试夹具使用。"""
    left = entity_a.strip()
    right = entity_b.strip()
    identity = record_id.strip() if isinstance(record_id, str) and record_id.strip() else None
    research = research_knowledge_relationship(
        left,
        right,
        entity_a_id=entity_a_id,
        entity_b_id=entity_b_id,
        entity_a_scope=entity_a_scope,
        entity_b_scope=entity_b_scope,
        allow_external_model=allow_external_model,
    )
    hypotheses = _hypotheses(research)
    loaded = _load_record(identity, left, right)
    checks = [*loaded["checks"], *_fixture_checks(evaluations or [])]
    note = VALIDATION_NOTE + ("" if hypotheses else "没有可供因果验证的候选链。")
    return _physics_report(research, left, right, identity, loaded, hypotheses, checks, "hypothesis", note)


def _load_record(identity: str | None, left: str, right: str) -> dict:
    checks: list[dict] = []
    observation = None
    conflicts: list[dict] = []
    missing = [
        "2X幅值",
        "相位",
        "原始振动波形",
        "峭度",
        "峰值因子",
        "BPFO及其谐波的频谱形态",
        "1X是否为频谱主导峰",
    ]
    supplements: list[dict] = []
    record_found = True
    record_message = ""
    if identity:
        try:
            found = get_observation(identity)
        except DatasetError as exc:
            found = {"found": False, "record": None}
            record_message = exc.message
        record = found.get("record") if isinstance(found, dict) else None
        if not isinstance(record, dict):
            record_found = False
            record_message = record_message or f"没有找到记录编号「{identity}」。"
            missing = []
        else:
            observation, conflicts = _observation_evidence(record)
            analysis = analyze_record_for_diagnosis(identity)
            checks.extend(_record_checks(left, right, record, analysis, conflicts))
            from app.services.diagnostic_evidence import evidence_for_record as structured_evidence_for_record
            from app.services.supplementary_measurement import evidence_for_record

            extra = evidence_for_record(record)
            checks.extend(extra["checks"])
            structured = structured_evidence_for_record(record)
            checks.extend(structured["checks"])
            resolved = set(extra["resolved_missing"]) | set(structured["resolved_missing"])
            missing = [item for item in missing if item not in resolved]
            supplements = list(extra["measurements"]) + list(structured.get("evidence") or [])
    else:
        missing.extend(["转速(rpm)", "转频(Hz)", "X向振动(mm/s)", "Y向振动(mm/s)", *TEMPERATURE_FIELDS])
        checks.append(
            _check(
                "no_observation",
                "未提供观测记录",
                "not_applicable",
                {},
                "没有 record_id，不能生成测量值。",
                "可检验条件只来自字段是否存在，不来自虚构读数。",
            )
        )
    return {
        "checks": checks,
        "observation": observation,
        "conflicts": conflicts,
        "missing": missing,
        "supplements": supplements,
        "record_found": record_found,
        "record_message": record_message,
    }


def _physics_report(research: dict, left: str, right: str, identity: str | None, loaded: dict, hypotheses: list[dict], checks: list[dict], level: str, note: str) -> dict:
    grouped = _group(checks)
    overall = _overall(loaded["record_found"], identity, hypotheses, checks)
    temporal = _temporal(identity, loaded["observation"], loaded["conflicts"], hypotheses)
    return {
        "research_id": research.get("research_id"),
        "entity_a": left,
        "entity_b": right,
        "entity_a_identity": research.get("entity_a_identity"),
        "entity_b_identity": research.get("entity_b_identity"),
        "entity_a_other_matches": research.get("entity_a_other_matches") or [],
        "entity_b_other_matches": research.get("entity_b_other_matches") or [],
        "record_id": identity,
        "record_found": loaded["record_found"],
        "record_message": loaded["record_message"],
        "research_status": research.get("research_status") if level == "hypothesis" else "record_only",
        "research_hypotheses": hypotheses,
        "physical_checks": checks,
        "observation_evidence": loaded["observation"],
        "consistent_checks": grouped["consistent"],
        "contradicted_checks": grouped["contradicted"],
        "insufficient_checks": grouped["insufficient_data"],
        "not_applicable_checks": grouped["not_applicable"],
        "missing_measurements": _unique(loaded["missing"]),
        "supplementary_measurements": loaded.get("supplements") or [],
        "temporal_validation": temporal,
        "overall_status": overall,
        "causal_validation_status": "unverified",
        "causal_conclusion": False,
        "written_to_graph": False,
        "validation_level": level,
        "validation_note": note,
    }


def _record_checks(left: str, right: str, record: dict, analysis: dict, conflicts: list[dict]) -> list[dict]:
    values = record.get("values") if isinstance(record.get("values"), dict) else {}
    checks = [
        _speed_check(values),
        _xy_check(values),
        _deviation_check(analysis, "Y向振动(mm/s)", "Y向振动的统计偏离不能认定轴承磨损。"),
        *_temperature_checks(left, right, record, analysis),
        *_spectrum_checks(analysis, values),
        _fixed_frequency_check(values),
        _conflict_check(record, conflicts),
        _deviation_summary(analysis),
        _measurement_inventory(values),
    ]
    return checks


def _deviation_summary(analysis: dict) -> dict:
    detected = analysis.get("statistical_deviation") if isinstance(analysis.get("statistical_deviation"), dict) else {}
    metrics = []
    for item in detected.get("analyzed_metrics") or []:
        if not isinstance(item, dict) or not item.get("field"):
            continue
        metrics.append(
            {
                "field": item.get("field"),
                "current_value": item.get("current_value"),
                "median": item.get("median"),
                "robust_z": item.get("robust_z"),
                "metric_status": item.get("metric_status"),
            }
        )
    return _check(
        "historical_deviation_summary",
        "已有统计偏离指标",
        "not_applicable" if metrics else "insufficient_data",
        {"detection_status": detected.get("detection_status"), "metrics": metrics},
        "复用现有统计分析结果，不重新计算 Robust Z-score。",
        "统计偏离只说明指标相对历史基线的状态，不能生成故障名称，也不是确诊。",
    )


def _measurement_inventory(values: dict) -> dict:
    present = []
    absent = []
    for field in MEASUREMENT_FIELDS:
        value = values.get(field)
        if value is None or value == "":
            absent.append(field)
        else:
            present.append(field)
    return _check(
        "measurement_inventory",
        "记录已有测量与缺失测量",
        "not_applicable",
        {"present": present, "absent": absent},
        "只列出这条记录实际带有的测量字段。",
        "缺失字段没有生成读数。没有波形、相位或谐波测量时不能补出这些结果。",
    )


def _speed_check(values: dict) -> dict:
    rpm = _number(values.get(SPEED_FIELD))
    measured = _number(values.get(FREQUENCY_FIELD))
    inputs = {SPEED_FIELD: values.get(SPEED_FIELD), FREQUENCY_FIELD: values.get(FREQUENCY_FIELD)}
    if rpm is None or measured is None:
        return _check(
            "speed_frequency",
            "转速与转频一致性",
            "insufficient_data",
            inputs,
            "f = RPM / 60",
            "转速或转频缺失，不能核对二者是否对应。",
        )
    computed = rpm / 60
    difference = measured - computed
    places = _decimal_places(measured)
    tolerance = 0.5 * (10 ** (-places))
    status = "consistent" if abs(difference) <= tolerance else "contradicted"
    return _check(
        "speed_frequency",
        "转速与转频一致性",
        status,
        inputs,
        "f = RPM / 60",
        f"转频记录值去掉末尾 0 后有 {places} 位小数，容差取该末位的一半，即 {tolerance} Hz。"
        "这只核对记录值与换算值的修约差异，不是工业报警规则。",
        computed={"rotation_frequency_hz": computed, "difference_hz": difference, "tolerance_hz": tolerance},
    )


def _xy_check(values: dict) -> dict:
    x_value = _number(values.get(X_FIELD))
    y_value = _number(values.get(Y_FIELD))
    inputs = {X_FIELD: values.get(X_FIELD), Y_FIELD: values.get(Y_FIELD)}
    if x_value is None or y_value is None:
        return _check("xy_vibration", "X向与Y向振动关系", "insufficient_data", inputs, "同一条记录直接比较两个振动值。", "缺少 X 向或 Y 向振动。")
    relation = "X>Y" if x_value > y_value else "X<Y" if x_value < y_value else "X=Y"
    return _check(
        "xy_vibration",
        "X向与Y向振动关系",
        "consistent",
        inputs,
        "只比较同一条记录中的 X向振动(mm/s) 和 Y向振动(mm/s)。",
        f"实测关系是 {relation}。X>Y 不能确认转子不平衡。",
        computed={"relation": relation},
    )


def _deviation_check(analysis: dict, field: str, limit: str) -> dict:
    metric = _metric(analysis, field)
    if metric is None:
        return _check("historical_deviation_" + field, field + "历史统计偏离", "insufficient_data", {}, "复用 detect_anomaly 的结果。", "没有该字段的统计偏离结果。")
    return _check(
        "historical_deviation_y" if field.startswith("Y") else "historical_deviation",
        field + "历史统计偏离",
        "not_applicable",
        {"field": field, "current_value": metric.get("current_value"), "median": metric.get("median"), "robust_z": metric.get("robust_z"), "metric_status": metric.get("metric_status")},
        "复用现有 median/MAD Robust Z-score 结果，不重新计算。",
        limit,
    )


def _temperature_checks(left: str, right: str, record: dict, analysis: dict) -> list[dict]:
    values = record.get("values") if isinstance(record.get("values"), dict) else {}
    readings = {field: values.get(field) for field in TEMPERATURE_FIELDS}
    feature = _feature(analysis, "temperature")
    trend = feature.get("temperature_checks") if isinstance(feature, dict) else {}
    checks = [
        _check(
            "temperature_reading",
            "轴承温度单次读数",
            "not_applicable",
            readings,
            "单次温度只作为观测值。",
            "单次温度不能当成持续升温，也不能认定润滑故障。",
            computed={"trend": _public_trend(trend)},
        )
    ]
    deviation_items = []
    for field in TEMPERATURE_FIELDS:
        metric = _metric(analysis, field)
        if metric is not None:
            deviation_items.append(
                {
                    "field": field,
                    "current_value": metric.get("current_value"),
                    "median": metric.get("median"),
                    "robust_z": metric.get("robust_z"),
                    "metric_status": metric.get("metric_status"),
                }
            )
    checks.append(
        _check(
            "temperature_history",
            "轴承温度历史统计偏离",
            "not_applicable" if deviation_items else "insufficient_data",
            {"metrics": deviation_items},
            "复用 detect_anomaly 对两侧轴承温度的历史比较。",
            "历史统计偏离不是润滑故障，也不是持续升温。",
        )
    )
    topic = _temperature_topic(left, right)
    threshold, line_number, condition = _kb02_temperature_threshold(feature if isinstance(feature, dict) else None)
    if not topic:
        checks.append(
            _check(
                "kb02_temperature_threshold",
                "KB-02 温度数值子句",
                "not_applicable",
                {},
                condition or "KB-02 润滑故障温度条目。",
                "当前研究对象不涉及润滑或温升，不套用该温度子句。",
            )
        )
    elif threshold is None:
        checks.append(
            _check(
                "kb02_temperature_threshold",
                "KB-02 温度数值子句",
                "insufficient_data",
                readings,
                "KB-02 温度条目没有给出可提取的数值界限。",
                "不能自行补一个温度合格限。",
            )
        )
    else:
        comparable = [(_number(values.get(field)), field) for field in TEMPERATURE_FIELDS]
        finite = [(value, field) for value, field in comparable if value is not None]
        if len(finite) != len(TEMPERATURE_FIELDS):
            status = "insufficient_data"
            detail = "有轴承温度缺失，不能完成该数值子句。"
        elif any(value > threshold for value, _field in finite):
            status = "insufficient_data"
            detail = "有读数高于 KB-02 写明的温度数值，但持续上升仍然不能用单次读数证明。"
        else:
            status = "contradicted"
            detail = f"两侧读数都没有高于 KB-02 条目中的 {threshold:g}°C。这不认定、也不排除润滑故障。"
        checks.append(
            _check(
                "kb02_temperature_threshold",
                "KB-02 温度数值子句",
                status,
                readings,
                f"KB-02_故障模式诊断知识库.md 第{line_number}行：{condition}",
                detail,
                computed={"threshold_celsius": threshold, "trend_algorithm_defined_by_kb02": False},
            )
        )
    checks.append(
        _check(
            "temperature_trend",
            "轴承温度上升趋势",
            "insufficient_data",
            {"reused_trend": _public_trend(trend)},
            "KB-02 只写了持续上升，没有规定可执行算法。现有三点检查不能代替该条件。",
            "不能把单次温度或统计偏离写成持续升温。",
        )
    )
    checks.append(_device_limit_check(record))
    return checks


def _device_limit_check(record: dict) -> dict:
    category = str(record.get("equipment_category") or "")
    device_id = str(record.get("device_id") or "")
    matched = _matching_limits(device_id, category)
    if not matched:
        return _check(
            "device_temperature_limit",
            "设备温度限值",
            "not_applicable",
            {"device_id": device_id, "equipment_category": category},
            "只使用 device_label 或适用范围与观测设备编号、设备类别完全相同的图谱参数。",
            "没有完全匹配的温度限值，不借用其他设备的限值，也不判断合格或超限。",
        )
    if len(matched) != 1 or not _plain_number(matched[0].get("value")):
        return _check(
            "device_temperature_limit",
            "设备温度限值",
            "insufficient_data",
            {"match_count": len(matched)},
            "匹配到的限值不是单一数值，或存在多个限值。",
            "不能把一段文字限值自动换成合格判断。",
        )
    return _check(
        "device_temperature_limit",
        "设备温度限值",
        "insufficient_data",
        {"value": matched[0].get("value"), "device_label": matched[0].get("device_label")},
        "已找到完全匹配的限值，但它不是可直接比较的单个数字。",
        "不把文字限值解释成报警规则。",
    )


def _spectrum_checks(analysis: dict, values: dict) -> list[dict]:
    checks = []
    envelope = _feature(analysis, "envelope_spectrum")
    checks.append(
        _check(
            "bpfo_harmonics",
            "BPFO及谐波",
            "insufficient_data",
            {"BPFO幅值(g)": values.get("BPFO幅值(g)"), "BPFI幅值(g)": values.get("BPFI幅值(g)"), "BSF幅值(g)": values.get("BSF幅值(g)")},
            _feature_basis(envelope),
            "BPFO幅值不是 BPFO 及其谐波同时存在的证据。",
        )
    )
    dominant = _feature(analysis, "dominant_frequency")
    checks.append(
        _check(
            "dominant_1x",
            "1X主导峰",
            "insufficient_data",
            {"1X转频幅值(mm/s)": values.get("1X转频幅值(mm/s)"), "转频(Hz)": values.get(FREQUENCY_FIELD)},
            _feature_basis(dominant),
            "1X幅值不是 1X 主导峰的证明。",
        )
    )
    harmonic = _feature(analysis, "harmonic_2x")
    checks.append(
        _check(
            "second_harmonic_ratio",
            "2X与1X比值",
            "insufficient_data",
            {},
            _feature_basis(harmonic),
            "没有 2X 幅值，不能计算 2X/1X。",
        )
    )
    phase = _feature(analysis, "phase")
    checks.append(
        _check(
            "phase_stability",
            "相位稳定",
            "insufficient_data",
            {},
            _feature_basis(phase),
            "没有相位，不能证明相位稳定。",
        )
    )
    kurtosis = _feature(analysis, "kurtosis")
    crest = _feature(analysis, "crest_factor")
    checks.append(
        _check(
            "waveform_statistics",
            "峭度与峰值因子",
            "insufficient_data",
            {},
            _feature_basis(kurtosis) or _feature_basis(crest),
            "没有原始波形，不能生成峭度或峰值因子。",
        )
    )
    return checks


def _fixed_frequency_check(values: dict) -> dict:
    return _check(
        "fixed_characteristic_frequency",
        "表内固定特征频率",
        "not_applicable",
        {field: values.get(field) for field in FIXED_FREQUENCY_FIELDS},
        "这些频率是工作表内的固定参数，现有统计检测也没有把它们当作检测到的频谱峰值。",
        "固定特征频率不是实际检测到了该频率的证明。",
    )


def _conflict_check(record: dict, conflicts: list[dict]) -> dict:
    return _check(
        "same_time_conflict",
        "同一时刻记录冲突",
        "not_applicable" if not conflicts else "insufficient_data",
        {
            "record_id": record.get("record_id"),
            "collected_at": record.get("collected_at"),
            "conflicts": conflicts,
            "values_merged": False,
        },
        "同一设备、同一采集时间的不同数值各自保留，不取平均，也不合并成一条观测。",
        "没有冲突时不需要合并；有冲突时不能用合并值继续做因果或限值判断。",
    )


def _observation_evidence(record: dict) -> tuple[dict, list[dict]]:
    siblings = get_device_records(
        str(record.get("device_id") or ""),
        start_time=str(record.get("collected_at") or ""),
        end_time=str(record.get("collected_at") or ""),
        limit=MAX_LIMIT,
    )
    rows = [item for item in siblings.get("records") or [] if isinstance(item, dict)]
    conflicts = []
    for field in COMPARED_FIELDS:
        present = []
        for item in rows:
            value = (item.get("values") or {}).get(field) if isinstance(item.get("values"), dict) else None
            if value is not None:
                present.append({"record_id": item.get("record_id"), "value": value})
        if len({str(item["value"]) for item in present}) > 1:
            conflicts.append({"field": field, "values_by_record": present, "values_merged": False})
    evidence = {
        "record_id": record.get("record_id"),
        "device_id": record.get("device_id"),
        "equipment_category": record.get("equipment_category"),
        "collected_at": record.get("collected_at"),
        "sheet_name": record.get("sheet_name"),
        "data_source": "钢铁设备数据集_开发集.xlsx",
        "duplicate_timestamp": record.get("duplicate_timestamp"),
        "same_timestamp_count": record.get("same_timestamp_count"),
        "values": {field: (record.get("values") or {}).get(field) for field in MEASUREMENT_FIELDS},
        "same_time_conflicts": conflicts,
        "values_merged": False,
    }
    return evidence, conflicts


def _matching_limits(device_id: str, category: str) -> list[dict]:
    graph = merge_imported_graph(load_graph())
    found = []
    identities = {item for item in (device_id, category) if item}
    for node in graph.get("nodes") or []:
        data = node.get("data") if isinstance(node, dict) else None
        if not isinstance(data, dict) or data.get("type") != "parameter":
            continue
        if "温度" not in str(data.get("label") or ""):
            continue
        device_label = str(data.get("device_label") or "")
        applicability = str(data.get("applicability") or "")
        if device_label in identities or applicability in identities:
            found.append(
                {
                    "node_id": node.get("id"),
                    "label": data.get("label"),
                    "value": data.get("value"),
                    "unit": data.get("unit"),
                    "device_label": device_label,
                    "applicability": applicability,
                }
            )
    return found


def _kb02_temperature_threshold(feature: dict | None) -> tuple[float | None, int | None, str]:
    if not isinstance(feature, dict):
        return None, None, ""
    condition = str(feature.get("official_condition") or "")
    match = re.search(r">\s*(\d+(?:\.\d+)?)", condition)
    number = float(match.group(1)) if match else None
    line = feature.get("line_number")
    return number, line if isinstance(line, int) else None, condition


def _fixture_checks(cases: list[dict]) -> list[dict]:
    checks = []
    for case in cases:
        if not isinstance(case, dict) or case.get("source") != "独立测试夹具":
            continue
        measured = case.get("measured")
        reference = case.get("reference")
        tolerance = case.get("tolerance")
        if case.get("applicable") is not True:
            status = "not_applicable"
            difference = None
        elif measured is None or reference is None or tolerance is None:
            status = "insufficient_data"
            difference = None
        else:
            difference = abs(float(measured) - float(reference))
            status = "consistent" if difference <= float(tolerance) else "contradicted"
        checks.append(
            _check(
                str(case.get("check_id") or "fixture"),
                "独立测试夹具数值约束",
                status,
                {"measured": measured, "reference": reference, "tolerance": tolerance, "source": "独立测试夹具"},
                str(case.get("basis") or "测试夹具给出了比较值和容差。"),
                "夹具结果只验证校验器的四种状态，不是官方观测，也不证明因果关系。",
                computed={"absolute_difference": difference},
            )
        )
    return checks


def _hypotheses(research: dict) -> list[dict]:
    rows = []
    for item in research.get("research_hypotheses") or []:
        if not isinstance(item, dict):
            continue
        copied = dict(item)
        copied["validation_status"] = "unverified"
        copied["causal_conclusion"] = False
        copied["in_graph"] = False
        rows.append(copied)
    return rows


def _temporal(identity: str | None, observation: dict | None, conflicts: list[dict], hypotheses: list[dict]) -> dict:
    if not identity or observation is None:
        return {
            "causal_order": "insufficient_data",
            "future_data_used": False,
            "conflicts_merged": False,
            "reason": "没有观测时刻，无法验证因果先后，也没有生成测量值。",
        }
    return {
        "record_id": observation.get("record_id"),
        "device_id": observation.get("device_id"),
        "collected_at": observation.get("collected_at"),
        "same_device_record": True,
        "same_time_conflict_count": len(conflicts),
        "conflicts_merged": False,
        "future_data_used": False,
        "single_timestamp": True,
        "causal_order": "insufficient_data",
        "reason": "只有一个采集时刻。不能用同一时刻的观测证明原因先于结果，也不能使用更晚的数据证明此前的过程。",
        "hypothesis_count": len(hypotheses),
    }


def _overall(found: bool, identity: str | None, hypotheses: list[dict], checks: list[dict]) -> str:
    if identity and not found:
        return "record_not_found"
    if any(item["status"] == "contradicted" for item in checks):
        return "numeric_contradiction"
    if not hypotheses:
        return "no_testable_hypothesis"
    if any(item["status"] == "consistent" for item in checks) and any(item["status"] == "insufficient_data" for item in checks):
        return "partial_numeric_support"
    if any(item["status"] == "insufficient_data" for item in checks):
        return "insufficient_data"
    if any(item["status"] == "consistent" for item in checks):
        return "partial_numeric_support"
    return "insufficient_data"


def _group(checks: list[dict]) -> dict[str, list[str]]:
    grouped = {status: [] for status in ("consistent", "contradicted", "insufficient_data", "not_applicable")}
    for item in checks:
        grouped.setdefault(item["status"], []).append(item["check_id"])
    return grouped


def _check(check_id: str, title: str, status: str, inputs: dict, basis: str, limit: str, computed: dict | None = None) -> dict:
    return {
        "check_id": check_id,
        "title": title,
        "status": status,
        "inputs": inputs,
        "basis": basis,
        "limit": limit,
        "computed": computed or {},
        "supports_fault": False,
        "supports_causal_hypothesis": False,
    }


def _feature(analysis: dict, code: str) -> dict | None:
    for item in analysis.get("feature_checks") or []:
        if isinstance(item, dict) and item.get("feature_code") == code:
            return item
    return None


def _feature_basis(feature: dict | None) -> str:
    if not isinstance(feature, dict):
        return "KB-02 中没有读到对应频谱条目。"
    return f"{feature.get('document')} 第{feature.get('line_number')}行：{feature.get('official_condition')}"


def _metric(analysis: dict, field: str) -> dict | None:
    detected = analysis.get("statistical_deviation") if isinstance(analysis.get("statistical_deviation"), dict) else {}
    for item in detected.get("analyzed_metrics") or []:
        if isinstance(item, dict) and item.get("field") == field:
            return item
    return None


def _public_trend(trend: object) -> dict:
    if not isinstance(trend, dict):
        return {}
    public = {}
    for field, item in trend.items():
        if not isinstance(item, dict):
            continue
        public[field] = {
            "history_value_count": item.get("history_value_count"),
            "conflict_count": item.get("conflict_count"),
            "strictly_increasing_into_current": item.get("strictly_increasing_into_current"),
            "method": item.get("method"),
        }
    return public


def _temperature_topic(left: str, right: str) -> bool:
    text = f"{left}{right}"
    return any(word in text for word in ("润滑", "温升", "温度"))


def _plain_number(value: object) -> bool:
    return _number(value) is not None and not isinstance(value, str)


def _decimal_places(value: float) -> int:
    text = format(value, "f").rstrip("0").rstrip(".")
    if "." not in text:
        return 0
    return len(text.split(".", 1)[1])


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _unique(items: list[str]) -> list[str]:
    result = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result
