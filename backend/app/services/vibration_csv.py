"""从上传的 CSV 文本中读取一列振动采样。

不打开服务器上的文件路径，也不猜测采样频率或物理单位。
"""

from __future__ import annotations

import csv
import io
import math

from app.services.vibration_analysis import MAX_SAMPLES, MIN_SAMPLES

MAX_CSV_BYTES = 5 * 1024 * 1024


class VibrationCsvError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def read_vibration_column(content: bytes, column: str = "value") -> list[float]:
    """读取 CSV 中的一个数值列。content 必须是上传内容，不是文件路径。"""
    if isinstance(content, (str, bytearray)):
        raise VibrationCsvError("CSV 内容必须是上传的字节，不能是服务器文件路径。")
    if not isinstance(content, bytes):
        raise VibrationCsvError("CSV 内容必须是上传的字节，不能是服务器文件路径。")
    if len(content) == 0:
        raise VibrationCsvError("CSV 文件为空。")
    if len(content) > MAX_CSV_BYTES:
        raise VibrationCsvError("CSV 请求体超过 5MB。", 413)
    if not isinstance(column, str) or not column.strip():
        raise VibrationCsvError("请提供要读取的数值列名。")
    column_name = column.strip()
    try:
        text = bytes(content).decode("utf-8-sig")
    except UnicodeDecodeError:
        raise VibrationCsvError("CSV 必须使用 UTF-8 编码。") from None
    if not text.strip():
        raise VibrationCsvError("CSV 文件为空。")

    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        raise VibrationCsvError("CSV 文件为空。") from None
    names = [cell.strip() for cell in header]
    if not any(names):
        raise VibrationCsvError("CSV 缺少表头。")
    if len(names) != len(set(names)):
        raise VibrationCsvError("CSV 表头存在重复列名。")
    if column_name not in names:
        raise VibrationCsvError(f"CSV 中没有列「{column_name}」。")
    column_index = names.index(column_name)

    values = []
    for row_number, row in enumerate(reader, start=2):
        if not row or all(not cell.strip() for cell in row):
            continue
        if column_index >= len(row) or not row[column_index].strip():
            raise VibrationCsvError(f"第 {row_number} 行的「{column_name}」不是有效数值。")
        cell = row[column_index].strip()
        try:
            number = float(cell)
        except ValueError:
            raise VibrationCsvError(f"第 {row_number} 行的「{column_name}」不是有效数值。") from None
        if not math.isfinite(number):
            raise VibrationCsvError(f"第 {row_number} 行的「{column_name}」包含 NaN 或 Infinity。")
        values.append(number)
        if len(values) > MAX_SAMPLES:
            raise VibrationCsvError(f"样本数量超过 {MAX_SAMPLES}。")
    if len(values) < MIN_SAMPLES:
        raise VibrationCsvError(f"样本数量不足，至少需要 {MIN_SAMPLES} 个采样点。")
    return values


def read_synchronous_csv(content: bytes, time_column: str, channel_columns: list[str]) -> dict:
    """读取同一文件里的共享时间列和多路通道。不把不同文件拼成同步测量。"""
    if isinstance(content, (str, bytearray)) or not isinstance(content, bytes):
        raise VibrationCsvError("CSV 内容必须是上传的字节，不能是服务器文件路径。")
    if len(content) == 0:
        raise VibrationCsvError("CSV 文件为空。")
    if len(content) > MAX_CSV_BYTES:
        raise VibrationCsvError("CSV 请求体超过 5MB。", 413)
    if not isinstance(time_column, str) or not time_column.strip():
        raise VibrationCsvError("缺少同步信息：同步 CSV 必须有共享时间列。")
    if not isinstance(channel_columns, list) or len(channel_columns) < 2:
        raise VibrationCsvError("至少需要两路通道才能讨论通道间相位。")
    names_needed = [time_column.strip(), *[str(name).strip() for name in channel_columns]]
    if any(not name for name in names_needed) or len(names_needed) != len(set(names_needed)):
        raise VibrationCsvError("时间列和通道列名必须各不相同。")
    try:
        text = bytes(content).decode("utf-8-sig")
    except UnicodeDecodeError:
        raise VibrationCsvError("CSV 必须使用 UTF-8 编码。") from None
    reader = csv.reader(io.StringIO(text, newline=""))
    try:
        header = next(reader)
    except StopIteration:
        raise VibrationCsvError("CSV 文件为空。") from None
    header_names = [cell.strip() for cell in header]
    if len(header_names) != len(set(header_names)):
        raise VibrationCsvError("CSV 表头存在重复列名。")
    missing = [name for name in names_needed if name not in header_names]
    if missing:
        raise VibrationCsvError("缺少同步信息：CSV 中没有列 " + "、".join(missing) + "。")
    indexes = {name: header_names.index(name) for name in names_needed}
    time_values: list[float] = []
    channels = {name: [] for name in names_needed[1:]}
    for row_number, row in enumerate(reader, start=2):
        if not row or all(not cell.strip() for cell in row):
            continue
        parsed = {}
        for name, index in indexes.items():
            if index >= len(row) or not row[index].strip():
                raise VibrationCsvError(f"第 {row_number} 行的「{name}」为空，各通道长度不一致。")
            try:
                number = float(row[index].strip())
            except ValueError:
                raise VibrationCsvError(f"第 {row_number} 行的「{name}」不是有效数值。") from None
            if not math.isfinite(number):
                raise VibrationCsvError(f"第 {row_number} 行的「{name}」包含 NaN 或 Infinity。")
            parsed[name] = number
        time_values.append(parsed[names_needed[0]])
        for name in names_needed[1:]:
            channels[name].append(parsed[name])
        if len(time_values) > MAX_SAMPLES:
            raise VibrationCsvError(f"样本数量超过 {MAX_SAMPLES}。")
    if len(time_values) < MIN_SAMPLES:
        raise VibrationCsvError(f"样本数量不足，至少需要 {MIN_SAMPLES} 个采样点。")
    return {"time_s": time_values, "channels": channels}
