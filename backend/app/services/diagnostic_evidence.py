"""结构化诊断证据登记。

保存轴向测量和其他知识条款相关的测量值。不把口头结论当成验证完成，
也不把一种物理量自动换成另一种。
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

_RECORD_ID = re.compile(r"REC-[A-Za-z0-9-]+")
_ORIGINS = {
    "synthetic_test_signal": "合成测试数据，只用于验证程序，不是工厂测量。",
    "uploaded_measurement": "登记的补充测量，不是官方 Excel 观测，不能当作已确认的工厂记录。",
}
_AXIAL = {
    "axial_displacement": {
        "title": "轴向位移",
        "quantity": "axial_displacement",
        "units": {"um", "μm", "mm", "mil"},
        "methods": {"eddy_current_probe", "laser_displacement", "dial_indicator"},
        "references": {"housing_relative", "probe_tip_zero", "shaft_shoulder"},
        "missing_label": None,
        "check_id": "supplement_axial_displacement",
        "clause": "轴向位移是几何位移量，不是轴向偏心，也不是故障确诊。",
    },
    "axial_vibration": {
        "title": "轴向振动",
        "quantity": "axial_vibration",
        "units": {"mm/s", "m/s", "g", "m/s^2", "m/s²", "um", "μm"},
        "methods": {"accelerometer", "velocity_probe"},
        "references": {"bearing_housing_axial", "machine_frame_axial"},
        "missing_label": None,
        "check_id": "supplement_axial_vibration",
        "clause": "轴向振动是振动量，不是轴向偏心几何量，也不是故障确诊。",
    },
    "axial_eccentricity": {
        "title": "轴向偏心或轴向跳动",
        "quantity": "axial_eccentricity",
        "units": {"um", "μm", "mm", "mil"},
        "methods": {"runout_measurement", "eddy_current_runout", "laser_runout"},
        "references": {"shaft_axis", "coupling_face", "probe_circle"},
        "missing_label": "轴向偏心测量",
        "check_id": "supplement_axial_eccentricity",
        "clause": "KB-02 第38行写有轴向侧偏心。登记偏心量只表示测量已获得，不能单独把转子不平衡条款标成已满足，也不能确诊。",
    },
}
_OTHER = {
    "load_1x_series": {
        "title": "负荷变化下的 1X 幅值与相位",
        "check_id": "supplement_load_1x_series",
        "kb02": "KB-02_故障模式诊断知识库.md 第36行：1X相位稳定，幅值随负荷变化",
        "missing_label": "相位",
    },
    "envelope_bearing_harmonics": {
        "title": "包络谱轴承谐波",
        "check_id": "supplement_envelope_bearing_harmonics",
        "kb02": "KB-02_故障模式诊断知识库.md 第16行：包络谱中出现明显的轴承特征频率及其谐波",
        "missing_label": "包络谱谐波形态",
    },
    "gear_mesh_sidebands": {
        "title": "啮合频率及边带",
        "check_id": "supplement_gear_mesh_sidebands",
        "kb02": "KB-02_故障模式诊断知识库.md 第50行：1X调制边带密集",
        "missing_label": "啮合频率边带",
    },
}
_CREATE = """
CREATE TABLE IF NOT EXISTS diagnostic_evidence (
    evidence_id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    measured_at TEXT NOT NULL,
    evidence_kind TEXT NOT NULL,
    quantity_name TEXT NOT NULL,
    value_json TEXT NOT NULL,
    unit TEXT NOT NULL,
    sensor_position TEXT NOT NULL,
    method TEXT NOT NULL,
    reference_basis TEXT NOT NULL,
    data_origin TEXT NOT NULL,
    origin_note TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    registration_status TEXT NOT NULL,
    validation_status TEXT NOT NULL,
    validation_note TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(record_id, content_sha256)
)
"""


class DiagnosticEvidenceError(RuntimeError):
    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def database_path() -> Path:
    override = os.environ.get("DIAGNOSTIC_EVIDENCE_DB", "").strip()
    root = Path(__file__).resolve().parents[3]
    path = Path(override) if override else root / "data" / "runtime" / "diagnostic_evidence.sqlite3"
    return _assert_runtime_location(path)


def register_evidence(record_id: str, payload: dict) -> dict:
    """登记一份结构化证据。拒绝口头结论，也不混用不同物理量。"""
    identity = _identity(record_id)
    try:
        record = _official_record(identity)
        kind = str(payload.get("evidence_kind") or "").strip()
        if kind in _AXIAL:
            body = _parse_axial(record, payload, kind)
        elif kind in _OTHER:
            body = _parse_other(record, payload, kind)
        else:
            raise DiagnosticEvidenceError("不支持的证据类型。只能登记轴向测量或已有知识条款对应的结构化证据。")
    except DiagnosticEvidenceError:
        raise
    except DatasetError as exc:
        raise DiagnosticEvidenceError(exc.message, 422) from None
    digest = _digest(body)
    connection = _connect(database_path())
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT * FROM diagnostic_evidence WHERE record_id = ? AND content_sha256 = ?",
            (identity, digest),
        ).fetchone()
        if existing is not None:
            connection.execute("ROLLBACK")
            return {"ok": True, "stored": False, "duplicate": True, "evidence": _public(existing), "confirmed": False, "written_to_graph": False}
        evidence_id = uuid.uuid4().hex
        created_at = _now()
        connection.execute(
            """
            INSERT INTO diagnostic_evidence (
                evidence_id, record_id, device_id, measured_at, evidence_kind, quantity_name,
                value_json, unit, sensor_position, method, reference_basis, data_origin, origin_note,
                content_sha256, registration_status, validation_status, validation_note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                identity,
                body["device_id"],
                body["measured_at"],
                body["evidence_kind"],
                body["quantity_name"],
                _dumps(body["values"]),
                body["unit"],
                body["sensor_position"],
                body["method"],
                body["reference_basis"],
                body["data_origin"],
                body["origin_note"],
                digest,
                body["registration_status"],
                body["validation_status"],
                body["validation_note"],
                created_at,
            ),
        )
        connection.execute("COMMIT")
        row = connection.execute("SELECT * FROM diagnostic_evidence WHERE evidence_id = ?", (evidence_id,)).fetchone()
    except sqlite3.Error as exc:
        _rollback(connection)
        raise DiagnosticEvidenceError(f"诊断证据没有写入：{exc}") from exc
    finally:
        connection.close()
    return {
        "ok": True,
        "stored": True,
        "duplicate": False,
        "evidence": _public(row),
        "confirmed": False,
        "written_to_graph": False,
        "official_excel_modified": False,
        "causal_conclusion": False,
    }


