"""离线审计官方开发集能否支持数值故障分类实验。

故障类型等结果列只在本进程内作为评估分组，不写入 official_dataset 缓存，
不调用 DeepSeek，不进入业务接口，也不作为输入特征。本脚本不训练模型。
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.official_dataset import (  # noqa: E402
    DATASET_FILE,
    IDENTITY_FIELDS,
    MEASUREMENT_FIELDS,
    _IGNORED_FIELDS,
    _WITHHELD_FIELDS,
)

EXPECTED_HASH = "1e9caeba1fe526f3ad1b5dd6445a9dbcc02fa22ec02273d9863ebc84e3e30f87"
EXPECTED_SHEETS = 6
EXPECTED_DEVICES = 46
EXPECTED_RECORDS = 24000
ENOUGH_RECORDS = 100
LIMITED_RECORDS = 30
ENOUGH_DEVICES = 2
ETA_NOTABLE = 0.05
ETA_WITHIN = 0.10
CONSTANT_SHORTCUT_RATIO = 0.90
RANDOM_SEED = 20261026
EMPTY_TOKEN = "（空）"

KB02_MD = DATASET_FILE.parents[1] / "processed" / "markdown" / "KB-02_故障模式诊断知识库.md"
OUT_DIR = DATASET_FILE.parents[1] / "processed" / "evaluation"
REPORT_PATH = OUT_DIR / "diagnosis_feasibility_report.md"
SUMMARY_PATH = OUT_DIR / "diagnosis_feasibility_summary.json"

_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_ROW = f"{{{_NS}}}row"
_CELL = f"{{{_NS}}}c"
_TEXT = f"{{{_NS}}}t"
_VALUE = f"{{{_NS}}}v"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_TIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
)
LABEL_FIELDS = tuple(sorted(_WITHHELD_FIELDS))
SPLIT_FIELD = "数据集划分"
DERIVED_FIELDS = ("最大振动(mm/s)",)
FREQUENCY_FIELDS = ("BPFO频率(Hz)", "BPFI频率(Hz)", "BSF频率(Hz)", "啮合频率(Hz)")
ANOMALY_EXCLUDED_NOTE = (
    "当前异常检测模块没有把 BPFO频率(Hz)、BPFI频率(Hz)、BSF频率(Hz)、啮合频率(Hz) 当作偏离指标，"
    "并把最大振动(mm/s)记为 X、Y 向振动较大值，不作为独立证据。"
)


class AuditError(Exception):
    pass


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def kb02_level2_headings() -> list[str]:
    if not KB02_MD.is_file():
        raise AuditError("未找到 KB-02 Markdown，无法对照故障名称。")
    names = []
    for line in KB02_MD.read_text(encoding="utf-8").splitlines():
        if line.startswith("## ") and not line.startswith("### "):
            names.append(line[3:].strip())
    if not names:
        raise AuditError("KB-02 Markdown 没有二级标题。")
    return names


def _column_index(cell_ref: str) -> int:
    letters = "".join(char for char in cell_ref if char.isalpha())
    number = 0
    for char in letters:
        number = number * 26 + ord(char.upper()) - 64
    return number - 1


def _cell_text(cell) -> str | None:
    cell_type = cell.get("t")
    if cell_type == "s":
        raise AuditError("官方 Excel 使用了共享字符串，审计读取已停止。")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(_TEXT))
    value = cell.find(_VALUE)
    if value is None or value.text is None:
        return None
    return value.text


def _parse_number(text: str) -> int | float | None:
    value = text.strip()
    if value.lower() in {"nan", "inf", "infinity", "+inf", "-inf", "-infinity", "+infinity"}:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    if value.lstrip("+-").isdigit():
        return int(value)
    return number


def _parse_time(value: str) -> datetime | None:
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _sheet_targets(workbook: ZipFile) -> list[tuple[str, str]]:
    workbook_xml = ElementTree.fromstring(workbook.read("xl/workbook.xml"))
    relations = ElementTree.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
    relation_map = {item.get("Id"): item.get("Target") for item in relations}
    sheets = []
    for sheet in workbook_xml.findall(f"{{{_NS}}}sheets/{{{_NS}}}sheet"):
        relation_id = sheet.get(f"{{{_REL_NS}}}id")
        target = relation_map.get(relation_id) or ""
        if target.startswith("/"):
            target = target[1:]
        elif not target.startswith("xl/"):
            target = "xl/" + target
        sheets.append((sheet.get("name") or "", target))
    return sheets


def _header_indexes(header: list[str]) -> dict[str, int]:
    expected = set(IDENTITY_FIELDS) | set(MEASUREMENT_FIELDS) | set(_WITHHELD_FIELDS) | set(_IGNORED_FIELDS)
    if len(header) != len(expected) or set(header) != expected:
        raise AuditError("官方 Excel 表头与已确认的 34 个字段不一致。")
    if len(header) != len(set(header)):
        raise AuditError("官方 Excel 表头存在重复字段。")
    return {name: index for index, name in enumerate(header)}


def load_rows() -> tuple[list[dict], list[str]]:
    """读取观测值和评估标签。标签只存在于返回的进程内对象。"""
    if not DATASET_FILE.is_file():
        raise AuditError("未找到官方开发集 Excel。")
    rows: list[dict] = []
    with ZipFile(DATASET_FILE) as workbook:
        sheets = _sheet_targets(workbook)
        for sheet_name, target in sheets:
            header = None
            indexes = None
            with workbook.open(target) as handle:
                for _, element in ElementTree.iterparse(handle, events=("end",)):
                    if element.tag != _ROW:
                        continue
                    cells = {}
                    for cell in element.findall(_CELL):
                        cells[_column_index(cell.get("r") or "A1")] = _cell_text(cell)
                    row_number = int(element.get("r") or 0)
                    element.clear()
                    if header is None:
                        width = max(cells) + 1 if cells else 0
                        header = [str(cells.get(index) or "").strip() for index in range(width)]
                        indexes = _header_indexes(header)
                        continue
                    assert indexes is not None
                    record_id = str(cells.get(indexes["记录编号"]) or "").strip()
                    device_id = str(cells.get(indexes["设备编号"]) or "").strip()
                    category = str(cells.get(indexes["设备类别"]) or "").strip()
                    collected_raw = str(cells.get(indexes["采集时间"]) or "").strip()
                    if not record_id or not device_id or not category or not collected_raw:
                        raise AuditError(f"{sheet_name} 第 {row_number} 行缺少身份字段。")
                    values = {}
                    for name in MEASUREMENT_FIELDS:
                        raw = cells.get(indexes[name])
                        values[name] = None if raw is None or str(raw).strip() == "" else _parse_number(str(raw))
                    labels = {}
                    for name in _WITHHELD_FIELDS:
                        labels[name] = str(cells.get(indexes[name]) or "").strip()
                    raw_fault = labels["故障类型"]
                    rows.append(
                        {
                            "sheet_name": sheet_name,
                            "excel_row": row_number,
                            "record_id": record_id,
                            "device_id": device_id,
                            "equipment_category": category,
                            "collected_at": collected_raw,
                            "collected_at_dt": _parse_time(collected_raw),
                            "values": values,
                            "labels": labels,
                            "split_value": str(cells.get(indexes[SPLIT_FIELD]) or "").strip(),
                            "fault_name": raw_fault if raw_fault else EMPTY_TOKEN,
                        }
                    )
            if header is None:
                raise AuditError(f"{sheet_name} 没有表头。")
    if len({row["record_id"] for row in rows}) != len(rows):
        raise AuditError("官方开发集存在重复的记录编号。")
    for device_id, items in _group(rows, "device_id").items():
        if len({item["sheet_name"] for item in items}) != 1 or len({item["equipment_category"] for item in items}) != 1:
            raise AuditError(f"设备编号「{device_id}」对应了多个工作表或设备类别。")
    return rows, [name for name, _target in sheets]


def _group(rows: list[dict], key: str) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row[key]].append(row)
    return grouped


def _round(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    return round(float(value), 6)


def _quartiles(values: list[float]) -> tuple[float | None, float | None, float | None]:
    if not values:
        return None, None, None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0], ordered[0], ordered[0]
    q1, _q2, q3 = statistics.quantiles(ordered, n=4, method="inclusive")
    return q1, statistics.median(ordered), q3


def _eta_squared(groups: list[list[float]]) -> float | None:
    values = [value for group in groups for value in group]
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    ss_total = sum((value - mean) ** 2 for value in values)
    if ss_total == 0:
        return 0.0
    ss_between = 0.0
    for group in groups:
        if not group:
            continue
        group_mean = sum(group) / len(group)
        ss_between += len(group) * (group_mean - mean) ** 2
    return ss_between / ss_total


def _time_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _sorted_counts(counter: Counter) -> dict[str, int]:
    return {name: counter[name] for name in sorted(counter, key=lambda item: (-counter[item], item))}


def _fault_counter(rows: list[dict]) -> Counter:
    return Counter(row["fault_name"] for row in rows)


def _device_fault_sets(rows: list[dict]) -> dict[str, set[str]]:
    found: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        found[row["fault_name"]].add(row["device_id"])
    return found


def _duplicate_stats(rows: list[dict]) -> tuple[dict, set[tuple[str, str]]]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[(row["device_id"], row["collected_at"])].append(row)
    duplicate_keys = {key for key, items in grouped.items() if len(items) > 1}
    measurement_conflict = 0
    fault_conflict = 0
    fault_conflict_records = 0
    other_label_conflict = 0
    identical = 0
    for key in duplicate_keys:
        items = grouped[key]
        measurement_conflict_here = False
        for field in MEASUREMENT_FIELDS:
            distinct = {None if item["values"][field] is None else float(item["values"][field]) for item in items}
            if len(distinct) > 1:
                measurement_conflict_here = True
                break
        if measurement_conflict_here:
            measurement_conflict += 1
        else:
            identical += 1
        if len({item["fault_name"] for item in items}) > 1:
            fault_conflict += 1
            fault_conflict_records += len(items)
        for field in _WITHHELD_FIELDS:
            if field == "故障类型":
                continue
            if len({item["labels"][field] for item in items}) > 1:
                other_label_conflict += 1
                break
    timestamp_devices: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        timestamp_devices[row["collected_at"]].add(row["device_id"])
    per_device_groups = Counter()
    for device_id, collected_at in duplicate_keys:
        per_device_groups[device_id] += 1
    stats = {
        "same_device_same_time_groups": len(duplicate_keys),
        "records_in_those_groups": sum(len(grouped[key]) for key in duplicate_keys),
        "groups_with_measurement_conflict": measurement_conflict,
        "groups_with_identical_measurements": identical,
        "groups_with_fault_type_conflict": fault_conflict,
        "records_in_fault_type_conflict_groups": fault_conflict_records,
        "groups_with_other_label_conflict": other_label_conflict,
        "timestamps_shared_by_multiple_devices": sum(1 for devices in timestamp_devices.values() if len(devices) > 1),
        "duplicate_groups_by_device": _sorted_counts(per_device_groups),
    }
    return stats, duplicate_keys


def _label_field_stats(rows: list[dict]) -> list[dict]:
    results = []
    quoted_ok = {"设备状态", "故障严重度", "故障位置", "优先级", "影响程度", "诊断置信度"}
    never_quote = {"频谱描述", "根因分析", "建议措施", "故障类型"}
    for field in LABEL_FIELDS:
        value_faults: dict[str, Counter] = defaultdict(Counter)
        contains = 0
        empty = 0
        for row in rows:
            text = row["labels"][field]
            raw_fault = row["labels"]["故障类型"]
            if not text:
                empty += 1
            value_faults[text][row["fault_name"]] += 1
            if raw_fault and raw_fault in text:
                contains += 1
        unique_non_empty = [value for value in value_faults if value]
        multi = sum(1 for value in unique_non_empty if len(value_faults[value]) > 1)
        single_records = sum(
            sum(counter.values()) for value, counter in value_faults.items() if value and len(counter) == 1
        )
        non_empty = len(rows) - empty
        item = {
            "field": field,
            "non_empty": non_empty,
            "empty": empty,
            "unique_non_empty_values": len(unique_non_empty),
            "values_with_multiple_fault_types": multi,
            "single_fault_value_record_ratio": _round(single_records / non_empty) if non_empty else None,
            "records_whose_text_contains_its_fault_name": contains,
            "determines_fault_type_when_present": bool(non_empty) and multi == 0 and empty == 0,
        }
        if field in quoted_ok and field not in never_quote and len(unique_non_empty) <= 12:
            item["value_counts"] = _sorted_counts(Counter({value: sum(value_faults[value].values()) for value in unique_non_empty}))
        results.append(item)
    return results


def _feature_stats(rows: list[dict], categories: list[str]) -> tuple[list[dict], dict]:
    features = []
    speed_diffs = []
    vibration_diffs = []
    for row in rows:
        speed = row["values"]["转速(rpm)"]
        frequency = row["values"]["转频(Hz)"]
        if speed is not None and frequency is not None:
            speed_diffs.append(abs(float(frequency) - float(speed) / 60.0))
        x_value = row["values"]["X向振动(mm/s)"]
        y_value = row["values"]["Y向振动(mm/s)"]
        max_value = row["values"]["最大振动(mm/s)"]
        if x_value is not None and y_value is not None and max_value is not None:
            vibration_diffs.append(abs(float(max_value) - max(float(x_value), float(y_value))))
    redundancy = {
        "speed_hz_pairs": len(speed_diffs),
        "speed_hz_max_abs_diff": _round(max(speed_diffs) if speed_diffs else None),
        "max_vibration_pairs": len(vibration_diffs),
        "max_vibration_max_abs_diff": _round(max(vibration_diffs) if vibration_diffs else None),
    }
    fault_names = list(_sorted_counts(_fault_counter(rows)))
    for field in MEASUREMENT_FIELDS:
        by_fault: dict[str, list[float]] = defaultdict(list)
        by_category: dict[str, list[float]] = defaultdict(list)
        by_device: dict[str, list[float]] = defaultdict(list)
        within: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        missing = 0
        for row in rows:
            value = row["values"][field]
            if value is None:
                missing += 1
                continue
            number = float(value)
            by_fault[row["fault_name"]].append(number)
            by_category[row["equipment_category"]].append(number)
            by_device[row["device_id"]].append(number)
            within[row["equipment_category"]][row["fault_name"]].append(number)
        present = [value for values in by_fault.values() for value in values]
        q1, median, q3 = _quartiles(present)
        constant_devices = sum(1 for values in by_device.values() if values and len({round(value, 8) for value in values}) == 1)
        eta_within = {}
        for category in categories:
            groups = [values for values in within[category].values() if values]
            eta_within[category] = _round(_eta_squared(groups)) if len(groups) >= 2 else None
        median_by_fault = {}
        quartile_by_fault = {}
        for name in fault_names:
            values = by_fault.get(name, [])
            fq1, fmedian, fq3 = _quartiles(values)
            median_by_fault[name] = _round(fmedian)
            quartile_by_fault[name] = {
                "count": len(values),
                "p25": _round(fq1),
                "median": _round(fmedian),
                "p75": _round(fq3),
            }
        item = {
            "field": field,
            "non_null": len(present),
            "missing_count": missing,
            "missing_ratio": _round(missing / len(rows)),
            "minimum": _round(min(present) if present else None),
            "maximum": _round(max(present) if present else None),
            "p25": _round(q1),
            "median": _round(median),
            "p75": _round(q3),
            "eta_squared_fault_type": _round(_eta_squared(list(by_fault.values()))),
            "eta_squared_equipment_category": _round(_eta_squared(list(by_category.values()))),
            "eta_squared_device": _round(_eta_squared(list(by_device.values()))),
            "devices_with_any_value": len(by_device),
            "devices_with_constant_values": constant_devices,
            "median_by_fault_type": median_by_fault,
            "quartile_by_fault_type": quartile_by_fault,
            "median_by_category": {category: _round(statistics.median(by_category[category])) if by_category[category] else None for category in categories},
            "eta_squared_fault_type_within_category": eta_within,
        }
        item["distribution_note"] = _feature_note(item, len({row["device_id"] for row in rows}))
        features.append(item)
    return features, redundancy


def _feature_note(item: dict, device_count: int) -> str:
    notes = []
    if item["field"] in DERIVED_FIELDS:
        notes.append("该列按现有异常检测定义不是独立测点。")
    if item["field"] in FREQUENCY_FIELDS:
        notes.append("该列属于异常检测模块排除的频率列。")
    constant_ratio = item["devices_with_constant_values"] / item["devices_with_any_value"] if item["devices_with_any_value"] else 0
    eta_fault = item["eta_squared_fault_type"] or 0
    eta_device = item["eta_squared_device"] or 0
    eta_category = item["eta_squared_equipment_category"] or 0
    within_hits = [name for name, value in item["eta_squared_fault_type_within_category"].items() if value is not None and value >= ETA_WITHIN]
    category_dominates = eta_category >= 0.95 and not within_hits
    if constant_ratio >= CONSTANT_SHORTCUT_RATIO and eta_category >= 0.999:
        notes.append("在每台设备内部取值不变，而且设备类别几乎解释了全部差异。它携带的是设备类别，不是设备内部的故障变化。")
    elif constant_ratio >= CONSTANT_SHORTCUT_RATIO:
        notes.append("在至少九成有数值的设备内部取值不变。")
    if category_dominates:
        notes.append("全局差异主要由设备类别贡献，同一设备类别内部没有达到门槛的故障类型分离。")
    elif eta_fault >= ETA_NOTABLE:
        notes.append("不同故障类型的组间均值差异达到本次描述门槛。")
    if eta_category >= ETA_NOTABLE and not category_dominates:
        notes.append("不同设备类别之间也有均值差异。")
    if eta_device >= eta_fault and eta_device >= ETA_NOTABLE and not category_dominates:
        notes.append("设备之间的差异不小于故障类型之间的差异。")
    if within_hits:
        notes.append("在部分设备类别内部，故障类型组间差异仍然存在：" + "、".join(within_hits) + "。")
    if not notes:
        notes.append("未见达到本次描述门槛的组间均值分离。")
    notes.append("组间分布差异不是故障诊断结论。")
    if device_count <= 0:
        notes.append("没有设备。")
    return "".join(notes)


def _candidate_lists(features: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    differences = []
    shortcuts = []
    category_dominated = []
    for item in features:
        constant_ratio = item["devices_with_constant_values"] / item["devices_with_any_value"] if item["devices_with_any_value"] else 0
        within_hits = [
            value for value in item["eta_squared_fault_type_within_category"].values() if value is not None and value >= ETA_WITHIN
        ]
        category_dominates = (item["eta_squared_equipment_category"] or 0) >= 0.95 and not within_hits
        if constant_ratio >= CONSTANT_SHORTCUT_RATIO:
            shortcuts.append({"field": item["field"], "reason": item["distribution_note"]})
        elif category_dominates:
            category_dominated.append({"field": item["field"], "reason": item["distribution_note"]})
        elif within_hits and item["missing_ratio"] <= 0.2:
            differences.append({"field": item["field"], "reason": item["distribution_note"]})
    return differences, shortcuts, category_dominated


def _fingerprint(rows: list[dict], features: list[dict]) -> dict:
    constant_fields = []
    for item in features:
        if item["devices_with_any_value"] and item["devices_with_constant_values"] == item["devices_with_any_value"]:
            constant_fields.append(item["field"])
    tuples: dict[str, tuple] = {}
    for device_id, items in _group(rows, "device_id").items():
        signature = []
        for field in constant_fields:
            values = {None if item["values"][field] is None else round(float(item["values"][field]), 8) for item in items}
            values.discard(None)
            signature.append(next(iter(values)) if len(values) == 1 else None)
        tuples[device_id] = tuple(signature)
    counts = Counter(tuples.values())
    unique_devices = sum(1 for signature in tuples.values() if counts[signature] == 1)
    return {
        "fields_constant_on_every_device_with_values": constant_fields,
        "devices_checked": len(tuples),
        "devices_with_unique_constant_signature": unique_devices,
        "distinct_constant_signatures": len(counts),
    }


def _split_column_stats(rows: list[dict]) -> dict:
    values = Counter(row["split_value"] if row["split_value"] else EMPTY_TOKEN for row in rows)
    device_values: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        device_values[row["device_id"]].add(row["split_value"] if row["split_value"] else EMPTY_TOKEN)
    per_value = []
    for name, count in _sorted_counts(values).items():
        subset = [row for row in rows if (row["split_value"] if row["split_value"] else EMPTY_TOKEN) == name]
        times = [row["collected_at_dt"] for row in subset if row["collected_at_dt"] is not None]
        per_value.append(
            {
                "value": name,
                "count": count,
                "device_count": len({row["device_id"] for row in subset}),
                "time_start": _time_text(min(times) if times else None),
                "time_end": _time_text(max(times) if times else None),
                "fault_counts": _sorted_counts(_fault_counter(subset)),
            }
        )
    return {
        "field": SPLIT_FIELD,
        "used_as_input_feature": False,
        "value_counts": _sorted_counts(values),
        "devices_in_multiple_values": sum(1 for item in device_values.values() if len(item) > 1),
        "per_value": per_value,
    }


def _side_summary(train: list[dict], test: list[dict], duplicate_keys: set[tuple[str, str]]) -> dict:
    train_devices = {row["device_id"] for row in train}
    test_devices = {row["device_id"] for row in test}
    train_keys = {(row["device_id"], row["collected_at"]) for row in train}
    test_keys = {(row["device_id"], row["collected_at"]) for row in test}
    latest_train: dict[str, datetime] = {}
    for row in train:
        if row["collected_at_dt"] is None:
            continue
        current = latest_train.get(row["device_id"])
        if current is None or row["collected_at_dt"] > current:
            latest_train[row["device_id"]] = row["collected_at_dt"]
    future_leaks = 0
    for row in test:
        latest = latest_train.get(row["device_id"])
        if row["collected_at_dt"] is not None and latest is not None and latest > row["collected_at_dt"]:
            future_leaks += 1
    names = sorted(set(_fault_counter(train)) | set(_fault_counter(test)))
    fault_rows = []
    for name in names:
        train_subset = [row for row in train if row["fault_name"] == name]
        test_subset = [row for row in test if row["fault_name"] == name]
        fault_rows.append(
            {
                "name": name,
                "train_records": len(train_subset),
                "test_records": len(test_subset),
                "train_devices": len({row["device_id"] for row in train_subset}),
                "test_devices": len({row["device_id"] for row in test_subset}),
            }
        )
    return {
        "train_records": len(train),
        "test_records": len(test),
        "train_devices": len(train_devices),
        "test_devices": len(test_devices),
        "devices_in_both": len(train_devices & test_devices),
        "devices_train_only": len(train_devices - test_devices),
        "devices_test_only": len(test_devices - train_devices),
        "same_time_groups_crossing_split": len(duplicate_keys & train_keys & test_keys),
        "test_records_with_later_same_device_record_in_train": future_leaks,
        "faults_missing_from_train": [item["name"] for item in fault_rows if item["train_records"] == 0],
        "faults_missing_from_test": [item["name"] for item in fault_rows if item["test_records"] == 0],
        "faults_with_fewer_than_30_test_records": [item["name"] for item in fault_rows if item["test_records"] < LIMITED_RECORDS],
        "faults_with_fewer_than_2_test_devices": [item["name"] for item in fault_rows if item["test_devices"] < ENOUGH_DEVICES],
        "fault_summary": fault_rows,
    }


def _split_stats(rows: list[dict], duplicate_keys: set[tuple[str, str]], sheet_order: list[str]) -> dict:
    rng = random.Random(RANDOM_SEED)
    order = list(range(len(rows)))
    rng.shuffle(order)
    cut = int(len(rows) * 0.8)
    train = [rows[index] for index in order[:cut]]
    test = [rows[index] for index in order[cut:]]
    random_split = _side_summary(train, test, duplicate_keys)
    random_split["seed"] = RANDOM_SEED
    random_split["rule"] = "按记录随机划分，约 80% 训练、20% 测试。这是一次固定种子的审计模拟，不是训练。"

    timed = sorted(
        rows,
        key=lambda row: (
            row["collected_at_dt"] is None,
            row["collected_at_dt"] or datetime.max,
            row["excel_row"],
            row["record_id"],
        ),
    )
    cutoff = timed[int(len(timed) * 0.8)]["collected_at_dt"]
    early = []
    late = []
    boundary = []
    for row in timed:
        if row["collected_at_dt"] is None or cutoff is None or row["collected_at_dt"] == cutoff:
            boundary.append(row)
        elif row["collected_at_dt"] < cutoff:
            early.append(row)
        else:
            late.append(row)
    global_time = _side_summary(early + boundary, late, duplicate_keys)
    global_time["rule"] = "按采集时间排序，切点时刻及更早的记录归入训练侧，更晚的记录归入测试侧。切点上的同时间记录不拆开。"
    global_time["cutoff"] = _time_text(cutoff)
    global_time["boundary_records_assigned_to_train"] = len(boundary)

    device_train: list[dict] = []
    device_test: list[dict] = []
    devices_not_split = 0
    for _device_id, items in _group(rows, "device_id").items():
        ordered_times = sorted({item["collected_at_dt"] for item in items if item["collected_at_dt"] is not None})
        if len(ordered_times) < 2:
            devices_not_split += 1
            device_train.extend(items)
            continue
        n_test = min(max(1, round(len(ordered_times) * 0.2)), len(ordered_times) - 1)
        test_times = set(ordered_times[-n_test:])
        for item in items:
            if item["collected_at_dt"] in test_times:
                device_test.append(item)
            else:
                device_train.append(item)
    within = _side_summary(device_train, device_test, duplicate_keys)
    within["rule"] = "每台设备按时间留出最后约 20% 的不同采集时刻；同一时刻的重复记录留在同一侧。时刻少于 2 个的设备不切分，全部留在训练侧。"
    within["devices_not_split"] = devices_not_split

    category_of_sheet = {}
    for row in rows:
        category_of_sheet.setdefault(row["sheet_name"], row["equipment_category"])
    held_ids = []
    for sheet_name in sheet_order:
        category = category_of_sheet[sheet_name]
        device_ids = sorted({row["device_id"] for row in rows if row["sheet_name"] == sheet_name})
        hold_count = max(1, round(len(device_ids) * 0.2))
        held_ids.extend(device_ids[-hold_count:])
    held = set(held_ids)
    hold_train = [row for row in rows if row["device_id"] not in held]
    hold_test = [row for row in rows if row["device_id"] in held]
    holdout = _side_summary(hold_train, hold_test, duplicate_keys)
    holdout["rule"] = "每个设备类别按设备编号排序，留出末尾 max(1, round(20%)) 台完整设备作为测试侧。"
    holdout["held_out_devices"] = [
        {
            "device_id": device_id,
            "equipment_category": category_of_sheet[_group(rows, "device_id")[device_id][0]["sheet_name"]],
            "record_count": len(_group(rows, "device_id")[device_id]),
            "fault_counts": _sorted_counts(_fault_counter(_group(rows, "device_id")[device_id])),
        }
        for device_id in held_ids
    ]
    return {
        "random_record_80_20": random_split,
        "global_time_80_20": global_time,
        "within_device_time_80_20": within,
        "device_holdout_by_category": holdout,
    }


def _sheet_rows(rows: list[dict], sheet_order: list[str], duplicate_groups_by_device: dict[str, int]) -> list[dict]:
    sheets = []
    for sheet_name in sheet_order:
        subset = [row for row in rows if row["sheet_name"] == sheet_name]
        categories = {row["equipment_category"] for row in subset}
        category = next(iter(categories)) if len(categories) == 1 else "、".join(sorted(categories))
        times = [row["collected_at_dt"] for row in subset if row["collected_at_dt"] is not None]
        device_ids = {row["device_id"] for row in subset}
        sheets.append(
            {
                "sheet_name": sheet_name,
                "equipment_category": category,
                "sheet_name_matches_category": category == sheet_name,
                "device_count": len(device_ids),
                "record_count": len(subset),
                "time_start": _time_text(min(times) if times else None),
                "time_end": _time_text(max(times) if times else None),
                "duplicate_timestamp_groups": sum(duplicate_groups_by_device.get(device_id, 0) for device_id in device_ids),
            }
        )
    return sheets


def _device_rows(rows: list[dict], sheet_order: list[str], duplicate_groups_by_device: dict[str, int]) -> list[dict]:
    sheet_rank = {name: index for index, name in enumerate(sheet_order)}
    devices = []
    for device_id, items in _group(rows, "device_id").items():
        times = [item["collected_at_dt"] for item in items if item["collected_at_dt"] is not None]
        ordered = sorted(items, key=lambda item: (item["collected_at_dt"] is None, item["collected_at_dt"] or datetime.max, item["excel_row"]))
        switches = 0
        previous = None
        for item in ordered:
            if previous is not None and item["fault_name"] != previous:
                switches += 1
            previous = item["fault_name"]
        devices.append(
            {
                "device_id": device_id,
                "equipment_category": items[0]["equipment_category"],
                "sheet_name": items[0]["sheet_name"],
                "record_count": len(items),
                "time_start": _time_text(min(times) if times else None),
                "time_end": _time_text(max(times) if times else None),
                "duplicate_timestamp_groups": duplicate_groups_by_device.get(device_id, 0),
                "fault_counts": _sorted_counts(_fault_counter(items)),
                "time_ordered_fault_label_changes": switches,
                "_sheet_rank": sheet_rank[items[0]["sheet_name"]],
            }
        )
    devices.sort(key=lambda item: (item["_sheet_rank"], item["device_id"]))
    for item in devices:
        del item["_sheet_rank"]
    return devices


def _range_overlap(ranges: dict[str, dict]) -> bool:
    items = [(name, values["start_dt"], values["end_dt"]) for name, values in ranges.items() if values["start_dt"] and values["end_dt"]]
    for index, (_name, start, end) in enumerate(items):
        for other_name, other_start, other_end in items[index + 1 :]:
            if start <= other_end and other_start <= end:
                return True
            _ = other_name
    return False


def build_summary(rows: list[dict], sheet_order: list[str]) -> dict:
    duplicate_stats, duplicate_keys = _duplicate_stats(rows)
    categories = []
    for sheet_name in sheet_order:
        category = next(row["equipment_category"] for row in rows if row["sheet_name"] == sheet_name)
        if category not in categories:
            categories.append(category)
    devices = _device_rows(rows, sheet_order, duplicate_stats["duplicate_groups_by_device"])
    fault_by_category = {category: _sorted_counts(_fault_counter([row for row in rows if row["equipment_category"] == category])) for category in categories}
    fault_device_sets = _device_fault_sets(rows)
    fault_types = []
    for name, count in _sorted_counts(_fault_counter(rows)).items():
        fault_types.append(
            {
                "name": name,
                "count": count,
                "device_count": len(fault_device_sets[name]),
                "category_counts": {category: fault_by_category[category].get(name, 0) for category in categories if fault_by_category[category].get(name, 0)},
            }
        )
    kb_names = kb02_level2_headings()
    excel_names = [item["name"] for item in fault_types]
    status_counter: Counter = Counter()
    status_by_fault: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        status = row["labels"]["设备状态"] if row["labels"]["设备状态"] else EMPTY_TOKEN
        status_counter[status] += 1
        status_by_fault[status][row["fault_name"]] += 1
    features, redundancy = _feature_stats(rows, categories)
    differences, constant_shortcuts, category_dominated = _candidate_lists(features)
    fingerprint = _fingerprint(rows, features)
    fault_ranges = {}
    range_dt = {}
    for name in excel_names:
        times = [row["collected_at_dt"] for row in rows if row["fault_name"] == name and row["collected_at_dt"] is not None]
        start = min(times) if times else None
        end = max(times) if times else None
        fault_ranges[name] = {"time_start": _time_text(start), "time_end": _time_text(end)}
        range_dt[name] = {"start_dt": start, "end_dt": end}
    label_changes = sum(item["time_ordered_fault_label_changes"] for item in devices)
    summary = {
        "audit": "official_dataset_diagnosis_feasibility",
        "model_trained": False,
        "deepseek_called": False,
        "labels_used_as_input_features": False,
        "source": {
            "file_name": DATASET_FILE.name,
            "sha256": file_sha256(DATASET_FILE),
            "byte_size": DATASET_FILE.stat().st_size,
            "sheet_count": len(sheet_order),
            "device_count": len(devices),
            "record_count": len(rows),
            "unparsed_time_records": sum(1 for row in rows if row["collected_at_dt"] is None),
            "sheets": _sheet_rows(rows, sheet_order, duplicate_stats["duplicate_groups_by_device"]),
        },
        "input_features": list(MEASUREMENT_FIELDS),
        "grouping_fields_not_features": list(IDENTITY_FIELDS) + ["工作表"],
        "excluded_label_fields": list(LABEL_FIELDS),
        "excluded_other_fields": [SPLIT_FIELD],
        "thresholds": {
            "enough_records": ENOUGH_RECORDS,
            "limited_records": LIMITED_RECORDS,
            "enough_devices": ENOUGH_DEVICES,
            "eta_squared_notable": ETA_NOTABLE,
            "eta_squared_within_category": ETA_WITHIN,
            "constant_shortcut_ratio": CONSTANT_SHORTCUT_RATIO,
            "meaning": "这些数字只用于描述样本量和分布差异，不是官方诊断标准，也不是模型准确率。",
        },
        "fault_types": fault_types,
        "fault_by_category": fault_by_category,
        "fault_by_device": devices,
        "kb02_level2_headings": kb_names,
        "kb02_exact_match_counts": {name: next((item["count"] for item in fault_types if item["name"] == name), 0) for name in kb_names},
        "fault_names_not_in_kb02_headings": [name for name in excel_names if name not in kb_names],
        "kb02_headings_absent_from_excel": [name for name in kb_names if name not in excel_names],
        "status_values": _sorted_counts(status_counter),
        "status_by_fault": {status: _sorted_counts(counter) for status, counter in status_by_fault.items()},
        "label_fields": _label_field_stats(rows),
        "split_column": _split_column_stats(rows),
        "features": features,
        "redundancy": redundancy,
        "duplicates": {
            **{key: value for key, value in duplicate_stats.items() if key != "duplicate_groups_by_device"},
            "devices_with_one_fault_type": sum(1 for item in devices if len(item["fault_counts"]) == 1),
            "devices_with_more_than_one_fault_type": sum(1 for item in devices if len(item["fault_counts"]) > 1),
            "time_ordered_fault_label_changes": label_changes,
        },
        "shortcuts": {
            "devices_with_one_fault_type": sum(1 for item in devices if len(item["fault_counts"]) == 1),
            "devices_with_more_than_one_fault_type": sum(1 for item in devices if len(item["fault_counts"]) > 1),
            "constant_signature": fingerprint,
            "fault_time_ranges": fault_ranges,
            "fault_time_ranges_overlap": _range_overlap(range_dt),
        },
        "possible_distribution_differences": differences,
        "category_dominated_fields": category_dominated,
        "device_constant_shortcuts": constant_shortcuts,
        "splits": _split_stats(rows, duplicate_keys, sheet_order),
        "anomaly_metric_note": ANOMALY_EXCLUDED_NOTE,
    }
    summary["class_assessment"] = _class_assessment(summary)
    summary["conclusions"] = _conclusions(summary)
    return summary


def _class_assessment(summary: dict) -> list[dict]:
    rows = []
    for item in summary["fault_types"]:
        rows.append(_assess_class(item["name"], item["count"], item["device_count"], item["name"] in summary["kb02_level2_headings"]))
    for name in summary["kb02_headings_absent_from_excel"]:
        rows.append(_assess_class(name, 0, 0, True))
    return rows


def _assess_class(name: str, count: int, device_count: int, kb_heading: bool) -> dict:
    if count >= ENOUGH_RECORDS:
        record_level = "记录数达到审计描述门槛"
    elif count >= LIMITED_RECORDS:
        record_level = "记录数偏少"
    elif count > 0:
        record_level = "记录数不足"
    else:
        record_level = "官方故障类型列没有这个字符串"
    if device_count >= ENOUGH_DEVICES:
        device_level = "至少2台设备"
    elif device_count == 1:
        device_level = "只有1台设备"
    else:
        device_level = "没有设备"
    return {
        "name": name,
        "count": count,
        "device_count": device_count,
        "record_level": record_level,
        "device_level": device_level,
        "exact_kb02_heading": kb_heading,
        "can_support_device_holdout_experiment": count >= LIMITED_RECORDS and device_count >= ENOUGH_DEVICES,
    }


def _join_names(names: list[str]) -> str:
    return "、".join(names) if names else "无"


def _conclusions(summary: dict) -> dict[str, str]:
    duplicates = summary["duplicates"]
    multi = duplicates["devices_with_more_than_one_fault_type"]
    changes = duplicates["time_ordered_fault_label_changes"]
    conflicts = duplicates["groups_with_fault_type_conflict"]
    conflict_records = duplicates["records_in_fault_type_conflict_groups"]
    supported = [item["name"] for item in summary["class_assessment"] if item["can_support_device_holdout_experiment"]]
    record_ready = [item["name"] for item in summary["fault_types"] if item["count"] >= ENOUGH_RECORDS]
    few = [item["name"] for item in summary["class_assessment"] if item["count"] < LIMITED_RECORDS]
    holdout = summary["splits"]["device_holdout_by_category"]
    random_split = summary["splits"]["random_record_80_20"]
    within = summary["splits"]["within_device_time_80_20"]
    global_time = summary["splits"]["global_time_80_20"]
    excel_names = [item["name"] for item in summary["fault_types"]]
    gaps = []
    for category, counts in summary["fault_by_category"].items():
        missing = [name for name in excel_names if name not in counts]
        if missing:
            gaps.append(f"{category}没有{_join_names(missing)}")
    if supported:
        answer_a = (
            "可以开展数值驱动的故障分类实验。这个结论只表示样本量、设备覆盖和白名单数值足够做一次对照实验，"
            "不表示已经能够诊断故障，也没有准确率。"
            f"达到“至少 {LIMITED_RECORDS} 条记录且至少 {ENOUGH_DEVICES} 台设备”的故障类型有：{_join_names(supported)}。"
            f"{multi} 台设备含有一种以上故障类型，按时间排序的标签变化次数为 {changes}。"
            f"同设备同时间的重复组中有 {conflicts} 组故障类型不一致，涉及 {conflict_records} 条记录，这些时刻没有唯一标签。"
        )
    else:
        answer_a = "按本次审计门槛，没有故障类型同时具备足够记录和至少两台设备。现有表格不足以开展可核验的跨设备数值分类实验。"
    lines_b = [
        f"{item['name']}：{item['count']} 条，{item['device_count']} 台设备，{item['record_level']}，{item['device_level']}。"
        for item in summary["class_assessment"]
    ]
    status_normal = summary["status_by_fault"].get("正常", {})
    answer_b = (
        "样本量门槛只用于本次描述，不是官方标准。"
        + "".join(lines_b)
        + f"KB-02 二级标题中未在故障类型列精确出现的名称：{_join_names(summary['kb02_headings_absent_from_excel'])}。"
        + f"故障类型列中不属于这些 KB-02 标题的名称：{_join_names(summary['fault_names_not_in_kb02_headings'])}。本次没有把不同字符串合并。"
        + f"记录数达到 {ENOUGH_RECORDS} 的类型：{_join_names(record_ready)}。记录数少于 {LIMITED_RECORDS} 或未出现的类型：{_join_names(few)}。"
        + f"并非每个设备类别都包含全部故障类型：{'；'.join(gaps) if gaps else '无'}。"
        + "正常与异常没有单一、可互换的官方定义。故障类型里的“正常”和设备状态里的“正常”不是同一列。"
        + f"设备状态的取值是 {_join_names(list(summary['status_values']))}，其中没有名为“异常”的取值。"
        + f"设备状态“正常”共 {summary['status_values'].get('正常', 0)} 条，其中故障类型同为“正常”的有 {status_normal.get('正常', 0)} 条，其余同时出现其他故障类型。"
        + "因此不能把设备状态直接当成故障类型，也不能自行规定哪一列才是正常样本。"
    )
    answer_c = (
        "可以合法作为数值输入的只有现有观测白名单："
        + "、".join(summary["input_features"])
        + "。设备编号、设备类别、工作表、记录编号和采集时间只用于分组、排序和划分，不作为模型输入。"
        f"设备内部恒定、实际携带设备类别的观测列：{_join_names([item['field'] for item in summary['device_constant_shortcuts']])}。"
        f"全局差异主要由设备类别造成、类别内部分不开故障类型的观测列：{_join_names([item['field'] for item in summary['category_dominated_fields']])}。"
        f"在同一设备类别内部仍能看到故障类型分布差异的观测列：{_join_names([item['field'] for item in summary['possible_distribution_differences']])}。"
        "最大振动与 X、Y 向振动的较大值重合，转频与转速除以 60 基本重合，这两对都不能算成独立证据。"
        "分布差异只说明数值位置不同，不能单独证明可以诊断故障。"
    )
    measured = []
    for item in summary["label_fields"]:
        if item["field"] in {"故障类型", "设备状态"}:
            continue
        if item["records_whose_text_contains_its_fault_name"]:
            measured.append(
                f"{item['field']}有 {item['records_whose_text_contains_its_fault_name']} 条记录的文本包含该记录的故障类型字符串，不同取值 {item['unique_non_empty_values']} 个，其中 {item['values_with_multiple_fault_types']} 个取值对应多种故障类型"
            )
        elif item["determines_fault_type_when_present"]:
            measured.append(f"{item['field']}的非空取值与故障类型一一对应")
    status_item = next(item for item in summary["label_fields"] if item["field"] == "设备状态")
    measured.append(
        f"设备状态有 {status_item['unique_non_empty_values']} 个取值，全部对应多种故障类型；其中 {summary['status_by_fault'].get('正常', {}).get('正常', 0)} 条是设备状态和故障类型同为“正常”，字符串因此发生包含，但这不能唯一确定故障类型"
    )
    signature = summary["shortcuts"]["constant_signature"]
    answer_d = (
        "禁止作为输入的 10 个结果字段是："
        + "、".join(summary["excluded_label_fields"])
        + f"。{SPLIT_FIELD}也不是输入特征，它在本文件中的取值只有：{_counts_text(summary['split_column']['value_counts'])}。"
        f"实测文本泄漏：{_join_names(measured) if measured else '除故障类型列本身外，没有结果字段的文本直接包含故障类型字符串'}。"
        "故障类型列本身是评估目标，不能同时作为输入。"
        f"四条恒定频率列形成 {signature['distinct_constant_signatures']} 种签名，能唯一对应到单台设备的签名为 {signature['devices_with_unique_constant_signature']} / {signature['devices_checked']}。"
        "它们不能唯一点名设备，但能区分设备类别。频谱描述、根因分析和建议措施的原文没有写入报告。"
    )
    answer_e = (
        f"随机按记录划分不适合验证泛化：同一种子模拟中，{random_split['devices_in_both']} 台设备同时出现在两侧，"
        f"{random_split['same_time_groups_crossing_split']} 个同设备同时间重复组被拆开，"
        f"{random_split['test_records_with_later_same_device_record_in_train']} 条测试记录能在训练侧找到同设备的更晚记录。"
        f"检查同一台设备上过去观测与以后标签的关系，应使用设备内时间切分：同时间组跨越 {within['same_time_groups_crossing_split']} 个，"
        f"未来记录进入训练侧的测试记录数为 {within['test_records_with_later_same_device_record_in_train']}。"
        f"该切分仍有 {within['devices_in_both']} 台设备同时出现在两侧，所以不能单独证明模型认识新设备。"
        f"全局时间切分可行，切点是 {global_time['cutoff']}，但测试侧只有 {global_time['test_devices']} 台设备，"
        f"只出现在训练侧的设备有 {global_time['devices_train_only']} 台，不能代表全部设备的未来。"
        f"验证跨设备泛化应把整台设备留出。本次示例留出 {holdout['test_devices']} 台设备、{holdout['test_records']} 条记录，"
        f"两侧共有设备 {holdout['devices_in_both']} 台，同时间组跨越 {holdout['same_time_groups_crossing_split']} 个，"
        f"未来记录进入训练侧 {holdout['test_records_with_later_same_device_record_in_train']} 条。"
        f"留出后测试记录少于 {LIMITED_RECORDS} 的类型：{_join_names(holdout['faults_with_fewer_than_30_test_records'])}；"
        f"测试设备少于 {ENOUGH_DEVICES} 台的类型：{_join_names(holdout['faults_with_fewer_than_2_test_devices'])}。"
    )
    answer_f = (
        "当前不具备开展真正故障预测的条件。设备内部有按时间变化的故障标签，所以可以设计“用较早记录对照较晚标签”的实验，"
        f"本次统计到 {changes} 次这样的标签变化。"
        f"实验还不能称为故障预测，因为缺少官方预测时距，而且 {conflicts} 个同时间组没有唯一故障类型。"
        "表格没有 KB-02 写明的峭度、峰值因子、2X/3X 谐波、调制边带、相位、宽频噪声和异音。"
        "已有的是单点振动、电流、轴承温度、包络谱峰值、BPFO/BPFI/BSF 幅值和频率、啮合频率及 1X 幅值。"
        "频谱描述属于结果字段，不能用来补这些证据。本审计也没有核验故障类型的生成过程，不能把分类实验写成现场确诊或提前预警。"
    )
    answer_g = (
        "下一步最简单的可解释基线是两层对照，本次不训练："
        "第一，分别在“设备内时间切分”和“整台设备留出”的训练侧使用多数故障类型；"
        "第二，只用同一设备类别内部仍有分布差异的白名单数值列，拟合深度不超过 3 的决策树或 L2 多项逻辑回归。"
        "四条恒定频率列、转速和转频、设备编号、工作表、采集时间以及 10 个结果字段都不进入特征。"
        "最大振动不要与 X、Y 向振动同时当作独立证据。训练前要先规定同时间冲突标签如何处理。"
        "若用设备编号能记住标签，该结果只说明泄漏，不能当作成绩。"
    )
    return {"A": answer_a, "B": answer_b, "C": answer_c, "D": answer_d, "E": answer_e, "F": answer_f, "G": answer_g}


def _num(value, digits=4) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, int):
        return str(value)
    return f"{float(value):.{digits}f}"


def _cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(headers: list[str], rows: list[list[object]]) -> str:
    head = "| " + " | ".join(headers) + " |"
    split = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(_cell(value) for value in row) + " |" for row in rows]
    return "\n".join([head, split, *body])


def _counts_text(counter: dict[str, int]) -> str:
    return "；".join(f"{name} {count}" for name, count in counter.items()) if counter else "无"


def render_markdown(summary: dict) -> str:
    source = summary["source"]
    fault_names = [item["name"] for item in summary["fault_types"]]
    lines = [
        "# 官方数据故障识别可行性审计",
        "",
        "本报告只统计官方开发集 Excel。没有训练分类器，没有输出分类准确率，没有调用 DeepSeek。",
        "故障类型和其他结果字段只用于分组和泄漏检查，没有写入业务缓存，也没有作为输入特征。",
        "",
        "## 1. 数据规模",
        "",
        f"- 文件：{source['file_name']}",
        f"- SHA-256：{source['sha256']}",
        f"- 工作表：{source['sheet_count']}",
        f"- 设备：{source['device_count']}",
        f"- 记录：{source['record_count']}",
        f"- 无法解析的采集时间：{source['unparsed_time_records']}",
        "",
        _table(
            ["工作表", "设备类别", "表名与类别一致", "设备数", "记录数", "开始", "结束", "重复时间组"],
            [
                [
                    item["sheet_name"],
                    item["equipment_category"],
                    "是" if item["sheet_name_matches_category"] else "否",
                    item["device_count"],
                    item["record_count"],
                    item["time_start"],
                    item["time_end"],
                    item["duplicate_timestamp_groups"],
                ]
                for item in source["sheets"]
            ],
        ),
        "",
        "## 2. 官方故障类型",
        "",
        "类别名称按 Excel「故障类型」单元格的原始字符串统计，没有合并近义名称。",
        "",
        _table(
            ["故障类型", "记录数", "设备数", "各类别记录数"],
            [
                [item["name"], item["count"], item["device_count"], _counts_text(item["category_counts"])]
                for item in summary["fault_types"]
            ],
        ),
        "",
        "### 各设备类别",
        "",
        _table(
            ["设备类别", "故障类型分布"],
            [[category, _counts_text(counts)] for category, counts in summary["fault_by_category"].items()],
        ),
        "",
        "### 各设备编号",
        "",
        _table(
            ["设备编号", "设备类别", "记录数", "故障类型分布", "标签随时间变化次数", "重复时间组"],
            [
                [
                    item["device_id"],
                    item["equipment_category"],
                    item["record_count"],
                    _counts_text(item["fault_counts"]),
                    item["time_ordered_fault_label_changes"],
                    item["duplicate_timestamp_groups"],
                ]
                for item in summary["fault_by_device"]
            ],
        ),
        "",
        "### 与 KB-02 二级标题的精确对照",
        "",
        _table(
            ["KB-02 二级标题", "故障类型列中的相同字符串记录数"],
            [[name, summary["kb02_exact_match_counts"][name]] for name in summary["kb02_level2_headings"]],
        ),
        "",
        f"故障类型列里没有对应 KB-02 二级标题的名称：{_join_names(summary['fault_names_not_in_kb02_headings'])}。",
        "",
        "## 3. 正常与异常标签是否有可核验定义",
        "",
        "这里只列出「设备状态」的取值，以及它和「故障类型」同时出现的次数。这是共现统计，不是自行规定的正常或异常定义。",
        "",
        _table(
            ["设备状态", "记录数", "同时出现的故障类型"],
            [
                [status, count, _counts_text(summary["status_by_fault"][status])]
                for status, count in summary["status_values"].items()
            ],
        ),
        "",
        "## 4. 结果字段的泄漏检查",
        "",
        "10 个结果字段全部排除在输入之外。下表只给计数和对应程度，不摘录频谱描述、根因分析或建议措施的原文。",
        "“取值只对应一种故障类型的记录比例”不是模型准确率。",
        "",
        _table(
            ["字段", "非空", "不同取值", "一值多故障的取值数", "单故障取值记录比例", "文本包含故障类型字符串", "非空时完全对应"],
            [
                [
                    item["field"],
                    item["non_empty"],
                    item["unique_non_empty_values"],
                    item["values_with_multiple_fault_types"],
                    _num(item["single_fault_value_record_ratio"]),
                    item["records_whose_text_contains_its_fault_name"],
                    "是" if item["determines_fault_type_when_present"] else "否",
                ]
                for item in summary["label_fields"]
            ],
        ),
        "",
        f"「{SPLIT_FIELD}」不作为输入。取值分布：{_counts_text(summary['split_column']['value_counts'])}。",
        f"同一设备出现多个划分取值的设备数：{summary['split_column']['devices_in_multiple_values']}。",
        "",
        _table(
            ["划分取值", "记录数", "设备数", "开始", "结束", "故障类型分布"],
            [
                [item["value"], item["count"], item["device_count"], item["time_start"], item["time_end"], _counts_text(item["fault_counts"])]
                for item in summary["split_column"]["per_value"]
            ],
        ),
        "",
        "## 5. 白名单观测字段",
        "",
        summary["anomaly_metric_note"],
        "",
        _table(
            ["字段", "非空", "缺失比例", "最小", "中位数", "最大", "故障类型 eta²", "设备类别 eta²", "设备 eta²", "设备内恒定数"],
            [
                [
                    item["field"],
                    item["non_null"],
                    _num(item["missing_ratio"]),
                    _num(item["minimum"]),
                    _num(item["median"]),
                    _num(item["maximum"]),
                    _num(item["eta_squared_fault_type"]),
                    _num(item["eta_squared_equipment_category"]),
                    _num(item["eta_squared_device"]),
                    item["devices_with_constant_values"],
                ]
                for item in summary["features"]
            ],
        ),
        "",
        "eta² 是组间平方和除以总平方和，用来描述均值位置差异。它不是分类准确率，也不是显著性检验。",
        "",
        "### 不同故障类型的中位数",
        "",
        _table(
            ["字段", *fault_names],
            [[item["field"], *[_num(item["median_by_fault_type"].get(name)) for name in fault_names]] for item in summary["features"]],
        ),
        "",
        "### 不同设备类别的中位数",
        "",
        _table(
            ["字段", *list(summary["fault_by_category"])],
            [
                [item["field"], *[_num(item["median_by_category"].get(category)) for category in summary["fault_by_category"]]]
                for item in summary["features"]
            ],
        ),
        "",
        "### 数值冗余",
        "",
        f"- 转速(rpm)/60 与转频(Hz) 可比较的记录：{summary['redundancy']['speed_hz_pairs']}，最大绝对差 {_num(summary['redundancy']['speed_hz_max_abs_diff'], 6)}。",
        f"- 最大振动(mm/s) 与 X、Y 较大值可比较的记录：{summary['redundancy']['max_vibration_pairs']}，最大绝对差 {_num(summary['redundancy']['max_vibration_max_abs_diff'], 6)}。",
        "",
        "### 可能看到分布差异的观测列",
        "",
    ]
    if summary["possible_distribution_differences"]:
        lines.append(_table(["字段", "说明"], [[item["field"], item["reason"]] for item in summary["possible_distribution_differences"]]))
    else:
        lines.append("没有观测列在同一设备类别内部达到本次分布差异门槛。")
    lines.extend(["", "### 差异主要来自设备类别的观测列", ""])
    if summary["category_dominated_fields"]:
        lines.append(_table(["字段", "说明"], [[item["field"], item["reason"]] for item in summary["category_dominated_fields"]]))
    else:
        lines.append("没有观测列主要表现为设备类别差异。")
    lines.extend(["", "### 更像设备类别常数的观测列", ""])
    if summary["device_constant_shortcuts"]:
        lines.append(_table(["字段", "说明"], [[item["field"], item["reason"]] for item in summary["device_constant_shortcuts"]]))
    else:
        lines.append("没有观测列在九成以上设备内部保持恒定。")
    signature = summary["shortcuts"]["constant_signature"]
    duplicates = summary["duplicates"]
    lines.extend(
        [
            "",
            "## 6. 重复时间、冲突和捷径",
            "",
            f"- 同设备同采集时间的重复组：{duplicates['same_device_same_time_groups']}",
            f"- 涉及记录：{duplicates['records_in_those_groups']}",
            f"- 测量值不完全相同的组：{duplicates['groups_with_measurement_conflict']}",
            f"- 测量值完全相同的组：{duplicates['groups_with_identical_measurements']}",
            f"- 故障类型不一致的组：{duplicates['groups_with_fault_type_conflict']}，涉及记录 {duplicates['records_in_fault_type_conflict_groups']}",
            f"- 其他结果字段不一致的组：{duplicates['groups_with_other_label_conflict']}",
            f"- 被多台设备共用的采集时间个数：{duplicates['timestamps_shared_by_multiple_devices']}",
            f"- 只有一种故障类型的设备：{duplicates['devices_with_one_fault_type']}",
            f"- 有多种故障类型的设备：{duplicates['devices_with_more_than_one_fault_type']}",
            f"- 按时间排序后的故障标签变化次数：{duplicates['time_ordered_fault_label_changes']}",
            f"- 每台设备都恒定的观测列：{_join_names(signature['fields_constant_on_every_device_with_values'])}",
            f"- 这些恒定列形成的不同签名：{signature['distinct_constant_signatures']} 种；能唯一对应单台设备的签名：{signature['devices_with_unique_constant_signature']} / {signature['devices_checked']}",
            f"- 各故障类型的时间范围是否有重叠：{'是' if summary['shortcuts']['fault_time_ranges_overlap'] else '否'}",
            "",
            _table(
                ["故障类型", "最早", "最晚"],
                [[name, item["time_start"], item["time_end"]] for name, item in summary["shortcuts"]["fault_time_ranges"].items()],
            ),
            "",
            "## 7. 数据划分风险",
            "",
            "以下都是固定规则的计数模拟，没有训练模型。",
            "",
        ]
    )
    for key, title in (
        ("random_record_80_20", "随机按记录 80/20"),
        ("global_time_80_20", "全局时间先后"),
        ("within_device_time_80_20", "每台设备内部的时间先后"),
        ("device_holdout_by_category", "按设备编号隔离"),
    ):
        item = summary["splits"][key]
        lines.extend(
            [
                f"### {title}",
                "",
                item["rule"],
                "",
                f"- 训练记录 {item['train_records']}，测试记录 {item['test_records']}",
                f"- 训练设备 {item['train_devices']}，测试设备 {item['test_devices']}，两侧都出现的设备 {item['devices_in_both']}",
                f"- 跨越两侧的同设备同时间重复组：{item['same_time_groups_crossing_split']}",
                f"- 测试记录中能在训练侧找到同设备更晚记录的条数：{item['test_records_with_later_same_device_record_in_train']}",
                f"- 测试侧没有出现的故障类型：{_join_names(item['faults_missing_from_test'])}",
                f"- 测试记录少于 {LIMITED_RECORDS} 的故障类型：{_join_names(item['faults_with_fewer_than_30_test_records'])}",
                f"- 测试设备少于 {ENOUGH_DEVICES} 台的故障类型：{_join_names(item['faults_with_fewer_than_2_test_devices'])}",
                "",
                _table(
                    ["故障类型", "训练记录", "测试记录", "训练设备", "测试设备"],
                    [
                        [row["name"], row["train_records"], row["test_records"], row["train_devices"], row["test_devices"]]
                        for row in item["fault_summary"]
                    ],
                ),
                "",
            ]
        )
    held = summary["splits"]["device_holdout_by_category"]["held_out_devices"]
    lines.extend(
        [
            "本次设备隔离示例留出的设备：",
            "",
            _table(
                ["设备编号", "设备类别", "记录数", "故障类型"],
                [[item["device_id"], item["equipment_category"], item["record_count"], _counts_text(item["fault_counts"])] for item in held],
            ),
            "",
            "## 8. 结论",
            "",
            "### A. 官方数据是否足以开展数值驱动的故障分类实验？",
            "",
            summary["conclusions"]["A"],
            "",
            "### B. 哪些故障类型有足够样本，哪些样本不足？",
            "",
            summary["conclusions"]["B"],
            "",
            "### C. 哪些输入特征可以合法使用？",
            "",
            summary["conclusions"]["C"],
            "",
            "### D. 哪些字段存在明显答案泄漏风险？",
            "",
            summary["conclusions"]["D"],
            "",
            "### E. 哪种数据划分方法更适合验证泛化能力？",
            "",
            summary["conclusions"]["E"],
            "",
            "### F. 当前是否具备开展真正故障预测的条件？",
            "",
            summary["conclusions"]["F"],
            "",
            "### G. 下一步应该尝试什么最简单、可解释的基线模型？",
            "",
            summary["conclusions"]["G"],
            "",
            "## 9. 本次没有做的事",
            "",
            "- 没有训练分类模型。",
            "- 没有修改诊断、异常检测、诊断桥接、后端接口或前端。",
            "- 没有把标签写入业务缓存，没有调用 DeepSeek。",
            "- 没有修改原始 Excel。",
            "",
        ]
    )
    return "\n".join(lines)


def _check_outputs(summary: dict, markdown: str) -> None:
    source = summary["source"]
    if source["sheet_count"] != EXPECTED_SHEETS or source["device_count"] != EXPECTED_DEVICES or source["record_count"] != EXPECTED_RECORDS:
        raise AuditError("读取规模不是 6 个工作表、46 台设备、24000 条记录。")
    if source["sha256"] != EXPECTED_HASH:
        raise AuditError("官方 Excel 的 SHA-256 与已确认的值不一致。")
    if set(summary["input_features"]) & set(summary["excluded_label_fields"]):
        raise AuditError("输入特征包含结果标签。")
    if sum(item["count"] for item in summary["fault_types"]) != EXPECTED_RECORDS:
        raise AuditError("故障类型计数之和不等于记录数。")
    blob = markdown + json.dumps(summary, ensure_ascii=False)
    if "REC-" in blob:
        raise AuditError("输出包含逐条记录编号，已停止。")
    for field in summary["excluded_label_fields"]:
        if field in summary["input_features"]:
            raise AuditError(f"输入特征包含{field}。")


def write_reports(summary: dict) -> None:
    markdown = render_markdown(summary)
    _check_outputs(summary, markdown)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(markdown, encoding="utf-8")
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if file_sha256(DATASET_FILE) != EXPECTED_HASH:
        raise AuditError("写出报告后 Excel 哈希发生变化。")


def main() -> None:
    before = file_sha256(DATASET_FILE)
    rows, sheet_order = load_rows()
    summary = build_summary(rows, sheet_order)
    write_reports(summary)
    after = file_sha256(DATASET_FILE)
    print(f"sheets={summary['source']['sheet_count']} devices={summary['source']['device_count']} records={summary['source']['record_count']}")
    print("fault_types=" + json.dumps(summary["kb02_exact_match_counts"] | {item["name"]: item["count"] for item in summary["fault_types"]}, ensure_ascii=False))
    print(f"excel_sha256_unchanged={before == after == EXPECTED_HASH}")
    print(f"report={REPORT_PATH}")
    print(f"summary={SUMMARY_PATH}")
    print("PASS")


if __name__ == "__main__":
    main()
