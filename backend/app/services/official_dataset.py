"""只读读取官方开发集 Excel，并按白名单返回观测数据。

答案标签只在解析表头时被识别并跳过，不会进入缓存或查询结果。
"""

from __future__ import annotations

import math
import re
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

DATASET_FILE = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "official_dataset"
    / "钢铁设备数据集_开发集.xlsx"
)
MAX_LIMIT = 100
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
_INTEGER_TEXT = re.compile(r"[+-]?\d+")

IDENTITY_FIELDS = ("记录编号", "设备编号", "设备类别", "采集时间")
MEASUREMENT_FIELDS = (
    "转速(rpm)",
    "转频(Hz)",
    "电机电流(A)",
    "X向振动(mm/s)",
    "Y向振动(mm/s)",
    "最大振动(mm/s)",
    "振动均方根",
    "驱动端轴承温度(°C)",
    "非驱动端轴承温度(°C)",
    "包络谱峰值(g)",
    "BPFO幅值(g)",
    "BPFI幅值(g)",
    "BSF幅值(g)",
    "啮合频率幅值(g)",
    "1X转频幅值(mm/s)",
    "BPFO频率(Hz)",
    "BPFI频率(Hz)",
    "BSF频率(Hz)",
    "啮合频率(Hz)",
)
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
_IGNORED_FIELDS = frozenset({"数据集划分"})
_EXPECTED_FIELDS = set(IDENTITY_FIELDS) | set(MEASUREMENT_FIELDS) | _WITHHELD_FIELDS | _IGNORED_FIELDS
_NON_FINITE = frozenset({"nan", "inf", "infinity", "+inf", "-inf", "-infinity", "+infinity"})


class DatasetError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class _Observation:
    record_id: str
    excel_row: int
    sheet_name: str
    device_id: str
    equipment_category: str
    collected_at: str
    collected_at_dt: datetime | None
    values: tuple[tuple[str, int | float | None], ...]
    duplicate_timestamp: bool
    same_timestamp_count: int

    def public_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "excel_row": self.excel_row,
            "sheet_name": self.sheet_name,
            "device_id": self.device_id,
            "equipment_category": self.equipment_category,
            "collected_at": self.collected_at,
            "duplicate_timestamp": self.duplicate_timestamp,
            "same_timestamp_count": self.same_timestamp_count,
            "values": {name: value for name, value in self.values},
        }


@dataclass(frozen=True)
class _DeviceInfo:
    device_id: str
    equipment_category: str
    sheet_name: str
    record_count: int
    time_start: str | None
    time_end: str | None
    duplicate_timestamp_groups: int


@dataclass(frozen=True)
class _SheetInfo:
    sheet_name: str
    equipment_category: str
    sheet_name_matches_category: bool
    device_count: int
    record_count: int
    time_start: str | None
    time_end: str | None
    duplicate_timestamp_groups: int


@dataclass(frozen=True)
class _Dataset:
    sheets: tuple[_SheetInfo, ...]
    devices: tuple[_DeviceInfo, ...]
    by_device: dict[str, tuple[_Observation, ...]]
    by_record: dict[str, _Observation]


_CACHE: _Dataset | None = None
_CACHE_LOCK = threading.Lock()


def get_dataset_summary() -> dict:
    """返回工作表、设备类别、设备数量、记录数量和时间范围。"""
    dataset = _dataset()
    return {
        "source_name": DATASET_FILE.name,
        "sheet_count": len(dataset.sheets),
        "device_count": len(dataset.devices),
        "record_count": sum(sheet.record_count for sheet in dataset.sheets),
        "sheets": [
            {
                "sheet_name": sheet.sheet_name,
                "equipment_category": sheet.equipment_category,
                "sheet_name_matches_category": sheet.sheet_name_matches_category,
                "device_count": sheet.device_count,
                "record_count": sheet.record_count,
                "time_start": sheet.time_start,
                "time_end": sheet.time_end,
                "duplicate_timestamp_groups": sheet.duplicate_timestamp_groups,
            }
            for sheet in dataset.sheets
        ],
    }