def list_evidence(record_id: str) -> list[dict]:
    identity = _identity(record_id)
    connection = _open_readonly()
    if connection is None:
        return []
    try:
        rows = connection.execute(
            "SELECT * FROM diagnostic_evidence WHERE record_id = ? ORDER BY created_at, evidence_id",
            (identity,),
        ).fetchall()
    finally:
        connection.close()
    return [_public(row) for row in rows]


def evidence_for_record(record: dict) -> dict:
    """给 L2 提供结构化证据检查。没有库文件时不创建。"""
    empty = {"checks": [], "resolved_missing": [], "evidence": []}
    if not isinstance(record, dict):
        return empty
    identity = str(record.get("record_id") or "").strip()
    if not identity or not database_path().is_file():
        return empty
    rows = list_evidence(identity)
    if not rows:
        return empty
    return {"checks": _checks(rows), "resolved_missing": _resolved(rows), "evidence": rows}


def _parse_axial(record: dict, payload: dict, kind: str) -> dict:
    spec = _AXIAL[kind]
    device, measured = _match_record(record, payload)
    if _looks_like_verdict(payload):
        raise DiagnosticEvidenceError("不能只登记「正常/异常」这类口头结论。请提供数值、单位、方法和参考基准。")
    unit = str(payload.get("unit") or "").strip()
    if unit not in spec["units"]:
        raise DiagnosticEvidenceError(f"{spec['title']}的单位不匹配，不能与其他物理量混用。")
    method = str(payload.get("method") or "").strip()
    if method not in spec["methods"]:
        raise DiagnosticEvidenceError(f"请提供{spec['title']}可用的测量方法。缺少可靠方法时不能作为可验证证据。")
    reference = str(payload.get("reference_basis") or "").strip()
    if reference not in spec["references"]:
        raise DiagnosticEvidenceError(f"请提供{spec['title']}的参考基准。没有基准时不能作为可验证证据。")
    value = _number(payload.get("value"))
    if value is None:
        raise DiagnosticEvidenceError(f"请提供{spec['title']}的数值。")
    if value < 0:
        raise DiagnosticEvidenceError(f"{spec['title']}数值不能为负。")
    origin = _origin(payload.get("data_origin"))
    position = _label(payload.get("sensor_position"), "传感器位置")
    note = (
        f"已登记{spec['title']} {value} {unit}。测量已获得，质量字段齐全。"
        f"{spec['clause']} 这不等于知识条款已经检验完成，也不是故障确诊。"
    )
    return {
        "device_id": device,
        "measured_at": measured,
        "evidence_kind": kind,
        "quantity_name": spec["quantity"],
        "values": {
            "value": value,
            "quantity": spec["quantity"],
            "not_axial_displacement": kind != "axial_displacement",
            "not_axial_vibration": kind != "axial_vibration",
            "not_axial_eccentricity": kind != "axial_eccentricity",
            "clause_satisfied": False,
            "supports_fault": False,
        },
        "unit": unit,
        "sensor_position": position,
        "method": method,
        "reference_basis": reference,
        "data_origin": origin[0],
        "origin_note": origin[1],
        "registration_status": "已登记",
        "validation_status": "可计算",
        "validation_note": note,
    }


