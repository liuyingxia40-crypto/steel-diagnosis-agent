"""保存用户上传的知识文件，并列出原有六份官方资料。

只做文件管理。不解析正文，不清洗，不修改图谱，也不执行上传内容。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sqlite3
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OFFICIAL_DIR = ROOT / "data" / "official"
MANIFEST_PATH = ROOT / "data" / "processed" / "manifest.json"
DEFAULT_UPLOAD_DIR = ROOT / "data" / "runtime" / "knowledge_uploads"
DEFAULT_CLEANED_DIR = ROOT / "data" / "runtime" / "knowledge_cleaned"
DEFAULT_EXTRACTED_DIR = ROOT / "data" / "runtime" / "knowledge_extracted"
DEFAULT_PREVIEW_DIR = ROOT / "data" / "runtime" / "knowledge_import_previews"
DEFAULT_IMPORTS_DB = ROOT / "data" / "runtime" / "knowledge_graph_imports.sqlite3"
DEFAULT_DATABASE = ROOT / "data" / "runtime" / "knowledge_files.sqlite3"
MONITORING_DATABASE = ROOT / "data" / "runtime" / "monitoring_events.sqlite3"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
PENDING_STATUS = "pending_cleaning"
CLEANING_STATUS = "cleaning"
CLEANED_STATUS = "cleaned"
FAILED_STATUS = "cleaning_failed"
OFFICIAL_STATUS = "official_catalog"
UPLOAD_STATUSES = frozenset({PENDING_STATUS, CLEANING_STATUS, CLEANED_STATUS, FAILED_STATUS})
STATUS_TEXT = {
    PENDING_STATUS: "待清洗",
    CLEANING_STATUS: "正在清洗",
    CLEANED_STATUS: "已清洗",
    FAILED_STATUS: "清洗失败",
    OFFICIAL_STATUS: "原有知识库",
}
USER_SOURCE = "user_upload"
OFFICIAL_SOURCE = "existing_knowledge"
ALLOWED_EXTENSIONS = frozenset({"pdf", "docx", "xlsx", "csv", "txt", "md", "json", "pptx"})
EXECUTABLE_EXTENSIONS = frozenset(
    {
        "exe",
        "dll",
        "bat",
        "cmd",
        "com",
        "scr",
        "msi",
        "ps1",
        "sh",
        "bash",
        "app",
        "jar",
        "vbs",
        "js",
        "cpl",
        "sys",
        "so",
        "dmg",
        "apk",
        "hta",
        "wsf",
        "lnk",
        "iso",
    }
)
OFFICE_MARKERS = {
    "docx": "wordprocessingml",
    "xlsx": "spreadsheetml",
    "pptx": "presentationml",
}
_NAME_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS knowledge_files (
    file_id TEXT PRIMARY KEY,
    original_filename TEXT NOT NULL,
    extension TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    source_type TEXT NOT NULL,
    processing_status TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    error_message TEXT NOT NULL DEFAULT '',
    cleaned_at TEXT,
    cleaning_started_at TEXT
)
"""
_EXTRA_COLUMNS = {
    "error_message": "TEXT NOT NULL DEFAULT ''",
    "cleaned_at": "TEXT",
    "cleaning_started_at": "TEXT",
}


class KnowledgeFileError(Exception):
    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _blocked(path: Path) -> bool:
    resolved = path.resolve()
    blocked = (
        (ROOT / "data" / "official").resolve(),
        (ROOT / "data" / "official_dataset").resolve(),
        (ROOT / "data" / "processed").resolve(),
        MONITORING_DATABASE.resolve(),
    )
    for item in blocked:
        if resolved == item or item in resolved.parents:
            return True
    return False


def _runtime_path(path: Path) -> Path:
    if _blocked(path):
        raise KnowledgeFileError("知识文件不能写入官方资料、处理结果或监测事件库。", 500)
    return path


def cleaned_dir() -> Path:
    override = os.environ.get("KNOWLEDGE_CLEANED_DIR", "").strip()
    path = Path(override) if override else DEFAULT_CLEANED_DIR
    return _runtime_path(path)


def extracted_dir() -> Path:
    override = os.environ.get("KNOWLEDGE_EXTRACTED_DIR", "").strip()
    path = Path(override) if override else DEFAULT_EXTRACTED_DIR
    return _runtime_path(path)


def import_preview_dir() -> Path:
    override = os.environ.get("KNOWLEDGE_IMPORT_PREVIEW_DIR", "").strip()
    path = Path(override) if override else DEFAULT_PREVIEW_DIR
    return _runtime_path(path)