def list_devices() -> dict:
    """返回实际存在的设备编号、设备类别和记录数量。"""
    dataset = _dataset()
    return {
        "device_count": len(dataset.devices),
        "devices": [
            {
                "device_id": device.device_id,
                "device_name": device.equipment_category,
                "equipment_category": device.equipment_category,
                "sheet_name": device.sheet_name,
                "record_count": device.record_count,
                "time_start": device.time_start,
                "time_end": device.time_end,
                "duplicate_timestamp_groups": device.duplicate_timestamp_groups,
            }
            for device in dataset.devices
        ],
    }


def get_device_records(
    device_id: str,
    start_time: str | datetime | None = None,
    end_time: str | datetime | None = None,
    limit: int = MAX_LIMIT,
) -> dict:
    """按设备编号和采集时间返回观测记录。相同时间的记录全部保留。"""
    applied_limit = _normalize_limit(limit)
    start = _parse_bound(start_time, "start_time")
    end = _parse_bound(end_time, "end_time")
    if start is not None and end is not None and start > end:
        raise DatasetError("start_time 不能晚于 end_time。")
    identity = _device_identity(device_id)
    dataset = _dataset()
    records = dataset.by_device.get(identity, ())
    if not records:
        return _empty_device_result(identity, applied_limit)
    matched = [record for record in records if _in_range(record, start, end)]
    duplicate_groups = len(
        {record.collected_at for record in matched if record.same_timestamp_count > 1}
    )
    selected = matched[:applied_limit]
    first = records[0]
    return {
        "found": True,
        "device_id": first.device_id,
        "equipment_category": first.equipment_category,
        "sheet_name": first.sheet_name,
        "matched_count": len(matched),
        "returned_count": len(selected),
        "limit": applied_limit,
        "truncated": len(matched) > applied_limit,
        "duplicate_timestamp_groups": duplicate_groups,
        "records": [record.public_dict() for record in selected],
    }


def list_device_record_ids(device_id: str) -> dict:
    """按采集时间返回一台设备的全部记录编号。相同时间的记录都保留，不合并。"""
    identity = _device_identity(device_id)
    records = _dataset().by_device.get(identity, ())
    if not records:
        return {"found": False, "device_id": identity, "record_count": 0, "record_ids": []}
    return {
        "found": True,
        "device_id": records[0].device_id,
        "record_count": len(records),
        "record_ids": [record.record_id for record in records],
    }