def _parse_other(record: dict, payload: dict, kind: str) -> dict:
    device, measured = _match_record(record, payload)
    if _looks_like_verdict(payload):
        raise DiagnosticEvidenceError("不能只登记口头结论。请提供可核对的测量值、单位和采集条件。")
    origin = _origin(payload.get("data_origin"))
    position = _label(payload.get("sensor_position"), "传感器位置")
    method = _label(payload.get("method"), "测量方法")
    reference = _label(payload.get("reference_basis"), "参考基准")
    if kind == "load_1x_series":
        return _parse_load_series(device, measured, position, method, reference, origin, payload)
    if kind == "envelope_bearing_harmonics":
        return _parse_peaks(device, measured, position, method, reference, origin, payload, kind, "envelope")
    return _parse_peaks(device, measured, position, method, reference, origin, payload, kind, "sideband")


def _parse_load_series(device: str, measured: str, position: str, method: str, reference: str, origin: tuple[str, str], payload: dict) -> dict:
    points = payload.get("observations")
    if not isinstance(points, list) or not points:
        raise DiagnosticEvidenceError("负荷变化观测需要至少一条含负荷和 1X 幅值的采样点。")
    parsed = []
    loads = set()
    for index, item in enumerate(points, start=1):
        if not isinstance(item, dict):
            raise DiagnosticEvidenceError(f"第 {index} 个负荷观测无效。")
        load = _number(item.get("load"))
        amplitude = _number(item.get("amplitude_1x"))
        if load is None or amplitude is None:
            raise DiagnosticEvidenceError(f"第 {index} 个负荷观测缺少负荷或 1X 幅值。")
        load_unit = str(item.get("load_unit") or "").strip()
        if not load_unit:
            raise DiagnosticEvidenceError("请提供负荷单位。")
        amp_unit = str(item.get("amplitude_unit") or payload.get("unit") or "").strip()
        if amp_unit not in {"mm/s", "m/s", "g", "m/s^2", "m/s²"}:
            raise DiagnosticEvidenceError("1X 幅值单位不兼容。")
        phase = item.get("phase_1x_deg")
        phase_value = None if phase in (None, "") else _number(phase)
        if phase not in (None, "") and phase_value is None:
            raise DiagnosticEvidenceError(f"第 {index} 个负荷观测的相位不是有效数值。")
        keyphasor = item.get("keyphasor_present") is True
        parsed.append(
            {
                "load": load,
                "load_unit": load_unit,
                "amplitude_1x": amplitude,
                "amplitude_unit": amp_unit,
                "phase_1x_deg": phase_value,
                "keyphasor_present": keyphasor,
            }
        )
        loads.add((load, load_unit))
    unit = str(parsed[0]["amplitude_unit"])
    has_keyphasor = all(item["keyphasor_present"] for item in parsed) and all(item["phase_1x_deg"] is not None for item in parsed)
    multi_load = len(loads) >= 2
    if multi_load and has_keyphasor:
        status = "已完成条款校验"
        note = (
            "已登记不少于两个负荷点，并且声明了键相参考下的 1X 相位。"
            "这只表示 KB-02 第36行所需的采集条件齐备，现有流程仍不自动判定稳定与否，也不能确诊。"
        )
        clause = False
    elif multi_load and not has_keyphasor:
        status = "仍然证据不足"
        note = "已有不同负荷下的 1X 幅值，但缺少键相参考或相位，不能验证「1X相位稳定，幅值随负荷变化」。单次通道间相位差也不能代替这项。"
        clause = False
    else:
        status = "仍然证据不足"
        note = "只有一次负荷采样。KB-02 第36行需要负荷变化下的观测，单次采样仍然证据不足。"
        clause = False
    return {
        "device_id": device,
        "measured_at": measured,
        "evidence_kind": "load_1x_series",
        "quantity_name": "load_1x_series",
        "values": {
            "observations": parsed,
            "distinct_loads": len(loads),
            "keyphasor_complete": has_keyphasor,
            "clause_satisfied": clause,
            "supports_fault": False,
        },
        "unit": unit,
        "sensor_position": position,
        "method": method,
        "reference_basis": reference,
        "data_origin": origin[0],
        "origin_note": origin[1],
        "registration_status": "已登记",
        "validation_status": status,
        "validation_note": note,
    }


