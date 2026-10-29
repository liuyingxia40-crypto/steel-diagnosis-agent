"""按官方开发集的历史记录自动连续做已有异常分析。

后台线程逐条调用 process_monitoring_record，不在 HTTP 请求里循环，
也不调用 DeepSeek，不把统计偏离写成故障确诊。
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime

from app.services.monitoring_events import (
    MonitoringEventError,
    clear_alert_episodes,
    database_path,
    process_monitoring_record,
)
from app.services.official_dataset import DatasetError, list_device_record_ids

RUNNING = "运行中"
STOPPING = "正在停止"
STOPPED = "已停止"
FINISHED = "已结束"
FAILED = "已出错"
INTERRUPTED = "已中断"
_ACTIVE = frozenset({RUNNING, STOPPING})
_TERMINAL = frozenset({STOPPED, FINISHED, FAILED, INTERRUPTED})
_NOTE = "这是官方开发集历史数据的自动回放分析，没有连接实时传感器，统计偏离也不是故障确诊。"
_INTERRUPTED_NOTE = "服务关闭或重启后，该监测任务已停止，没有继续处理记录。"
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS automatic_monitoring_tasks (
    task_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    status TEXT NOT NULL,
    current_record_id TEXT NOT NULL,
    processed_count INTEGER NOT NULL,
    deviation_count INTEGER NOT NULL,
    total_count INTEGER NOT NULL,
    interval_seconds REAL NOT NULL,
    last_processed_at TEXT NOT NULL,
    error_message TEXT NOT NULL,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""
_CREATE_DEVICE_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_automatic_monitoring_device ON automatic_monitoring_tasks (device_id)"
)

_lock = threading.Lock()
_live: dict[str, "_Worker"] = {}
_shutting_down = False


class AutomaticMonitoringError(Exception):
    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class _Worker:
    def __init__(self, task_id: str, device_id: str) -> None:
        self.task_id = task_id
        self.device_id = device_id
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None


def start_automatic_monitoring(device_id: str, interval_seconds: float = 1.0) -> dict:
    """启动一台设备的历史回放。同一设备已有活动任务时拒绝第二次启动。"""
    identity, record_ids = _device_sequence(device_id)
    interval = _interval(interval_seconds)
    with _lock:
        if _shutting_down:
            raise AutomaticMonitoringError("服务正在关闭，不能启动新的自动监测。", 503)
        current = _latest_unlocked(identity, None)
        if current is not None and current["status"] in _ACTIVE:
            raise AutomaticMonitoringError("这台设备已有自动监测任务正在运行。", 409)
        if identity in _live:
            raise AutomaticMonitoringError("这台设备已有自动监测任务正在运行。", 409)
        now = _now()
        task_id = f"auto-{uuid.uuid4().hex}"
        _insert_unlocked(
            {
                "task_id": task_id,
                "device_id": identity,
                "status": RUNNING,
                "current_record_id": "",
                "processed_count": 0,
                "deviation_count": 0,
                "total_count": len(record_ids),
                "interval_seconds": interval,
                "last_processed_at": "",
                "error_message": "",
                "started_at": now,
                "updated_at": now,
            }
        )
        worker = _Worker(task_id, identity)
        thread = threading.Thread(
            target=_run,
            args=(worker, tuple(record_ids), interval),
            name=f"auto-monitor-{identity}",
            daemon=True,
        )
        worker.thread = thread
        _live[identity] = worker
        thread.start()
        stored = _task_unlocked(task_id)
    if stored is None:
        raise AutomaticMonitoringError("自动监测任务没有记录下来。", 500)
    return stored


def stop_automatic_monitoring(device_id: str | None = None, task_id: str | None = None) -> dict:
    """请求停止。当前正在分析的那一条会完成，之后不再处理后续记录。"""
    with _lock:
        stored = _resolve_unlocked(device_id, task_id)
        if stored is None:
            raise AutomaticMonitoringError("没有找到可停止的自动监测任务。", 404)
        if stored["status"] not in _ACTIVE:
            return stored
        worker = _live.get(stored["device_id"])
        if worker is None or worker.task_id != stored["task_id"]:
            return _write_unlocked(
                stored["task_id"],
                status=INTERRUPTED,
                error_message=_INTERRUPTED_NOTE,
            )
        worker.stop.set()
        return _write_unlocked(stored["task_id"], status=STOPPING, error_message="")


def task_status(device_id: str | None = None, task_id: str | None = None) -> dict | list[dict]:
    with _lock:
        if device_id or task_id:
            stored = _resolve_unlocked(device_id, task_id)
            if stored is None:
                raise AutomaticMonitoringError("没有找到自动监测任务。", 404)
            return stored
        return _all_unlocked()


def recover_abandoned_tasks() -> int:
    """进程启动时把上次留下的运行中标记改成已中断。当前线程仍在跑的任务不动。"""
    with _lock:
        live_ids = {worker.task_id for worker in _live.values()}
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT task_id FROM automatic_monitoring_tasks WHERE status IN (?, ?)",
                (RUNNING, STOPPING),
            ).fetchall()
            changed = 0
            now = _now()
            for row in rows:
                if row["task_id"] in live_ids:
                    continue
                conn.execute(
                    """
                    UPDATE automatic_monitoring_tasks
                    SET status = ?, error_message = ?, updated_at = ?
                    WHERE task_id = ? AND status IN (?, ?)
                    """,
                    (INTERRUPTED, _INTERRUPTED_NOTE, now, row["task_id"], RUNNING, STOPPING),
                )
                changed += 1
            return changed
        finally:
            conn.close()


def shutdown_automatic_monitoring() -> None:
    """服务关闭时停止后台线程，不把任务留在运行中。"""
    global _shutting_down
    with _lock:
        _shutting_down = True
        threads = []
        for worker in _live.values():
            worker.stop.set()
            if worker.thread is not None:
                threads.append(worker.thread)
    for thread in threads:
        thread.join(timeout=3)
    with _lock:
        now = _now()
        conn = _connect()
        try:
            conn.execute(
                """
                UPDATE automatic_monitoring_tasks
                SET status = ?, error_message = ?, updated_at = ?
                WHERE status IN (?, ?)
                """,
                (INTERRUPTED, _INTERRUPTED_NOTE, now, RUNNING, STOPPING),
            )
        finally:
            conn.close()
        _live.clear()


def _run(worker: _Worker, record_ids: tuple[str, ...], interval: float) -> None:
    processed = 0
    deviations = 0
    current = ""
    try:
        for index, record_id in enumerate(record_ids):
            if _should_stop(worker):
                _finish_stop(worker.task_id, processed, deviations, current)
                return
            try:
                # 诊断线索来自这一次分析，监测线程不再单独调用诊断。
                result = process_monitoring_record(worker.task_id, record_id)
            except MonitoringEventError as exc:
                _write(worker.task_id, status=FAILED, current_record_id=record_id, processed_count=processed, deviation_count=deviations, error_message=exc.message)
                return
            if not isinstance(result, dict) or result.get("ok") is not True:
                message = "记录分析失败，自动监测已停止，没有把这条记录写成异常事件。"
                if isinstance(result, dict) and isinstance(result.get("error"), str) and result["error"].strip():
                    message = result["error"]
                _write(
                    worker.task_id,
                    status=FAILED,
                    current_record_id=record_id,
                    processed_count=processed,
                    deviation_count=deviations,
                    error_message=message,
                )
                return
            processed += 1
            current = record_id
            if result.get("saved") is True:
                deviations += 1
            _write(
                worker.task_id,
                status=RUNNING,
                current_record_id=current,
                processed_count=processed,
                deviation_count=deviations,
                last_processed_at=_now(),
                error_message="",
            )
            if _should_stop(worker):
                _finish_stop(worker.task_id, processed, deviations, current)
                return
            if interval > 0 and index < len(record_ids) - 1 and worker.stop.wait(interval):
                _finish_stop(worker.task_id, processed, deviations, current)
                return
            if _shutting_down:
                _finish_stop(worker.task_id, processed, deviations, current)
                return
        _write(
            worker.task_id,
            status=FINISHED,
            current_record_id=current,
            processed_count=processed,
            deviation_count=deviations,
            last_processed_at=_now(),
            error_message="",
        )
    except Exception:
        _write(
            worker.task_id,
            status=FAILED,
            current_record_id=current,
            processed_count=processed,
            deviation_count=deviations,
            error_message="自动监测在处理记录时中断，没有把未完成的记录写成异常事件。",
        )
    finally:
        clear_alert_episodes(worker.task_id)
        with _lock:
            live = _live.get(worker.device_id)
            if live is worker:
                _live.pop(worker.device_id, None)


def _should_stop(worker: _Worker) -> bool:
    return worker.stop.is_set() or _shutting_down


def _finish_stop(task_id: str, processed: int, deviations: int, current: str) -> None:
    status = INTERRUPTED if _shutting_down else STOPPED
    message = _INTERRUPTED_NOTE if status == INTERRUPTED else ""
    _write(
        task_id,
        status=status,
        current_record_id=current,
        processed_count=processed,
        deviation_count=deviations,
        error_message=message,
    )


def _device_sequence(device_id: str) -> tuple[str, list[str]]:
    try:
        listed = list_device_record_ids(device_id)
    except DatasetError as exc:
        raise AutomaticMonitoringError(exc.message, 422) from None
    if listed.get("found") is not True:
        raise AutomaticMonitoringError(f"没有找到设备编号「{listed.get('device_id') or device_id}」。", 404)
    record_ids = listed.get("record_ids")
    if not isinstance(record_ids, list) or not record_ids or listed.get("record_count") != len(record_ids):
        raise AutomaticMonitoringError("这台设备没有可回放的历史记录。", 422)
    if len(set(record_ids)) != len(record_ids):
        raise AutomaticMonitoringError("设备历史记录编号重复，自动监测已停止。", 422)
    return str(listed["device_id"]), [str(item) for item in record_ids]


def _interval(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AutomaticMonitoringError("回放间隔必须是 0 到 60 秒。", 422)
    number = float(value)
    if number < 0 or number > 60:
        raise AutomaticMonitoringError("回放间隔必须是 0 到 60 秒。", 422)
    return number


def _write(task_id: str, **fields: object) -> dict:
    with _lock:
        return _write_unlocked(task_id, **fields)


def _write_unlocked(task_id: str, **fields: object) -> dict:
    current = _task_unlocked(task_id)
    if current is None:
        raise AutomaticMonitoringError("没有找到自动监测任务。", 404)
    status = fields.get("status", current["status"])
    if status == RUNNING and current["status"] == STOPPING:
        status = STOPPING
    elif status == RUNNING and current["status"] in _TERMINAL:
        return current
    elif status == RUNNING and _shutting_down:
        status = INTERRUPTED
        fields["error_message"] = _INTERRUPTED_NOTE
    payload = {
        "status": status,
        "current_record_id": fields.get("current_record_id", current["current_record_id"] or ""),
        "processed_count": fields.get("processed_count", current["processed_count"]),
        "deviation_count": fields.get("deviation_count", current["deviation_count"]),
        "last_processed_at": fields.get("last_processed_at", current["last_processed_at"] or ""),
        "error_message": fields.get("error_message", current["error_message"] or ""),
        "updated_at": _now(),
    }
    if payload["status"] == INTERRUPTED and not payload["error_message"]:
        payload["error_message"] = _INTERRUPTED_NOTE
    conn = _connect()
    try:
        conn.execute(
            """
            UPDATE automatic_monitoring_tasks
            SET status = ?, current_record_id = ?, processed_count = ?, deviation_count = ?,
                last_processed_at = ?, error_message = ?, updated_at = ?
            WHERE task_id = ?
            """,
            (
                payload["status"],
                payload["current_record_id"] or "",
                payload["processed_count"],
                payload["deviation_count"],
                payload["last_processed_at"] or "",
                payload["error_message"] or "",
                payload["updated_at"],
                task_id,
            ),
        )
    finally:
        conn.close()
    stored = _task_unlocked(task_id)
    if stored is None:
        raise AutomaticMonitoringError("自动监测状态没有写回。", 500)
    return stored


def _resolve_unlocked(device_id: str | None, task_id: str | None) -> dict | None:
    if task_id:
        stored = _task_unlocked(task_id.strip())
        if stored is None:
            return None
        if device_id and stored["device_id"] != device_id.strip():
            return None
        return stored
    if device_id:
        return _latest_unlocked(device_id.strip(), None)
    return None


def _latest_unlocked(device_id: str, task_id: str | None) -> dict | None:
    if task_id:
        return _task_unlocked(task_id)
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT * FROM automatic_monitoring_tasks
            WHERE device_id = ?
            ORDER BY updated_at DESC, started_at DESC
            LIMIT 1
            """,
            (device_id,),
        ).fetchone()
    finally:
        conn.close()
    return None if row is None else _public(row)