def search_records(
    q: str | None = None,
    device_id: str | None = None,
    start_time: str | datetime | None = None,
    end_time: str | datetime | None = None,
    page: int = 1,
    page_size: int = 20,
    analysis_status: str = "all",
    known_deviation_ids: set[str] | frozenset[str] | None = None,
) -> dict:
    """按设备名称、设备编号、记录编号和时间分页检索官方历史记录。

    相同采集时间的不同 record_id 全部保留，不合并。
    analysis_status 只使用已给出的偏离记录编号集合；尚未分析的记录不会被标成正常。
    """
    query = str(q or "").strip()
    identity = _device_identity(device_id) if device_id else ""
    start = _parse_bound(start_time, "start_time")
    end = _parse_bound(end_time, "end_time")
    if start is not None and end is not None and start > end:
        raise DatasetError("start_time 不能晚于 end_time。")
    page_number = _normalize_page(page)
    size = _normalize_page_size(page_size)
    status = str(analysis_status or "all").strip()
    if status not in {"all", "deviation", "no_significant_deviation"}:
        raise DatasetError("analysis_status 只能是 all、deviation 或 no_significant_deviation。")

    note = ""
    if status == "no_significant_deviation":
        return {
            "found": False,
            "query": query,
            "device_id": identity or None,
            "matched_count": 0,
            "returned_count": 0,
            "page": page_number,
            "page_size": size,
            "total_pages": 0,
            "analysis_status": status,
            "status_filter_note": (
                "未发现显著偏离的结果不会作为监测事件长期保存。"
                "请在「全部记录」中打开详情并执行分析查看；尚未分析的记录不会被标为正常。"
            ),
            "records": [],
        }

    deviation_ids = set(known_deviation_ids or ())
    if status == "deviation" and not deviation_ids:
        return {
            "found": False,
            "query": query,
            "device_id": identity or None,
            "matched_count": 0,
            "returned_count": 0,
            "page": page_number,
            "page_size": size,
            "total_pages": 0,
            "analysis_status": status,
            "status_filter_note": "当前没有已保存的偏离基线监测事件，无法按该状态筛选。尚未分析的记录不会被标为正常。",
            "records": [],
        }

    dataset = _dataset()
    matched: list[_Observation] = []
    query_lower = query.casefold()
    for device in dataset.devices:
        if identity and device.device_id != identity:
            continue
        device_hit = (not query) or _device_matches_query(device, query, query_lower)
        for record in dataset.by_device.get(device.device_id, ()):
            if not _in_range(record, start, end):
                continue
            if query and not device_hit and query_lower not in record.record_id.casefold():
                continue
            if status == "deviation" and record.record_id not in deviation_ids:
                continue
            matched.append(record)

    matched.sort(key=_sort_key)
    total = len(matched)
    start_index = (page_number - 1) * size
    selected = matched[start_index : start_index + size]
    total_pages = (total + size - 1) // size if total else 0
    records = []
    for record in selected:
        item = record.public_dict()
        if record.record_id in deviation_ids:
            item["analysis_status"] = "偏离基线"
            item["analysis_status_note"] = "来自已保存的监测事件统计偏离结果，不是故障确诊。"
        else:
            item["analysis_status"] = "尚未分析"
            item["analysis_status_note"] = "尚未执行或尚未保存统计分析。尚未分析不等于正常。"
        item["device_name"] = record.equipment_category
        records.append(item)
    return {
        "found": total > 0,
        "query": query,
        "device_id": identity or None,
        "matched_count": total,
        "returned_count": len(selected),
        "page": page_number,
        "page_size": size,
        "total_pages": total_pages,
        "analysis_status": status,
        "status_filter_note": note,
        "records": records,
    }


def _device_matches_query(device: _DeviceInfo, query: str, query_lower: str) -> bool:
    if query_lower in device.device_id.casefold():
        return True
    if query in device.equipment_category or query_lower in device.equipment_category.casefold():
        return True
    if query in device.sheet_name or query_lower in device.sheet_name.casefold():
        return True
    return False


def get_observation(record_id: str) -> dict:
    """按唯一记录编号返回一条观测，不包含答案标签。"""
    identity = str(record_id or "").strip()
    if not identity:
        return {"found": False, "record": None}
    record = _dataset().by_record.get(identity)
    if record is None:
        return {"found": False, "record": None}
    return {"found": True, "record": record.public_dict()}


def _dataset() -> _Dataset:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    with _CACHE_LOCK:
        if _CACHE is None:
            _CACHE = _load_dataset()
        return _CACHE


