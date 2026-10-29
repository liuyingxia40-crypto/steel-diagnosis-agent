"""用同一设备的历史观测做保守的统计偏离检查。

不读取答案标签，不把统计偏离写成故障、报警或处置建议。
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime

from app.services.official_dataset import MAX_LIMIT, get_device_records, get_observation

MIN_HISTORY = 30
ROBUST_Z_THRESHOLD = 3.5
CONSISTENCY_CONSTANT = 0.6745
SPEED_RELATIVE_SPAN = 0.10
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_STATUS_DEVIATED = "偏离基线"
_STATUS_STABLE = "未发现显著偏离"
_STATUS_SHORT = "基线不足"
_STATUS_UNKNOWN = "无法判定"
_DISCLAIMER = "这是统计异常判据，不是官方设备报警限值，也不能当作诊断结论。"
_SPEED_FIELD = "转速(rpm)"
_ROTATION_FIELD = "转频(Hz)"

_METRICS = (
    {"field": "X向振动(mm/s)", "unit": "mm/s", "independent": True, "speed_sensitive": True, "note": ""},
    {"field": "Y向振动(mm/s)", "unit": "mm/s", "independent": True, "speed_sensitive": True, "note": ""},
    {
        "field": "最大振动(mm/s)",
        "unit": "mm/s",
        "independent": False,
        "speed_sensitive": True,
        "note": "最大振动是 X、Y 向振动的较大值，不作为独立证据。",
    },
    {
        "field": "振动均方根",
        "unit": None,
        "independent": True,
        "speed_sensitive": True,
        "note": "表头未标注单位，本模块不补充单位，只与同一列的历史数值比较。",
    },
    {"field": "驱动端轴承温度(°C)", "unit": "°C", "independent": True, "speed_sensitive": False, "note": ""},
    {"field": "非驱动端轴承温度(°C)", "unit": "°C", "independent": True, "speed_sensitive": False, "note": ""},
    {"field": "电机电流(A)", "unit": "A", "independent": True, "speed_sensitive": True, "note": ""},
    {"field": "包络谱峰值(g)", "unit": "g", "independent": True, "speed_sensitive": True, "note": ""},
    {"field": "BPFO幅值(g)", "unit": "g", "independent": True, "speed_sensitive": True, "note": ""},
    {"field": "BPFI幅值(g)", "unit": "g", "independent": True, "speed_sensitive": True, "note": ""},
    {"field": "BSF幅值(g)", "unit": "g", "independent": True, "speed_sensitive": True, "note": ""},
    {"field": "啮合频率幅值(g)", "unit": "g", "independent": True, "speed_sensitive": True, "note": ""},
    {"field": "1X转频幅值(mm/s)", "unit": "mm/s", "independent": True, "speed_sensitive": True, "note": ""},
)
_EXCLUDED_CONSTANT_FIELDS = ("BPFO频率(Hz)", "BPFI频率(Hz)", "BSF频率(Hz)", "啮合频率(Hz)")


def detect_anomaly(
    record_id: str,
    min_history: int = MIN_HISTORY,
    z_threshold: float = ROBUST_Z_THRESHOLD,
) -> dict:
    """检查一条官方观测是否偏离该设备此前的历史基线。"""
    _check_settings(min_history, z_threshold)
    found = get_observation(record_id)
    if not found.get("found") or not isinstance(found.get("record"), dict):
        return _empty_result(
            str(record_id or "").strip(),
            None,
            None,
            _STATUS_UNKNOWN,
            ["没有找到该记录编号。"],
            min_history,
            z_threshold,
            official=True,
        )
    record = found["record"]
    current_dt = _parse_time(str(record.get("collected_at") or ""))
    if current_dt is None:
        return _empty_result(
            record.get("record_id"),
            record.get("device_id"),
            record.get("collected_at"),
            _STATUS_UNKNOWN,
            ["当前记录的采集时间无法解析，不能确定历史范围。"],
            min_history,
            z_threshold,
            official=True,
        )
    history, excluded_same_time = _earlier_records(record["device_id"], current_dt, record["record_id"])
    return _score(
        record_id=record["record_id"],
        device_id=record["device_id"],
        observation_time=record["collected_at"],
        current_values=record.get("values") or {},
        history_rows=history,
        same_timestamp_excluded_count=excluded_same_time,
        min_history=min_history,
        z_threshold=z_threshold,
        official=True,
    )


def detect_test_series(
    current_values: dict,
    history_rows: list[dict],
    min_history: int = MIN_HISTORY,
    z_threshold: float = ROBUST_Z_THRESHOLD,
) -> dict:
    """检查人工构造的算法测试序列。结果不是官方设备记录。"""
    _check_settings(min_history, z_threshold)
    rows = []
    for index, row in enumerate(history_rows):
        if isinstance(row, dict) and isinstance(row.get("values"), dict):
            values = row["values"]
            stamp = str(row.get("collected_at") or f"测试序号-{index:04d}")
        elif isinstance(row, dict):
            values = row
            stamp = f"测试序号-{index:04d}"
        else:
            raise ValueError("算法测试序列的每一行都必须是数值映射。")
        rows.append({"collected_at": stamp, "values": values})
    result = _score(
        record_id=None,
        device_id=None,
        observation_time=None,
        current_values=current_values if isinstance(current_values, dict) else {},
        history_rows=rows,
        same_timestamp_excluded_count=0,
        min_history=min_history,
        z_threshold=z_threshold,
        official=False,
    )
    result["data_source"] = "算法测试序列"
    result["official_record"] = False
    return result


def _score(
    record_id,
    device_id,
    observation_time,
    current_values: dict,
    history_rows: list[dict],
    same_timestamp_excluded_count: int,
    min_history: int,
    z_threshold: float,
    official: bool,
) -> dict:
    warnings = [
        _DISCLAIMER,
        "BPFO频率(Hz)、BPFI频率(Hz)、BSF频率(Hz)、啮合频率(Hz) 是工作表内的固定参数，没有参与偏离检测。",
    ]
    if same_timestamp_excluded_count:
        warnings.append(
            f"同一采集时间还有 {same_timestamp_excluded_count} 条记录，这些记录没有进入历史基线。"
        )
    speed_info = _operating_condition(current_values, history_rows, min_history, z_threshold)
    warnings.extend(speed_info["warnings"])
    analyzed = []
    anomalous = []
    for spec in _METRICS:
        item = _score_metric(spec, current_values, history_rows, min_history, z_threshold, speed_info)
        if item is None:
            continue
        analyzed.append(item)
        if spec["independent"] and item["deviated"] is True:
            anomalous.append(spec["field"])
    status = _overall_status(analyzed)
    times = [row["collected_at"] for row in history_rows if row.get("collected_at")]
    return {
        "record_id": record_id,
        "device_id": device_id,
        "observation_time": observation_time,
        "detection_status": status,
        "analyzed_metrics": analyzed,
        "history_sample_count": len(history_rows),
        "history_time_start": times[0] if times else None,
        "history_time_end": times[-1] if times else None,
        "same_timestamp_excluded_count": same_timestamp_excluded_count,
        "baseline_method": "median_mad_robust_z",
        "formula": "robust_z = 0.6745 * (当前值 - 历史中位数) / MAD；MAD = median(|历史值 - 历史中位数|)",
        "experimental_threshold": {
            "robust_z_abs": z_threshold,
            "min_history": min_history,
            "consistency_constant": CONSISTENCY_CONSTANT,
            "speed_relative_span": SPEED_RELATIVE_SPAN,
            "note": "以上均为实验性统计判据，不是官方设备报警限值。",
        },
        "anomalous_metrics": anomalous,
        "operating_condition": speed_info["public"],
        "warnings": warnings,
        "data_source": "official_dataset" if official else "算法测试序列",
        "official_record": official,
    }


def _score_metric(spec: dict, current_values: dict, history_rows: list[dict], min_history: int, z_threshold: float, speed_info: dict) -> dict:
    current = _finite(current_values.get(spec["field"]))
    baseline, conflict_count = _baseline_numbers(history_rows, spec["field"])
    if current is None and not baseline and conflict_count == 0:
        return None
    item = {
        "field": spec["field"],
        "unit": spec["unit"],
        "note": spec["note"],
        "counts_as_independent_evidence": spec["independent"],
        "current_value": current,
        "history_value_count": len(baseline),
        "history_conflict_timestamps": conflict_count,
        "median": None,
        "mad": None,
        "robust_z": None,
        "deviated": None,
        "metric_status": _STATUS_UNKNOWN,
        "reason": "",
    }
    if conflict_count:
        item["reason"] = f"有 {conflict_count} 个历史时刻的数值互相冲突，这些时刻没有进入基线。"
    if current is None:
        item["metric_status"] = _STATUS_UNKNOWN
        item["reason"] = _join_reason(item["reason"], "当前值为空或不是有限数值。")
        return item
    if len(baseline) < min_history:
        item["metric_status"] = _STATUS_SHORT
        item["reason"] = _join_reason(item["reason"], f"该指标可用历史值不足 {min_history} 个。")
        return item
    center = _median(baseline)
    mad = _median([abs(value - center) for value in baseline])
    item["median"] = center
    item["mad"] = mad
    if mad == 0:
        item["metric_status"] = _STATUS_UNKNOWN
        item["reason"] = _join_reason(item["reason"], "历史中位数绝对偏差为 0，不能计算 Robust Z-score。")
        return item
    if spec["speed_sensitive"] and not speed_info["comparable"]:
        item["robust_z"] = CONSISTENCY_CONSTANT * (current - center) / mad
        item["metric_status"] = _STATUS_UNKNOWN
        item["reason"] = _join_reason(item["reason"], speed_info["block_reason"])
        return item
    score = CONSISTENCY_CONSTANT * (current - center) / mad
    item["robust_z"] = score
    if abs(score) > z_threshold:
        item["deviated"] = True
        item["metric_status"] = _STATUS_DEVIATED
        item["reason"] = _join_reason(item["reason"], "绝对 Robust Z-score 超过实验性统计判据。")
        return item
    item["deviated"] = False
    item["metric_status"] = _STATUS_STABLE
    item["reason"] = _join_reason(item["reason"], "未超过实验性统计判据。")
    return item


def _operating_condition(current_values: dict, history_rows: list[dict], min_history: int, z_threshold: float) -> dict:
    current_speed = _finite(current_values.get(_SPEED_FIELD))
    current_rotation = _finite(current_values.get(_ROTATION_FIELD))
    baseline, conflicts = _baseline_numbers(history_rows, _SPEED_FIELD)
    public = {
        "field": _SPEED_FIELD,
        "unit": "rpm",
        "current_speed_rpm": current_speed,
        "current_rotation_frequency_hz": current_rotation,
        "rotation_frequency_note": "转频由转速换算，只作为工况信息，不作为独立证据。",
        "history_value_count": len(baseline),
        "history_conflict_timestamps": conflicts,
        "median": None,
        "mad": None,
        "minimum": min(baseline) if baseline else None,
        "maximum": max(baseline) if baseline else None,
        "relative_span": None,
        "robust_z": None,
        "comparable": False,
    }
    warnings = []
    block_reason = "没有足够的转速历史，不能确认振动和电流是否处于相近工况。"
    comparable = False
    if len(baseline) < min_history or current_speed is None:
        if current_speed is None:
            block_reason = "当前转速为空，不能确认振动和电流是否处于相近工况。"
        warnings.append(block_reason)
    else:
        center = _median(baseline)
        mad = _median([abs(value - center) for value in baseline])
        public["median"] = center
        public["mad"] = mad
        span = (max(baseline) - min(baseline)) / abs(center) if center else None
        public["relative_span"] = span
        if mad == 0:
            public["robust_z"] = None
            comparable = current_speed == center
            block_reason = "历史转速完全相同，但当前转速不同，振动和电流不能与该基线比较。"
        else:
            score = CONSISTENCY_CONSTANT * (current_speed - center) / mad
            public["robust_z"] = score
            comparable = abs(score) <= z_threshold
            block_reason = "当前转速偏离历史转速基线，振动和电流不能与该基线比较。"
        if span is not None and span > SPEED_RELATIVE_SPAN:
            comparable = False
            block_reason = "历史转速跨度较大，振动和电流的单一基线不可比。"
        if not comparable:
            warnings.append(block_reason + "该限制是实验性工况筛选，不是官方转速限值。")
    public["comparable"] = comparable
    return {"public": public, "comparable": comparable, "block_reason": block_reason, "warnings": warnings}


def _baseline_numbers(history_rows: list[dict], field: str) -> tuple[list[float], int]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for index, row in enumerate(history_rows):
        value = _finite((row.get("values") or {}).get(field))
        if value is None:
            continue
        grouped[str(row.get("collected_at") or index)].append(value)
    numbers = []
    conflicts = 0
    for values in grouped.values():
        if any(value != values[0] for value in values):
            conflicts += 1
            continue
        numbers.append(values[0])
    return numbers, conflicts


def _overall_status(analyzed: list[dict]) -> str:
    independent = [item for item in analyzed if item["counts_as_independent_evidence"]]
    statuses = {item["metric_status"] for item in independent}
    if _STATUS_DEVIATED in statuses:
        return _STATUS_DEVIATED
    if _STATUS_UNKNOWN in statuses:
        return _STATUS_UNKNOWN
    if _STATUS_STABLE in statuses:
        return _STATUS_STABLE
    return _STATUS_SHORT


def _earlier_records(device_id: str, current_dt: datetime, current_id: str) -> tuple[list[dict], int]:
    end_text = current_dt.strftime(_TIME_FORMAT)
    collected = []
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
            if row_dt is None or row_dt > current_dt:
                continue
            if row_dt == current_dt or record_id == current_id:
                reached_current = True
                continue
            collected.append(row)
        if not page.get("truncated") or reached_current:
            break
        next_start = rows[-1].get("collected_at")
        if not next_start or next_start == start:
            break
        start = next_start
    same_page = get_device_records(device_id, start_time=end_text, end_time=end_text, limit=MAX_LIMIT)
    excluded = sum(1 for row in same_page.get("records") or [] if row.get("record_id") != current_id)
    return collected, excluded


def _empty_result(record_id, device_id, observation_time, status: str, warnings: list[str], min_history: int, z_threshold: float, official: bool) -> dict:
    return {
        "record_id": record_id,
        "device_id": device_id,
        "observation_time": observation_time,
        "detection_status": status,
        "analyzed_metrics": [],
        "history_sample_count": 0,
        "history_time_start": None,
        "history_time_end": None,
        "same_timestamp_excluded_count": 0,
        "baseline_method": "median_mad_robust_z",
        "formula": "robust_z = 0.6745 * (当前值 - 历史中位数) / MAD；MAD = median(|历史值 - 历史中位数|)",
        "experimental_threshold": {
            "robust_z_abs": z_threshold,
            "min_history": min_history,
            "consistency_constant": CONSISTENCY_CONSTANT,
            "speed_relative_span": SPEED_RELATIVE_SPAN,
            "note": "以上均为实验性统计判据，不是官方设备报警限值。",
        },
        "anomalous_metrics": [],
        "operating_condition": None,
        "warnings": [_DISCLAIMER, *warnings],
        "data_source": "official_dataset" if official else "算法测试序列",
        "official_record": official,
    }


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, _TIME_FORMAT)
    except ValueError:
        return None


def _check_settings(min_history: int, z_threshold: float) -> None:
    if isinstance(min_history, bool) or not isinstance(min_history, int) or min_history < 1:
        raise ValueError("min_history 必须是大于 0 的整数。")
    if isinstance(z_threshold, bool) or not isinstance(z_threshold, (int, float)) or not math.isfinite(z_threshold) or z_threshold <= 0:
        raise ValueError("z_threshold 必须是大于 0 的有限数值。")


def _join_reason(prefix: str, sentence: str) -> str:
    if not prefix:
        return sentence
    if prefix.endswith("。"):
        return prefix + sentence
    return prefix + "。" + sentence
