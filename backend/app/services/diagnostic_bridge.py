"""把一条官方数值观测和统计偏离整理成保守的诊断线索。

只使用官方观测白名单和 KB-02 已经写明的频谱特征。
不读取结果标签，不把统计偏离或单条线索写成确诊、概率或报警。
"""

from __future__ import annotations

import math
from datetime import datetime

from app.services.agent_tools import diagnose_fault
from app.services.anomaly_detection import detect_anomaly
from app.services.knowledge_search import MARKDOWN_DIR
from app.services.official_dataset import (
    DATASET_FILE,
    MAX_LIMIT,
    MEASUREMENT_FIELDS,
    DatasetError,
    get_device_records,
    get_observation,
)

_KB02_DOCUMENT = "KB-02_故障模式诊断知识库.md"
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_X_FIELD = "X向振动(mm/s)"
_Y_FIELD = "Y向振动(mm/s)"
_DRIVE_TEMP = "驱动端轴承温度(°C)"
_NONDRIVE_TEMP = "非驱动端轴承温度(°C)"
_TEMP_FIELDS = (_DRIVE_TEMP, _NONDRIVE_TEMP)
_TREND_POINTS = 3
_WITHHELD_FIELDS = frozenset(
    {
        "设备状态",
        "故障类型",
        "故障严重度",
        "故障位置",
        "诊断置信度",
        "频谱描述",
        "根因分析",
        "建议措施",
        "优先级",
        "影响程度",
    }
)
_MISSING_DATA = (
    "原始振动波形",
    "峭度",
    "峰值因子",
    "2X幅值",
    "相位",
    "包络谱谐波形态",
    "1X是否为频谱主导峰",
    "啮合频率边带",
    "裙边形态",
    "宽频噪声形态",
    "中低频段能量",
    "金属摩擦异音",
    "负荷变化",
    "轴向偏心测量",
)
_BLOCKED_REASONS = {
    "envelope_spectrum": "BPFO、BPFI、BSF 或包络谱峰值升高，不能证明包络谱出现对应特征频率及其谐波。",
    "amplitude_modulation": "没有调制频谱，不能证明故障频率调制在 1X 上。",
    "kurtosis": "没有原始波形，不能计算峭度，也不能生成冲击特征。",
    "crest_factor": "没有原始波形，不能计算峰值因子。",
    "dominant_frequency": "只有 1X 幅值和转频数值，不能证明 1X 是频谱主导峰。",
    "harmonic_2x": "没有 2X 幅值，不能判断 2X 与 1X 的比例。",
    "phase": "没有相位数据，不能声称 1X 相位稳定，也没有负荷序列证明幅值随负荷变化。",
    "gear_mesh_freq": "啮合频率或幅值的数值不能证明啮合频率在频谱中主导。",
    "sidebands": "没有边带频谱，不能证明 1X 调制边带密集。",
    "modulation": "没有频谱形态，不能证明啮合频率被 1X 调制或呈裙边状。",
    "tooth_impact": "没有宽频冲击波形，啮合频率幅值不能证明断齿。",
    "broadband": "没有频谱，不能证明宽频噪声抬升且没有显著离散峰值。",
    "noise": "没有声音记录，不能声称金属摩擦异音。",
    "vibration": "单点振动值和振动均方根不能证明中低频段能量增加。",
}
_CONTEXT_FIELDS = {
    "envelope_spectrum": ("包络谱峰值(g)", "BPFO幅值(g)", "BPFI幅值(g)", "BSF幅值(g)"),
    "dominant_frequency": ("转频(Hz)", "1X转频幅值(mm/s)"),
    "gear_mesh_freq": ("啮合频率(Hz)", "啮合频率幅值(g)"),
    "tooth_impact": ("啮合频率幅值(g)",),
}
_TREND_METHOD = (
    "自行定义的检查：同一设备、当前时刻之前、去掉同一时刻冲突值后，"
    f"最近 {_TREND_POINTS} 个温度严格递增且都低于当前值。"
    "这不是 KB-02 规定的持续上升算法。"
)
_KB02_FEATURES: tuple[dict, ...] | None = None