def imports_database_path() -> Path:
    override = os.environ.get("KNOWLEDGE_GRAPH_IMPORTS_DB", "").strip()
    path = Path(override) if override else DEFAULT_IMPORTS_DB
    return _runtime_path(path)


def upload_dir() -> Path:
    override = os.environ.get("KNOWLEDGE_UPLOAD_DIR", "").strip()
    path = Path(override) if override else DEFAULT_UPLOAD_DIR
    return _runtime_path(path)


def database_path() -> Path:
    override = os.environ.get("KNOWLEDGE_FILES_DB", "").strip()
    path = Path(override) if override else DEFAULT_DATABASE
    return _runtime_path(path)


def _connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(_CREATE_TABLE)
    existing = {row[1] for row in connection.execute("PRAGMA table_info(knowledge_files)")}
    for name, declaration in _EXTRA_COLUMNS.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE knowledge_files ADD COLUMN {name} {declaration}")
    return connection


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _public(
    *,
    file_id: str,
    original_filename: str,
    extension: str,
    size_bytes: int,
    sha256: str,
    uploaded_at: str | None,
    source_type: str,
    processing_status: str,
    error_message: str = "",
    cleaned_at: str | None = None,
) -> dict:
    return {
        "file_id": file_id,
        "original_filename": original_filename,
        "extension": extension,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "uploaded_at": uploaded_at,
        "source_type": source_type,
        "processing_status": processing_status,
        "status_text": STATUS_TEXT.get(processing_status, "状态未确认"),
        "error_message": error_message,
        "cleaned_at": cleaned_at,
        "participates_in_qa": False,
        "in_graph": False,
    }


def _public_from_row(item: sqlite3.Row) -> dict:
    return _public(
        file_id=item["file_id"],
        original_filename=item["original_filename"],
        extension=item["extension"],
        size_bytes=int(item["size_bytes"]),
        sha256=item["sha256"],
        uploaded_at=item["uploaded_at"],
        source_type=item["source_type"],
        processing_status=item["processing_status"],
        error_message=item["error_message"] or "",
        cleaned_at=item["cleaned_at"],
    )


def _clean_filename(filename: str) -> tuple[str, str]:
    name = filename.strip()
    if not name or name in {".", ".."}:
        raise KnowledgeFileError("请提供文件名。")
    if _NAME_CHARS.search(name) or any(char in name for char in ("/", "\\", ":", "*", "?", '"', "<", ">", "|")):
        raise KnowledgeFileError("文件名不能包含路径或控制字符。")
    if name.startswith("~$"):
        raise KnowledgeFileError("不能上传临时文件。")
    if len(name) > 180:
        raise KnowledgeFileError("文件名过长。")
    suffix = Path(name).suffix.lower().lstrip(".")
    parts = [part.lower() for part in name.split(".") if part]
    if not suffix or suffix not in ALLOWED_EXTENSIONS or not parts:
        raise KnowledgeFileError("不支持的文件格式。当前只接收 PDF、DOCX、XLSX、CSV、TXT、MD、JSON、PPTX。")
    if any(part in EXECUTABLE_EXTENSIONS for part in parts):
        raise KnowledgeFileError("不允许上传可执行文件。")
    return name, suffix


def _office_marker(payload: bytes, extension: str) -> None:
    if not payload.startswith(b"PK"):
        raise KnowledgeFileError("文件内容和扩展名不一致。")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = archive.namelist()
            if any(item.startswith(("/", "\\")) or ".." in Path(item).parts for item in names):
                raise KnowledgeFileError("文件内容包含不安全路径。")
            info = archive.getinfo("[Content_Types].xml")
            if info.file_size > 1_000_000:
                raise KnowledgeFileError("文件内容无法核验。")
            content = archive.read("[Content_Types].xml").decode("utf-8", errors="ignore").lower()
    except KnowledgeFileError:
        raise
    except (zipfile.BadZipFile, KeyError, UnicodeError) as exc:
        raise KnowledgeFileError("文件内容和扩展名不一致。") from exc
    if OFFICE_MARKERS[extension] not in content:
        raise KnowledgeFileError("文件内容和扩展名不一致。")


