"""把官方开发集历史回放中的统计偏离写入 SQLite。

只保存 analyze_record_for_diagnosis 实际返回、且指标状态为「偏离基线」的结果。
不读取答案标签，不把统计偏离写成故障、概率或正式报警。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from app.services.diagnostic_bridge import analyze_record_for_diagnosis

DEFAULT_DATABASE = (
    Path(__file__).resolve().parents[3] / "data" / "runtime" / "monitoring_events.sqlite3"
)
EVENT_TYPE = "统计偏离"
EVENT_SCOPE = "official_development_set_history_replay"
EVENT_NOTE = (
    "这是官方开发集历史回放中的实验性统计偏离，不是正式设备报警，"
    "也不是故障确诊、故障概率或危险等级。"
)
_ANSWER_LABELS = frozenset(
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
_NO_EVENT_STATUSES = frozenset({"基线不足", "无法判定", "未发现显著偏离"})
_SESSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_METRIC_KEYS = ("field", "unit", "current_value", "median", "mad", "robust_z", "metric_status")
# 同一会话内：设备 + 异常类型 + 指标 的持续异常过程。只影响提醒，不删除事件。
_ALERT_EPISODES: dict[str, set[str]] = {}
_ALERT_EPISODE_LOCK = threading.Lock()
_CLUE_KEYS = (
    "feature_code",
    "related_fault_section",
    "document",
    "line_number",
    "official_condition",
    "observed_fields",
    "method",
    "partial_condition_met",
    "complete_condition_met",
    "unverified_reason",
)
_CANDIDATE_KEYS = ("fault", "confirmed", "evidence_level", "matched_feature_codes", "source", "note")
_INSUFFICIENT_STATUS = "证据不足，无法确定具体故障原因。"
_CLUE_STATUS = "已关联部分诊断线索。证据不足，无法确定具体故障原因。"
_MATCH_STATUS = "知识库特征仅可能匹配，不是故障确诊。"
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS monitoring_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    diagnosis_level TEXT NOT NULL,
    detection_status TEXT NOT NULL,
    data_source TEXT NOT NULL,
    event_scope TEXT NOT NULL,
    event_note TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    clues_json TEXT NOT NULL,
    candidate_faults_json TEXT NOT NULL,
    diagnosis_json TEXT NOT NULL DEFAULT '',
    reminder_required INTEGER NOT NULL DEFAULT 0,
    fault_alert_required INTEGER NOT NULL DEFAULT 0,
    diagnosis_phase TEXT NOT NULL DEFAULT '',
    assessment_id TEXT NOT NULL DEFAULT '',
    episode_key TEXT NOT NULL DEFAULT '',
    UNIQUE (session_id, record_id, event_type)
)
"""
_CREATE_SESSION_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_monitoring_events_session ON monitoring_events (session_id)"
)
_CREATE_DEVICE_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_monitoring_events_device ON monitoring_events (device_id)"
)
_INSERT_EVENT = """
INSERT INTO monitoring_events (
    session_id, record_id, device_id, collected_at, processed_at,
    event_type, diagnosis_level, detection_status, data_source,
    event_scope, event_note, metrics_json, clues_json, candidate_faults_json, diagnosis_json,
    reminder_required, fault_alert_required, diagnosis_phase, assessment_id, episode_key
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
_EVENT_COLUMNS = """
event_id, session_id, record_id, device_id, collected_at, processed_at,
event_type, diagnosis_level, detection_status, data_source,
event_scope, event_note, metrics_json, clues_json, candidate_faults_json, diagnosis_json,
reminder_required, fault_alert_required, diagnosis_phase, assessment_id, episode_key
"""
_SELECT_EVENT = f"""
SELECT {_EVENT_COLUMNS}
FROM monitoring_events
WHERE session_id = ? AND record_id = ? AND event_type = ?
"""
_SELECT_BY_ID = f"""
SELECT {_EVENT_COLUMNS}
FROM monitoring_events
WHERE event_id = ?
"""


class MonitoringEventError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def database_path() -> Path:
    override = os.environ.get("MONITORING_EVENTS_DB", "").strip()
    if override:
        return _assert_runtime_location(Path(override))
    return _assert_runtime_location(DEFAULT_DATABASE)


def process_monitoring_record(session_id: str, record_id: str) -> dict:
    """重新分析一条官方记录。只有真实统计偏离才写入当前回放会话。"""
    session = _session_id(session_id)
    identity = record_id.strip() if isinstance(record_id, str) else ""
    analysis = _scrub(analyze_record_for_diagnosis(identity))
    if not isinstance(analysis, dict) or analysis.get("ok") is not True:
        message = ""
        if isinstance(analysis, dict) and isinstance(analysis.get("error"), str):
            message = analysis["error"]
        return {
            "ok": False,
            "error": message or "记录分析失败，没有保存监测事件。",
            "analysis": None,
            "event": None,
            "saved": False,
            "duplicate": False,
            "reminder_required": False,
        }
    if not _analysis_matches_record(analysis, identity):
        raise MonitoringEventError("记录分析没有返回可核对的观测结果，没有保存监测事件。")
    metrics = deviation_metrics(analysis)
    public = _public_analysis(analysis)
    observation = analysis["observation"]
    device_id = str(observation.get("device_id") or "")
    if not metrics:
        # 当前记录无偏离：结束该设备在本会话中的持续异常过程，允许之后再次提醒。
        _close_alert_episodes(session, device_id, EVENT_TYPE)
        return {
            "ok": True,
            "session_id": session,
            "record_id": identity,
            "analysis": public,
            "event": None,
            "saved": False,
            "duplicate": False,
            "reminder_required": False,
        }
    fields = {str(item.get("field") or "").strip() for item in metrics if isinstance(item, dict) and str(item.get("field") or "").strip()}
    started = _open_alert_episodes(session, device_id, EVENT_TYPE, fields)
    reminder_required = len(started) > 0
    episode_key = _episode_key(session, device_id, EVENT_TYPE)
    # 统计过程开始 ≠ 故障告警；告警资格仅由后续结构化确认写入。
    from app.services.episode_diagnosis import episode_diagnosis_enabled

    initial_phase = (
        "diagnosing" if reminder_required and episode_diagnosis_enabled() else "statistical_deviation"
    )
    deviation = analysis["statistical_deviation"]
    trace = _diagnosis_trace(analysis, diagnosis_phase=initial_phase, fault_alert_required=False)
    payload = {
        "session_id": session,
        "record_id": identity,
        "device_id": device_id,
        "collected_at": observation["collected_at"],
        "processed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "event_type": EVENT_TYPE,
        "diagnosis_level": analysis["diagnosis_level"],
        "detection_status": deviation["detection_status"],
        "data_source": str(analysis.get("data_source") or ""),
        "event_scope": EVENT_SCOPE,
        "event_note": EVENT_NOTE,
        "metrics_json": json.dumps(metrics, ensure_ascii=False, separators=(",", ":")),
        "clues_json": json.dumps(_public_clues(analysis.get("diagnostic_clues")), ensure_ascii=False, separators=(",", ":")),
        "candidate_faults_json": json.dumps(
            _public_candidates(analysis.get("candidate_faults")),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "diagnosis_json": json.dumps(trace, ensure_ascii=False, separators=(",", ":")),
        "reminder_required": 1 if reminder_required else 0,
        "fault_alert_required": 0,
        "diagnosis_phase": initial_phase,
        "assessment_id": "",
        "episode_key": episode_key,
    }
    event, saved, duplicate = _save_event(payload)
    if isinstance(event, dict):
        # reminder_required 仅标记统计异常过程起点，不触发故障弹窗。
        event["reminder_required"] = bool(reminder_required) and saved and not duplicate
        event["fault_alert_required"] = False
    if reminder_required and saved and not duplicate and isinstance(event, dict):
        from app.services.episode_diagnosis import schedule_episode_diagnosis

        schedule_episode_diagnosis(
            session_id=session,
            device_id=device_id,
            record_id=identity,
            event_type=EVENT_TYPE,
            event_id=int(event["event_id"]),
            background=True,
        )
    return {
        "ok": True,
        "session_id": session,
        "record_id": identity,
        "analysis": public,
        "event": event,
        "saved": saved,
        "duplicate": duplicate,
        "reminder_required": bool(event.get("reminder_required")) if isinstance(event, dict) else False,
        "fault_alert_required": bool(event.get("fault_alert_required")) if isinstance(event, dict) else False,
    }


def clear_alert_episodes(session_id: str) -> None:
    """会话结束时清空提醒过程，不影响已保存事件。"""
    session = str(session_id or "").strip()
    if not session:
        return
    prefix = f"{session}|"
    with _ALERT_EPISODE_LOCK:
        for key in [item for item in _ALERT_EPISODES if item.startswith(prefix)]:
            _ALERT_EPISODES.pop(key, None)
    try:
        from app.services.episode_diagnosis import clear_episode_diagnosis_state

        clear_episode_diagnosis_state(session)
    except Exception:
        return


def apply_episode_diagnosis_result(event_id: int, result: dict) -> dict | None:
    """把过程诊断结果写回监测事件。同一 episode 仅首次 fault_alert_required 可置 true。"""
    if not isinstance(event_id, int) or event_id <= 0 or not isinstance(result, dict):
        return None
    confirmation = result.get("confirmation") if isinstance(result.get("confirmation"), dict) else {}
    phase = str(confirmation.get("diagnosis_phase") or result.get("diagnosis_phase") or "diagnosis_failed")
    want_alert = confirmation.get("fault_alert_required") is True or result.get("fault_alert_required") is True
    assessment_id = str(result.get("assessment_id") or "")
    episode_key = str(result.get("episode_key") or "")
    analysis = result.get("analysis") if isinstance(result.get("analysis"), dict) else {}
    conn = _connect(database_path())
    try:
        row = conn.execute(_SELECT_BY_ID, (event_id,)).fetchone()
        if row is None:
            return None
        keys = set(row.keys())
        existing_alert = bool(row["fault_alert_required"]) if "fault_alert_required" in keys else False
        # 已告警的事件保持告警资格；新确认仅在本 episode 尚无告警时写入一次。
        alert_allowed = existing_alert
        if want_alert and not existing_alert:
            alert_allowed = True
            if episode_key:
                prior = conn.execute(
                    """
                    SELECT event_id FROM monitoring_events
                    WHERE episode_key = ? AND fault_alert_required = 1 AND event_id != ?
                    LIMIT 1
                    """,
                    (episode_key, event_id),
                ).fetchone()
                if prior is not None:
                    alert_allowed = False
        candidates_json = row["candidate_faults_json"]
        clues_json = row["clues_json"]
        if analysis.get("ok") is True:
            if isinstance(analysis.get("candidate_faults"), list):
                candidates_json = json.dumps(
                    _public_candidates(analysis.get("candidate_faults")),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            if isinstance(analysis.get("diagnostic_clues"), list):
                clues_json = json.dumps(
                    _public_clues(analysis.get("diagnostic_clues")),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
        trace = _diagnosis_trace(
            analysis if analysis.get("ok") is True else {},
            diagnosis_phase=phase,
            fault_alert_required=alert_allowed,
            confirmation=confirmation,
            diagnosis_error=str(result.get("diagnosis_error") or ""),
            assessment_id=assessment_id,
        )
        diagnosis_level = str(analysis.get("diagnosis_level") or row["diagnosis_level"])
        conn.execute(
            """
            UPDATE monitoring_events
            SET clues_json = ?, candidate_faults_json = ?, diagnosis_json = ?,
                diagnosis_level = ?, fault_alert_required = ?, diagnosis_phase = ?,
                assessment_id = ?, episode_key = CASE WHEN ? != '' THEN ? ELSE episode_key END,
                processed_at = ?
            WHERE event_id = ?
            """,
            (
                clues_json,
                candidates_json,
                json.dumps(trace, ensure_ascii=False, separators=(",", ":")),
                diagnosis_level,
                1 if alert_allowed else 0,
                phase,
                assessment_id,
                episode_key,
                episode_key,
                datetime.now().astimezone().isoformat(timespec="seconds"),
                event_id,
            ),
        )
        stored = conn.execute(_SELECT_BY_ID, (event_id,)).fetchone()
        return _row_to_event(stored) if stored is not None else None
    except sqlite3.Error as exc:
        raise MonitoringEventError(f"诊断结果未能写回监测事件：{exc}") from exc
    finally:
        conn.close()


def _episode_key(session_id: str, device_id: str, event_type: str) -> str:
    return f"{session_id}|{device_id}|{event_type}"


def _open_alert_episodes(session_id: str, device_id: str, event_type: str, fields: set[str]) -> set[str]:
    """更新持续异常集合，返回新进入异常的指标。"""
    key = _episode_key(session_id, device_id, event_type)
    with _ALERT_EPISODE_LOCK:
        previous = set(_ALERT_EPISODES.get(key, set()))
        started = {field for field in fields if field not in previous}
        # 当前记录的偏离指标成为新的持续集合；未再出现的指标视为已恢复。
        _ALERT_EPISODES[key] = set(fields)
        return started


def _close_alert_episodes(session_id: str, device_id: str, event_type: str) -> None:
    key = _episode_key(session_id, device_id, event_type)
    with _ALERT_EPISODE_LOCK:
        _ALERT_EPISODES.pop(key, None)


def list_deviation_record_ids(device_id: str | None = None) -> set[str]:
    """返回已保存且检测状态为偏离基线的记录编号。只读，不修改事件库。"""
    device = None if device_id is None else _optional_token(device_id, "设备编号")
    clauses = ["detection_status = ?"]
    params: list[object] = ["偏离基线"]
    if device is not None:
        clauses.append("device_id = ?")
        params.append(device)
    query = f"SELECT DISTINCT record_id FROM monitoring_events WHERE {' AND '.join(clauses)}"
    conn = _connect(database_path())
    try:
        try:
            rows = conn.execute(query, params).fetchall()
        except sqlite3.Error as exc:
            raise MonitoringEventError(f"监测事件没有查询成功：{exc}") from exc
    finally:
        conn.close()
    return {str(row["record_id"]) for row in rows if row["record_id"]}


def list_monitoring_events(session_id: str | None, device_id: str | None, limit: int) -> dict:
    session = None if session_id is None else _session_id(session_id)
    device = None if device_id is None else _optional_token(device_id, "设备编号")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 100:
        raise MonitoringEventError("limit 必须是 1 到 100 的整数。")
    clauses: list[str] = []
    params: list[object] = []
    if session is not None:
        clauses.append("session_id = ?")
        params.append(session)
    if device is not None:
        clauses.append("device_id = ?")
        params.append(device)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    query = (
        f"SELECT {_EVENT_COLUMNS} "
        f"FROM monitoring_events{where} ORDER BY event_id DESC LIMIT ?"
    )
    conn = _connect(database_path())
    try:
        try:
            rows = conn.execute(query, params).fetchall()
        except sqlite3.Error as exc:
            raise MonitoringEventError(f"监测事件没有查询成功：{exc}") from exc
    finally:
        conn.close()
    events = [_row_to_event(row) for row in rows]
    return {
        "session_id": session,
        "device_id": device,
        "count": len(events),
        "limit": limit,
        "events": events,
    }


def deviation_metrics(analysis: dict) -> list[dict]:
    """只收集指标状态确实为偏离基线的项。基线不足、无法判定和未发现显著偏离都不收集。"""
    deviation = analysis.get("statistical_deviation")
    status = deviation.get("detection_status") if isinstance(deviation, dict) else None
    if not isinstance(deviation, dict) or status in _NO_EVENT_STATUSES or status != "偏离基线":
        return []
    items = deviation.get("analyzed_metrics")
    if not isinstance(items, list):
        return []
    metrics = []
    for item in items:
        if not isinstance(item, dict) or item.get("metric_status") != "偏离基线":
            continue
        field = item.get("field")
        if not isinstance(field, str) or not field or field in _ANSWER_LABELS:
            continue
        metrics.append({key: item.get(key) for key in _METRIC_KEYS})
    return metrics


def _session_id(value: object) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise MonitoringEventError("请提供会话标识 session_id。")
    if _SESSION_PATTERN.fullmatch(text) is None or "/" in text or "\\" in text or ".." in text:
        raise MonitoringEventError("会话标识格式不正确。")
    return text


def _optional_token(value: str, label: str) -> str:
    text = value.strip()
    if not text or _SESSION_PATTERN.fullmatch(text) is None or "/" in text or "\\" in text or ".." in text:
        raise MonitoringEventError(f"{label}格式不正确。")
    return text


def _analysis_matches_record(analysis: dict, record_id: str) -> bool:
    observation = analysis.get("observation")
    deviation = analysis.get("statistical_deviation")
    status = deviation.get("detection_status") if isinstance(deviation, dict) else None
    return (
        analysis.get("record_id") == record_id
        and isinstance(observation, dict)
        and observation.get("record_id") == record_id
        and isinstance(observation.get("device_id"), str)
        and bool(observation.get("device_id"))
        and isinstance(observation.get("collected_at"), str)
        and bool(observation.get("collected_at"))
        and isinstance(deviation, dict)
        and deviation.get("record_id") == record_id
        and isinstance(status, str)
        and bool(status)
        and isinstance(analysis.get("diagnostic_clues"), list)
        and isinstance(analysis.get("candidate_faults"), list)
        and analysis.get("confirmed") is not True
    )


def _public_analysis(analysis: dict) -> dict:
    return _scrub(dict(analysis))


def _public_clues(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    clues = []
    for item in value:
        if not isinstance(item, dict):
            continue
        clues.append({key: item.get(key) for key in _CLUE_KEYS})
    return clues


def _diagnosis_trace(
    analysis: dict,
    *,
    diagnosis_phase: str = "",
    fault_alert_required: bool = False,
    confirmation: dict | None = None,
    diagnosis_error: str = "",
    assessment_id: str = "",
) -> dict:
    """整理分析痕迹与结构化确认结果。不把 possible_match 写成确诊。"""
    clues = _public_clues(analysis.get("diagnostic_clues")) if isinstance(analysis, dict) else []
    candidates = _public_candidates(analysis.get("candidate_faults")) if isinstance(analysis, dict) else []
    level = str(analysis.get("diagnosis_level") or "") if isinstance(analysis, dict) else ""
    phase = diagnosis_phase or "statistical_deviation"
    if fault_alert_required and phase == "confirmed_fault":
        status = "结构化规则已确认故障，允许故障告警。"
    elif phase == "diagnosis_failed":
        status = diagnosis_error.strip() or "诊断失败或模型不可用。"
    elif phase == "diagnosing":
        status = "已发现统计异常过程，正在进行智能体诊断。"
    elif candidates and level == "possible_match":
        status = _MATCH_STATUS
    elif clues:
        status = _CLUE_STATUS
    else:
        status = _INSUFFICIENT_STATUS
    missing: list[str] = []
    if isinstance(analysis, dict):
        for item in analysis.get("missing_data") or []:
            if isinstance(item, str) and item.strip() and item.strip() not in missing:
                missing.append(item.strip())
        for item in analysis.get("insufficient_reasons") or []:
            if isinstance(item, str) and item.strip() and item.strip() not in missing:
                missing.append(item.strip())
    if confirmation and isinstance(confirmation.get("reasons"), list):
        for item in confirmation.get("reasons") or []:
            if isinstance(item, str) and item.strip() and item.strip() not in missing:
                missing.append(item.strip())
    if diagnosis_error.strip() and diagnosis_error.strip() not in missing:
        missing.append(diagnosis_error.strip())
    if not fault_alert_required and not candidates and _INSUFFICIENT_STATUS not in missing and phase != "diagnosing":
        missing.append(_INSUFFICIENT_STATUS)
    return {
        "diagnosis_status": status,
        "evidence_sources": _evidence_sources(clues),
        "missing_conditions": missing,
        "confirmed": False,
        "diagnosis_phase": phase,
        "fault_alert_required": bool(fault_alert_required),
        "confirmation": confirmation if isinstance(confirmation, dict) else {},
        "assessment_id": assessment_id,
        "diagnosis_error": diagnosis_error.strip(),
    }


def _evidence_sources(clues: list[dict]) -> list[dict]:
    sources = []
    for clue in clues:
        document = clue.get("document")
        line_number = clue.get("line_number")
        if not isinstance(document, str) or not document.strip() or isinstance(line_number, bool) or not isinstance(line_number, int):
            continue
        sources.append(
            {
                "document": document,
                "line_number": line_number,
                "feature_code": clue.get("feature_code") or "",
                "related_fault_section": clue.get("related_fault_section") or "",
            }
        )
    return sources


def _public_candidates(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    candidates = []
    for item in value:
        if not isinstance(item, dict) or item.get("confirmed") is True:
            continue
        fault = item.get("fault")
        if not isinstance(fault, str) or not fault.strip():
            continue
        candidates.append({key: item.get(key) for key in _CANDIDATE_KEYS})
    return candidates


def _save_event(payload: dict) -> tuple[dict, bool, bool]:
    conn = _connect(database_path())
    try:
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                _SELECT_EVENT,
                (payload["session_id"], payload["record_id"], payload["event_type"]),
            ).fetchone()
            if existing is not None:
                conn.execute("COMMIT")
                return _row_to_event(existing), False, True
            cursor = conn.execute(
                _INSERT_EVENT,
                (
                    payload["session_id"],
                    payload["record_id"],
                    payload["device_id"],
                    payload["collected_at"],
                    payload["processed_at"],
                    payload["event_type"],
                    payload["diagnosis_level"],
                    payload["detection_status"],
                    payload["data_source"],
                    payload["event_scope"],
                    payload["event_note"],
                    payload["metrics_json"],
                    payload["clues_json"],
                    payload["candidate_faults_json"],
                    payload["diagnosis_json"],
                    int(payload.get("reminder_required") or 0),
                    int(payload.get("fault_alert_required") or 0),
                    str(payload.get("diagnosis_phase") or ""),
                    str(payload.get("assessment_id") or ""),
                    str(payload.get("episode_key") or ""),
                ),
            )
            stored = conn.execute(_SELECT_BY_ID, (cursor.lastrowid,)).fetchone()
            if stored is None:
                raise MonitoringEventError("监测事件没有写入数据库。")
            conn.execute("COMMIT")
            return _row_to_event(stored), True, False
        except MonitoringEventError:
            _rollback(conn)
            raise
        except sqlite3.IntegrityError:
            _rollback(conn)
            existing = conn.execute(
                _SELECT_EVENT,
                (payload["session_id"], payload["record_id"], payload["event_type"]),
            ).fetchone()
            if existing is None:
                raise MonitoringEventError("监测事件没有写入数据库。") from None
            return _row_to_event(existing), False, True
        except sqlite3.Error as exc:
            _rollback(conn)
            raise MonitoringEventError(f"监测事件没有写入数据库：{exc}") from exc
    finally:
        conn.close()


def _rollback(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error:
        return


def _connect(path: Path) -> sqlite3.Connection:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=5, isolation_level=None)
    except OSError as exc:
        raise MonitoringEventError(f"监测事件数据库无法打开：{exc}") from exc
    except sqlite3.Error as exc:
        raise MonitoringEventError(f"监测事件数据库无法打开：{exc}") from exc
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(_CREATE_TABLE)
        _ensure_schema_columns(conn)
        conn.execute(_CREATE_SESSION_INDEX)
        conn.execute(_CREATE_DEVICE_INDEX)
    except sqlite3.Error as exc:
        conn.close()
        raise MonitoringEventError(f"监测事件数据库无法初始化：{exc}") from exc
    return conn


def _row_to_event(row: sqlite3.Row) -> dict:
    try:
        metrics = json.loads(row["metrics_json"])
        clues = json.loads(row["clues_json"])
        candidates = json.loads(row["candidate_faults_json"])
        trace = _stored_trace(row["diagnosis_json"])
    except json.JSONDecodeError as exc:
        raise MonitoringEventError("监测事件数据库中的记录无法读取。") from exc
    if not isinstance(metrics, list) or not isinstance(clues, list) or not isinstance(candidates, list):
        raise MonitoringEventError("监测事件数据库中的记录无法读取。")
    keys = set(row.keys())
    reminder = bool(row["reminder_required"]) if "reminder_required" in keys else False
    fault_alert = bool(row["fault_alert_required"]) if "fault_alert_required" in keys else bool(trace.get("fault_alert_required"))
    phase = str(row["diagnosis_phase"]) if "diagnosis_phase" in keys and row["diagnosis_phase"] else str(trace.get("diagnosis_phase") or "")
    assessment_id = str(row["assessment_id"]) if "assessment_id" in keys else str(trace.get("assessment_id") or "")
    episode_key = str(row["episode_key"]) if "episode_key" in keys else ""
    return _scrub(
        {
            "event_id": row["event_id"],
            "session_id": row["session_id"],
            "record_id": row["record_id"],
            "device_id": row["device_id"],
            "collected_at": row["collected_at"],
            "processed_at": row["processed_at"],
            "event_type": row["event_type"],
            "diagnosis_level": row["diagnosis_level"],
            "diagnosis_available": trace["diagnosis_available"],
            "diagnosis_status": trace["diagnosis_status"],
            "detection_status": row["detection_status"],
            "data_source": row["data_source"],
            "event_scope": row["event_scope"],
            "event_note": row["event_note"],
            "metrics": metrics,
            "diagnostic_clues": clues,
            "evidence_sources": trace["evidence_sources"],
            "missing_conditions": trace["missing_conditions"],
            "candidate_faults": candidates,
            "confirmed": False,
            "reminder_required": reminder,
            "fault_alert_required": fault_alert,
            "diagnosis_phase": phase,
            "assessment_id": assessment_id,
            "episode_key": episode_key,
        }
    )


def _ensure_schema_columns(conn: sqlite3.Connection) -> None:
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(monitoring_events)")}
    if "diagnosis_json" not in columns:
        conn.execute("ALTER TABLE monitoring_events ADD COLUMN diagnosis_json TEXT NOT NULL DEFAULT ''")
    if "reminder_required" not in columns:
        conn.execute(
            "ALTER TABLE monitoring_events ADD COLUMN reminder_required INTEGER NOT NULL DEFAULT 0"
        )
    if "fault_alert_required" not in columns:
        conn.execute(
            "ALTER TABLE monitoring_events ADD COLUMN fault_alert_required INTEGER NOT NULL DEFAULT 0"
        )
    if "diagnosis_phase" not in columns:
        conn.execute("ALTER TABLE monitoring_events ADD COLUMN diagnosis_phase TEXT NOT NULL DEFAULT ''")
    if "assessment_id" not in columns:
        conn.execute("ALTER TABLE monitoring_events ADD COLUMN assessment_id TEXT NOT NULL DEFAULT ''")
    if "episode_key" not in columns:
        conn.execute("ALTER TABLE monitoring_events ADD COLUMN episode_key TEXT NOT NULL DEFAULT ''")


def _stored_trace(raw: object) -> dict:
    """只读取已经写入的诊断痕迹。空字段不另做诊断。"""
    if isinstance(raw, str) and raw.strip():
        parsed = json.loads(raw)
        status = parsed.get("diagnosis_status") if isinstance(parsed, dict) else None
        if isinstance(status, str) and status.strip():
            sources = parsed.get("evidence_sources")
            missing = parsed.get("missing_conditions")
            return {
                "diagnosis_available": True,
                "diagnosis_status": status,
                "evidence_sources": sources if isinstance(sources, list) else [],
                "missing_conditions": missing if isinstance(missing, list) else [],
                "confirmed": False,
                "diagnosis_phase": str(parsed.get("diagnosis_phase") or "") if isinstance(parsed, dict) else "",
                "fault_alert_required": parsed.get("fault_alert_required") is True if isinstance(parsed, dict) else False,
                "assessment_id": str(parsed.get("assessment_id") or "") if isinstance(parsed, dict) else "",
                "confirmation": parsed.get("confirmation") if isinstance(parsed, dict) and isinstance(parsed.get("confirmation"), dict) else {},
                "diagnosis_error": str(parsed.get("diagnosis_error") or "") if isinstance(parsed, dict) else "",
            }
    return {
        "diagnosis_available": False,
        "diagnosis_status": "",
        "evidence_sources": [],
        "missing_conditions": [],
        "confirmed": False,
        "diagnosis_phase": "",
        "fault_alert_required": False,
        "assessment_id": "",
        "confirmation": {},
        "diagnosis_error": "",
    }


def _assert_runtime_location(path: Path) -> Path:
    root = Path(__file__).resolve().parents[3]
    resolved = path if path.is_absolute() else root / path
    try:
        resolved = resolved.resolve()
    except OSError as exc:
        raise MonitoringEventError(f"监测事件数据库无法打开：{exc}") from exc
    forbidden = (
        (root / "data" / "official").resolve(),
        (root / "data" / "official_dataset").resolve(),
        (root / "data" / "processed").resolve(),
    )
    if any(resolved == base or base in resolved.parents for base in forbidden):
        raise MonitoringEventError("监测事件数据库不能放在官方数据目录。")
    return resolved


def _scrub(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _scrub(item)
            for key, item in value.items()
            if key not in _ANSWER_LABELS and "api_key" not in key.casefold() and "authorization" not in key.casefold()
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value