def _parse_peaks(device: str, measured: str, position: str, method: str, reference: str, origin: tuple[str, str], payload: dict, kind: str, mode: str) -> dict:
    peaks = payload.get("peaks")
    if not isinstance(peaks, list) or not peaks:
        raise DiagnosticEvidenceError("请提供可核对的频率峰列表。")
    unit = str(payload.get("unit") or "").strip()
    if unit not in {"g", "m/s^2", "m/s²", "mm/s"}:
        raise DiagnosticEvidenceError("频谱幅值单位不兼容。")
    parsed = []
    for index, item in enumerate(peaks, start=1):
        if not isinstance(item, dict):
            raise DiagnosticEvidenceError(f"第 {index} 个频率峰无效。")
        frequency = _number(item.get("frequency_hz"))
        amplitude = _number(item.get("amplitude"))
        if frequency is None or amplitude is None or frequency <= 0 or amplitude < 0:
            raise DiagnosticEvidenceError(f"第 {index} 个频率峰缺少有效频率或幅值。")
        label = str(item.get("label") or "").strip()
        parsed.append({"frequency_hz": frequency, "amplitude": amplitude, "label": label, "unit": unit})
    title = _OTHER[kind]["title"]
    note = (
        f"已登记{title}共 {len(parsed)} 个峰。现有算法不能自动判定谐波族或边带是否满足 KB-02 原文，"
        "因此状态是已登记、待验证，不能标成条款已检验，也不能确诊。"
    )
    return {
        "device_id": device,
        "measured_at": measured,
        "evidence_kind": kind,
        "quantity_name": kind,
        "values": {
            "peaks": parsed,
            "mode": mode,
            "clause_satisfied": False,
            "supports_fault": False,
            "algorithm_verified": False,
        },
        "unit": unit,
        "sensor_position": position,
        "method": method,
        "reference_basis": reference,
        "data_origin": origin[0],
        "origin_note": origin[1],
        "registration_status": "已登记",
        "validation_status": "已登记、待验证",
        "validation_note": note,
    }