def analyze_record_for_diagnosis(record_id: str) -> dict:
    """串联一条官方观测、统计偏离和 KB-02 中当前数据能够核对的线索。"""
    text = record_id.strip() if isinstance(record_id, str) else ""
    if not text:
        return _failure("", "请提供官方数据集中的记录编号。没有记录编号时不能选择其他记录。")
    try:
        found = get_observation(text)
        detected = detect_anomaly(text)
    except DatasetError as exc:
        return _failure(text, exc.message)
    except (OSError, ValueError) as exc:
        return _failure(text, f"读取观测或统计偏离失败：{exc}")
    record = found.get("record") if isinstance(found, dict) else None
    if not isinstance(record, dict) or detected.get("device_id") is None:
        return _failure(text, f"没有找到记录编号「{text}」，不能改用其他记录，也不能编造观测。")
    observation = _public_observation(record)
    values = observation["values"]
    temperature = _temperature_context(observation, values)
    axial_evidence = _axial_eccentricity_evidence(str(observation["record_id"]))
    checks = [
        _assess(feature, values, temperature, observation=observation, axial_evidence=axial_evidence)
        for feature in _kb02_features()
    ]
    complete = [item["official_condition"] for item in checks if item["complete_condition_met"] is True]
    # 完整条件优先；否则用带知识库短语的结构化观测描述对照（仍为 possible_match，不降低完整条件门槛）。
    match_features = _observable_match_features(checks, observation=observation)
    diagnosis = diagnose_fault(match_features) if match_features else None
    candidates = _candidates(diagnosis)
    clues = [item for item in checks if item["partial_condition_met"] is True or item["complete_condition_met"] is True]
    for item in checks:
        item["passed_to_diagnose_fault"] = bool(match_features) and (
            item.get("complete_condition_met") is True or item.get("partial_condition_met") is True
        )
    level = _level(str(detected.get("detection_status") or ""), clues, candidates)
    missing = _missing_for_analysis(checks, axial_evidence)
    return _scrub(
        {
            "ok": True,
            "record_id": observation["record_id"],
            "error": "",
            "diagnosis_level": level,
            "confirmed": False,
            "data_source": DATASET_FILE.name,
            "observation": observation,
            "statistical_deviation": detected,
            "feature_checks": checks,
            "diagnostic_clues": clues,
            "candidate_faults": candidates,
            "diagnose_fault_called": diagnosis is not None,
            "diagnose_fault_result": diagnosis,
            "missing_data": missing,
            "insufficient_reasons": _reasons(detected, checks),
            "warnings": _warnings(detected),
            "supporting_evidence": _supporting_evidence_public(axial_evidence),
        }
    )


def _failure(record_id: str, message: str) -> dict:
    return _scrub(
        {
            "ok": False,
            "record_id": record_id,
            "error": message,
            "diagnosis_level": "unable_to_evaluate",
            "confirmed": False,
            "data_source": DATASET_FILE.name,
            "observation": None,
            "statistical_deviation": None,
            "feature_checks": [],
            "diagnostic_clues": [],
            "candidate_faults": [],
            "diagnose_fault_called": False,
            "diagnose_fault_result": None,
            "missing_data": list(_MISSING_DATA),
            "insufficient_reasons": [message],
            "warnings": ["没有可核对的官方观测，不能编造诊断特征。"],
        }
    )


