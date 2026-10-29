"""同一记录多次故障重新评估的追加历史。

读取时不创建数据库。不保存原始波形、密钥或正式图谱。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

_RECORD_ID = re.compile(r"REC-[A-Za-z0-9-]+")
UNCHANGED_LINE = "本次评估未发现足以改变假设的新增证据，版本保持不变"
NO_HYPOTHESIS_LINE = "尚无可验证的故障假设"

_CREATE = """
CREATE TABLE IF NOT EXISTS diagnosis_assessments (
    assessment_id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    assessment_no INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    diagnosis_level TEXT NOT NULL,
    stop_reason TEXT NOT NULL,
    model_called INTEGER NOT NULL,
    confirmed INTEGER NOT NULL,
    prior_hypotheses_json TEXT NOT NULL,
    hypotheses_json TEXT NOT NULL,
    physics_json TEXT NOT NULL,
    measurements_json TEXT NOT NULL,
    changes_json TEXT NOT NULL,
    missing_json TEXT NOT NULL,
    public_answer TEXT NOT NULL,
    UNIQUE(record_id, assessment_no)
)
"""


class DiagnosisHistoryError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def database_path() -> Path:
    override = os.environ.get("DIAGNOSIS_HISTORY_DB", "").strip()
    root = Path(__file__).resolve().parents[3]
    path = Path(override) if override else root / "data" / "runtime" / "diagnosis_history.sqlite3"
    return _assert_runtime_location(path)


def latest_state(record_id: str) -> dict | None:
    """返回该记录最后一次评估的完整假设和 L2 备注。没有库时不创建。"""
    identity = _identity(record_id)
    row = _latest_row(identity)
    if row is None:
        return None
    hypotheses = _loads(row["hypotheses_json"], [])
    physics = _loads(row["physics_json"], {})
    return {
        "assessment_id": row["assessment_id"],
        "hypotheses": hypotheses if isinstance(hypotheses, list) else [],
        "physics": physics if isinstance(physics, dict) else {},
    }


def save_assessment(
    record_id: str,
    device_id: str,
    *,
    trace_id: str = "",
    diagnosis_level: str = "",
    stop_reason: str = "",
    model_called: bool = False,
    prior_hypotheses: list | None = None,
    hypotheses: list | None = None,
    physics: dict | None = None,
    measurements: list | None = None,
    missing_measurements: list | None = None,
    public_answer: str = "",
) -> dict:
    """追加一次评估。统计偏离和数值一致都不能记成确诊。"""
    identity = _identity(record_id)
    device = device_id.strip() if isinstance(device_id, str) else ""
    if not device:
        raise DiagnosisHistoryError("评估记录缺少设备编号。")
    prior = [item for item in prior_hypotheses or [] if isinstance(item, dict)]
    current = [item for item in hypotheses or [] if isinstance(item, dict)]
    stored_physics = _physics_for_storage(physics if isinstance(physics, dict) else {})
    stored_measurements = _slim_measurements(measurements or [])
    missing = [item.strip() for item in missing_measurements or [] if isinstance(item, str) and item.strip()]
    changes = _changes(prior, current)
    path = database_path()
    connection = _connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT device_id FROM diagnosis_assessments WHERE record_id = ? LIMIT 1",
            (identity,),
        ).fetchone()
        if existing is not None and existing["device_id"] != device:
            raise DiagnosisHistoryError("这条记录已经属于其他设备，不能混入新的评估。")
        number_row = connection.execute(
            "SELECT COALESCE(MAX(assessment_no), 0) + 1 AS next_no FROM diagnosis_assessments WHERE record_id = ?",
            (identity,),
        ).fetchone()
        assessment_no = int(number_row["next_no"])
        assessment_id = uuid.uuid4().hex
        created_at = datetime.now().astimezone().isoformat(timespec="seconds")
        connection.execute(
            """
            INSERT INTO diagnosis_assessments (
                assessment_id, record_id, device_id, assessment_no, created_at, trace_id,
                diagnosis_level, stop_reason, model_called, confirmed,
                prior_hypotheses_json, hypotheses_json, physics_json, measurements_json,
                changes_json, missing_json, public_answer
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                assessment_id,
                identity,
                device,
                assessment_no,
                created_at,
                trace_id.strip() if isinstance(trace_id, str) else "",
                diagnosis_level.strip() if isinstance(diagnosis_level, str) else "",
                stop_reason,
                1 if model_called else 0,
                0,
                _dumps(prior),
                _dumps(current),
                _dumps(stored_physics),
                _dumps(stored_measurements),
                _dumps(changes),
                _dumps(missing),
                public_answer if isinstance(public_answer, str) else "",
            ),
        )
        connection.execute("COMMIT")
    except DiagnosisHistoryError:
        _rollback(connection)
        raise
    except sqlite3.Error as exc:
        _rollback(connection)
        raise DiagnosisHistoryError(f"诊断历史没有写入：{exc}") from exc
    finally:
        connection.close()
    return get_assessment(identity, assessment_id) or {}