def _task_unlocked(task_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM automatic_monitoring_tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    finally:
        conn.close()
    return None if row is None else _public(row)


def _all_unlocked() -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM automatic_monitoring_tasks ORDER BY updated_at DESC, started_at DESC"
        ).fetchall()
    finally:
        conn.close()
    return [_public(row) for row in rows]


def _insert_unlocked(payload: dict) -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO automatic_monitoring_tasks (
                task_id, device_id, status, current_record_id, processed_count,
                deviation_count, total_count, interval_seconds, last_processed_at,
                error_message, started_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["task_id"],
                payload["device_id"],
                payload["status"],
                payload["current_record_id"],
                payload["processed_count"],
                payload["deviation_count"],
                payload["total_count"],
                payload["interval_seconds"],
                payload["last_processed_at"],
                payload["error_message"],
                payload["started_at"],
                payload["updated_at"],
            ),
        )
    finally:
        conn.close()


def _public(row: sqlite3.Row) -> dict:
    current = row["current_record_id"] or None
    processed_at = row["last_processed_at"] or None
    return {
        "task_id": row["task_id"],
        "device_id": row["device_id"],
        "status": row["status"],
        "current_record_id": current,
        "processed_count": row["processed_count"],
        "deviation_count": row["deviation_count"],
        "total_count": row["total_count"],
        "interval_seconds": row["interval_seconds"],
        "last_processed_at": processed_at,
        "error_message": row["error_message"] or "",
        "monitoring_note": _NOTE,
    }


def _connect() -> sqlite3.Connection:
    path = database_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=5, isolation_level=None)
    except (OSError, sqlite3.Error) as exc:
        raise AutomaticMonitoringError(f"自动监测状态无法写入：{exc}", 500) from exc
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(_CREATE_TABLE)
        conn.execute(_CREATE_DEVICE_INDEX)
    except sqlite3.Error as exc:
        conn.close()
        raise AutomaticMonitoringError(f"自动监测状态无法写入：{exc}", 500) from exc
    return conn


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