def _public_observation(record: dict) -> dict:
    raw_values = record.get("values") if isinstance(record.get("values"), dict) else {}
    return {
        "record_id": record.get("record_id"),
        "device_id": record.get("device_id"),
        "equipment_category": record.get("equipment_category"),
        "collected_at": record.get("collected_at"),
        "sheet_name": record.get("sheet_name"),
        "excel_row": record.get("excel_row"),
        "duplicate_timestamp": record.get("duplicate_timestamp"),
        "same_timestamp_count": record.get("same_timestamp_count"),
        "values": {field: raw_values.get(field) for field in MEASUREMENT_FIELDS},
    }


def _kb02_features() -> tuple[dict, ...]:
    global _KB02_FEATURES
    if _KB02_FEATURES is not None:
        return _KB02_FEATURES
    path = MARKDOWN_DIR / _KB02_DOCUMENT
    fault = ""
    in_spectrum = False
    features = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("## "):
            fault = stripped[3:].strip()
            in_spectrum = False
            continue
        if stripped.startswith("### "):
            in_spectrum = stripped[4:].strip() == "频谱特征"
            continue
        if not in_spectrum or not stripped.startswith("•") or "：" not in stripped:
            continue
        code, description = stripped.lstrip("•").strip().split("：", 1)
        features.append(
            {
                "fault": fault,
                "section": f"{fault} / 频谱特征",
                "code": code.strip(),
                "description": description.strip(),
                "official_condition": description.strip(),
                "line_number": number,
                "document": _KB02_DOCUMENT,
            }
        )
    if len(features) != 16:
        raise RuntimeError("KB-02 频谱特征不是已核对的 16 条，联动已停止。")
    _KB02_FEATURES = tuple(features)
    return _KB02_FEATURES


def _assess(
    feature: dict,
    values: dict,
    temperature: dict,
    *,
    observation: dict | None = None,
    axial_evidence: dict | None = None,
) -> dict:
    code = feature["code"]
    item = {
        "feature_code": code,
        "related_fault_section": feature["section"],
        "document": feature["document"],
        "line_number": feature["line_number"],
        "official_condition": feature["official_condition"],
        "observed_fields": [_field_value(field, values) for field in _CONTEXT_FIELDS.get(code, ())],
        "method": "当前 Excel 没有该条目所需的波形、频谱形态或相位，不能把数值转换成这条特征。",
        "partial_condition_met": False,
        "complete_condition_met": False,
        "unverified_reason": _BLOCKED_REASONS.get(code, "这条官方特征没有可用的数值核对规则。"),
        "passed_to_diagnose_fault": False,
        "evidence_refs": [],
    }
    if code == "amplitude" and "X>Y" in feature["official_condition"]:
        return _assess_xy(item, values, axial_evidence=axial_evidence)
    if code == "temperature" and "持续上升" in feature["official_condition"] and ">80" in feature["official_condition"]:
        return _assess_temperature(item, values, temperature)
    return item


