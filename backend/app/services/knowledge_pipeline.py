"""把已有的清洗、抽取、预演和安全入图按顺序执行。

不新增抽取规则，不改写基础图谱文件。清洗状态仍由 processing_status 表示。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime

from app.services.knowledge_cleaner import clean_uploaded_file, cleaned_preview
from app.services.knowledge_extractor import extract_knowledge
from app.services.knowledge_file_store import (
    CLEANED_STATUS,
    KnowledgeFileError,
    database_path,
    get_uploaded_record,
    valid_file_id,
)
from app.services.knowledge_graph_import import apply_import
from app.services.knowledge_graph_import_preview import preview_knowledge_import

pipeline_pause = threading.Event()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_processes (
    process_id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL,
    pipeline_status TEXT NOT NULL,
    stage TEXT NOT NULL,
    outcome TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    entity_count INTEGER NOT NULL DEFAULT 0,
    relation_count INTEGER NOT NULL DEFAULT 0,
    matched_node_count INTEGER NOT NULL DEFAULT 0,
    new_node_count INTEGER NOT NULL DEFAULT 0,
    new_relation_count INTEGER NOT NULL DEFAULT 0,
    duplicate_relation_count INTEGER NOT NULL DEFAULT 0,
    conflict_count INTEGER NOT NULL DEFAULT 0,
    review_count INTEGER NOT NULL DEFAULT 0,
    blocked_count INTEGER NOT NULL DEFAULT 0,
    failure_stage TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    status_code INTEGER NOT NULL DEFAULT 200,
    result_json TEXT NOT NULL
)
"""
_INDEX = """
CREATE INDEX IF NOT EXISTS idx_knowledge_processes_file
ON knowledge_processes (file_id, started_at DESC)
"""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(_SCHEMA)
    connection.execute(_INDEX)
    return connection


def _counts() -> dict:
    return {
        "entity_count": 0,
        "relation_count": 0,
        "matched_node_count": 0,
        "new_node_count": 0,
        "new_relation_count": 0,
        "duplicate_relation_count": 0,
        "conflict_count": 0,
        "review_count": 0,
        "blocked_count": 0,
    }


def _file_record(file_id: str) -> dict:
    record = get_uploaded_record(file_id)
    if record is None:
        raise KnowledgeFileError("没有这个上传文件。", 404)
    record.pop("stored_name", None)
    return record


def _pipeline(
    *,
    process_id: str,
    pipeline_status: str,
    stage: str,
    outcome: str,
    started_at: str,
    finished_at: str,
    duration_ms: int,
    counts: dict,
    stages: list[dict],
    failure_stage: str,
    error_message: str,
    status_code: int,
    knowledge_added: bool,
    nothing_added: bool,
    pending_review: bool,
    import_result: dict | None,
    message: str,
) -> dict:
    return {
        "process_id": process_id,
        "pipeline_status": pipeline_status,
        "stage": stage,
        "outcome": outcome,
        "knowledge_added": knowledge_added,
        "nothing_added": nothing_added,
        "pending_review": pending_review,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": duration_ms,
        "counts": counts,
        "stages": stages,
        "failure_stage": failure_stage,
        "error_message": error_message,
        "status_code": status_code,
        "import_result": import_result,
        "message": message,
        "in_graph": False,
        "notice": "流程结束只表示各阶段已执行。候选关系不会全部标成已在图谱中，只有通过安全校验的包含关系写入运行时数据库。",
    }


def _body(file_id: str, pipeline: dict) -> dict:
    record = _file_record(file_id)
    record["pipeline"] = pipeline
    return record