def _load_dataset() -> _Dataset:
    if not DATASET_FILE.is_file():
        raise DatasetError("未找到官方开发集 Excel。")
    with ZipFile(DATASET_FILE) as workbook:
        sheets = _sheet_targets(workbook)
        pending = [
            record
            for sheet_name, target in sheets
            for record in _read_sheet(workbook, sheet_name, target)
        ]
    if len({record.record_id for record in pending}) != len(pending):
        raise DatasetError("官方开发集存在重复的记录编号，读取已停止。")
    by_device_lists: dict[str, list[_Observation]] = defaultdict(list)
    for record in pending:
        by_device_lists[record.device_id].append(record)
    observations: list[_Observation] = []
    devices: list[_DeviceInfo] = []
    sheet_records: dict[str, list[_Observation]] = defaultdict(list)
    for device_id in sorted(by_device_lists):
        ordered = sorted(by_device_lists[device_id], key=_sort_key)
        if len({record.sheet_name for record in ordered}) != 1 or len({record.equipment_category for record in ordered}) != 1:
            raise DatasetError(f"设备编号「{device_id}」对应了多个工作表或设备类别，读取已停止。")
        counts: dict[str, int] = defaultdict(int)
        for record in ordered:
            counts[record.collected_at] += 1
        marked = tuple(_with_duplicate_mark(record, counts[record.collected_at]) for record in ordered)
        observations.extend(marked)
        parsed_times = [record.collected_at_dt for record in marked if record.collected_at_dt is not None]
        devices.append(
            _DeviceInfo(
                device_id=device_id,
                equipment_category=marked[0].equipment_category,
                sheet_name=marked[0].sheet_name,
                record_count=len(marked),
                time_start=min(parsed_times).strftime("%Y-%m-%d %H:%M:%S") if parsed_times else None,
                time_end=max(parsed_times).strftime("%Y-%m-%d %H:%M:%S") if parsed_times else None,
                duplicate_timestamp_groups=sum(1 for count in counts.values() if count > 1),
            )
        )
        sheet_records[marked[0].sheet_name].extend(marked)
    by_record = {record.record_id: record for record in observations}
    by_device = {device.device_id: tuple(record for record in observations if record.device_id == device.device_id) for device in devices}
    sheet_info = []
    for sheet_name, _target in sheets:
        records = sheet_records.get(sheet_name, [])
        categories = {record.equipment_category for record in records}
        category = next(iter(categories)) if len(categories) == 1 else "、".join(sorted(categories))
        parsed_times = [record.collected_at_dt for record in records if record.collected_at_dt is not None]
        sheet_info.append(
            _SheetInfo(
                sheet_name=sheet_name,
                equipment_category=category,
                sheet_name_matches_category=category == sheet_name,
                device_count=len({record.device_id for record in records}),
                record_count=len(records),
                time_start=min(parsed_times).strftime("%Y-%m-%d %H:%M:%S") if parsed_times else None,
                time_end=max(parsed_times).strftime("%Y-%m-%d %H:%M:%S") if parsed_times else None,
                duplicate_timestamp_groups=sum(
                    device.duplicate_timestamp_groups for device in devices if device.sheet_name == sheet_name
                ),
            )
        )
    return _Dataset(
        sheets=tuple(sheet_info),
        devices=tuple(devices),
        by_device=by_device,
        by_record=by_record,
    )


def _with_duplicate_mark(record: _Observation, count: int) -> _Observation:
    return _Observation(
        record_id=record.record_id,
        excel_row=record.excel_row,
        sheet_name=record.sheet_name,
        device_id=record.device_id,
        equipment_category=record.equipment_category,
        collected_at=record.collected_at,
        collected_at_dt=record.collected_at_dt,
        values=record.values,
        duplicate_timestamp=count > 1,
        same_timestamp_count=count,
    )


def _sort_key(record: _Observation) -> tuple:
    return (record.collected_at_dt is None, record.collected_at_dt or datetime.max, record.excel_row, record.record_id)