def list_assessments(record_id: str) -> list[dict]:
    identity = _identity(record_id)
    connection = _open_readonly()
    if connection is None:
        return []
    try:
        rows = connection.execute(
            "SELECT * FROM diagnosis_assessments WHERE record_id = ? ORDER BY assessment_no",
            (identity,),
        ).fetchall()
    finally:
        connection.close()
    return [_summary(row) for row in rows]


def get_assessment(record_id: str, assessment_id: str) -> dict | None:
    identity = _identity(record_id)
    token = assessment_id.strip() if isinstance(assessment_id, str) else ""
    if not token:
        raise DiagnosisHistoryError("缺少评估编号。")
    connection = _open_readonly()
    if connection is None:
        return None
    try:
        row = connection.execute(
            "SELECT * FROM diagnosis_assessments WHERE record_id = ? AND assessment_id = ?",
            (identity, token),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    return _detail(row)


def compare_assessments(record_id: str, before_id: str = "", after_id: str = "") -> dict:
    identity = _identity(record_id)
    rows = _rows_for_record(identity)
    if not rows:
        return {"ok": True, "record_id": identity, "comparable": False, "message": "还没有已保存的评估。", "before": None, "after": None, "change_lines": []}
    before = _pick(rows, before_id, -2 if len(rows) >= 2 else None)
    after = _pick(rows, after_id, -1)
    if before is None or after is None or before["assessment_id"] == after["assessment_id"]:
        return {
            "ok": True,
            "record_id": identity,
            "comparable": False,
            "message": "还没有两次评估可以对比。",
            "before": _summary(before) if before is not None else None,
            "after": _summary(after) if after is not None else None,
            "change_lines": [],
        }
    prior = _loads(before["hypotheses_json"], [])
    current = _loads(after["hypotheses_json"], [])
    changes = _changes(prior if isinstance(prior, list) else [], current if isinstance(current, list) else [])
    return {
        "ok": True,
        "record_id": identity,
        "comparable": True,
        "before": _summary(before),
        "after": _summary(after),
        "change_lines": changes["lines"],
        "added_support": changes["added_support"],
        "added_oppose": changes["added_oppose"],
        "confirmed": False,
        "written_to_graph": False,
    }


def _pick(rows: list[sqlite3.Row], assessment_id: str, fallback_index: int | None) -> sqlite3.Row | None:
    token = assessment_id.strip() if isinstance(assessment_id, str) else ""
    if token:
        matched = next((row for row in rows if row["assessment_id"] == token), None)
        if matched is None:
            raise DiagnosisHistoryError("没有找到这次评估，或者它不属于这条记录。", 404)
        return matched
    if fallback_index is None:
        return None
    return rows[fallback_index]


def _rows_for_record(record_id: str) -> list[sqlite3.Row]:
    connection = _open_readonly()
    if connection is None:
        return []
    try:
        return connection.execute(
            "SELECT * FROM diagnosis_assessments WHERE record_id = ? ORDER BY assessment_no",
            (record_id,),
        ).fetchall()
    finally:
        connection.close()


def _latest_row(record_id: str) -> sqlite3.Row | None:
    connection = _open_readonly()
    if connection is None:
        return None
    try:
        return connection.execute(
            "SELECT * FROM diagnosis_assessments WHERE record_id = ? ORDER BY assessment_no DESC LIMIT 1",
            (record_id,),
        ).fetchone()
    finally:
        connection.close()


def _summary(row: sqlite3.Row) -> dict:
    hypotheses = _named(_loads(row["hypotheses_json"], []))
    changes = _loads(row["changes_json"], {})
    physics = _loads(row["physics_json"], {})
    measurements = _loads(row["measurements_json"], [])
    missing = _loads(row["missing_json"], [])
    l2 = _public_l2(physics if isinstance(physics, dict) else {})
    return {
        "assessment_id": row["assessment_id"],
        "assessment_no": row["assessment_no"],
        "record_id": row["record_id"],
        "device_id": row["device_id"],
        "created_at": row["created_at"],
        "trace_id": row["trace_id"],
        "diagnosis_level": row["diagnosis_level"],
        "stop_reason": row["stop_reason"],
        "model_called": bool(row["model_called"]),
        "confirmed": False,
        "no_hypothesis": not hypotheses,
        "change_lines": list(changes.get("lines") or []) if isinstance(changes, dict) else [],
        "hypotheses": [
            {
                "fault": name,
                "version": int(item.get("version") or 1),
                "verification_status": item.get("verification_status") or "",
            }
            for name, item in hypotheses.items()
        ],
        "measurements": measurements if isinstance(measurements, list) else [],
        "missing_measurements": missing if isinstance(missing, list) else [],
        "l2_status": l2["overall_status"],
        "written_to_graph": False,
        "official_excel_modified": False,
    }


def _detail(row: sqlite3.Row) -> dict:
    body = _summary(row)
    hypotheses = [item for item in _loads(row["hypotheses_json"], []) if isinstance(item, dict)]
    prior = [item for item in _loads(row["prior_hypotheses_json"], []) if isinstance(item, dict)]
    changes = _loads(row["changes_json"], {})
    physics = _loads(row["physics_json"], {})
    body["hypotheses_detail"] = [_public_hypothesis(item) for item in hypotheses]
    body["prior_hypotheses"] = [_public_hypothesis(item) for item in prior]
    body["added_support"] = list(changes.get("added_support") or []) if isinstance(changes, dict) else []
    body["added_oppose"] = list(changes.get("added_oppose") or []) if isinstance(changes, dict) else []
    body["l2"] = _public_l2(physics if isinstance(physics, dict) else {})
    body["public_answer"] = row["public_answer"]
    body["causal_conclusion"] = False
    return body


def _changes(prior: list, current: list) -> dict:
    prior_named = _named(prior)
    current_named = _named(current)
    added_support = []
    added_oppose = []
    lines = []
    changed = False
    if not prior_named and not current_named:
        return {"lines": [NO_HYPOTHESIS_LINE], "no_hypothesis": True, "added_support": [], "added_oppose": []}
    for name, item in current_named.items():
        old = prior_named.get(name)
        added_support.extend(_public_evidence(_new_evidence(item.get("supporting_evidence"), [] if old is None else old.get("supporting_evidence"))))
        added_oppose.extend(_public_evidence(_new_evidence(item.get("opposing_evidence"), [] if old is None else old.get("opposing_evidence"))))
        new_status = item.get("verification_status") or "未验证"
        new_version = int(item.get("version") or 1)
        if old is None:
            changed = True
            lines.append(f"首次评估：{name} {new_status} V{new_version}")
            continue
        old_status = old.get("verification_status") or "未验证"
        old_version = int(old.get("version") or 1)
        if old_status != new_status or old_version != new_version:
            changed = True
            lines.append(f"{name}：{old_status} V{old_version} → {new_status} V{new_version}")
    if not changed:
        lines = [UNCHANGED_LINE] if current_named else [NO_HYPOTHESIS_LINE]
    return {
        "lines": lines,
        "no_hypothesis": not current_named,
        "added_support": added_support,
        "added_oppose": added_oppose,
    }


def _named(items: object) -> dict[str, dict]:
    named = {}
    if not isinstance(items, list):
        return named
    for item in items:
        if not isinstance(item, dict) or item.get("compound"):
            continue
        faults = item.get("faults") if isinstance(item.get("faults"), list) else []
        if not faults or not isinstance(faults[0], str) or not faults[0].strip():
            continue
        named[faults[0]] = item
    return named


def _new_evidence(current: object, prior: object) -> list[dict]:
    known = {_evidence_key(item) for item in prior or [] if isinstance(item, dict)} if isinstance(prior, list) else set()
    found = []
    if not isinstance(current, list):
        return found
    for item in current:
        if not isinstance(item, dict):
            continue
        key = _evidence_key(item)
        if key in known:
            continue
        known.add(key)
        found.append(item)
    return found


def _evidence_key(item: dict) -> tuple:
    return (
        str(item.get("source") or ""),
        str(item.get("locator") or ""),
        str(item.get("detail") or ""),
        str(item.get("fault") or ""),
    )


def _public_hypothesis(item: dict) -> dict:
    return {
        "hypothesis_id": item.get("hypothesis_id") or "",
        "faults": list(item.get("faults") or []),
        "version": int(item.get("version") or 1),
        "verification_status": item.get("verification_status") or "",
        "revision_reason": item.get("revision_reason") or "",
        "version_history": [
            {
                "version": int(entry.get("version") or 1),
                "verification_status": entry.get("verification_status") or "",
                "revision_reason": entry.get("revision_reason") or "",
            }
            for entry in item.get("version_history") or []
            if isinstance(entry, dict)
        ],
        "supporting_evidence": _public_evidence(item.get("supporting_evidence")),
        "opposing_evidence": _public_evidence(item.get("opposing_evidence")),
        "missing_measurements": [label for label in item.get("missing_measurements") or [] if isinstance(label, str)],
        "confirmed": False,
        "written_to_graph": False,
        "causal_conclusion": False,
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
                "evidence_id": item.get("evidence_id") or "",
                "source": item.get("source") or "",
                "locator": item.get("locator") or "",
                "detail": item.get("detail") or "",
                "fault": item.get("fault") or "",
            }
        )
    return rows