def _assess_xy(item: dict, values: dict, *, axial_evidence: dict | None = None) -> dict:
    """KB-02 第38行：径向振动X>Y方向（轴向侧偏心）。

    完整条件要求：同一官方记录 X>Y，且已登记可核对的轴向偏心测量。
    仅有 X>Y 或仅有轴向偏心都不能标为完整条件。
    """
    x_value = _number(values.get(_X_FIELD))
    y_value = _number(values.get(_Y_FIELD))
    item["observed_fields"] = [_field_value(_X_FIELD, values), _field_value(_Y_FIELD, values)]
    item["method"] = (
        "核对同一官方记录的 X/Y 振动，并在存在结构化轴向偏心证据时合并 KB-02 第38行的轴向侧偏心要求。"
        "不把轴向位移或轴向振动当成轴向偏心。"
    )
    if x_value is None or y_value is None:
        item["unverified_reason"] = "同一条记录缺少有限的 X 向或 Y 向振动，不能核对 X>Y。"
        return item
    xy_ok = x_value > y_value
    if not xy_ok:
        item["unverified_reason"] = "同一条记录的 X向振动没有大于 Y向振动，不记录这条线索。"
        if axial_evidence:
            item["unverified_reason"] += "虽有轴向偏心测量，但缺少 X>Y，不能把第38行标成完整条件。"
            item["evidence_refs"] = [_evidence_ref(axial_evidence)]
        return item

    if axial_evidence is None:
        item["partial_condition_met"] = True
        item["unverified_reason"] = (
            "同一条记录满足 X向振动大于 Y向振动。KB-02 第38行还写有轴向侧偏心，"
            "当前没有可核对的轴向偏心测量（轴向位移/轴向振动不能代替）。"
            "这是部分条件，不能确认转子不平衡。"
        )
        return item

    # 完整条件：官方 X>Y + 已登记轴向偏心测量。不发明偏心阈值；KB 未给出数值限。
    item["partial_condition_met"] = True
    item["complete_condition_met"] = True
    item["evidence_refs"] = [_evidence_ref(axial_evidence)]
    item["observed_fields"] = [
        *_field_list_with_axial(item["observed_fields"], axial_evidence),
    ]
    value = _axial_value(axial_evidence)
    unit = str(axial_evidence.get("unit") or "")
    evidence_id = str(axial_evidence.get("evidence_id") or "")
    item["unverified_reason"] = (
        f"同一官方记录满足 X>Y，并且已登记轴向偏心测量"
        f"{'' if value is None else f' {value} {unit}'.rstrip()}"
        f"（evidence_id={evidence_id}）。"
        "KB-02 第38行的 X>Y 与轴向侧偏心均已获得可核验观测；"
        "这只表示完整条件可核对，不是故障确诊，也不把 diagnose_fault 的 possible_match 当成确诊。"
    )
    return item


def _assess_temperature(item: dict, values: dict, temperature: dict) -> dict:
    """KB-02 第66行：轴承温度持续上升（>80°C）。

    KB 未给出“持续上升”的官方算法或采样规范，因此即使出现 >80°C 与自定义趋势，
    也只能标部分条件，不能标完整条件。
    """
    item["observed_fields"] = [_field_value(field, values) for field in _TEMP_FIELDS]
    item["method"] = _TREND_METHOD
    item["temperature_checks"] = temperature
    hotter = [
        field
        for field in _TEMP_FIELDS
        if _number(values.get(field)) is not None and _number(values.get(field)) > 80
    ]
    rising = [
        field
        for field in _TEMP_FIELDS
        if temperature.get(field, {}).get("strictly_increasing_into_current") is True
    ]
    if hotter and rising:
        item["partial_condition_met"] = True
        item["complete_condition_met"] = False
        item["unverified_reason"] = (
            "当前温度高于 80°C，并且自行定义的最近三点检查为严格上升。"
            "KB-02 写有“轴承温度持续上升（>80°C）”，但没有规定趋势算法、采样间隔或传感器位置，"
            "因此不能把完整条件标成已满足，也不能认定润滑故障。"
        )
        return item
    if hotter:
        item["unverified_reason"] = "温度单次超过 80°C，不能证明温度持续上升，也不能认定润滑故障。"
        return item
    if rising:
        item["unverified_reason"] = "自行定义的历史检查看到上升，但当前温度没有高于 80°C，不能满足官方条目。"
        return item
    item["unverified_reason"] = "当前轴承温度没有同时呈现高于 80°C 和可核对的持续上升。"
    return item