def _checks(rows: list[dict]) -> list[dict]:
    from app.services.knowledge_physical_validator import _check

    checks = []
    for item in rows:
        kind = item["evidence_kind"]
        source = {
            "kind": "diagnostic_evidence",
            "evidence_ids": [item["evidence_id"]],
            "content_sha256": [item["content_sha256"]],
            "data_origin": [item["data_origin"]],
            "origin_note": [item["origin_note"]],
            "values_merged": False,
            "official_excel_modified": False,
        }
        if kind in _AXIAL:
            spec = _AXIAL[kind]
            status = "consistent"
            limit = item["validation_note"]
            computed = {
                "measurement_obtained": True,
                "quality_ok": True,
                "definition_matched": True,
                "clause_satisfied": False,
                "supports_fault": False,
                "quantity": spec["quantity"],
                "value": item["values"].get("value"),
                "unit": item["unit"],
                "method": item["method"],
                "reference_basis": item["reference_basis"],
                "registration_status": item["registration_status"],
                "validation_status": item["validation_status"],
            }
            check = _check(spec["check_id"], spec["title"], status, {"evidence_id": item["evidence_id"]}, item["method"] + " / " + item["reference_basis"], limit, computed=computed)
        elif kind == "load_1x_series":
            values = item["values"] if isinstance(item["values"], dict) else {}
            status = "insufficient_data"
            computed = {
                "measurement_obtained": True,
                "quality_ok": bool(values.get("distinct_loads")),
                "definition_matched": False,
                "clause_satisfied": False,
                "supports_fault": False,
                "distinct_loads": values.get("distinct_loads"),
                "keyphasor_complete": values.get("keyphasor_complete"),
                "observations": values.get("observations") or [],
                "registration_status": item["registration_status"],
                "validation_status": item["validation_status"],
            }
            check = _check(
                "supplement_load_1x_series",
                "负荷变化下的 1X 观测",
                status,
                {"evidence_id": item["evidence_id"]},
                _OTHER[kind]["kb02"],
                item["validation_note"],
                computed=computed,
            )
        else:
            spec = _OTHER[kind]
            computed = {
                "measurement_obtained": True,
                "quality_ok": True,
                "definition_matched": False,
                "clause_satisfied": False,
                "supports_fault": False,
                "algorithm_verified": False,
                "peaks": (item["values"] or {}).get("peaks") if isinstance(item["values"], dict) else [],
                "registration_status": item["registration_status"],
                "validation_status": item["validation_status"],
            }
            check = _check(spec["check_id"], spec["title"], "insufficient_data", {"evidence_id": item["evidence_id"]}, spec["kb02"], item["validation_note"], computed=computed)
        check["supports_fault"] = False
        check["supports_causal_hypothesis"] = False
        check["condition_effect"] = "registered_evidence"
        check["evidence_source"] = source
        checks.append(check)
    return checks


def _resolved(rows: list[dict]) -> list[str]:
    labels = []
    for item in rows:
        kind = item["evidence_kind"]
        if kind == "axial_eccentricity" and item["validation_status"] in {"可计算", "已获得可用证据", "已完成条款校验"}:
            labels.append("轴向偏心测量")
        # 相位、包络、边带即便已登记，也不能从官方缺失中去掉，因为条款仍未检验。
    return labels


def _match_record(record: dict, payload: dict) -> tuple[str, str]:
    device = str(payload.get("device_id") or "").strip()
    if device != str(record.get("device_id") or ""):
        raise DiagnosticEvidenceError("设备编号与这条记录不一致，不能合并观测。")
    measured = str(payload.get("measured_at") or "").strip()
    if measured != str(record.get("collected_at") or ""):
        raise DiagnosticEvidenceError("测量时间与记录采集时间不一致，不能合并观测。")
    return device, measured