def _persist(process_id: str, file_id: str, pipeline: dict, body: dict) -> None:
    counts = pipeline["counts"]
    with _connect() as connection:
        connection.execute(
            """
            UPDATE knowledge_processes
            SET pipeline_status = ?, stage = ?, outcome = ?, finished_at = ?, duration_ms = ?,
                entity_count = ?, relation_count = ?, matched_node_count = ?, new_node_count = ?,
                new_relation_count = ?, duplicate_relation_count = ?, conflict_count = ?,
                review_count = ?, blocked_count = ?, failure_stage = ?, error_message = ?,
                status_code = ?, result_json = ?
            WHERE process_id = ?
            """,
            (
                pipeline["pipeline_status"],
                pipeline["stage"],
                pipeline["outcome"],
                pipeline["finished_at"],
                pipeline["duration_ms"],
                counts["entity_count"],
                counts["relation_count"],
                counts["matched_node_count"],
                counts["new_node_count"],
                counts["new_relation_count"],
                counts["duplicate_relation_count"],
                counts["conflict_count"],
                counts["review_count"],
                counts["blocked_count"],
                pipeline["failure_stage"],
                pipeline["error_message"],
                pipeline["status_code"],
                json.dumps(body, ensure_ascii=False),
                process_id,
            ),
        )
        connection.commit()


def _claim(file_id: str) -> tuple[str, str]:
    started_at = _now()
    connection = _connect()
    connection.isolation_level = None
    try:
        connection.execute("BEGIN IMMEDIATE")
        running = connection.execute(
            """
            SELECT process_id, started_at FROM knowledge_processes
            WHERE file_id = ? AND pipeline_status = 'running'
            """,
            (file_id,),
        ).fetchone()
        if running is not None:
            connection.rollback()
            raise KnowledgeFileError("这个文件正在处理，不能同时再跑一遍。", 409)
        process_id = uuid.uuid4().hex
        connection.execute(
            """
            INSERT INTO knowledge_processes (
                process_id, file_id, pipeline_status, stage, started_at, result_json
            ) VALUES (?, ?, 'running', 'cleaning', ?, '{}')
            """,
            (process_id, file_id, started_at),
        )
        connection.commit()
        return process_id, started_at
    finally:
        connection.close()


def _mark(
    file_id: str,
    process_id: str,
    started_at: str,
    started: float,
    counts: dict,
    stages: list[dict],
    stage: str,
) -> None:
    pipeline = _pipeline(
        process_id=process_id,
        pipeline_status="running",
        stage=stage,
        outcome="",
        started_at=started_at,
        finished_at="",
        duration_ms=int((time.perf_counter() - started) * 1000),
        counts=counts,
        stages=list(stages),
        failure_stage="",
        error_message="",
        status_code=200,
        knowledge_added=False,
        nothing_added=False,
        pending_review=False,
        import_result=None,
        message="正在处理。",
    )
    _persist(process_id, file_id, pipeline, _body(file_id, pipeline))


def _pause(stage: str) -> None:
    if os.environ.get("KNOWLEDGE_PIPELINE_PAUSE_STAGE") == stage:
        pipeline_pause.wait(timeout=20)


def _injected(stage: str) -> None:
    if os.environ.get("KNOWLEDGE_PIPELINE_FAIL_STAGE") == stage:
        raise KnowledgeFileError(f"{stage}阶段失败，后续阶段没有执行。", 500)


def _summary_counts(preview: dict, counts: dict) -> None:
    summary = preview.get("summary") if isinstance(preview.get("summary"), dict) else {}
    counts["matched_node_count"] = int(summary.get("matched_nodes") or 0)
    counts["duplicate_relation_count"] = int(summary.get("duplicate_relations") or 0)
    counts["conflict_count"] = int(summary.get("conflicts") or 0)
    counts["review_count"] = int(summary.get("review_required") or 0)
    counts["blocked_count"] = int(summary.get("blocked_items") or 0)


def _outcome(knowledge_added: bool, nothing_added: bool, pending_review: bool, import_result: dict | None) -> tuple[str, str]:
    if knowledge_added and pending_review:
        return "knowledge_added_with_pending_review", "已写入符合安全规则的包含关系。仍有待审核内容没有入图。"
    if knowledge_added:
        return "knowledge_added", "已写入符合安全规则的包含关系。"
    if import_result and import_result.get("repeated"):
        return "nothing_added", "同一版本已经处理过，没有重复创建节点或关系。"
    if import_result and import_result.get("status") == "evidence_recorded":
        text = str(import_result.get("message") or "没有新建节点或关系。")
        if pending_review:
            text += "仍有待审核内容没有入图。"
        return "pending_review" if pending_review else "nothing_added", text
    if pending_review:
        return "pending_review", "没有符合安全规则的新知识，没有入图。仍有待审核内容。"
    return "nothing_added", "没有符合安全规则的新知识，没有入图。"