def _temperature_context(observation: dict, values: dict) -> dict:
    current_dt = _parse_time(str(observation.get("collected_at") or ""))
    context = {}
    for field in _TEMP_FIELDS:
        if current_dt is None:
            context[field] = {
                "history_value_count": 0,
                "conflict_count": 0,
                "compared_points": [],
                "strictly_increasing_into_current": False,
                "method": _TREND_METHOD,
                "note": "采集时间无法解析，不能排列历史温度。",
            }
            continue
        series, conflicts = _earlier_series(
            str(observation.get("device_id") or ""),
            current_dt,
            str(observation.get("record_id") or ""),
            field,
        )
        usable = series[-_TREND_POINTS:]
        current = _number(values.get(field))
        increasing = (
            current is not None
            and len(usable) == _TREND_POINTS
            and usable[0][1] < usable[1][1] < usable[2][1] < current
        )
        context[field] = {
            "history_value_count": len(series),
            "conflict_count": conflicts,
            "compared_points": [
                {"time": moment.strftime(_TIME_FORMAT), "value": number} for moment, number in usable
            ],
            "strictly_increasing_into_current": increasing,
            "method": _TREND_METHOD,
            "note": "同一时刻数值冲突的历史点已排除，没有参与趋势检查。",
        }
    return context


def _earlier_series(device_id: str, current_dt: datetime, current_id: str, field: str) -> tuple[list[tuple[datetime, float]], int]:
    end_text = current_dt.strftime(_TIME_FORMAT)
    grouped: dict[str, list[float]] = {}
    order: list[str] = []
    seen = set()
    start = None
    while True:
        page = get_device_records(device_id, start_time=start, end_time=end_text, limit=MAX_LIMIT)
        rows = page.get("records") or []
        if not rows:
            break
        reached_current = False
        for row in rows:
            record_id = row.get("record_id")
            if record_id in seen:
                continue
            seen.add(record_id)
            row_dt = _parse_time(str(row.get("collected_at") or ""))
            if row_dt is None or row_dt > current_dt or record_id == current_id or row_dt == current_dt:
                if row_dt == current_dt or record_id == current_id:
                    reached_current = True
                continue
            value = _number((row.get("values") or {}).get(field))
            if value is None:
                continue
            stamp = row_dt.strftime(_TIME_FORMAT)
            if stamp not in grouped:
                order.append(stamp)
                grouped[stamp] = []
            grouped[stamp].append(value)
        if not page.get("truncated") or reached_current:
            break
        next_start = rows[-1].get("collected_at")
        if not next_start or next_start == start:
            break
        start = next_start
    series = []
    conflicts = 0
    for stamp in order:
        values = grouped[stamp]
        if any(value != values[0] for value in values):
            conflicts += 1
            continue
        parsed = _parse_time(stamp)
        if parsed is not None:
            series.append((parsed, values[0]))
    return series, conflicts


def _observable_match_features(checks: list[dict], *, observation: dict | None = None) -> list[str]:
    """把可核验观测转成带知识库短语、单位和记录定位的特征描述，供 diagnose_fault 对照。

    - 完整条件：附带官方条款全文与特征代码，便于 matched_feature_codes 对齐。
    - 部分条件：只附已核验短语（如 X>Y / >80°C），明确标注缺少项，不把 partial 升为 complete。
    - 匹配结果仍是 possible_match，不是确诊。
    """
    record_id = str((observation or {}).get("record_id") or "").strip()
    features: list[str] = []
    for item in checks:
        if not isinstance(item, dict):
            continue
        complete = item.get("complete_condition_met") is True
        partial = item.get("partial_condition_met") is True
        if not complete and not partial:
            continue
        code = str(item.get("feature_code") or "").strip()
        official = str(item.get("official_condition") or "").strip()
        line_number = item.get("line_number")
        locator = f"KB-02 第{line_number}行" if isinstance(line_number, int) else "KB-02"
        field_bits = []
        for field in item.get("observed_fields") or []:
            if not isinstance(field, dict):
                continue
            name = str(field.get("field") or "").strip()
            value = field.get("value")
            unit = field.get("unit")
            if not name or value is None or value == "":
                continue
            unit_text = f" {unit}" if isinstance(unit, str) and unit.strip() else ""
            field_bits.append(f"{name}={value}{unit_text}".strip())
        if complete and official:
            text = (
                f"{code}：{official}；条件满足程度=完整；记录 {record_id}；"
                f"{'；'.join(field_bits)}；来源={DATASET_FILE.name}；定位={locator}"
            ).strip("；")
            _append_unique(features, official)
            _append_unique(features, code)
            _append_unique(features, text)
        elif code == "amplitude" and "X>Y" in official:
            text = (
                f"{code}：径向振动X>Y方向；条件满足程度=部分；缺少轴向侧偏心测量；"
                f"记录 {record_id}；{'；'.join(field_bits)}；来源={DATASET_FILE.name}；定位={locator}"
            )
            _append_unique(features, "径向振动X>Y方向")
            _append_unique(features, "X>Y")
            _append_unique(features, code)
            _append_unique(features, text)
        elif code == "temperature":
            text = (
                f"{code}：轴承温度；>80°C；条件满足程度=部分；"
                f"KB未给出持续上升官方算法故不能标完整；记录 {record_id}；"
                f"{'；'.join(field_bits)}；来源={DATASET_FILE.name}；定位={locator}"
            )
            _append_unique(features, "轴承温度")
            _append_unique(features, ">80°C")
            _append_unique(features, code)
            _append_unique(features, text)
        else:
            phrase = official or code
            text = (
                f"{code}：{phrase}；条件满足程度=部分；记录 {record_id}；"
                f"{'；'.join(field_bits)}；来源={DATASET_FILE.name}；定位={locator}"
            )
            if phrase:
                _append_unique(features, phrase)
            if code:
                _append_unique(features, code)
            _append_unique(features, text)
        if len(features) >= 20:
            break
    return features[:20]