def _check_content(extension: str, payload: bytes) -> None:
    if payload.startswith(b"MZ") or payload.startswith(b"\x7fELF"):
        raise KnowledgeFileError("文件内容像可执行程序，已拒绝。")
    if extension == "pdf":
        if not payload.startswith(b"%PDF"):
            raise KnowledgeFileError("文件内容和扩展名不一致。")
        return
    if extension in OFFICE_MARKERS:
        _office_marker(payload, extension)
        return
    if extension == "json":
        try:
            json.loads(payload.decode("utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise KnowledgeFileError("JSON 文件无法读取。") from exc
        return
    if b"\x00" in payload or payload.startswith(b"#!"):
        raise KnowledgeFileError("文本文件包含不能保存的内容。")


def list_official_files() -> list[dict]:
    if not MANIFEST_PATH.is_file():
        raise KnowledgeFileError("未找到原有知识资料清单。", 500)
    try:
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KnowledgeFileError("原有知识资料清单无法读取。", 500) from exc
    documents = payload.get("documents") if isinstance(payload, dict) else None
    if payload.get("source_directory") != "data/official" or not isinstance(documents, list):
        raise KnowledgeFileError("原有知识资料清单缺少官方目录。", 500)
    official_root = OFFICIAL_DIR.resolve()
    rows = []
    for item in documents:
        if not isinstance(item, dict):
            raise KnowledgeFileError("原有知识资料清单格式不正确。", 500)
        name = item.get("source_name")
        source_path = item.get("source_path")
        if not isinstance(name, str) or not isinstance(source_path, str):
            raise KnowledgeFileError("原有知识资料清单缺少文件名。", 500)
        if Path(source_path).name != name or name.startswith("~$"):
            raise KnowledgeFileError("原有知识资料清单中的文件名和路径不一致。", 500)
        path = (official_root / name).resolve()
        if path.parent != official_root or not path.is_file():
            raise KnowledgeFileError("原有知识资料不在官方目录中。", 500)
        content = path.read_bytes()
        extension = Path(name).suffix.lower().lstrip(".")
        rows.append(
            _public(
                file_id="kb-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:16],
                original_filename=name,
                extension=extension,
                size_bytes=path.stat().st_size,
                sha256=_sha256(content),
                uploaded_at=None,
                source_type=OFFICIAL_SOURCE,
                processing_status=OFFICIAL_STATUS,
            )
        )
    if len(rows) != 6:
        raise KnowledgeFileError("原有知识资料不是六份。", 500)
    return rows


def list_uploaded_files() -> list[dict]:
    path = database_path()
    if not path.is_file():
        return []
    recover_stale_cleaning()
    with _connect() as connection:
        found = connection.execute(
            """
            SELECT file_id, original_filename, extension, size_bytes, sha256,
                   uploaded_at, source_type, processing_status, error_message, cleaned_at
            FROM knowledge_files
            ORDER BY uploaded_at DESC, file_id DESC
            """
        ).fetchall()
    rows = []
    for item in found:
        if item["source_type"] != USER_SOURCE or item["processing_status"] not in UPLOAD_STATUSES:
            raise KnowledgeFileError("上传记录的来源或状态不正确。", 500)
        rows.append(_public_from_row(item))
    return rows


def list_knowledge_files() -> dict:
    return {"files": [*list_official_files(), *list_uploaded_files()]}


def save_upload(filename: str, payload: bytes) -> dict:
    name, extension = _clean_filename(filename)
    if not isinstance(payload, bytes) or not payload:
        raise KnowledgeFileError("没有收到文件内容。")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise KnowledgeFileError("单个文件不能超过 50MB。", 413)
    _check_content(extension, payload)
    file_id = uuid.uuid4().hex
    directory = upload_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = (directory / file_id).resolve()
    if target.parent != directory.resolve():
        raise KnowledgeFileError("不能把文件写到上传目录之外。", 500)
    digest = _sha256(payload)
    uploaded_at = datetime.now().astimezone().isoformat(timespec="seconds")
    temp_name = f".{file_id}.partial"
    temporary = directory / temp_name
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, target)
        with _connect() as connection:
            connection.execute(
                """
                INSERT INTO knowledge_files (
                    file_id, original_filename, extension, size_bytes, sha256,
                    uploaded_at, source_type, processing_status, stored_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_id,
                    name,
                    extension,
                    len(payload),
                    digest,
                    uploaded_at,
                    USER_SOURCE,
                    PENDING_STATUS,
                    file_id,
                ),
            )
            connection.commit()
    except Exception:
        temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
    return _public(
        file_id=file_id,
        original_filename=name,
        extension=extension,
        size_bytes=len(payload),
        sha256=digest,
        uploaded_at=uploaded_at,
        source_type=USER_SOURCE,
        processing_status=PENDING_STATUS,
    )


_FILE_ID = re.compile(r"^[a-f0-9]{32}$")
_STALE_SECONDS = 120


def valid_file_id(file_id: str) -> bool:
    return isinstance(file_id, str) and _FILE_ID.fullmatch(file_id) is not None


def _now() -> datetime:
    return datetime.now().astimezone()


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=_now().tzinfo)
    return parsed


def recover_stale_cleaning() -> None:
    path = database_path()
    if not path.is_file():
        return
    now = _now()
    with _connect() as connection:
        rows = connection.execute(
            "SELECT file_id, cleaning_started_at FROM knowledge_files WHERE processing_status = ?",
            (CLEANING_STATUS,),
        ).fetchall()
        for row in rows:
            started = _parse_time(row["cleaning_started_at"])
            if started is not None and (now - started).total_seconds() < _STALE_SECONDS:
                continue
            connection.execute(
                """
                UPDATE knowledge_files
                SET processing_status = ?, error_message = ?, cleaning_started_at = NULL
                WHERE file_id = ? AND processing_status = ?
                """,
                (
                    FAILED_STATUS,
                    "上次清洗中断，没有生成可用结果。原始文件仍保留。",
                    row["file_id"],
                    CLEANING_STATUS,
                ),
            )
        connection.commit()


def get_uploaded_record(file_id: str) -> dict | None:
    if not valid_file_id(file_id):
        return None
    path = database_path()
    if not path.is_file():
        return None
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT file_id, original_filename, extension, size_bytes, sha256,
                   uploaded_at, source_type, processing_status, error_message, cleaned_at,
                   stored_name
            FROM knowledge_files
            WHERE file_id = ?
            """,
            (file_id,),
        ).fetchone()
    if row is None:
        return None
    record = _public_from_row(row)
    record["stored_name"] = row["stored_name"]
    return record


def read_upload_bytes(file_id: str) -> bytes:
    record = get_uploaded_record(file_id)
    if record is None or record["stored_name"] != file_id:
        raise KnowledgeFileError("没有这个上传文件。", 404)
    directory = upload_dir().resolve()
    target = (directory / file_id).resolve()
    if target.parent != directory or not target.is_file():
        raise KnowledgeFileError("没有这个上传文件。", 404)
    return target.read_bytes()


def claim_cleaning(file_id: str) -> str:
    """返回 proceed、busy 或 missing。busy 表示另一个清洗还在进行。"""
    if not valid_file_id(file_id):
        return "missing"
    recover_stale_cleaning()
    with _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT processing_status, cleaning_started_at FROM knowledge_files WHERE file_id = ?",
            (file_id,),
        ).fetchone()
        if row is None:
            connection.rollback()
            return "missing"
        if row["processing_status"] == CLEANING_STATUS:
            started = _parse_time(row["cleaning_started_at"])
            if started is not None and (_now() - started).total_seconds() < _STALE_SECONDS:
                connection.rollback()
                return "busy"
        connection.execute(
            """
            UPDATE knowledge_files
            SET processing_status = ?, error_message = '', cleaning_started_at = ?
            WHERE file_id = ?
            """,
            (CLEANING_STATUS, _now().isoformat(timespec="seconds"), file_id),
        )
        connection.commit()
    return "proceed"


def finish_cleaning(file_id: str, *, status: str, error_message: str = "") -> dict:
    if status not in {CLEANED_STATUS, FAILED_STATUS}:
        raise KnowledgeFileError("清洗结果状态不正确。", 500)
    cleaned_at = _now().isoformat(timespec="seconds") if status == CLEANED_STATUS else None
    with _connect() as connection:
        connection.execute(
            """
            UPDATE knowledge_files
            SET processing_status = ?, error_message = ?, cleaned_at = ?, cleaning_started_at = NULL
            WHERE file_id = ?
            """,
            (status, error_message, cleaned_at, file_id),
        )
        connection.commit()
        row = connection.execute(
            """
            SELECT file_id, original_filename, extension, size_bytes, sha256,
                   uploaded_at, source_type, processing_status, error_message, cleaned_at
            FROM knowledge_files
            WHERE file_id = ?
            """,
            (file_id,),
        ).fetchone()
    if row is None:
        raise KnowledgeFileError("没有这个上传文件。", 404)
    return _public_from_row(row)