def _finish(
    file_id: str,
    process_id: str,
    started_at: str,
    started: float,
    counts: dict,
    stages: list[dict],
    *,
    pipeline_status: str,
    stage: str,
    outcome: str,
    failure_stage: str,
    error_message: str,
    status_code: int,
    knowledge_added: bool,
    nothing_added: bool,
    pending_review: bool,
    import_result: dict | None,
    message: str,
) -> dict:
    pipeline = _pipeline(
        process_id=process_id,
        pipeline_status=pipeline_status,
        stage=stage,
        outcome=outcome,
        started_at=started_at,
        finished_at=_now(),
        duration_ms=int((time.perf_counter() - started) * 1000),
        counts=counts,
        stages=stages,
        failure_stage=failure_stage,
        error_message=error_message,
        status_code=status_code,
        knowledge_added=knowledge_added,
        nothing_added=nothing_added,
        pending_review=pending_review,
        import_result=import_result,
        message=message,
    )
    body = _body(file_id, pipeline)
    _persist(process_id, file_id, pipeline, body)
    return body


def _fail(
    file_id: str,
    process_id: str,
    started_at: str,
    started: float,
    counts: dict,
    stages: list[dict],
    stage: str,
    message: str,
    status_code: int,
) -> dict:
    stages.append({"stage": stage, "status": "failed", "at": _now()})
    return _finish(
        file_id,
        process_id,
        started_at,
        started,
        counts,
        stages,
        pipeline_status="failed",
        stage="failed",
        outcome="failed",
        failure_stage=stage,
        error_message=message,
        status_code=status_code,
        knowledge_added=False,
        nothing_added=False,
        pending_review=counts["review_count"] > 0,
        import_result=None,
        message=message,
    )


def _has_cleaned_json(file_id: str) -> bool:
    try:
        preview = cleaned_preview(file_id)
    except KnowledgeFileError:
        return False
    return preview.get("processing_status") == CLEANED_STATUS and preview.get("preview") is not None


