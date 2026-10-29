"""只读列出白名单内的知识目录。

不接收任意服务器路径，不返回环境变量、密钥、授权文件或数据库。
官方 Excel 只提供文件名和元信息，不读取表内观测内容。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.services.knowledge_cleaner import UNSUPPORTED, CleanFailure, _extract
from app.services.knowledge_file_store import (
    OFFICIAL_DIR,
    ROOT,
    KnowledgeFileError,
    cleaned_dir,
    extracted_dir,
    get_uploaded_record,
    upload_dir,
    valid_file_id,
)

MAX_PARSE_BYTES = 2 * 1024 * 1024
JSON_FULL_LIMIT = 48 * 1024
TEXT_LIMIT = 4000
_SKIP_NAMES = {
    ".env",
    "research_release_grants.json",
    "research_model_audit.jsonl",
    "knowledge_files.sqlite3",
    "knowledge_graph_imports.sqlite3",
    "monitoring_events.sqlite3",
}
_SKIP_SUFFIXES = (".sqlite", ".sqlite3", ".pem", ".key", ".db", ".env")
_TEXT_EXTENSIONS = {"txt", "md", "json", "csv", "docx"}


def _directories() -> list[tuple[str, Path]]:
    return [
        ("data/official", OFFICIAL_DIR),
        ("data/official_dataset", ROOT / "data" / "official_dataset"),
        ("data/processed/graph", ROOT / "data" / "processed" / "graph"),
        ("data/processed/markdown", ROOT / "data" / "processed" / "markdown"),
        ("data/processed/rag", ROOT / "data" / "processed" / "rag"),
        ("data/runtime/knowledge_uploads", upload_dir()),
        ("data/runtime/knowledge_cleaned", cleaned_dir()),
        ("data/runtime/knowledge_extracted", extracted_dir()),
    ]


def _files() -> list[tuple[str, Path]]:
    return [("data/processed/manifest.json", ROOT / "data" / "processed" / "manifest.json")]


def list_knowledge_tree() -> dict:
    index: dict[str, dict] = {}
    missing = []
    for relative, directory in _directories():
        resolved = directory.resolve()
        if not resolved.is_dir():
            if not relative.startswith("data/runtime/"):
                missing.append(relative)
            continue
        folder = _ensure_dir(index, relative)
        folder["children"] = _directory(relative, resolved, resolved, 0)["children"]
    for relative, path in _files():
        resolved = path.resolve()
        if not resolved.is_file() or not _visible(resolved):
            missing.append(relative)
            continue
        parent = _ensure_dir(index, str(Path(relative).parent).replace("\\", "/"))
        parent["children"].append(_file(relative, resolved))
    data = index.get("data")
    if data is None:
        raise KnowledgeFileError("没有找到可浏览的知识目录。", 404)
    _sort(data)
    return {
        "roots": [data],
        "missing_roots": missing,
        "notice": "只列出 data 下允许浏览的现有知识文件，不浏览整个工作区。",
    }


def preview_knowledge_file(relative: str) -> dict:
    path, root_relative = _resolve_file(relative)
    extension = _extension(path)
    record = _related_record(path)
    if record and not extension:
        extension = str(record.get("extension") or "")
    modified = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")
    payload = {
        "path": root_relative,
        "name": path.name,
        "extension": extension,
        "size_bytes": path.stat().st_size,
        "modified_at": modified,
        "related_file_id": record.get("file_id") if record else "",
        "related_filename": record.get("original_filename") if record else "",
        "preview_kind": "unsupported",
        "text": "",
        "truncated": False,
        "notice": "",
    }
    if extension == "xlsx":
        payload["preview_kind"] = "metadata"
        payload["notice"] = "官方 Excel 只展示文件名、大小和修改时间，不读取表内观测内容。"
        return payload
    if extension in UNSUPPORTED:
        payload["notice"] = UNSUPPORTED[extension]
        return payload
    if extension not in _TEXT_EXTENSIONS:
        payload["notice"] = "这种格式暂未完成预览。文件仍在目录中，没有把它当成已入图。"
        return payload
    if extension == "json":
        try:
            text, truncated, notice = _json_preview(path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            payload["notice"] = "JSON 文件无法按现有方式读取。"
            return payload
        payload["preview_kind"] = "text"
        payload["text"] = text
        payload["truncated"] = truncated
        payload["notice"] = notice
        return payload
    if path.stat().st_size > MAX_PARSE_BYTES:
        payload["preview_kind"] = "metadata"
        payload["notice"] = "文件较大，当前只提供元信息，没有截取可能不完整的正文。"
        return payload
    try:
        raw = path.read_bytes()
        sections, warnings = _extract(extension, raw)
    except CleanFailure as exc:
        payload["notice"] = exc.message
        return payload
    except (OSError, UnicodeError, ValueError):
        payload["notice"] = "文件无法按现有解析方式读取。"
        return payload
    text = "\n\n".join(str(item.get("text") or "") for item in sections if item.get("text"))
    if len(text) > TEXT_LIMIT:
        text = text[:TEXT_LIMIT]
        payload["truncated"] = True
    payload["preview_kind"] = "text"
    payload["text"] = text
    payload["notice"] = "当前只显示有限预览。文件清洗成功不代表已经加入知识图谱。"
    if warnings:
        payload["notice"] += " " + " ".join(str(item) for item in warnings[:3])
    return payload


def _json_preview(path: Path) -> tuple[str, bool, str]:
    raw = path.read_bytes()
    if len(raw) <= JSON_FULL_LIMIT and path.name not in {"graph.json", "graph.json.bak"}:
        sections, warnings = _extract("json", raw)
        text = "\n\n".join(str(item.get("text") or "") for item in sections if item.get("text"))
        truncated = len(text) > TEXT_LIMIT
        notice = "当前只显示有限预览。"
        if warnings:
            notice += " " + " ".join(str(item) for item in warnings[:3])
        return text[:TEXT_LIMIT], truncated, notice
    payload = json.loads(raw)
    if isinstance(payload, dict) and isinstance(payload.get("nodes"), list) and isinstance(payload.get("edges"), list):
        first = payload["nodes"][0] if payload["nodes"] else None
        text = json.dumps(
            {
                "file": path.name,
                "size_bytes": len(raw),
                "node_count": len(payload["nodes"]),
                "edge_count": len(payload["edges"]),
                "top_level_keys": list(payload.keys()),
                "first_node": first,
            },
            ensure_ascii=False,
            indent=2,
        )
        return text[:TEXT_LIMIT], True, "只预览图谱规模和第一条节点，没有输出全部节点和关系。"
    text = raw[:TEXT_LIMIT].decode("utf-8", errors="replace")
    return text, True, "文件较大，只显示开头，没有展开全部内容。"


def _ensure_dir(index: dict[str, dict], relative: str) -> dict:
    current = index.get(relative)
    if current is not None:
        return current
    parent_text, _, name = relative.rpartition("/")
    node = {"kind": "directory", "name": name or relative, "path": relative, "children": []}
    index[relative] = node
    if parent_text:
        _ensure_dir(index, parent_text)["children"].append(node)
    return node


def _sort(node: dict) -> None:
    children = node.get("children")
    if not isinstance(children, list):
        return
    children.sort(key=lambda item: (item.get("kind") == "file", str(item.get("name") or "").lower()))
    for child in children:
        if child.get("kind") == "directory":
            _sort(child)


def _directory(relative: str, directory: Path, root: Path, depth: int) -> dict:
    children = []
    if depth < 8:
        try:
            entries = sorted(directory.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
        except OSError:
            entries = []
        for entry in entries:
            if not _visible(entry):
                continue
            resolved = entry.resolve()
            if root not in resolved.parents:
                continue
            child = f"{relative}/{entry.name}"
            if entry.is_dir():
                children.append(_directory(child, resolved, root, depth + 1))
            elif entry.is_file():
                children.append(_file(child, resolved))
    return {"kind": "directory", "name": Path(relative).name, "path": relative, "children": children}


def _file(relative: str, path: Path) -> dict:
    record = _related_record(path)
    modified = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")
    return {
        "kind": "file",
        "name": path.name,
        "path": relative,
        "size_bytes": path.stat().st_size,
        "extension": _extension(path) or (str(record.get("extension") or "") if record else ""),
        "modified_at": modified,
        "related_file_id": record.get("file_id") if record else "",
        "related_filename": record.get("original_filename") if record else "",
    }


def _related_record(path: Path) -> dict | None:
    stem = path.stem if path.suffix else path.name
    if not valid_file_id(stem):
        return None
    return get_uploaded_record(stem)


def _extension(path: Path) -> str:
    return path.suffix.lower().lstrip(".")


def _visible(path: Path) -> bool:
    name = path.name
    lowered = name.lower()
    if name.startswith(".") or name.startswith("~$") or path.is_symlink():
        return False
    if lowered in _SKIP_NAMES or lowered.endswith(_SKIP_SUFFIXES):
        return False
    if "api_key" in lowered or "secret" in lowered:
        return False
    return True


def _resolve_file(relative: str) -> tuple[Path, str]:
    text = relative.replace("\\", "/").strip()
    if not text or text.startswith("/") or ":" in text:
        raise KnowledgeFileError("路径不在知识目录范围内。", 400)
    parts = [part for part in text.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise KnowledgeFileError("路径不在知识目录范围内。", 400)
    normalized = "/".join(parts)
    for logical, physical in _files():
        if normalized == logical:
            target = physical.resolve()
            if target.is_file() and _visible(target):
                return target, normalized
            raise KnowledgeFileError("没有找到这个知识文件。", 404)
    for prefix, directory in _directories():
        if normalized != prefix and not normalized.startswith(prefix + "/"):
            continue
        root = directory.resolve()
        if not root.is_dir():
            raise KnowledgeFileError("没有找到这个知识目录。", 404)
        remainder = normalized[len(prefix) :].lstrip("/")
        target = (root / remainder).resolve() if remainder else root
        if target != root and root not in target.parents:
            raise KnowledgeFileError("路径不在知识目录范围内。", 400)
        if not target.is_file() or not _visible(target):
            raise KnowledgeFileError("没有找到这个知识文件。", 404)
        return target, normalized
    raise KnowledgeFileError("路径不在知识目录范围内。", 400)
