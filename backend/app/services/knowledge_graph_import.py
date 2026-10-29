"""把通过安全规则的候选知识写入独立数据库，并在查询时合并到基础图谱。

不修改 data/processed/graph/graph.json。不调用 DeepSeek。
因果候选、不确定关系和归属不明的名称不会进入正式事实。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime

from app.services.knowledge_file_store import (
    CLEANED_STATUS,
    KnowledgeFileError,
    get_uploaded_record,
    imports_database_path,
    valid_file_id,
)
from app.services.knowledge_graph import GRAPH_FILE, load_graph
from app.services.knowledge_graph_import_preview import build_current_preview, import_preview

_PLACEHOLDER = re.compile(r"^(某|某个|某台|某种|未知|待定|不详)")
_GENERIC_DEVICES = frozenset({"主电机", "电机", "设备", "部件"})
_SCHEMA = """
CREATE TABLE IF NOT EXISTS import_batches (
    batch_id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    candidate_sha256 TEXT NOT NULL,
    graph_sha256 TEXT NOT NULL,
    preview_schema_version TEXT NOT NULL,
    basis_key TEXT NOT NULL,
    status TEXT NOT NULL,
    applied INTEGER NOT NULL,
    imported_node_count INTEGER NOT NULL,
    imported_relation_count INTEGER NOT NULL,
    existing_node_count INTEGER NOT NULL,
    existing_relation_count INTEGER NOT NULL,
    new_evidence_count INTEGER NOT NULL,
    review_count INTEGER NOT NULL,
    rejected_count INTEGER NOT NULL,
    message TEXT NOT NULL,
    result_json TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS import_nodes (
    node_id TEXT PRIMARY KEY,
    created_batch_id TEXT NOT NULL,
    label TEXT NOT NULL,
    node_type TEXT NOT NULL,
    parent_id TEXT NOT NULL,
    category TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS import_node_support (
    node_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    PRIMARY KEY (node_id, batch_id)
);
CREATE TABLE IF NOT EXISTS import_edges (
    edge_id TEXT PRIMARY KEY,
    created_batch_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS import_edge_support (
    edge_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    PRIMARY KEY (edge_id, batch_id)
);
CREATE TABLE IF NOT EXISTS import_evidence (
    evidence_id TEXT PRIMARY KEY,
    node_id TEXT,
    edge_id TEXT,
    batch_id TEXT NOT NULL,
    file_id TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    section_id TEXT NOT NULL,
    locator_json TEXT NOT NULL,
    quote TEXT NOT NULL,
    source_type TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE (edge_id, file_id, section_id, locator_json, quote)
);
CREATE TABLE IF NOT EXISTS import_rejections (
    rejection_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    file_id TEXT NOT NULL,
    item_type TEXT NOT NULL,
    item_key TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _stable(prefix: str, *parts: str) -> str:
    raw = "\n".join(parts)
    return prefix + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _locator_text(locator: object) -> str:
    if isinstance(locator, dict):
        kind = str(locator.get("type") or "")
        index = locator.get("index")
        names = {"line": "行", "paragraph": "段落", "table": "表格", "csv_row": "CSV行"}
        if kind in names and index is not None:
            return f"{names[kind]} {index}"
        return json.dumps(locator, ensure_ascii=False, sort_keys=True)
    return str(locator or "")


def _placeholder(label: str) -> bool:
    text = str(label or "").strip()
    return not text or _PLACEHOLDER.match(text) is not None


def _generic_device(label: str) -> bool:
    return str(label or "").strip() in _GENERIC_DEVICES


def _record(file_id: str) -> dict:
    if not valid_file_id(file_id):
        raise KnowledgeFileError("没有这个上传文件。", 404)
    record = get_uploaded_record(file_id)
    if record is None:
        raise KnowledgeFileError("没有这个上传文件。", 404)
    record.pop("stored_name", None)
    return record


def _connect() -> sqlite3.Connection:
    path = imports_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(_SCHEMA)
    return connection


def _basis(document: dict) -> dict:
    fingerprint = document.get("graph_fingerprint") if isinstance(document.get("graph_fingerprint"), dict) else {}
    return {
        "source_sha256": document.get("source_sha256"),
        "candidate_sha256": document.get("candidate_sha256"),
        "graph_sha256": fingerprint.get("sha256"),
        "schema_version": document.get("schema_version"),
        "new_nodes": [
            (item.get("candidate_entity_id"), item.get("entity_type"), item.get("label"), item.get("scope"))
            for item in document.get("new_nodes") or []
        ],
        "new_relations": [
            (
                item.get("candidate_relation_id"),
                item.get("source_label"),
                item.get("target_label"),
                item.get("graph_relation"),
                item.get("certainty"),
            )
            for item in document.get("new_relations") or []
        ],
    }


def _graph_sha() -> str:
    digest = hashlib.sha256()
    with GRAPH_FILE.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _evidence_rows(relation: dict, file_id: str) -> list[dict]:
    rows = []
    for item in relation.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        if item.get("file_id") != file_id or not item.get("section_id") or not isinstance(item.get("locator"), dict):
            continue
        if not str(item.get("quote") or "").strip() or not item.get("sha256"):
            continue
        rows.append(item)
    return rows


def _plan_import(preview: dict, base_nodes: dict[str, dict], file_id: str) -> tuple[list[dict], list[dict], list[dict]]:
    matches = {item.get("candidate_entity_id"): item for item in preview.get("existing_node_matches") or []}
    new_nodes = {item.get("candidate_entity_id"): item for item in preview.get("new_nodes") or []}
    accepted = []
    rejections = []
    existing = []
    for relation in preview.get("duplicate_relations") or []:
        existing.append({"item_type": "relation", "item_key": relation.get("candidate_relation_id"), "reason": "正式图谱已经有这条关系，不重复创建。"})
    for bucket, default_reason in (
        ("review_required", "预演标记为待审核，不能自动入图。"),
        ("blocked_items", "预演标记为阻止入图。"),
        ("conflicts", "与已有图谱冲突，不能自动入图。"),
    ):
        for item in preview.get(bucket) or []:
            key = item.get("candidate_relation_id") or item.get("candidate_entity_id") or item.get("label")
            reason = str(item.get("reason") or default_reason)
            rejections.append({"item_type": item.get("item_type") or "item", "item_key": key, "reason": reason})
    for relation in preview.get("new_relations") or []:
        reason = _reject_reason(relation, matches, new_nodes, base_nodes, file_id)
        if reason:
            rejections.append({"item_type": "relation", "item_key": relation.get("candidate_relation_id"), "reason": reason})
            continue
        source_plan = _endpoint(preview, relation, "source", matches, new_nodes)
        target_plan = _endpoint(preview, relation, "target", matches, new_nodes)
        if source_plan is None or target_plan is None:
            rejections.append({"item_type": "relation", "item_key": relation.get("candidate_relation_id"), "reason": "关系两端不能解析为确定节点。"})
            continue
        accepted.append({"relation": relation, "source": source_plan, "target": target_plan})
    accepted_targets = {item["target"]["candidate_entity_id"] for item in accepted}
    for node in preview.get("new_nodes") or []:
        if node.get("candidate_entity_id") in accepted_targets:
            continue
        rejections.append(
            {
                "item_type": "entity",
                "item_key": node.get("candidate_entity_id"),
                "reason": _node_reject_reason(node),
            }
        )
    unique = []
    seen = set()
    for item in rejections:
        marker = (item["item_type"], item["item_key"], item["reason"])
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(item)
    return accepted, unique, existing


def _endpoint(preview: dict, relation: dict, side: str, matches: dict, new_nodes: dict) -> dict | None:
    label = relation.get("source_label" if side == "source" else "target_label")
    entity_type = relation.get("source_entity_type" if side == "source" else "target_entity_type")
    for pool in (matches, new_nodes):
        for item in pool.values():
            if item.get("label") == label and item.get("entity_type") == entity_type:
                if side == "target" and item.get("scope") not in {relation.get("source_label"), ""}:
                    continue
                if side == "source" and item.get("entity_type") == "device":
                    return item
                if side == "target":
                    return item
    return None


def _node_reject_reason(node: dict) -> str:
    label = str(node.get("label") or "")
    if _placeholder(label):
        return f"「{label}」是占位名称，不能作为真实工厂对象自动入图。"
    if node.get("entity_type") == "device" and _generic_device(label):
        return f"「{label}」是泛称，不能作为真实设备自动入图。"
    if node.get("entity_type") != "component":
        return "第一版只自动导入归属明确的新部件，其他新节点保持待审核。"
    if not node.get("scope"):
        return "新部件没有明确归属，不能自动入图。"
    return "这条新节点没有同时满足安全入图条件。"


def _reject_reason(relation: dict, matches: dict, new_nodes: dict, base_nodes: dict[str, dict], file_id: str) -> str:
    if relation.get("graph_relation") != "HAS_COMPONENT" or relation.get("certainty") != "explicit":
        return "只有原文明确的设备包含部件关系可以自动入图。"
    if relation.get("direction") == "cause_to_effect" or relation.get("causal_status") == "unverified":
        return "未验证因果关系不能自动入图。"
    if relation.get("import_allowed") is not True:
        return "预演没有把这条关系标为可以新增。"
    evidence = _evidence_rows(relation, file_id)
    if not evidence:
        return "关系缺少可追溯到当前文件的原文证据。"
    source = None
    for item in matches.values():
        if item.get("label") == relation.get("source_label") and item.get("entity_type") == "device" and item.get("graph_node_id"):
            source = item
            break
    if source is None:
        return "关系起点不是已唯一匹配的现有设备。"
    parent_id = str(source.get("graph_node_id"))
    parent = base_nodes.get(parent_id)
    if parent is None or str((parent.get("data") or {}).get("type")) != "device":
        return "归属设备不在基础图谱中，不能挂接新部件。"
    if _placeholder(str(source.get("label"))) or _generic_device(str(source.get("label"))):
        return f"「{source.get('label')}」是占位名称或泛称，不能作为自动入图的归属设备。"
    target = None
    for item in new_nodes.values():
        if item.get("label") == relation.get("target_label") and item.get("entity_type") == "component" and item.get("scope") == source.get("label"):
            target = item
            break
    if target is None:
        return "新部件没有明确且唯一的归属设备。"
    if _placeholder(str(target.get("label"))) or _generic_device(str(target.get("label"))):
        return f"「{target.get('label')}」不能作为真实部件自动入图。"
    child_id = _stable("import-component-", parent_id, "component", str(target.get("label")).strip())
    if child_id in base_nodes or child_id == parent_id:
        return "新节点编号与基础图谱冲突，没有入图。"
    return ""


def _node_payload(parent_id: str, parent_label: str, label: str, evidence: list[dict], batch_id: str) -> dict:
    node_id = _stable("import-component-", parent_id, "component", label.strip())
    refs = []
    for item in evidence:
        refs.append(
            {
                "document": item.get("original_filename") or "",
                "section": item.get("section_id") or "",
                "locator": _locator_text(item.get("locator")),
                "source_type": "user_upload",
                "file_id": item.get("file_id"),
                "sha256": item.get("sha256"),
                "batch_id": batch_id,
                "quote": item.get("quote"),
            }
        )
    return {
        "id": node_id,
        "parentId": parent_id,
        "data": {
            "label": label.strip(),
            "type": "component",
            "category": "equipment",
            "device_label": parent_label,
            "source_refs": refs,
        },
    }


def _edge_payload(parent_id: str, node_id: str, evidence: list[dict], batch_id: str) -> dict:
    edge_id = _stable("import-edge-", parent_id, node_id, "HAS_COMPONENT")
    refs = []
    for item in evidence:
        refs.append(
            {
                "document": item.get("original_filename") or "",
                "section": item.get("section_id") or "",
                "locator": _locator_text(item.get("locator")),
                "source_type": "user_upload",
                "file_id": item.get("file_id"),
                "sha256": item.get("sha256"),
                "batch_id": batch_id,
                "quote": item.get("quote"),
            }
        )
    return {
        "id": edge_id,
        "source": parent_id,
        "target": node_id,
        "data": {"relation": "HAS_COMPONENT", "source_refs": refs},
    }


def _result_body(record: dict, batch_id: str, status: str, applied: bool, message: str, counts: dict, rejections: list[dict], created_at: str, repeated: bool = False) -> dict:
    return {
        "file_id": record["file_id"],
        "batch_id": batch_id,
        "status": status,
        "applied": applied,
        "repeated": repeated,
        "message": message,
        "imported_node_count": counts["imported_node_count"],
        "imported_relation_count": counts["imported_relation_count"],
        "existing_node_count": counts["existing_node_count"],
        "existing_relation_count": counts["existing_relation_count"],
        "new_evidence_count": counts["new_evidence_count"],
        "review_count": counts["review_count"],
        "rejected_count": len(rejections),
        "rejections": rejections,
        "created_at": created_at,
        "in_graph_file": False,
        "notice": "安全入图只写入运行时数据库，没有修改基础图谱文件。",
    }


def _empty_counts() -> dict:
    return {
        "imported_node_count": 0,
        "imported_relation_count": 0,
        "existing_node_count": 0,
        "existing_relation_count": 0,
        "new_evidence_count": 0,
        "review_count": 0,
    }


def _refresh_active(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        UPDATE import_nodes
        SET active = CASE WHEN node_id IN (
            SELECT support.node_id
            FROM import_node_support AS support
            JOIN import_batches AS batch ON batch.batch_id = support.batch_id
            WHERE batch.active = 1 AND batch.status IN ('applied', 'evidence_recorded')
        ) THEN 1 ELSE 0 END
        """
    )
    connection.execute(
        """
        UPDATE import_edges
        SET active = CASE WHEN edge_id IN (
            SELECT support.edge_id
            FROM import_edge_support AS support
            JOIN import_batches AS batch ON batch.batch_id = support.batch_id
            WHERE batch.active = 1 AND batch.status IN ('applied', 'evidence_recorded')
        ) THEN 1 ELSE 0 END
        """
    )


def deactivate_batch(batch_id: str) -> None:
    """按批次停用新增知识。证据保留，供以后恢复。本次没有开放给页面。"""
    with _connect() as connection:
        connection.execute("UPDATE import_batches SET active = 0 WHERE batch_id = ?", (batch_id,))
        _refresh_active(connection)
        connection.commit()


def activate_batch(batch_id: str) -> None:
    with _connect() as connection:
        connection.execute("UPDATE import_batches SET active = 1 WHERE batch_id = ?", (batch_id,))
        _refresh_active(connection)
        connection.commit()


def import_result(file_id: str) -> dict:
    record = _record(file_id)
    path = imports_database_path()
    if not path.is_file():
        return _result_body(record, "", "not_imported", False, "这个文件还没有入图结果。", _empty_counts(), [], "", False)
    connection = _connect()
    try:
        row = connection.execute(
            """
            SELECT result_json FROM import_batches
            WHERE file_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (file_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return _result_body(record, "", "not_imported", False, "这个文件还没有入图结果。", _empty_counts(), [], "", False)
    return json.loads(row["result_json"])


def _saved_basis_row(connection: sqlite3.Connection, basis_key: str):
    return connection.execute(
        """
        SELECT result_json FROM import_batches
        WHERE basis_key = ? AND status IN ('applied', 'evidence_recorded', 'nothing_imported')
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (basis_key,),
    ).fetchone()


def apply_import(file_id: str) -> dict:
    record = _record(file_id)
    if record["processing_status"] != CLEANED_STATUS:
        raise KnowledgeFileError("只有已清洗的文件才能入图。", 409)
    saved = import_preview(file_id)
    if saved.get("status") != "previewed":
        raise KnowledgeFileError("还没有入图预演，不能入图。", 409)
    if saved.get("source_sha256") != record["sha256"] or saved.get("graph_fingerprint", {}).get("sha256") != _graph_sha():
        raise KnowledgeFileError("预演所依据的图谱或文件已经变化，请重新预演。", 409)
    try:
        fresh = build_current_preview(record)
    except KnowledgeFileError:
        raise
    if _basis(saved) != _basis(fresh):
        raise KnowledgeFileError("预演已经过期，请重新预演。", 409)
    base = load_graph()
    base_nodes = {node["id"]: node for node in base["nodes"]}
    accepted, rejections, existing = _plan_import(fresh, base_nodes, file_id)
    review_count = len(fresh.get("review_required") or [])
    basis_key = hashlib.sha256(json.dumps(_basis(fresh), ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    connection = _connect()
    connection.isolation_level = None
    try:
        previous = _saved_basis_row(connection, basis_key)
        if previous is not None:
            body = json.loads(previous["result_json"])
            body["repeated"] = True
            body["message"] = "同一份预演已经处理过，没有重复创建节点或关系。"
            return body
        connection.execute("BEGIN IMMEDIATE")
        batch_id = uuid.uuid4().hex
        created_at = _now()
        counts = _empty_counts()
        counts["review_count"] = review_count
        counts["existing_relation_count"] = len(existing)
        for item in accepted:
            relation = item["relation"]
            source = item["source"]
            target = item["target"]
            evidence = _evidence_rows(relation, file_id)
            parent_id = str(source["graph_node_id"])
            label = str(target["label"]).strip()
            node = _node_payload(parent_id, str(source["label"]), label, evidence, batch_id)
            edge = _edge_payload(parent_id, node["id"], evidence, batch_id)
            node_exists = connection.execute("SELECT node_id FROM import_nodes WHERE node_id = ?", (node["id"],)).fetchone()
            if node_exists is None:
                connection.execute(
                    """
                    INSERT INTO import_nodes (
                        node_id, created_batch_id, label, node_type, parent_id, category, active, created_at, payload_json
                    ) VALUES (?, ?, ?, 'component', ?, 'equipment', 1, ?, ?)
                    """,
                    (node["id"], batch_id, label, parent_id, created_at, json.dumps(node, ensure_ascii=False)),
                )
                counts["imported_node_count"] += 1
            else:
                counts["existing_node_count"] += 1
            connection.execute("INSERT OR IGNORE INTO import_node_support (node_id, batch_id) VALUES (?, ?)", (node["id"], batch_id))
            edge_exists = connection.execute("SELECT edge_id FROM import_edges WHERE edge_id = ?", (edge["id"],)).fetchone()
            if edge_exists is None:
                connection.execute(
                    """
                    INSERT INTO import_edges (
                        edge_id, created_batch_id, source_id, target_id, relation, active, created_at
                    ) VALUES (?, ?, ?, ?, 'HAS_COMPONENT', 1, ?)
                    """,
                    (edge["id"], batch_id, parent_id, node["id"], created_at),
                )
                counts["imported_relation_count"] += 1
            else:
                counts["existing_relation_count"] += 1
            connection.execute("INSERT OR IGNORE INTO import_edge_support (edge_id, batch_id) VALUES (?, ?)", (edge["id"], batch_id))
            for entry in evidence:
                locator_json = json.dumps(entry.get("locator"), ensure_ascii=False, sort_keys=True)
                before = connection.total_changes
                connection.execute(
                    """
                    INSERT OR IGNORE INTO import_evidence (
                        evidence_id, node_id, edge_id, batch_id, file_id, source_sha256, original_filename,
                        section_id, locator_json, quote, source_type, active, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'user_upload', 1, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        node["id"],
                        edge["id"],
                        batch_id,
                        file_id,
                        entry.get("sha256"),
                        entry.get("original_filename") or record["original_filename"],
                        entry.get("section_id"),
                        locator_json,
                        entry.get("quote"),
                        created_at,
                    ),
                )
                if connection.total_changes > before:
                    counts["new_evidence_count"] += 1
        for item in rejections:
            connection.execute(
                """
                INSERT INTO import_rejections (rejection_id, batch_id, file_id, item_type, item_key, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (uuid.uuid4().hex, batch_id, file_id, item["item_type"], str(item["item_key"] or ""), item["reason"], created_at),
            )
        if counts["imported_node_count"] or counts["imported_relation_count"]:
            status = "applied"
            applied = True
            message = "已把符合安全规则的包含关系写入运行时图谱。"
        elif counts["new_evidence_count"]:
            status = "evidence_recorded"
            applied = False
            message = "没有新建关系，已为已有关系补充证据。"
        else:
            status = "nothing_imported"
            applied = False
            message = "没有符合安全规则的新知识，没有入图。"
        if os.environ.get("KNOWLEDGE_IMPORT_INJECT_ERROR") == "1":
            raise KnowledgeFileError("导入事务失败，没有写入不完整图谱。", 500)
        body = _result_body(record, batch_id, status, applied, message, counts, rejections, created_at)
        connection.execute(
            """
            INSERT INTO import_batches (
                batch_id, file_id, source_sha256, candidate_sha256, graph_sha256, preview_schema_version,
                basis_key, status, applied, imported_node_count, imported_relation_count, existing_node_count,
                existing_relation_count, new_evidence_count, review_count, rejected_count, message, result_json,
                active, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                batch_id,
                file_id,
                fresh["source_sha256"],
                fresh["candidate_sha256"],
                fresh["graph_fingerprint"]["sha256"],
                fresh["schema_version"],
                basis_key,
                status,
                1 if applied else 0,
                counts["imported_node_count"],
                counts["imported_relation_count"],
                counts["existing_node_count"],
                counts["existing_relation_count"],
                counts["new_evidence_count"],
                review_count,
                len(rejections),
                message,
                json.dumps(body, ensure_ascii=False),
                created_at,
            ),
        )
        _refresh_active(connection)
        connection.commit()
        return body
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _merged_refs(connection: sqlite3.Connection, edge_id: str, node_id: str) -> list[dict]:
    rows = connection.execute(
        """
        SELECT file_id, source_sha256, original_filename, section_id, locator_json, quote, batch_id, created_at
        FROM import_evidence
        WHERE active = 1 AND (edge_id = ? OR node_id = ?)
        ORDER BY created_at, file_id, section_id
        """,
        (edge_id, node_id),
    ).fetchall()
    refs = []
    seen = set()
    for row in rows:
        locator = json.loads(row["locator_json"])
        marker = (row["file_id"], row["section_id"], row["locator_json"], row["quote"])
        if marker in seen:
            continue
        seen.add(marker)
        refs.append(
            {
                "document": row["original_filename"],
                "section": row["section_id"],
                "locator": _locator_text(locator),
                "source_type": "user_upload",
                "file_id": row["file_id"],
                "sha256": row["source_sha256"],
                "batch_id": row["batch_id"],
                "quote": row["quote"],
            }
        )
    return refs


def merge_imported_graph(base: dict | None = None) -> dict:
    """在基础图谱副本上附加仍有效的安全导入结果。不写 graph.json。"""
    graph = base if base is not None else load_graph()
    nodes = [dict(node, data=dict(node["data"])) for node in graph["nodes"]]
    edges = [dict(edge, data=dict(edge["data"])) for edge in graph["edges"]]
    path = imports_database_path()
    if not path.is_file():
        return {"nodes": nodes, "edges": edges}
    known = {node["id"] for node in nodes}
    edge_ids = {edge["id"] for edge in edges}
    connection = _connect()
    try:
        node_rows = connection.execute("SELECT node_id, parent_id, payload_json FROM import_nodes WHERE active = 1 ORDER BY node_id").fetchall()
        for row in node_rows:
            if row["node_id"] in known or row["parent_id"] not in known:
                continue
            payload = json.loads(row["payload_json"])
            payload["parentId"] = row["parent_id"]
            payload["data"]["source_refs"] = _merged_refs(connection, "", row["node_id"]) or payload["data"].get("source_refs") or []
            payload["data"]["category"] = "equipment"
            payload["data"]["type"] = "component"
            nodes.append(payload)
            known.add(row["node_id"])
        edge_rows = connection.execute(
            "SELECT edge_id, source_id, target_id, relation FROM import_edges WHERE active = 1 ORDER BY edge_id"
        ).fetchall()
        for row in edge_rows:
            if row["edge_id"] in edge_ids or row["source_id"] not in known or row["target_id"] not in known:
                continue
            if any(edge["source"] == row["source_id"] and edge["target"] == row["target_id"] and edge["data"].get("relation") == row["relation"] for edge in edges):
                continue
            edges.append(
                {
                    "id": row["edge_id"],
                    "source": row["source_id"],
                    "target": row["target_id"],
                    "data": {
                        "relation": row["relation"],
                        "source_refs": _merged_refs(connection, row["edge_id"], row["target_id"]),
                    },
                }
            )
            edge_ids.add(row["edge_id"])
    finally:
        connection.close()
    return {"nodes": nodes, "edges": edges}