def process_knowledge_file(file_id: str) -> dict:
    """同步执行清洗、抽取、预演和安全入图。失败时保留已成功的清洗结果。"""
    if not valid_file_id(file_id):
        raise KnowledgeFileError("没有这个上传文件。", 404)
    _file_record(file_id)
    process_id, started_at = _claim(file_id)
    started = time.perf_counter()
    counts = _counts()
    stages: list[dict] = []
    current = "cleaning"
    try:
        if _has_cleaned_json(file_id):
            stages.append({"stage": "cleaning", "status": "completed", "reused": True, "at": _now()})
        else:
            _mark(file_id, process_id, started_at, started, counts, stages, "cleaning")
            _pause("cleaning")
            _injected("cleaning")
            cleaned = clean_uploaded_file(file_id)
            if cleaned.get("processing_status") != CLEANED_STATUS:
                message = str(cleaned.get("error_message") or "清洗没有完成。原始文件仍保留。")
                return _fail(file_id, process_id, started_at, started, counts, stages, "cleaning", message, 200)
            stages.append({"stage": "cleaning", "status": "completed", "reused": False, "at": _now()})

        current = "extracting"
        _mark(file_id, process_id, started_at, started, counts, stages, "extracting")
        _pause("extracting")
        _injected("extracting")
        extracted = extract_knowledge(file_id)
        if extracted.get("extraction_status") != "extracted":
            message = str(extracted.get("error_message") or "抽取没有完成。")
            return _fail(file_id, process_id, started_at, started, counts, stages, "extracting", message, 422)
        counts["entity_count"] = int(extracted.get("entity_count") or 0)
        counts["relation_count"] = int(extracted.get("relation_count") or 0)
        stages.append({"stage": "extracting", "status": "completed", "at": _now()})

        current = "previewing"
        _mark(file_id, process_id, started_at, started, counts, stages, "previewing")
        _pause("previewing")
        _injected("previewing")
        preview = preview_knowledge_import(file_id)
        if preview.get("status") != "previewed" or not isinstance(preview.get("summary"), dict):
            message = str(preview.get("error_message") or "入图预演没有完成。")
            return _fail(file_id, process_id, started_at, started, counts, stages, "previewing", message, 422)
        _summary_counts(preview, counts)
        stages.append({"stage": "previewing", "status": "completed", "at": _now()})

        current = "importing"
        _mark(file_id, process_id, started_at, started, counts, stages, "importing")
        _pause("importing")
        _injected("importing")
        imported = apply_import(file_id)
        repeated = bool(imported.get("repeated"))
        counts["new_node_count"] = 0 if repeated else int(imported.get("imported_node_count") or 0)
        counts["new_relation_count"] = 0 if repeated else int(imported.get("imported_relation_count") or 0)
        knowledge_added = counts["new_node_count"] > 0 or counts["new_relation_count"] > 0
        pending_review = counts["review_count"] > 0
        outcome, message = _outcome(knowledge_added, not knowledge_added, pending_review, imported)
        if repeated:
            message = "同一版本已经处理过，没有重复创建节点或关系。"
        elif (
            not knowledge_added
            and imported.get("status") != "evidence_recorded"
            and int(imported.get("existing_node_count") or 0) + int(imported.get("existing_relation_count") or 0) > 0
        ):
            message = "没有重复创建节点或关系。"
            if pending_review:
                message += "仍有待审核内容没有入图。"
                outcome = "pending_review"
            else:
                outcome = "nothing_added"
        stages.append({"stage": "importing", "status": "completed", "at": _now()})
        return _finish(
            file_id,
            process_id,
            started_at,
            started,
            counts,
            stages,
            pipeline_status="completed",
            stage="completed",
            outcome=outcome,
            failure_stage="",
            error_message="",
            status_code=200,
            knowledge_added=knowledge_added,
            nothing_added=not knowledge_added,
            pending_review=pending_review,
            import_result=imported,
            message=message,
        )
    except KnowledgeFileError as exc:
        return _fail(file_id, process_id, started_at, started, counts, stages, current, exc.message, exc.status_code)
    except Exception:
        return _fail(file_id, process_id, started_at, started, counts, stages, current, "处理没有完成。已保留原始文件和已经成功的阶段。", 500)


def process_result(file_id: str) -> dict:
    record = _file_record(file_id)
    path = database_path()
    if not path.is_file():
        record["pipeline"] = _pipeline(
            process_id="",
            pipeline_status="not_started",
            stage="not_started",
            outcome="",
            started_at="",
            finished_at="",
            duration_ms=0,
            counts=_counts(),
            stages=[],
            failure_stage="",
            error_message="这个文件还没有处理记录。",
            status_code=200,
            knowledge_added=False,
            nothing_added=False,
            pending_review=False,
            import_result=None,
            message="这个文件还没有处理记录。",
        )
        return record
    connection = _connect()
    try:
        row = connection.execute(
            """
            SELECT result_json, pipeline_status FROM knowledge_processes
            WHERE file_id = ?
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (file_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None or not row["result_json"] or row["result_json"] == "{}":
        record["pipeline"] = _pipeline(
            process_id="",
            pipeline_status="not_started",
            stage="not_started",
            outcome="",
            started_at="",
            finished_at="",
            duration_ms=0,
            counts=_counts(),
            stages=[],
            failure_stage="",
            error_message="这个文件还没有处理记录。",
            status_code=200,
            knowledge_added=False,
            nothing_added=False,
            pending_review=False,
            import_result=None,
            message="这个文件还没有处理记录。",
        )
        return record
    saved = json.loads(row["result_json"])
    if isinstance(saved, dict) and "pipeline" in saved:
        return saved
    record["pipeline"] = saved
    return record