def _official_record(record_id: str) -> dict:
    found = get_observation(record_id)
    record = found.get("record") if isinstance(found, dict) else None
    if not isinstance(record, dict):
        raise DiagnosticEvidenceError(f"没有找到记录编号「{record_id}」，不能把证据关联到其他记录。")
    return record


def _looks_like_verdict(payload: dict) -> bool:
    text = " ".join(str(payload.get(key) or "") for key in ("value", "note", "comment", "result", "verdict"))
    banned = ("正常", "异常", "合格", "不合格", "确诊", "无故障", "有故障")
    if any(word in text for word in banned) and _number(payload.get("value")) is None and not isinstance(payload.get("observations"), list) and not isinstance(payload.get("peaks"), list):
        return True
    return False


def _origin(value: object) -> tuple[str, str]:
    text = value.strip() if isinstance(value, str) else ""
    if text not in _ORIGINS:
        raise DiagnosticEvidenceError("数据来源只能标明为合成测试数据或登记的补充测量。")
    return text, _ORIGINS[text]


def _label(value: object, name: str) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text or len(text) > 120:
        raise DiagnosticEvidenceError(f"请提供{name}。")
    if "/" in text or "\\" in text or ".." in text or "\x00" in text:
        raise DiagnosticEvidenceError(f"{name}只能是名称，不能是服务器路径。")
    return text


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _digest(body: dict) -> str:
    payload = {
        "evidence_kind": body["evidence_kind"],
        "quantity_name": body["quantity_name"],
        "values": body["values"],
        "unit": body["unit"],
        "sensor_position": body["sensor_position"],
        "method": body["method"],
        "reference_basis": body["reference_basis"],
        "measured_at": body["measured_at"],
        "device_id": body["device_id"],
    }
    return hashlib.sha256(_dumps(payload).encode("utf-8")).hexdigest()


def _public(row: sqlite3.Row) -> dict:
    values = json.loads(row["value_json"])
    return {
        "evidence_id": row["evidence_id"],
        "record_id": row["record_id"],
        "device_id": row["device_id"],
        "measured_at": row["measured_at"],
        "evidence_kind": row["evidence_kind"],
        "quantity_name": row["quantity_name"],
        "values": values if isinstance(values, dict) else {},
        "unit": row["unit"],
        "sensor_position": row["sensor_position"],
        "method": row["method"],
        "reference_basis": row["reference_basis"],
        "data_origin": row["data_origin"],
        "origin_note": row["origin_note"],
        "content_sha256": row["content_sha256"],
        "registration_status": row["registration_status"],
        "validation_status": row["validation_status"],
        "validation_note": row["validation_note"],
        "created_at": row["created_at"],
        "confirmed": False,
        "written_to_graph": False,
        "supports_fault": False,
        "official_excel_modified": False,
    }


def _identity(record_id: str) -> str:
    identity = record_id.strip() if isinstance(record_id, str) else ""
    if _RECORD_ID.fullmatch(identity) is None:
        raise DiagnosticEvidenceError("记录编号格式不正确。")
    return identity


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _open_readonly() -> sqlite3.Connection | None:
    path = database_path()
    if not path.is_file():
        return None
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def _connect(path: Path) -> sqlite3.Connection:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=5, isolation_level=None)
    except sqlite3.Error as exc:
        raise DiagnosticEvidenceError(f"诊断证据数据库无法打开：{exc}") from exc
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute(_CREATE)
    except sqlite3.Error as exc:
        connection.close()
        raise DiagnosticEvidenceError(f"诊断证据数据库无法初始化：{exc}") from exc
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
        raise DiagnosticEvidenceError(f"诊断证据数据库无法打开：{exc}") from exc
    forbidden = (
        (root / "data" / "official").resolve(),
        (root / "data" / "official_dataset").resolve(),
        (root / "data" / "processed").resolve(),
    )
    if any(resolved == base or base in resolved.parents for base in forbidden):
        raise DiagnosticEvidenceError("诊断证据不能写入官方数据目录。")
    return resolved