def _read_sheet(workbook: ZipFile, sheet_name: str, target: str) -> list[_Observation]:
    records = []
    header: list[str] | None = None
    indexes: dict[str, int] | None = None
    with workbook.open(target) as handle:
        for _, element in ElementTree.iterparse(handle, events=("end",)):
            if element.tag != _ROW:
                continue
            values = {}
            for cell in element.findall(_CELL):
                values[_column_index(cell.get("r") or "A1")] = _cell_text(cell)
            row_number = int(element.get("r") or 0)
            element.clear()
            if header is None:
                width = max(values) + 1 if values else 0
                header = [str(values.get(index) or "").strip() for index in range(width)]
                indexes = _header_indexes(header)
                continue
            assert indexes is not None
            record_id = str(values.get(indexes["记录编号"]) or "").strip()
            device_id = str(values.get(indexes["设备编号"]) or "").strip()
            category = str(values.get(indexes["设备类别"]) or "").strip()
            collected_raw = str(values.get(indexes["采集时间"]) or "").strip()
            if not record_id or not device_id or not category or not collected_raw:
                raise DatasetError(f"{sheet_name} 第 {row_number} 行缺少记录编号、设备编号、设备类别或采集时间。")
            measurements = []
            for name in MEASUREMENT_FIELDS:
                raw = values.get(indexes[name])
                measurements.append((name, None if raw is None or str(raw).strip() == "" else _parse_number(str(raw))))
            records.append(
                _Observation(
                    record_id=record_id,
                    excel_row=row_number,
                    sheet_name=sheet_name,
                    device_id=device_id,
                    equipment_category=category,
                    collected_at=collected_raw,
                    collected_at_dt=_parse_time(collected_raw),
                    values=tuple(measurements),
                    duplicate_timestamp=False,
                    same_timestamp_count=1,
                )
            )
    if header is None:
        raise DatasetError(f"{sheet_name} 没有表头。")
    return records


def _header_indexes(header: list[str]) -> dict[str, int]:
    if len(header) != len(_EXPECTED_FIELDS) or set(header) != _EXPECTED_FIELDS:
        raise DatasetError("官方 Excel 表头与已确认的 34 个字段不一致。")
    if len(header) != len(set(header)):
        raise DatasetError("官方 Excel 表头存在重复字段。")
    return {name: index for index, name in enumerate(header)}


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
        name = sheet.get("name") or ""
        sheets.append((name, target))
    return sheets


def _column_index(cell_ref: str) -> int:
    letters = "".join(char for char in cell_ref if char.isalpha())
    number = 0
    for char in letters:
        number = number * 26 + ord(char.upper()) - 64
    return number - 1


def _cell_text(cell) -> str | None:
    cell_type = cell.get("t")
    if cell_type == "s":
        raise DatasetError("官方 Excel 使用了共享字符串，当前读取程序不能解析。")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(_TEXT))
    value = cell.find(_VALUE)
    if value is None or value.text is None:
        return None
    return value.text


def _parse_number(text: str) -> int | float | None:
    value = text.strip()
    if value.lower() in _NON_FINITE:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    if _INTEGER_TEXT.fullmatch(value):
        return int(value)
    return number


def _parse_time(value: str) -> datetime | None:
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _parse_bound(value: str | datetime | None, name: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        parsed = _parse_time(value.strip())
        if parsed is None:
            raise DatasetError(f"{name} 必须是 YYYY-MM-DD HH:MM:SS。")
        return parsed
    raise DatasetError(f"{name} 必须是 YYYY-MM-DD HH:MM:SS。")


def _normalize_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > MAX_LIMIT:
        raise DatasetError(f"limit 必须是 1 到 {MAX_LIMIT} 的整数。")
    return limit


def _normalize_page(page: int) -> int:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise DatasetError("page 必须是大于等于 1 的整数。")
    return page


def _normalize_page_size(page_size: int) -> int:
    if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size < 1 or page_size > MAX_LIMIT:
        raise DatasetError(f"page_size 必须是 1 到 {MAX_LIMIT} 的整数。")
    return page_size


def _device_identity(device_id: str) -> str:
    return str(device_id or "").strip()


def _in_range(record: _Observation, start: datetime | None, end: datetime | None) -> bool:
    if record.collected_at_dt is None:
        return start is None and end is None
    if start is not None and record.collected_at_dt < start:
        return False
    if end is not None and record.collected_at_dt > end:
        return False
    return True


def _empty_device_result(device_id: str, limit: int) -> dict:
    return {
        "found": False,
        "device_id": device_id,
        "equipment_category": None,
        "sheet_name": None,
        "matched_count": 0,
        "returned_count": 0,
        "limit": limit,
        "truncated": False,
        "duplicate_timestamp_groups": 0,
        "records": [],
    }