def _append_unique(bucket: list[str], text: str) -> None:
    cleaned = " ".join(str(text or "").split())
    if cleaned and cleaned not in bucket and len(bucket) < 20:
        bucket.append(cleaned)


def _axial_eccentricity_evidence(record_id: str) -> dict | None:
    """读取该记录已登记、可计算的轴向偏心证据。轴向位移/振动不算。"""
    identity = record_id.strip() if isinstance(record_id, str) else ""
    if not identity:
        return None
    try:
        from app.services.diagnostic_evidence import database_path, list_evidence
    except Exception:
        return None
    try:
        if not database_path().is_file():
            return None
        rows = list_evidence(identity)
    except Exception:
        return None
    for item in rows:
        if not isinstance(item, dict) or item.get("evidence_kind") != "axial_eccentricity":
            continue
        status = str(item.get("validation_status") or "")
        if status not in {"可计算", "已获得可用证据", "已完成条款校验"}:
            continue
        values = item.get("values") if isinstance(item.get("values"), dict) else {}
        if _number(values.get("value")) is None:
            continue
        unit = str(item.get("unit") or "").strip()
        if unit not in {"um", "μm", "mm", "mil"}:
            continue
        return item
    return None


def _axial_value(evidence: dict) -> float | None:
    values = evidence.get("values") if isinstance(evidence.get("values"), dict) else {}
    return _number(values.get("value"))


def _evidence_ref(evidence: dict) -> dict:
    return {
        "evidence_id": evidence.get("evidence_id"),
        "evidence_kind": evidence.get("evidence_kind"),
        "quantity_name": evidence.get("quantity_name"),
        "unit": evidence.get("unit"),
        "value": _axial_value(evidence),
        "method": evidence.get("method"),
        "reference_basis": evidence.get("reference_basis"),
        "data_origin": evidence.get("data_origin"),
        "validation_status": evidence.get("validation_status"),
    }


def _field_list_with_axial(fields: list, evidence: dict) -> list:
    result = list(fields)
    result.append(
        {
            "field": "轴向偏心测量",
            "value": _axial_value(evidence),
            "unit": evidence.get("unit"),
            "evidence_id": evidence.get("evidence_id"),
            "evidence_kind": "axial_eccentricity",
        }
    )
    return result


def _supporting_evidence_public(axial_evidence: dict | None) -> list[dict]:
    if not axial_evidence:
        return []
    return [_evidence_ref(axial_evidence)]