def _public_l2(physics: dict) -> dict:
    results = []
    overall = ""
    for item in physics.get("results") or []:
        if not isinstance(item, dict):
            continue
        status = str(item.get("overall_status") or "")
        if status and not overall:
            overall = status
        sources = []
        for source in item.get("supplementary_measurements") or []:
            if not isinstance(source, dict):
                continue
            sources.append(
                {
                    "measurement_id": source.get("measurement_id") or "",
                    "content_sha256": source.get("content_sha256") or "",
                    "measured_at": source.get("measured_at") or "",
                    "data_origin": source.get("data_origin") or "",
                    "origin_note": source.get("origin_note") or "",
                }
            )
        results.append(
            {
                "fault": item.get("fault") or "",
                "level": item.get("level") or physics.get("level") or "",
                "overall_status": status,
                "consistent_checks": [str(check) for check in item.get("consistent_checks") or []],
                "contradicted_checks": [str(check) for check in item.get("contradicted_checks") or []],
                "insufficient_checks": [str(check) for check in item.get("insufficient_checks") or []],
                "not_applicable_checks": [str(check) for check in item.get("not_applicable_checks") or []],
                "supplementary_measurements": sources,
            }
        )
    return {
        "called": physics.get("called") is True,
        "level": physics.get("level") or "",
        "no_fault_hypothesis": physics.get("no_fault_hypothesis") is True,
        "overall_status": overall,
        "results": results,
    }


