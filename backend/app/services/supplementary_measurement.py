"""把额外振动测量关联到已有记录，并算出现有算法能够支持的指标。

不修改官方 Excel，不把补充信号写成故障确诊，也不合并无法对应的来源。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from app.services.official_dataset import DatasetError, get_observation
from app.services.vibration_analysis import analyze_vibration
from app.services.vibration_csv import VibrationCsvError, read_synchronous_csv, read_vibration_column
from app.services.vibration_phase import interchannel_phase, rotation_target_hz

_RECORD_ID = re.compile(r"REC-[A-Za-z0-9-]+")
_UNITS = {
    "mm/s": "velocity",
    "m/s": "velocity",
    "g": "acceleration",
    "m/s^2": "acceleration",
    "m/s²": "acceleration",
    "mm/s^2": "acceleration",
    "mm/s²": "acceleration",
    "um": "displacement",
    "μm": "displacement",
    "mil": "displacement",
}
_ORIGINS = {
    "synthetic_test_signal": "合成测试信号，只用于验证程序，不是工厂测量。",
    "uploaded_csv": "上传的补充测量，不是官方 Excel 观测，不能当作已确认的工厂记录。",
}
_SYNC_METHODS = {"shared_time_column": "同一 CSV 的共享时间列。不同文件没有被当成同步测量。"}
_TIME_BASES = {"relative_seconds": "时间列是从 0 秒开始的相对秒，不是另一套绝对时钟。"}
_CREATE_SYNC = """
CREATE TABLE IF NOT EXISTS synchronous_vibration_measurements (
    measurement_id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    measured_at TEXT NOT NULL,
    sampling_rate_hz REAL NOT NULL,
    unit TEXT NOT NULL,
    file_name TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    channel_count INTEGER NOT NULL,
    data_origin TEXT NOT NULL,
    origin_note TEXT NOT NULL,
    sync_method TEXT NOT NULL,
    time_base TEXT NOT NULL,
    time_start_s REAL NOT NULL,
    time_step_s REAL NOT NULL,
    channels_json TEXT NOT NULL,
    phase_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (record_id, content_sha256)
)
"""
_CREATE_REASSESSMENT = """
CREATE TABLE IF NOT EXISTS fault_reassessments (
    record_id TEXT PRIMARY KEY,
    hypotheses_json TEXT NOT NULL,
    physics_json TEXT NOT NULL,
    stop_reason TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""
_CREATE = """
CREATE TABLE IF NOT EXISTS supplementary_measurements (
    measurement_id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    measured_at TEXT NOT NULL,
    sensor_position TEXT NOT NULL,
    sampling_rate_hz REAL NOT NULL,
    unit TEXT NOT NULL,
    file_name TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    data_origin TEXT NOT NULL,
    origin_note TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (record_id, content_sha256)
)
"""


class SupplementaryMeasurementError(Exception):
    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def database_path() -> Path:
    override = os.environ.get("SUPPLEMENTARY_MEASUREMENT_DB", "").strip()
    root = Path(__file__).resolve().parents[3]
    path = Path(override) if override else root / "data" / "runtime" / "supplementary_measurements.sqlite3"
    return _assert_runtime_location(path)


def attach_vibration_csv(
    record_id: str,
    device_id: str,
    measured_at: str,
    sensor_position: str,
    sampling_rate_hz: float,
    unit: str,
    file_name: str,
    content: bytes,
    *,
    column: str = "value",
    data_origin: str,
) -> dict:
    """关联一份振动 CSV。设备、时间或单位对不上时不保存，也不改官方记录。"""
    try:
        record = _official_record(record_id)
        meta = _metadata(record, device_id, measured_at, sensor_position, sampling_rate_hz, unit, file_name, data_origin)
        samples = read_vibration_column(content, column)
        analysis = analyze_vibration(samples, meta["sampling_rate_hz"], meta["unit"])
    except SupplementaryMeasurementError as exc:
        return _rejected(exc.message, exc.status_code)
    except VibrationCsvError as exc:
        return _rejected(exc.message, exc.status_code)
    except DatasetError as exc:
        return _rejected(exc.message, 422)
    if analysis.get("ok") is not True:
        return _rejected(str(analysis.get("error") or "振动数据无法分析。"), 422)
    digest = hashlib.sha256(content).hexdigest()
    metrics = _metrics(analysis, meta["unit"])
    path = database_path()
    connection = _connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT * FROM supplementary_measurements WHERE record_id = ? AND content_sha256 = ?",
            (record["record_id"], digest),
        ).fetchone()
        if existing is not None:
            connection.execute("ROLLBACK")
            return {
                "ok": True,
                "usable": True,
                "stored": False,
                "duplicate": True,
                "official_excel_modified": False,
                "written_to_graph": False,
                "measurement": _public_row(existing),
            }
        measurement_id = uuid.uuid4().hex
        created_at = datetime.now().astimezone().isoformat(timespec="seconds")
        connection.execute(
            """
            INSERT INTO supplementary_measurements (
                measurement_id, record_id, device_id, measured_at, sensor_position,
                sampling_rate_hz, unit, file_name, content_sha256, sample_count,
                data_origin, origin_note, metrics_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                measurement_id,
                record["record_id"],
                record["device_id"],
                record["collected_at"],
                meta["sensor_position"],
                meta["sampling_rate_hz"],
                meta["unit"],
                meta["file_name"],
                digest,
                int(analysis["sample_count"]),
                meta["data_origin"],
                meta["origin_note"],
                json.dumps(metrics, ensure_ascii=False, separators=(",", ":")),
                created_at,
            ),
        )
        connection.execute("COMMIT")
        stored = connection.execute(
            "SELECT * FROM supplementary_measurements WHERE measurement_id = ?",
            (measurement_id,),
        ).fetchone()
    except sqlite3.IntegrityError:
        _rollback(connection)
        return _rejected("相同内容已经关联到这条记录，没有重复保存。", 409)
    except sqlite3.Error as exc:
        _rollback(connection)
        raise SupplementaryMeasurementError(f"补充测量没有写入数据库：{exc}") from exc
    finally:
        connection.close()
    return {
        "ok": True,
        "usable": True,
        "stored": True,
        "duplicate": False,
        "official_excel_modified": False,
        "written_to_graph": False,
        "measurement": _public_row(stored),
    }


def attach_synchronous_csv(
    record_id: str,
    device_id: str,
    measured_at: str,
    sampling_rate_hz: float,
    unit: str,
    file_name: str,
    content: bytes,
    channels: list[dict],
    *,
    sync_method: str,
    time_base: str,
    data_origin: str,
) -> dict:
    """关联一份带共享时间列的多通道 CSV。不把单通道或不同文件算成同步相位。"""
    try:
        record = _official_record(record_id)
        meta = _sync_metadata(record, device_id, measured_at, sampling_rate_hz, unit, file_name, sync_method, time_base, data_origin, channels)
        table = read_synchronous_csv(content, "time_s", [item["column"] for item in meta["channels"]])
        timing = _shared_timing(table["time_s"], meta["sampling_rate_hz"])
        prepared = _prepare_channels(table["channels"], meta["channels"], meta["unit"], meta["sampling_rate_hz"])
        phase = _phase_result(record, prepared, meta["sampling_rate_hz"])
        stored_channels = [{key: value for key, value in item.items() if key != "samples"} for item in prepared]
    except SupplementaryMeasurementError as exc:
        return _rejected(exc.message, exc.status_code)
    except VibrationCsvError as exc:
        return _rejected(exc.message, exc.status_code)
    except DatasetError as exc:
        return _rejected(exc.message, 422)
    digest = hashlib.sha256(content).hexdigest()
    path = database_path()
    connection = _connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT * FROM synchronous_vibration_measurements WHERE record_id = ? AND content_sha256 = ?",
            (record["record_id"], digest),
        ).fetchone()
        if existing is not None:
            connection.execute("ROLLBACK")
            return {
                "ok": True,
                "usable": phase.get("phase_reliable") is True,
                "stored": False,
                "duplicate": True,
                "channel_mode": "synchronous",
                "official_excel_modified": False,
                "written_to_graph": False,
                "confirmed": False,
                "measurement": _public_sync_row(existing),
            }
        measurement_id = uuid.uuid4().hex
        created_at = datetime.now().astimezone().isoformat(timespec="seconds")
        connection.execute(
            """
            INSERT INTO synchronous_vibration_measurements (
                measurement_id, record_id, device_id, measured_at, sampling_rate_hz, unit, file_name,
                content_sha256, sample_count, channel_count, data_origin, origin_note, sync_method,
                time_base, time_start_s, time_step_s, channels_json, phase_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                measurement_id,
                record["record_id"],
                record["device_id"],
                record["collected_at"],
                meta["sampling_rate_hz"],
                meta["unit"],
                meta["file_name"],
                digest,
                len(table["time_s"]),
                len(prepared),
                meta["data_origin"],
                meta["origin_note"],
                meta["sync_method"],
                meta["time_base"],
                timing["start_s"],
                timing["step_s"],
                json.dumps(stored_channels, ensure_ascii=False, separators=(",", ":")),
                json.dumps(phase, ensure_ascii=False, separators=(",", ":")),
                created_at,
            ),
        )
        connection.execute("COMMIT")
        stored = connection.execute(
            "SELECT * FROM synchronous_vibration_measurements WHERE measurement_id = ?",
            (measurement_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        _rollback(connection)
        raise SupplementaryMeasurementError(f"同步振动测量没有写入数据库：{exc}") from exc
    finally:
        connection.close()
    return {
        "ok": True,
        "usable": phase.get("phase_reliable") is True,
        "stored": True,
        "duplicate": False,
        "channel_mode": "synchronous",
        "official_excel_modified": False,
        "written_to_graph": False,
        "confirmed": False,
        "measurement": _public_sync_row(stored),
    }


def list_synchronous_for_record(record_id: str) -> list[dict]:
    identity = record_id.strip() if isinstance(record_id, str) else ""
    path = database_path()
    if not identity or not path.is_file():
        return []
    connection = _connect(path)
    try:
        rows = connection.execute(
            "SELECT * FROM synchronous_vibration_measurements WHERE record_id = ? ORDER BY created_at, measurement_id",
            (identity,),
        ).fetchall()
    finally:
        connection.close()
    return [_public_sync_row(row) for row in rows]


def list_for_record(record_id: str) -> list[dict]:
    identity = record_id.strip() if isinstance(record_id, str) else ""
    path = database_path()
    if not identity or not path.is_file():
        return []
    connection = _connect(path)
    try:
        rows = connection.execute(
            "SELECT * FROM supplementary_measurements WHERE record_id = ? ORDER BY created_at, measurement_id",
            (identity,),
        ).fetchall()
    finally:
        connection.close()
    return [_public_row(row) for row in rows]


def evidence_for_record(record: dict) -> dict:
    """只读取已保存的补充测量。没有数据库文件时不创建文件。"""
    empty = {"measurements": [], "checks": [], "resolved_missing": []}
    if not isinstance(record, dict):
        return empty
    identity = str(record.get("record_id") or "").strip()
    path = database_path()
    if not identity or not path.is_file():
        return empty
    connection = _connect(path)
    try:
        rows = connection.execute(
            "SELECT * FROM supplementary_measurements WHERE record_id = ? ORDER BY created_at, measurement_id",
            (identity,),
        ).fetchall()
        sync_rows = connection.execute(
            "SELECT * FROM synchronous_vibration_measurements WHERE record_id = ? ORDER BY created_at, measurement_id",
            (identity,),
        ).fetchall()
    finally:
        connection.close()
    measurements = [_public_row(row) for row in rows]
    synchronous = [_public_sync_row(row) for row in sync_rows]
    if not measurements and not synchronous:
        return empty
    checks, resolved = _checks(record, measurements) if measurements else ([], [])
    checks = [item for item in checks if item.get("check_id") != "supplement_phase"]
    checks.extend(_phase_checks(measurements, synchronous))
    return {
        "measurements": measurements,
        "synchronous_measurements": synchronous,
        "checks": checks,
        "resolved_missing": resolved,
    }


def load_reassessment_state(record_id: str) -> dict | None:
    identity = record_id.strip() if isinstance(record_id, str) else ""
    path = database_path()
    if not identity or not path.is_file():
        return None
    connection = _connect(path)
    try:
        row = connection.execute(
            "SELECT hypotheses_json, physics_json, stop_reason FROM fault_reassessments WHERE record_id = ?",
            (identity,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    hypotheses = json.loads(row["hypotheses_json"])
    physics = json.loads(row["physics_json"])
    return {
        "hypotheses": hypotheses if isinstance(hypotheses, list) else [],
        "physics": physics if isinstance(physics, dict) else {},
        "stop_reason": row["stop_reason"],
    }


def save_reassessment_state(record_id: str, hypotheses: list, physics: dict, stop_reason: str) -> None:
    identity = record_id.strip() if isinstance(record_id, str) else ""
    if not identity:
        return
    connection = _connect(database_path())
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO fault_reassessments (record_id, hypotheses_json, physics_json, stop_reason, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(record_id) DO UPDATE SET
                hypotheses_json = excluded.hypotheses_json,
                physics_json = excluded.physics_json,
                stop_reason = excluded.stop_reason,
                updated_at = excluded.updated_at
            """,
            (
                identity,
                json.dumps(hypotheses, ensure_ascii=False, separators=(",", ":")),
                json.dumps(physics, ensure_ascii=False, separators=(",", ":")),
                stop_reason,
                datetime.now().astimezone().isoformat(timespec="seconds"),
            ),
        )
        connection.execute("COMMIT")
    except sqlite3.Error as exc:
        _rollback(connection)
        raise SupplementaryMeasurementError(f"故障重评估状态没有写入：{exc}") from exc
    finally:
        connection.close()


def revalidate_record(record_id: str) -> dict:
    """用当前补充测量重新执行记录级 L2。不写图谱，也不把结果升级为确诊。"""
    from app.services.knowledge_physical_validator import validate_record_physics

    report = validate_record_physics(record_id)
    return {
        "ok": True,
        "revalidated": True,
        "confirmed": False,
        "written_to_graph": False,
        "causal_conclusion": False,
        "official_excel_modified": False,
        "report": report,
    }


def _official_record(record_id: str) -> dict:
    identity = record_id.strip() if isinstance(record_id, str) else ""
    if _RECORD_ID.fullmatch(identity) is None:
        raise SupplementaryMeasurementError("记录编号格式不正确。")
    found = get_observation(identity)
    record = found.get("record") if isinstance(found, dict) else None
    if not isinstance(record, dict):
        raise SupplementaryMeasurementError(f"没有找到记录编号「{identity}」，不能把测量关联到其他记录。")
    return record


def _metadata(record: dict, device_id: str, measured_at: str, sensor_position: str, sampling_rate_hz: float, unit: str, file_name: str, data_origin: str) -> dict:
    device = device_id.strip() if isinstance(device_id, str) else ""
    if device != str(record.get("device_id") or ""):
        raise SupplementaryMeasurementError("设备编号与这条记录不一致，不能合并观测。")
    measured = measured_at.strip() if isinstance(measured_at, str) else ""
    if measured != str(record.get("collected_at") or ""):
        raise SupplementaryMeasurementError("测量时间与记录采集时间不一致，不能合并观测。")
    position = _plain_label(sensor_position, "传感器位置")
    rate = _rate(sampling_rate_hz)
    text_unit = unit.strip() if isinstance(unit, str) else ""
    if text_unit not in _UNITS:
        raise SupplementaryMeasurementError("单位不能与振动波形对应，不能把这份数据并入记录。")
    name = _plain_label(file_name, "文件来源")
    if data_origin not in _ORIGINS:
        raise SupplementaryMeasurementError("数据来源只能标明为合成测试信号或上传的 CSV。")
    return {
        "sensor_position": position,
        "sampling_rate_hz": rate,
        "unit": text_unit,
        "file_name": name,
        "data_origin": data_origin,
        "origin_note": _ORIGINS[data_origin],
    }


def _sync_metadata(
    record: dict,
    device_id: str,
    measured_at: str,
    sampling_rate_hz: float,
    unit: str,
    file_name: str,
    sync_method: str,
    time_base: str,
    data_origin: str,
    channels: list[dict],
) -> dict:
    base = _metadata(record, device_id, measured_at, "同步多通道", sampling_rate_hz, unit, file_name, data_origin)
    method = sync_method.strip() if isinstance(sync_method, str) else ""
    if method not in _SYNC_METHODS:
        raise SupplementaryMeasurementError("缺少同步信息：只接受同一 CSV 的共享时间列，不能把不同文件当成同步测量。")
    base_name = time_base.strip() if isinstance(time_base, str) else ""
    if base_name not in _TIME_BASES:
        raise SupplementaryMeasurementError("缺少同步信息：时间基准只能是 relative_seconds。")
    if not isinstance(channels, list) or len(channels) < 2:
        raise SupplementaryMeasurementError("至少需要两路通道才能讨论通道间相位。")
    prepared = []
    seen_columns = set()
    for index, item in enumerate(channels, start=1):
        if not isinstance(item, dict):
            raise SupplementaryMeasurementError(f"第 {index} 路通道描述无效。")
        column = _plain_label(item.get("column"), f"第 {index} 路通道列名")
        if column == "time_s" or column in seen_columns:
            raise SupplementaryMeasurementError("时间列和通道列名必须各不相同。")
        seen_columns.add(column)
        prepared.append(
            {
                "column": column,
                "name": _plain_label(item.get("name") or column, f"第 {index} 路通道名称"),
                "direction": _plain_label(item.get("direction") or "未标注", f"第 {index} 路通道方向"),
                "sensor_position": _plain_label(item.get("sensor_position") or item.get("name") or column, f"第 {index} 路传感器位置"),
            }
        )
    return {
        **base,
        "sync_method": method,
        "sync_method_note": _SYNC_METHODS[method],
        "time_base": base_name,
        "time_base_note": _TIME_BASES[base_name],
        "channels": prepared,
    }


def _shared_timing(time_s: list[float], sampling_rate_hz: float) -> dict:
    if len(time_s) < 2:
        raise SupplementaryMeasurementError("时间列太短，不能核对同步采样。")
    start = time_s[0]
    if abs(start) > 1e-6:
        raise SupplementaryMeasurementError("缺少同步信息：相对时间列应从接近 0 秒开始。")
    steps = [time_s[index] - time_s[index - 1] for index in range(1, len(time_s))]
    if any(step <= 0 for step in steps):
        raise SupplementaryMeasurementError("时间列不是严格递增，不能作为同步时间基准。")
    expected = 1.0 / sampling_rate_hz
    tolerance = max(expected * 1e-6, 1e-9)
    if any(abs(step - expected) > tolerance for step in steps):
        raise SupplementaryMeasurementError("时间列步长与声明的采样率不一致，不能直接计算相位差。")
    if any(abs(step - steps[0]) > tolerance for step in steps):
        raise SupplementaryMeasurementError("各采样点时间间隔不一致，不能直接计算相位差。")
    return {"start_s": start, "step_s": steps[0], "sample_count": len(time_s)}


def _prepare_channels(raw: dict, channels: list[dict], unit: str, sampling_rate_hz: float) -> list[dict]:
    prepared = []
    for item in channels:
        samples = raw.get(item["column"])
        if not isinstance(samples, list):
            raise SupplementaryMeasurementError(f"通道「{item['column']}」没有读到样本。")
        analysis = analyze_vibration(samples, sampling_rate_hz, unit)
        if analysis.get("ok") is not True:
            raise SupplementaryMeasurementError(str(analysis.get("error") or f"通道「{item['name']}」无法分析。"))
        prepared.append(
            {
                "column": item["column"],
                "name": item["name"],
                "direction": item["direction"],
                "sensor_position": item["sensor_position"],
                "sample_count": len(samples),
                "samples": samples,
                "metrics": _metrics(analysis, unit),
            }
        )
    lengths = {item["sample_count"] for item in prepared}
    if len(lengths) != 1:
        raise SupplementaryMeasurementError("各通道样本长度不同，不能计算通道间相位差。")
    return prepared


def _phase_result(record: dict, prepared: list[dict], sampling_rate_hz: float) -> dict:
    values = record.get("values") if isinstance(record.get("values"), dict) else {}
    target, reason = rotation_target_hz(values)
    if target is None:
        empty = interchannel_phase([], [], sampling_rate_hz, 0.0, prepared[0]["name"], prepared[1]["name"])
        empty["reason"] = reason or "没有可用的 1X 频率。"
        empty["channels"] = [
            {"name": item["name"], "frequency_1x_hz": None, "amplitude_1x": None, "reliable_1x": False}
            for item in prepared[:2]
        ]
        return empty
    first = prepared[0]
    second = prepared[1]
    result = interchannel_phase(first["samples"], second["samples"], sampling_rate_hz, target, first["name"], second["name"])
    result["channel_count"] = len(prepared)
    result["extra_channels_ignored"] = [item["name"] for item in prepared[2:]]
    return result


def _public_sync_row(row: sqlite3.Row) -> dict:
    channels = json.loads(row["channels_json"])
    phase = json.loads(row["phase_json"])
    public_channels = []
    for item in channels if isinstance(channels, list) else []:
        if not isinstance(item, dict):
            continue
        metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
        public_channels.append(
            {
                "column": item.get("column") or "",
                "name": item.get("name") or "",
                "direction": item.get("direction") or "",
                "sensor_position": item.get("sensor_position") or "",
                "sample_count": item.get("sample_count"),
                "metrics": {
                    "frequency_resolution_hz": metrics.get("frequency_resolution_hz"),
                    "rms": metrics.get("rms"),
                    "crest_factor": metrics.get("crest_factor"),
                    "kurtosis_pearson": metrics.get("kurtosis_pearson"),
                    "peaks": metrics.get("peaks") or [],
                },
            }
        )
    return {
        "measurement_id": row["measurement_id"],
        "record_id": row["record_id"],
        "device_id": row["device_id"],
        "measured_at": row["measured_at"],
        "sampling_rate_hz": row["sampling_rate_hz"],
        "unit": row["unit"],
        "file_name": row["file_name"],
        "content_sha256": row["content_sha256"],
        "sample_count": row["sample_count"],
        "channel_count": row["channel_count"],
        "data_origin": row["data_origin"],
        "origin_note": row["origin_note"],
        "sync_method": row["sync_method"],
        "sync_method_note": _SYNC_METHODS.get(row["sync_method"], ""),
        "time_base": row["time_base"],
        "time_base_note": _TIME_BASES.get(row["time_base"], ""),
        "time_start_s": row["time_start_s"],
        "time_step_s": row["time_step_s"],
        "channels": public_channels,
        "phase": phase if isinstance(phase, dict) else {},
        "channel_mode": "synchronous",
        "created_at": row["created_at"],
        "official_excel_modified": False,
        "written_to_graph": False,
        "confirmed": False,
        "supports_fault": False,
    }


def _plain_label(value: object, label: str) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text or len(text) > 120:
        raise SupplementaryMeasurementError(f"请提供{label}。")
    if "/" in text or "\\" in text or ".." in text or "\x00" in text:
        raise SupplementaryMeasurementError(f"{label}只能是名称，不能是服务器路径。")
    return text


def _rate(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SupplementaryMeasurementError("采样频率必须是大于 0 的数值，单位是 Hz。")
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")} or number <= 0:
        raise SupplementaryMeasurementError("采样频率必须是大于 0 的有限数值，单位是 Hz。")
    return number


def _metrics(analysis: dict, unit: str) -> dict:
    spectrum = analysis.get("fft") if isinstance(analysis.get("fft"), dict) else {}
    peaks = []
    for item in spectrum.get("peaks") or []:
        if not isinstance(item, dict):
            continue
        peaks.append(
            {
                "frequency_hz": item.get("frequency_hz"),
                "amplitude": item.get("amplitude"),
                "unit": unit,
                "algorithm": "analyze_vibration",
            }
        )
    return {
        "algorithm": "analyze_vibration",
        "algorithm_note": "复用现有时域指标和单边谱峰值。没有另写故障规则。",
        "unit": unit,
        "quantity": _UNITS.get(unit, ""),
        "sampling_rate_hz": analysis.get("sampling_rate_hz"),
        "frequency_resolution_hz": spectrum.get("frequency_resolution_hz"),
        "sample_count": analysis.get("sample_count"),
        "rms": analysis.get("rms"),
        "crest_factor": analysis.get("crest_factor"),
        "kurtosis_pearson": analysis.get("kurtosis_pearson"),
        "kurtosis_type": analysis.get("kurtosis_type"),
        "peaks": peaks,
        "channel_count": 1,
        "phase_difference": None,
        "bearing_harmonics": None,
        "compared_with_official_amplitudes": False,
    }


def _checks(record: dict, measurements: list[dict]) -> tuple[list[dict], list[str]]:
    from app.services.knowledge_physical_validator import _check

    values = record.get("values") if isinstance(record.get("values"), dict) else {}
    rotation = _number(values.get("转频(Hz)"))
    evaluations = [_evaluate(item, rotation) for item in measurements]
    checks = [
        _waveform_check(_check, evaluations),
        _peak_check(_check, evaluations),
        _dominant_check(_check, evaluations, rotation),
        _ratio_check(_check, evaluations, rotation),
        _phase_check(_check, measurements),
        _bearing_check(_check, measurements),
        _isolation_check(_check, record, measurements),
    ]
    resolved = []
    if all(item["crest_factor"] is not None and item["kurtosis_pearson"] is not None for item in evaluations):
        resolved.extend(["原始振动波形", "峭度", "峰值因子"])
    if rotation is not None and all(item["amplitude_2x"] is not None for item in evaluations):
        resolved.append("2X幅值")
    if rotation is not None and all(item["dominant_is_1x"] is not None for item in evaluations):
        resolved.append("1X是否为频谱主导峰")
    return checks, resolved


def _evaluate(measurement: dict, rotation: float | None) -> dict:
    metrics = measurement.get("metrics") if isinstance(measurement.get("metrics"), dict) else {}
    peaks = [item for item in metrics.get("peaks") or [] if isinstance(item, dict)]
    resolution = _number(metrics.get("frequency_resolution_hz"))
    first = _match_peak(peaks, rotation, resolution)
    second = _match_peak(peaks, None if rotation is None else rotation * 2, resolution)
    dominant = peaks[0] if peaks else None
    dominant_is_1x = None
    if rotation is not None and resolution is not None and dominant is not None:
        dominant_frequency = _number(dominant.get("frequency_hz"))
        dominant_is_1x = dominant_frequency is not None and abs(dominant_frequency - rotation) <= resolution / 2
    amplitude_1x = _number(first.get("amplitude")) if isinstance(first, dict) else None
    amplitude_2x = _number(second.get("amplitude")) if isinstance(second, dict) else None
    ratio = amplitude_2x / amplitude_1x if amplitude_1x not in (None, 0) and amplitude_2x is not None else None
    return {
        "measurement_id": measurement.get("measurement_id"),
        "content_sha256": measurement.get("content_sha256"),
        "file_name": measurement.get("file_name"),
        "sensor_position": measurement.get("sensor_position"),
        "unit": measurement.get("unit"),
        "data_origin": measurement.get("data_origin"),
        "origin_note": measurement.get("origin_note"),
        "resolution_hz": resolution,
        "peaks": peaks,
        "crest_factor": _number(metrics.get("crest_factor")),
        "kurtosis_pearson": _number(metrics.get("kurtosis_pearson")),
        "frequency_1x_hz": None if not isinstance(first, dict) else first.get("frequency_hz"),
        "amplitude_1x": amplitude_1x,
        "frequency_2x_hz": None if not isinstance(second, dict) else second.get("frequency_hz"),
        "amplitude_2x": amplitude_2x,
        "ratio_2x_to_1x": ratio,
        "dominant_frequency_hz": None if dominant is None else dominant.get("frequency_hz"),
        "dominant_amplitude": None if dominant is None else dominant.get("amplitude"),
        "dominant_is_1x": dominant_is_1x,
    }


def _match_peak(peaks: list[dict], target: float | None, resolution: float | None) -> dict | None:
    if target is None or resolution is None or resolution <= 0:
        return None
    tolerance = resolution / 2
    found = None
    distance = None
    for peak in peaks:
        frequency = _number(peak.get("frequency_hz"))
        if frequency is None:
            continue
        delta = abs(frequency - target)
        if delta <= tolerance and (distance is None or delta < distance):
            found = peak
            distance = delta
    return found


def _waveform_check(check, evaluations: list[dict]) -> dict:
    ready = [item for item in evaluations if item["crest_factor"] is not None and item["kurtosis_pearson"] is not None]
    if len(ready) != len(evaluations):
        return _annotate(
            check(
                "supplement_waveform_statistics",
                "补充波形的峭度与峰值因子",
                "insufficient_data",
                {},
                _basis(evaluations),
                "现有算法没有同时给出峭度和峰值因子，不能编造统计特征。",
            ),
            evaluations,
            "insufficient",
        )
    return _annotate(
        check(
            "supplement_waveform_statistics",
            "补充波形的峭度与峰值因子",
            "consistent",
            {},
            _basis(evaluations),
            "Pearson 峭度和峰值因子来自补充波形。这里没有故障阈值，不能确诊。",
            computed={"values_merged": False, "measurements": [_stats(item) for item in ready]},
        ),
        evaluations,
        "supports_condition",
    )


def _peak_check(check, evaluations: list[dict]) -> dict:
    if any(not item["peaks"] for item in evaluations):
        return _annotate(
            check(
                "supplement_frequency_peaks",
                "补充信号的频率峰",
                "insufficient_data",
                {},
                _basis(evaluations),
                "没有报告频率峰时，不能编造频谱峰值。",
            ),
            evaluations,
            "insufficient",
        )
    return _annotate(
        check(
            "supplement_frequency_peaks",
            "补充信号的频率峰",
            "consistent",
            {},
            _basis(evaluations),
            "频率峰来自 analyze_vibration 的单边谱。峰值不是故障名称。",
            computed={"values_merged": False, "measurements": [_peak_summary(item) for item in evaluations]},
        ),
        evaluations,
        "supports_condition",
    )


def _dominant_check(check, evaluations: list[dict], rotation: float | None) -> dict:
    if rotation is None or any(item["dominant_is_1x"] is None for item in evaluations):
        return _annotate(
            check(
                "supplement_dominant_1x",
                "补充信号的1X主导峰",
                "insufficient_data",
                {"转频(Hz)": rotation},
                _basis(evaluations),
                "没有转频或主峰时，不能判断 1X 是否为主导峰。",
            ),
            evaluations,
            "insufficient",
        )
    flags = {item["dominant_is_1x"] for item in evaluations}
    if len(flags) != 1:
        return _annotate(
            check(
                "supplement_dominant_1x",
                "补充信号的1X主导峰",
                "insufficient_data",
                {"转频(Hz)": rotation},
                _basis(evaluations),
                "多份补充测量对主导峰的结论不一致，没有合并，也不能据此确诊。",
                computed={"values_merged": False, "measurements": [_dominant_summary(item) for item in evaluations]},
            ),
            evaluations,
            "insufficient",
        )
    matched = flags.pop() is True
    return _annotate(
        check(
            "supplement_dominant_1x",
            "补充信号的1X主导峰",
            "consistent" if matched else "contradicted",
            {"转频(Hz)": rotation},
            _basis(evaluations),
            "主峰靠近转频。这只支持「1X 是主导峰」这一数值条件，不能确认故障。"
            if matched
            else "主峰不在转频附近。这反对「1X 是主导峰」，不是另一种故障的确诊。",
            computed={"values_merged": False, "measurements": [_dominant_summary(item) for item in evaluations]},
        ),
        evaluations,
        "supports_condition" if matched else "opposes_condition",
    )


def _ratio_check(check, evaluations: list[dict], rotation: float | None) -> dict:
    if rotation is None or any(item["ratio_2x_to_1x"] is None for item in evaluations):
        return _annotate(
            check(
                "supplement_second_harmonic_ratio",
                "补充信号的2X与1X比值",
                "insufficient_data",
                {"转频(Hz)": rotation},
                _basis(evaluations),
                "没有同时找到 1X 和 2X 峰时，不能编造 2X 幅值或比值。",
            ),
            evaluations,
            "insufficient",
        )
    return _annotate(
        check(
            "supplement_second_harmonic_ratio",
            "补充信号的2X与1X比值",
            "consistent",
            {"转频(Hz)": rotation},
            _basis(evaluations),
            "2X/1X 只在同一补充信号内计算。没有故障阈值，也不能和另一种物理量的幅值比较。",
            computed={"values_merged": False, "measurements": [_ratio_summary(item) for item in evaluations]},
        ),
        evaluations,
        "supports_condition",
    )


def _phase_checks(measurements: list[dict], synchronous: list[dict]) -> list[dict]:
    from app.services.knowledge_physical_validator import _check

    if not synchronous:
        return [_phase_check(_check, measurements)]
    phase = _sync_phase_payload(synchronous)
    limit = phase["reason"]
    shared = {
        "channel_mode": "synchronous",
        "clause_satisfied": False,
        "reference_matches_clause": False,
        "supports_fault": False,
        "values_merged": False,
        "measurements": phase["measurements"],
        **{key: phase[key] for key in (
            "interchannel_phase_computed",
            "phase_attempted",
            "phase_reliable",
            "phase_difference_deg",
            "absolute_phase_deg",
            "keyphasor_present",
            "keyphasor_valid",
            "reference_type",
            "kb02_condition",
            "kb02_line",
            "reason",
        )},
    }
    basis = "；".join(
        f"同步测量 {item['measurement_id']}，SHA-256 {item['content_sha256']}，{item['origin_note']}"
        for item in synchronous
    )
    source_rows = synchronous
    clause = _annotate(
        _check(
            "supplement_phase",
            "补充信号的相位",
            "insufficient_data",
            {"kb02_condition": phase["kb02_condition"], "kb02_line": phase["kb02_line"]},
            basis,
            limit,
            computed=shared,
        ),
        source_rows,
        "insufficient",
    )
    if phase.get("interchannel_phase_computed") is True:
        evidence_status = "consistent"
        evidence_effect = "supports_condition"
        evidence_limit = (
            f"已算出通道间 1X 相位差 {phase.get('phase_difference_deg')}°。"
            "这不是键相绝对相位，也不能把 KB-02「1X相位稳定，幅值随负荷变化」标成已满足。"
        )
    else:
        evidence_status = "insufficient_data"
        evidence_effect = "insufficient"
        evidence_limit = limit
    evidence = _annotate(
        _check(
            "supplement_interchannel_phase",
            "同步通道的 1X 相位差",
            evidence_status,
            {"from_channel": phase.get("from_channel"), "to_channel": phase.get("to_channel")},
            basis,
            evidence_limit,
            computed=shared,
        ),
        source_rows,
        evidence_effect,
    )
    return [clause, evidence]


def _sync_phase_payload(synchronous: list[dict]) -> dict:
    rows = []
    for item in synchronous:
        phase = item.get("phase") if isinstance(item.get("phase"), dict) else {}
        rows.append(
            {
                "measurement_id": item.get("measurement_id"),
                "content_sha256": item.get("content_sha256"),
                "phase_difference_deg": phase.get("phase_difference_deg"),
                "phase_reliable": phase.get("phase_reliable") is True,
                "reason": phase.get("reason") or "",
                "channels": item.get("channels") or [],
            }
        )
    reliable = [item for item in synchronous if isinstance(item.get("phase"), dict) and item["phase"].get("phase_reliable") is True]
    chosen = reliable[0]["phase"] if len(reliable) == 1 else {}
    if len(synchronous) > 1 and len(reliable) != 1:
        reason = "多份同步测量没有合并成一个相位差。KB-02 相位条款仍然不能标成已满足。"
        computed = False
        difference = None
    elif chosen:
        reason = str(chosen.get("reason") or "缺少键相参考")
        computed = True
        difference = chosen.get("phase_difference_deg")
    else:
        reason = str((synchronous[0].get("phase") or {}).get("reason") or "没有有效 1X 峰")
        computed = False
        difference = None
    base = reliable[0]["phase"] if len(reliable) == 1 else (synchronous[0].get("phase") or {})
    return {
        "interchannel_phase_computed": computed,
        "phase_attempted": True,
        "phase_reliable": computed,
        "phase_difference_deg": difference,
        "absolute_phase_deg": None,
        "from_channel": base.get("from_channel"),
        "to_channel": base.get("to_channel"),
        "keyphasor_present": False,
        "keyphasor_valid": False,
        "reference_type": "interchannel",
        "kb02_condition": base.get("kb02_condition") or "1X相位稳定，幅值随负荷变化",
        "kb02_line": base.get("kb02_line") or 36,
        "reason": reason,
        "measurements": rows,
    }


def _phase_check(check, measurements: list[dict]) -> dict:
    return _annotate(
        check(
            "supplement_phase",
            "补充信号的相位",
            "insufficient_data",
            {"channel_count": 1},
            _basis_from_measurements(measurements),
            "只有一个通道，不能计算相位差，也不能编造相位稳定。",
        ),
        measurements,
        "insufficient",
    )


def _bearing_check(check, measurements: list[dict]) -> dict:
    return _annotate(
        check(
            "supplement_bearing_harmonics",
            "补充信号的轴承谐波",
            "insufficient_data",
            {},
            _basis_from_measurements(measurements),
            "现有算法没有轴承几何参数和谐波族判定，不能由频率峰编造 BPFO 谐波。",
        ),
        measurements,
        "insufficient",
    )


def _isolation_check(check, record: dict, measurements: list[dict]) -> dict:
    values = record.get("values") if isinstance(record.get("values"), dict) else {}
    return _annotate(
        check(
            "supplement_amplitude_isolation",
            "补充幅值与官方幅值隔离",
            "not_applicable",
            {
                "X向振动(mm/s)": values.get("X向振动(mm/s)"),
                "Y向振动(mm/s)": values.get("Y向振动(mm/s)"),
                "1X转频幅值(mm/s)": values.get("1X转频幅值(mm/s)"),
                "BPFO幅值(g)": values.get("BPFO幅值(g)"),
                "supplement_units": [item.get("unit") for item in measurements],
            },
            _basis_from_measurements(measurements),
            "频谱幅值保留补充信号自己的单位。不与官方记录中的振动速度、加速度或位移幅值比较。",
            computed={"compared_with_official_amplitudes": False, "values_merged": False},
        ),
        measurements,
        "not_compared",
    )


def _annotate(item: dict, rows: list[dict], effect: str) -> dict:
    item["supports_fault"] = False
    item["supports_causal_hypothesis"] = False
    item["condition_effect"] = effect
    item["evidence_source"] = _source(rows)
    return item


def _source(rows: list[dict]) -> dict:
    return {
        "kind": "supplementary_measurement",
        "measurement_ids": [str(item.get("measurement_id") or "") for item in rows],
        "content_sha256": [str(item.get("content_sha256") or "") for item in rows],
        "data_origin": [str(item.get("data_origin") or "") for item in rows],
        "origin_note": [str(item.get("origin_note") or "") for item in rows],
        "values_merged": False,
        "official_excel_modified": False,
    }


def _basis(evaluations: list[dict]) -> str:
    return "；".join(
        f"补充测量 {item['measurement_id']}，SHA-256 {item['content_sha256']}，{item['origin_note']}"
        for item in evaluations
    )


def _basis_from_measurements(measurements: list[dict]) -> str:
    return "；".join(
        f"补充测量 {item.get('measurement_id')}，SHA-256 {item.get('content_sha256')}，{item.get('origin_note')}"
        for item in measurements
    )


def _stats(item: dict) -> dict:
    return {
        "measurement_id": item["measurement_id"],
        "content_sha256": item["content_sha256"],
        "unit": item["unit"],
        "crest_factor": item["crest_factor"],
        "kurtosis_pearson": item["kurtosis_pearson"],
        "values_merged": False,
    }


def _peak_summary(item: dict) -> dict:
    return {
        "measurement_id": item["measurement_id"],
        "content_sha256": item["content_sha256"],
        "unit": item["unit"],
        "peaks": item["peaks"],
        "values_merged": False,
    }


def _dominant_summary(item: dict) -> dict:
    return {
        "measurement_id": item["measurement_id"],
        "content_sha256": item["content_sha256"],
        "dominant_frequency_hz": item["dominant_frequency_hz"],
        "dominant_is_1x": item["dominant_is_1x"],
        "values_merged": False,
    }


def _ratio_summary(item: dict) -> dict:
    return {
        "measurement_id": item["measurement_id"],
        "content_sha256": item["content_sha256"],
        "unit": item["unit"],
        "frequency_1x_hz": item["frequency_1x_hz"],
        "amplitude_1x": item["amplitude_1x"],
        "frequency_2x_hz": item["frequency_2x_hz"],
        "amplitude_2x": item["amplitude_2x"],
        "ratio_2x_to_1x": item["ratio_2x_to_1x"],
        "values_merged": False,
    }


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _public_row(row: sqlite3.Row) -> dict:
    metrics = json.loads(row["metrics_json"])
    return {
        "measurement_id": row["measurement_id"],
        "record_id": row["record_id"],
        "device_id": row["device_id"],
        "measured_at": row["measured_at"],
        "sensor_position": row["sensor_position"],
        "sampling_rate_hz": row["sampling_rate_hz"],
        "unit": row["unit"],
        "file_name": row["file_name"],
        "content_sha256": row["content_sha256"],
        "sample_count": row["sample_count"],
        "data_origin": row["data_origin"],
        "origin_note": row["origin_note"],
        "metrics": metrics,
        "created_at": row["created_at"],
        "official_excel_modified": False,
        "written_to_graph": False,
    }


def _rejected(message: str, status_code: int) -> dict:
    return {
        "ok": False,
        "usable": False,
        "stored": False,
        "duplicate": False,
        "error": message,
        "status_code": status_code,
        "official_excel_modified": False,
        "written_to_graph": False,
        "measurement": None,
    }


def _connect(path: Path) -> sqlite3.Connection:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=5, isolation_level=None)
    except OSError as exc:
        raise SupplementaryMeasurementError(f"补充测量数据库无法打开：{exc}") from exc
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute(_CREATE)
        connection.execute(_CREATE_REASSESSMENT)
        connection.execute(_CREATE_SYNC)
    except sqlite3.Error as exc:
        connection.close()
        raise SupplementaryMeasurementError(f"补充测量数据库无法初始化：{exc}") from exc
    return connection


def _rollback(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error:
        return


def _assert_runtime_location(path: Path) -> Path:
    root = Path(__file__).resolve().parents[3]
    resolved = path if path.is_absolute() else root / path
    try:
        resolved = resolved.resolve()
    except OSError as exc:
        raise SupplementaryMeasurementError(f"补充测量数据库无法打开：{exc}") from exc
    forbidden = (
        (root / "data" / "official").resolve(),
        (root / "data" / "official_dataset").resolve(),
        (root / "data" / "processed").resolve(),
    )
    if any(resolved == base or base in resolved.parents for base in forbidden):
        raise SupplementaryMeasurementError("补充测量不能写入官方数据目录。")
    return resolved