def _missing_for_analysis(checks: list[dict], axial_evidence: dict | None) -> list[str]:
    missing = [item for item in _MISSING_DATA]
    amplitude_complete = any(
        isinstance(item, dict)
        and item.get("feature_code") == "amplitude"
        and item.get("complete_condition_met") is True
        for item in checks
    )
    if axial_evidence is not None or amplitude_complete:
        missing = [item for item in missing if item != "轴向偏心测量"]
    return missing


def _candidates(diagnosis: dict | None) -> list[dict]:
    if not isinstance(diagnosis, dict) or diagnosis.get("ok") is not True or diagnosis.get("status") != "possible_match":
        return []
    matches = diagnosis.get("matches")
    if not isinstance(matches, list):
        return []
    candidates = []
    for match in matches:
        if not isinstance(match, dict):
            continue
        # 工具层即便误带 confirmed=True 也按未确诊候选处理。
        fault = str(match.get("fault") or "").strip()
        features = match.get("matched_features")
        if not fault or not isinstance(features, list):
            continue
        codes = [str(feature.get("code")) for feature in features if isinstance(feature, dict) and feature.get("code")]
        candidates.append(
            {
                "fault": fault,
                "confirmed": False,
                "evidence_level": "possible_match",
                "matched_feature_codes": codes,
                "source": "diagnose_fault",
                "note": "只表示官方特征的可能匹配，不是确诊。",
            }
        )
    return candidates


def _level(anomaly_status: str, clues: list[dict], candidates: list[dict]) -> str:
    if candidates:
        return "possible_match"
    if clues:
        return "diagnostic_clue"
    if anomaly_status == "偏离基线":
        return "statistical_deviation"
    if anomaly_status in {"基线不足", "无法判定"}:
        return "unable_to_evaluate"
    return "insufficient_evidence"


def _reasons(detected: dict, checks: list[dict]) -> list[str]:
    reasons = [
        "Robust Z-score 超过实验性界限，只说明统计偏离，不能证明某种具体故障。",
    ]
    status = detected.get("detection_status")
    if status == "基线不足":
        reasons.append("历史基线不足，不能把缺少偏离写成设备正常。")
    elif status == "无法判定":
        reasons.append("统计检测无法判定，必须保留工具给出的原因。")
    elif status == "未发现显著偏离":
        reasons.append("未发现显著偏离只表示没有超过实验性统计界限，不是设备正常的确认。")
    reasons.extend(item["unverified_reason"] for item in checks if item.get("complete_condition_met") is not True)
    return reasons


def _warnings(detected: dict) -> list[str]:
    warnings = [
        "统计偏离和单条数值线索都不是故障确诊，也不生成概率、严重度或正式报警。",
    ]
    threshold = detected.get("experimental_threshold")
    if isinstance(threshold, dict) and threshold.get("robust_z_abs") is not None:
        warnings.append(
            f"本次统计检测使用的绝对 Robust Z-score 界限是 {threshold.get('robust_z_abs')}，这不是官方报警限值。"
        )
    for warning in detected.get("warnings") or []:
        if isinstance(warning, str) and warning not in warnings:
            warnings.append(warning)
    return warnings


def _field_value(field: str, values: dict) -> dict:
    return {"field": field, "value": values.get(field), "unit": _unit(field)}


def _unit(field: str) -> str | None:
    if field == "振动均方根":
        return None
    for suffix, unit in (("(mm/s)", "mm/s"), ("(°C)", "°C"), ("(g)", "g"), ("(Hz)", "Hz"), ("(A)", "A"), ("(rpm)", "rpm")):
        if field.endswith(suffix):
            return unit
    return None


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    return float(value)


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, _TIME_FORMAT)
    except ValueError:
        return None


def _scrub(value):
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items() if key not in _WITHHELD_FIELDS}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value
