"""把已上传的知识文件清洗成可追溯的 JSON。

只读取 file_id 对应的上传文件。不接收客户端路径，不调用 DeepSeek，
不修改官方资料、图谱或监测事件库，也不执行文档里的文字。
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import uuid
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.services.knowledge_file_store import (
    CLEANED_STATUS,
    FAILED_STATUS,
    KnowledgeFileError,
    claim_cleaning,
    cleaned_dir,
    finish_cleaning,
    get_uploaded_record,
    read_upload_bytes,
    recover_stale_cleaning,
)

SCHEMA_VERSION = "1.0"
PREVIEW_SECTIONS = 20
PREVIEW_CHARS = 400
NOTICE = "文件清洗成功不代表已经加入知识图谱。"
UNSUPPORTED = {
    "pdf": "PDF 正文清洗暂未完成。当前环境没有可靠的 PDF 提取库，文件已保留，没有生成清洗结果。",
    "xlsx": "XLSX 正文清洗暂未完成。当前环境没有可靠的表格提取库，文件已保留，没有生成清洗结果。",
    "pptx": "PPTX 正文清洗暂未完成。当前环境没有可靠的演示文稿提取库，文件已保留，没有生成清洗结果。",
}
HEADING_STYLES = {
    "Title",
    "Heading 1",
    "Heading 2",
    "Heading 3",
    "Heading 4",
    "标题",
    "标题 1",
    "标题 2",
    "标题 3",
    "标题 4",
}
_SPACE = re.compile(r"[ \t\u00a0]+")


class CleanFailure(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _normalize(text: str) -> str:
    collapsed = _SPACE.sub(" ", text.replace("\r\n", "\n").replace("\r", "\n")).strip()
    return collapsed


def _section(index: int, heading: str, text: str, kind: str, locator: dict, extra: dict | None = None) -> dict:
    item = {
        "section_id": f"section-{index:03d}",
        "heading": heading,
        "text": text,
        "kind": kind,
        "locator": locator,
    }
    if extra:
        item.update(extra)
    return item


def _decode_text(payload: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return payload.decode(encoding), encoding
        except UnicodeError:
            continue
    raise CleanFailure("文本编码无法识别，没有生成清洗结果。原始文件仍保留。")


def _clean_lines(payload: bytes, extension: str) -> tuple[list[dict], list[str]]:
    text, encoding = _decode_text(payload)
    warnings = []
    if encoding != "utf-8-sig":
        warnings.append(f"文本按 {encoding} 读取。")
    sections = []
    heading = ""
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = _normalize(raw)
        if not line:
            continue
        kind = "paragraph"
        current = heading
        if extension == "md":
            marks = re.match(r"^(#{1,6})\s+(\S.*)$", line)
            if marks:
                heading = _normalize(marks.group(2))
                current = heading
                kind = "heading"
                line = heading
        sections.append(
            _section(
                len(sections) + 1,
                current,
                line,
                kind,
                {"type": "line", "index": line_number},
            )
        )
    return sections, warnings


def _clean_csv(payload: bytes) -> tuple[list[dict], list[str]]:
    text, encoding = _decode_text(payload)
    warnings = []
    if encoding != "utf-8-sig":
        warnings.append(f"CSV 按 {encoding} 读取。")
    sample = text[:4096]
    delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    sections = []
    for index, row in enumerate(rows, start=1):
        cells = [_normalize(cell) for cell in row]
        if not any(cells):
            continue
        sections.append(
            _section(
                len(sections) + 1,
                "",
                "\t".join(cells),
                "csv_row",
                {"type": "csv_row", "index": index, "column_count": len(cells)},
                {"cells": cells},
            )
        )
    return sections, warnings


def _json_path(parts: list[str | int]) -> str:
    path = "$"
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        elif re.fullmatch(r"[A-Za-z_\u0080-\uffff][\w\u0080-\uffff]*", part):
            path += f".{part}"
        else:
            escaped = part.replace("\\", "\\\\").replace('"', '\\"')
            path += f'["{escaped}"]'
    return path


def _walk_json(value: object, parts: list[str | int], sections: list[dict], warnings: list[str], depth: int) -> None:
    if depth > 40:
        warnings.append("JSON 嵌套超过 40 层的部分没有展开。")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _walk_json(item, [*parts, str(key)], sections, warnings, depth + 1)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _walk_json(item, [*parts, index], sections, warnings, depth + 1)
        return
    if value is None:
        text = "null"
    elif isinstance(value, str):
        text = _normalize(value)
    else:
        text = json.dumps(value, ensure_ascii=False)
    if text == "":
        return
    sections.append(
        _section(
            len(sections) + 1,
            "",
            text,
            "json_value",
            {"type": "json_path", "path": _json_path(parts)},
        )
    )


def _clean_json(payload: bytes) -> tuple[list[dict], list[str]]:
    text, _encoding = _decode_text(payload)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CleanFailure("JSON 内容无法解析，没有生成清洗结果。原始文件仍保留。") from exc
    warnings: list[str] = []
    sections: list[dict] = []
    _walk_json(value, [], sections, warnings, 0)
    return sections, warnings


def _style_name(paragraph: Paragraph) -> str:
    if paragraph.style is None or not paragraph.style.name:
        return "Normal"
    return paragraph.style.name


def _is_heading(style: str) -> bool:
    return style in HEADING_STYLES or style.startswith("Heading ") or style.startswith("标题")


def _iter_blocks(document: Document):
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def _docx_warnings(document: Document) -> list[str]:
    xml = document.element.body.xml
    warnings = []
    if "a:blip" in xml or "w:drawing" in xml or "w:pict" in xml:
        warnings.append("文档包含图片，图片内容未提取。")
    if "m:oMath" in xml:
        warnings.append("文档包含公式，公式内容未提取。")
    if "w:txbxContent" in xml:
        warnings.append("文档包含文本框，文本框内容未按正文顺序提取。")
    if "w:object" in xml or "w:oleObject" in xml:
        warnings.append("文档包含嵌入对象，对象内容未提取。")
    return warnings


def _clean_docx(payload: bytes) -> tuple[list[dict], list[str]]:
    try:
        document = Document(io.BytesIO(payload))
    except Exception as exc:
        raise CleanFailure("DOCX 内容无法解析，没有生成清洗结果。原始文件仍保留。") from exc
    sections = []
    heading = ""
    paragraph_index = 0
    table_index = 0
    for block in _iter_blocks(document):
        if isinstance(block, Paragraph):
            paragraph_index += 1
            text = _normalize(block.text)
            if not text:
                continue
            style = _style_name(block)
            kind = "paragraph"
            if _is_heading(style):
                heading = text
                kind = "heading"
            elif style == "List Bullet":
                kind = "list"
            sections.append(
                _section(
                    len(sections) + 1,
                    heading,
                    text,
                    kind,
                    {"type": "paragraph", "index": paragraph_index, "style": style},
                )
            )
            continue
        table_index += 1
        rows = []
        for row in block.rows:
            cells = []
            for cell in row.cells:
                parts = [_normalize(paragraph.text) for paragraph in cell.paragraphs]
                cells.append(" ".join(part for part in parts if part))
            if any(cells):
                rows.append(cells)
        if not rows:
            continue
        text = "\n".join("\t".join(cells) for cells in rows)
        sections.append(
            _section(
                len(sections) + 1,
                heading,
                text,
                "table",
                {"type": "table", "index": table_index, "row_count": len(rows)},
                {"table": {"rows": rows}},
            )
        )
    return sections, _docx_warnings(document)


def _extract(extension: str, payload: bytes) -> tuple[list[dict], list[str]]:
    if extension in UNSUPPORTED:
        raise CleanFailure(UNSUPPORTED[extension])
    if extension in {"txt", "md"}:
        return _clean_lines(payload, extension)
    if extension == "csv":
        return _clean_csv(payload)
    if extension == "json":
        return _clean_json(payload)
    if extension == "docx":
        return _clean_docx(payload)
    raise CleanFailure("这种格式暂未完成清洗。文件已保留，没有生成清洗结果。")


def _document(record: dict, sections: list[dict], warnings: list[str]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "file_id": record["file_id"],
        "source": {
            "original_filename": record["original_filename"],
            "source_type": record["source_type"],
            "extension": record["extension"],
            "sha256": record["sha256"],
        },
        "sections": sections,
        "warnings": warnings,
    }


def _write_document(file_id: str, document: dict) -> None:
    directory = cleaned_dir().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    target = (directory / f"{file_id}.json").resolve()
    if target.parent != directory:
        raise KnowledgeFileError("清洗结果不能写到清洗目录之外。", 500)
    temporary = directory / f".{file_id}.{uuid.uuid4().hex}.partial"
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)


def _result_path(file_id: str) -> Path | None:
    directory = cleaned_dir().resolve()
    target = (directory / f"{file_id}.json").resolve()
    if target.parent != directory or not target.is_file():
        return None
    return target


def clean_uploaded_file(file_id: str) -> dict:
    recover_stale_cleaning()
    claimed = claim_cleaning(file_id)
    if claimed == "missing":
        raise KnowledgeFileError("没有这个上传文件。", 404)
    if claimed == "busy":
        record = get_uploaded_record(file_id)
        if record is None:
            raise KnowledgeFileError("没有这个上传文件。", 404)
        record.pop("stored_name", None)
        return record
    record = get_uploaded_record(file_id)
    if record is None:
        raise KnowledgeFileError("没有这个上传文件。", 404)
    previous = _result_path(file_id)
    try:
        payload = read_upload_bytes(file_id)
        if hashlib_sha(payload) != record["sha256"]:
            raise CleanFailure("上传文件内容和记录的哈希不一致，没有覆盖已有清洗结果。")
        sections, warnings = _extract(record["extension"], payload)
        if not sections:
            raise CleanFailure("没有提取到正文，没有生成清洗结果。原始文件仍保留。")
        _write_document(file_id, _document(record, sections, warnings))
    except CleanFailure as exc:
        if previous is not None:
            kept = finish_cleaning(file_id, status=CLEANED_STATUS)
            kept["error_message"] = f"重新清洗没有成功，已保留上一次结果。{exc.message}"
            return kept
        return finish_cleaning(file_id, status=FAILED_STATUS, error_message=exc.message)
    except Exception:
        if previous is not None:
            kept = finish_cleaning(file_id, status=CLEANED_STATUS)
            kept["error_message"] = "重新清洗没有成功，已保留上一次结果。"
            return kept
        return finish_cleaning(file_id, status=FAILED_STATUS, error_message="清洗没有完成。原始文件仍保留。")
    return finish_cleaning(file_id, status=CLEANED_STATUS)


def hashlib_sha(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()


def _clip_section(section: dict) -> dict:
    text = str(section.get("text") or "")
    clipped = dict(section)
    if len(text) > PREVIEW_CHARS:
        clipped["text"] = text[:PREVIEW_CHARS]
        clipped["text_truncated"] = True
    else:
        clipped["text_truncated"] = False
    return clipped


def cleaned_preview(file_id: str) -> dict:
    recover_stale_cleaning()
    record = get_uploaded_record(file_id)
    if record is None:
        raise KnowledgeFileError("没有这个上传文件。", 404)
    record.pop("stored_name", None)
    body = {
        "file_id": record["file_id"],
        "original_filename": record["original_filename"],
        "processing_status": record["processing_status"],
        "error_message": record["error_message"],
        "in_graph": False,
        "notice": NOTICE,
        "section_count": 0,
        "preview": None,
        "preview_truncated": False,
    }
    if record["processing_status"] != CLEANED_STATUS:
        return body
    path = _result_path(file_id)
    if path is None:
        body["processing_status"] = FAILED_STATUS
        body["error_message"] = "清洗状态为已完成，但没有找到清洗结果。"
        return body
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KnowledgeFileError("清洗结果无法读取。", 500) from exc
    sections = document.get("sections") if isinstance(document, dict) else None
    if not isinstance(sections, list):
        raise KnowledgeFileError("清洗结果格式不正确。", 500)
    preview_sections = [_clip_section(item) for item in sections[:PREVIEW_SECTIONS] if isinstance(item, dict)]
    source = document.get("source") if isinstance(document.get("source"), dict) else {}
    body["section_count"] = len(sections)
    body["preview_truncated"] = len(sections) > PREVIEW_SECTIONS or any(item.get("text_truncated") for item in preview_sections)
    body["preview"] = {
        "schema_version": document.get("schema_version"),
        "file_id": document.get("file_id"),
        "source": {
            "original_filename": source.get("original_filename"),
            "source_type": source.get("source_type"),
            "extension": source.get("extension"),
            "sha256": source.get("sha256"),
        },
        "sections": preview_sections,
        "warnings": document.get("warnings") if isinstance(document.get("warnings"), list) else [],
    }
    return body
