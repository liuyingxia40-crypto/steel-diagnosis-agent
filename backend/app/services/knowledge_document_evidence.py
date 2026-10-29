"""从已保存的官方知识块、清洗 JSON 和候选原文中检索证据。

只读取本地文件。不调用模型，不把原文共现写成因果关系，也不执行原文中的命令。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.services.knowledge_file_store import CLEANED_STATUS, cleaned_dir, extracted_dir, get_uploaded_record, valid_file_id
from app.services.knowledge_search import SearchIndexError, bm25_scores, load_index, tokenize

ROOT = Path(__file__).resolve().parents[3]
OFFICIAL_DIR = ROOT / "data" / "official"
MAX_HITS = 5
MAX_SNIPPET = 400
UNCONFIRMED = "无法确认"


def collect_document_evidence(entity_a: str, entity_b: str, *, left_in_graph: bool, right_in_graph: bool) -> dict:
    """分别检索两个名称，并找出同一已保存文本中同时出现两者的片段。"""
    left_name = entity_a.strip()
    right_name = entity_b.strip()
    warnings: list[str] = []
    left_items, left_truncated = _hits_for(left_name, left_in_graph, warnings)
    right_items, right_truncated = _hits_for(right_name, right_in_graph, warnings)
    cross, cross_truncated = _cross(left_name, right_name, left_in_graph, right_in_graph, warnings)
    return {
        "ok": True,
        "warnings": _unique(warnings),
        "document_evidence": {
            "entity_a": left_items,
            "entity_b": right_items,
            "limit_per_entity": MAX_HITS,
            "snippet_limit": MAX_SNIPPET,
            "truncated": {"entity_a": left_truncated, "entity_b": right_truncated},
            "external_model_allowed": False,
            "release_scope": "local_only",
        },
        "cross_document_evidence": cross,
        "cross_document_truncated": cross_truncated,
    }


def _hits_for(name: str, in_graph: bool, warnings: list[str]) -> tuple[list[dict], bool]:
    found: list[tuple[tuple, dict]] = []
    found.extend(_cleaned_hits(name, in_graph))
    found.extend(_candidate_hits(name, in_graph))
    try:
        found.extend(_official_hits(name, in_graph))
    except SearchIndexError as exc:
        warnings.append(f"官方知识检索索引不可用：{exc.message}")
    found.sort(key=lambda item: item[0])
    truncated = len(found) > MAX_HITS
    return [item for _, item in found[:MAX_HITS]], truncated


def _official_hits(name: str, in_graph: bool) -> list[tuple[tuple, dict]]:
    payload = load_index()
    chunks = [chunk for chunk in payload.get("chunks") or [] if isinstance(chunk, dict)]
    method = str(payload.get("method") or "")
    scores = bm25_scores(chunks, tokenize(name), name)
    rows = []
    for index, (chunk, score) in enumerate(zip(chunks, scores)):
        text = str(chunk.get("text") or "")
        section = str(chunk.get("section") or "")
        direct = name in text or name in section
        if not direct and score <= 0:
            continue
        document = str(chunk.get("document") or "")
        snippet, truncated = _window(section + "\n" + text if section else text, name)
        rows.append(
            (
                (0 if direct else 1, 2, -score, index),
                _item(
                    source_type="existing_knowledge",
                    original_filename=_official_original(document),
                    indexed_document=document,
                    file_id="",
                    section_id="",
                    section=section,
                    locator=str(chunk.get("locator") or ""),
                    snippet=snippet,
                    snippet_truncated=truncated,
                    matched_query=name,
                    retrieval_method=method,
                    retrieval_source="official_rag",
                    direct=direct,
                    in_graph=in_graph,
                    knowledge_chunk_id=str(chunk.get("id") or ""),
                ),
            )
        )
    return rows


def _cleaned_hits(name: str, in_graph: bool) -> list[tuple[tuple, dict]]:
    documents = _cleaned_documents()
    chunks = []
    owners = []
    for document in documents:
        source = document.get("source") if isinstance(document.get("source"), dict) else {}
        for section_index, section in enumerate(document.get("sections") or []):
            if not isinstance(section, dict):
                continue
            text = str(section.get("text") or "")
            heading = str(section.get("heading") or "")
            chunks.append({"section": heading, "text": text})
            owners.append((document, source, section, section_index, text, heading))
    if not chunks:
        return []
    scores = bm25_scores(chunks, tokenize(name), name)
    rows = []
    for index, (score, owner) in enumerate(zip(scores, owners)):
        document, source, section, section_index, text, heading = owner
        direct = name in text or (bool(heading) and name in heading)
        if not direct and score <= 0:
            continue
        snippet, truncated = _window(heading + "\n" + text if heading else text, name)
        rows.append(
            (
                (0 if direct else 1, 0, -score, index),
                _item(
                    source_type=str(source.get("source_type") or ""),
                    original_filename=str(source.get("original_filename") or ""),
                    indexed_document="",
                    file_id=str(document.get("file_id") or ""),
                    section_id=str(section.get("section_id") or ""),
                    section=heading,
                    locator=_locator_text(section.get("locator")),
                    snippet=snippet,
                    snippet_truncated=truncated,
                    matched_query=name,
                    retrieval_method="bm25",
                    retrieval_source="cleaned_json",
                    direct=direct,
                    in_graph=in_graph,
                    knowledge_chunk_id="",
                ),
            )
        )
    return rows


def _candidate_hits(name: str, in_graph: bool) -> list[tuple[tuple, dict]]:
    rows = []
    for index, (document, evidence) in enumerate(_candidate_evidence()):
        quote = str(evidence.get("quote") or "")
        heading = str(evidence.get("heading") or "")
        if name not in quote and name not in heading:
            continue
        source = document.get("source") if isinstance(document.get("source"), dict) else {}
        snippet, truncated = _window(quote, name)
        method = evidence.get("method") if isinstance(evidence.get("method"), str) else ""
        rows.append(
            (
                (0, 1, 0, index),
                _item(
                    source_type=str(source.get("source_type") or "user_upload"),
                    original_filename=str(evidence.get("original_filename") or source.get("original_filename") or ""),
                    indexed_document="",
                    file_id=str(evidence.get("file_id") or document.get("file_id") or ""),
                    section_id=str(evidence.get("section_id") or ""),
                    section=heading,
                    locator=_locator_text(evidence.get("locator")),
                    snippet=snippet,
                    snippet_truncated=truncated,
                    matched_query=name,
                    retrieval_method=method,
                    retrieval_source="candidate_evidence",
                    direct=True,
                    in_graph=in_graph,
                    knowledge_chunk_id="",
                ),
            )
        )
    return rows


def _cross(left: str, right: str, left_in_graph: bool, right_in_graph: bool, warnings: list[str]) -> tuple[list[dict], bool]:
    if not left or not right or left == right:
        return [], False
    found: list[dict] = []
    seen: set[tuple] = set()

    def add(item: dict) -> None:
        marker = (
            item["retrieval_source"],
            item["file_id"],
            item["indexed_document"],
            item["section_id"],
            item["section"],
            item["locator"],
            item["snippet"],
        )
        if marker in seen:
            return
        seen.add(marker)
        found.append(item)

    try:
        payload = load_index()
    except SearchIndexError as exc:
        warnings.append(f"官方知识检索索引不可用：{exc.message}")
        payload = {"chunks": [], "method": ""}
    for chunk in payload.get("chunks") or []:
        if not isinstance(chunk, dict):
            continue
        section = str(chunk.get("section") or "")
        text = str(chunk.get("text") or "")
        haystack = section + "\n" + text
        if left not in haystack or right not in haystack:
            continue
        document = str(chunk.get("document") or "")
        snippet, truncated = _window(haystack, left, right)
        add(
            _cross_item(
                source_type="existing_knowledge",
                original_filename=_official_original(document),
                indexed_document=document,
                file_id="",
                section_id="",
                section=section,
                locator=str(chunk.get("locator") or ""),
                snippet=snippet,
                snippet_truncated=truncated,
                left=left,
                right=right,
                left_in_graph=left_in_graph,
                right_in_graph=right_in_graph,
                retrieval_method="exact_substring",
                retrieval_source="official_rag",
                knowledge_chunk_id=str(chunk.get("id") or ""),
            )
        )
    for document in _cleaned_documents():
        source = document.get("source") if isinstance(document.get("source"), dict) else {}
        for section in document.get("sections") or []:
            if not isinstance(section, dict):
                continue
            heading = str(section.get("heading") or "")
            text = str(section.get("text") or "")
            haystack = heading + "\n" + text
            if left not in haystack or right not in haystack:
                continue
            snippet, truncated = _window(haystack, left, right)
            add(
                _cross_item(
                    source_type=str(source.get("source_type") or ""),
                    original_filename=str(source.get("original_filename") or ""),
                    indexed_document="",
                    file_id=str(document.get("file_id") or ""),
                    section_id=str(section.get("section_id") or ""),
                    section=heading,
                    locator=_locator_text(section.get("locator")),
                    snippet=snippet,
                    snippet_truncated=truncated,
                    left=left,
                    right=right,
                    left_in_graph=left_in_graph,
                    right_in_graph=right_in_graph,
                    retrieval_method="exact_substring",
                    retrieval_source="cleaned_json",
                    knowledge_chunk_id="",
                )
            )
    for document, evidence in _candidate_evidence():
        quote = str(evidence.get("quote") or "")
        heading = str(evidence.get("heading") or "")
        haystack = heading + "\n" + quote
        if left not in haystack or right not in haystack:
            continue
        source = document.get("source") if isinstance(document.get("source"), dict) else {}
        snippet, truncated = _window(haystack, left, right)
        method = evidence.get("method") if isinstance(evidence.get("method"), str) else "exact_substring"
        add(
            _cross_item(
                source_type=str(source.get("source_type") or "user_upload"),
                original_filename=str(evidence.get("original_filename") or source.get("original_filename") or ""),
                indexed_document="",
                file_id=str(evidence.get("file_id") or document.get("file_id") or ""),
                section_id=str(evidence.get("section_id") or ""),
                section=heading,
                locator=_locator_text(evidence.get("locator")),
                snippet=snippet,
                snippet_truncated=truncated,
                left=left,
                right=right,
                left_in_graph=left_in_graph,
                right_in_graph=right_in_graph,
                retrieval_method=method,
                retrieval_source="candidate_evidence",
                knowledge_chunk_id="",
            )
        )
    return found[:MAX_HITS], len(found) > MAX_HITS


def _cleaned_documents() -> list[dict]:
    directory = cleaned_dir()
    if not directory.is_dir():
        return []
    documents = []
    for path in sorted(directory.glob("*.json")):
        if not path.is_file():
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(document, dict) or not isinstance(document.get("sections"), list):
            continue
        file_id = str(document.get("file_id") or "")
        if not valid_file_id(file_id):
            continue
        record = get_uploaded_record(file_id)
        if record is None or record.get("processing_status") != CLEANED_STATUS:
            continue
        documents.append(document)
    return documents


def _candidate_evidence() -> list[tuple[dict, dict]]:
    directory = extracted_dir()
    if not directory.is_dir():
        return []
    rows = []
    for path in sorted(directory.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(document, dict) or document.get("extraction_status") != "extracted":
            continue
        for bucket_name in ("entities", "relations"):
            for item in document.get(bucket_name) or []:
                if not isinstance(item, dict):
                    continue
                for evidence in item.get("evidence") or []:
                    if isinstance(evidence, dict) and str(evidence.get("quote") or ""):
                        rows.append((document, evidence))
    return rows


def _item(
    *,
    source_type: str,
    original_filename: str,
    indexed_document: str,
    file_id: str,
    section_id: str,
    section: str,
    locator: str,
    snippet: str,
    snippet_truncated: bool,
    matched_query: str,
    retrieval_method: str,
    retrieval_source: str,
    direct: bool,
    in_graph: bool,
    knowledge_chunk_id: str,
) -> dict:
    return {
        "source_type": source_type,
        "original_filename": original_filename,
        "indexed_document": indexed_document,
        "file_id": file_id,
        "official_document_id": indexed_document if source_type == "existing_knowledge" else "",
        "section_id": section_id,
        "section": section,
        "locator": locator,
        "snippet": snippet,
        "snippet_truncated": snippet_truncated,
        "matched_query": matched_query,
        "retrieval_method": retrieval_method,
        "retrieval_source": retrieval_source,
        "direct_mention": direct,
        "match_kind": "direct_mention" if direct else "keyword_related",
        "in_graph": in_graph,
        "graph_relation_established": False,
        "fact_class": "document_evidence",
        "causal_conclusion": False,
        "knowledge_chunk_id": knowledge_chunk_id,
        "external_model_allowed": False,
        "release_scope": "local_only",
    }


def _cross_item(**kwargs: str | bool) -> dict:
    left = str(kwargs["left"])
    right = str(kwargs["right"])
    item = _item(
        source_type=str(kwargs["source_type"]),
        original_filename=str(kwargs["original_filename"]),
        indexed_document=str(kwargs["indexed_document"]),
        file_id=str(kwargs["file_id"]),
        section_id=str(kwargs["section_id"]),
        section=str(kwargs["section"]),
        locator=str(kwargs["locator"]),
        snippet=str(kwargs["snippet"]),
        snippet_truncated=bool(kwargs["snippet_truncated"]),
        matched_query=left,
        retrieval_method=str(kwargs["retrieval_method"]),
        retrieval_source=str(kwargs["retrieval_source"]),
        direct=True,
        in_graph=bool(kwargs["left_in_graph"]) and bool(kwargs["right_in_graph"]),
        knowledge_chunk_id=str(kwargs["knowledge_chunk_id"]),
    )
    item["matched_queries"] = [left, right]
    item["entity_in_graph"] = {left: bool(kwargs["left_in_graph"]), right: bool(kwargs["right_in_graph"])}
    item["note"] = "同一处同时出现不代表因果关系。"
    return item


def _official_original(markdown_name: str) -> str:
    if not markdown_name.endswith(".md"):
        return UNCONFIRMED
    candidate = markdown_name[: -len(".md")] + ".docx"
    path = OFFICIAL_DIR / candidate
    if candidate.startswith("~$") or not path.is_file():
        return UNCONFIRMED
    return candidate


def _locator_text(locator: object) -> str:
    if isinstance(locator, dict):
        kind = str(locator.get("type") or "")
        index = locator.get("index")
        names = {"line": "行", "paragraph": "段落", "table": "表格", "csv_row": "CSV行"}
        if kind in names and index is not None:
            return f"{names[kind]} {index}"
        return json.dumps(locator, ensure_ascii=False, sort_keys=True)
    return str(locator or "")


def _window(text: str, *needles: str) -> tuple[str, bool]:
    if len(text) <= MAX_SNIPPET:
        return text, False
    positions = [text.find(needle) for needle in needles if needle and needle in text]
    start = max(0, min(positions) - 40) if positions else 0
    clipped = text[start : start + MAX_SNIPPET]
    return clipped, True


def _unique(items: list[str]) -> list[str]:
    result = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result