def _physics_for_storage(physics: dict) -> dict:
    stored = json.loads(json.dumps(physics, ensure_ascii=False))
    if not isinstance(stored, dict):
        return {}
    results = stored.get("results")
    if isinstance(results, list):
        for item in results:
            if not isinstance(item, dict):
                continue
            item.pop("inputs", None)
            item.pop("computed", None)
            sources = item.get("supplementary_measurements")
            if isinstance(sources, list):
                item["supplementary_measurements"] = [
                    {
                        "measurement_id": source.get("measurement_id") or "",
                        "content_sha256": source.get("content_sha256") or "",
                        "measured_at": source.get("measured_at") or "",
                        "data_origin": source.get("data_origin") or "",
                        "origin_note": source.get("origin_note") or "",
                    }
                    for source in sources
                    if isinstance(source, dict)
                ]
    return stored


def _slim_measurements(items: list) -> list[dict]:
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "measurement_id": item.get("measurement_id") or "",
                "content_sha256": item.get("content_sha256") or "",
                "measured_at": item.get("measured_at") or "",
                "sensor_position": item.get("sensor_position") or "",
                "unit": item.get("unit") or "",
                "file_name": item.get("file_name") or "",
                "data_origin": item.get("data_origin") or "",
                "origin_note": item.get("origin_note") or "",
            }
        )
    return rows


def _identity(record_id: str) -> str:
    identity = record_id.strip() if isinstance(record_id, str) else ""
    if _RECORD_ID.fullmatch(identity) is None:
        raise DiagnosisHistoryError("记录编号格式不正确。")
    return identity


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str, fallback: object) -> object:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


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
        raise DiagnosisHistoryError(f"诊断历史数据库无法打开：{exc}") from exc
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute(_CREATE)
    except sqlite3.Error as exc:
        connection.close()
        raise DiagnosisHistoryError(f"诊断历史数据库无法初始化：{exc}") from exc
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
        raise DiagnosisHistoryError(f"诊断历史数据库无法打开：{exc}") from exc
    forbidden = (
        (root / "data" / "official").resolve(),
        (root / "data" / "official_dataset").resolve(),
        (root / "data" / "processed").resolve(),
    )
    if any(resolved == base or base in resolved.parents for base in forbidden):
        raise DiagnosisHistoryError("诊断历史不能写入官方数据目录。")
    return resolved
