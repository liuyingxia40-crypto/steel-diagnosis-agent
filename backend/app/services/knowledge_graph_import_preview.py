"""把候选知识和正式图谱做入图前对照。

只生成预演计划。不修改 graph.json，不写知识检索索引，也不调用 DeepSeek。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime

from app.services.knowledge_file_store import (
    CLEANED_STATUS,
    KnowledgeFileError,
    extracted_dir,
    get_uploaded_record,
    import_preview_dir,
    valid_file_id,
)
from app.services.knowledge_graph import GRAPH_FILE

SCHEMA_VERSION = "1.0"
NOTICE = "入图预演没有修改正式知识图谱。"
_STALE_SECONDS = 120
_MAPPED_RELATIONS = frozenset(
    {"HAS_ACTION", "HAS_COMPONENT", "HAS_FAULT", "HAS_FEATURE", "HAS_PARAMETER"}
)
_CAUSE_RELATIONS = frozenset({"CAUSE_CANDIDATE"})
_PARAMETER_RELATIONS = frozenset({"HAS_PARAMETER", "COMPONENT_PARAMETER_CANDIDATE"})
_OWNER_RELATIONS = frozenset({"HAS_COMPONENT", "HAS_FAULT", "HAS_PARAMETER", "COMPONENT_PARAMETER_CANDIDATE"})
_EVIDENCE_KEYS = (
    "file_id",
    "original_filename",
    "sha256",
    "section_id",
    "heading",
    "locator",
    "quote",
    "method",
    "candidate_status",
    "table_row",
    "header_locator",
)
_LOCKS: dict[str, threading.Lock] = {}
_LOCK_GUARD = threading.Lock()
_SPACE = re.compile(r"\s+")


class PreviewFailure(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _norm(value: object) -> str:
    return _SPACE.sub("", str(value or "")).strip()


def _thread_lock(file_id: str) -> threading.Lock:
    with _LOCK_GUARD:
        if file_id not in _LOCKS:
            _LOCKS[file_id] = threading.Lock()
        return _LOCKS[file_id]


def _preview_path(file_id: str):
    directory = import_preview_dir().resolve()
    target = (directory / f"{file_id}.json").resolve()
    if target.parent != directory or not target.is_file():
        return None
    return target


def _extracted_path(file_id: str):
    directory = extracted_dir().resolve()
    target = (directory / f"{file_id}.json").resolve()
    if target.parent != directory or not target.is_file():
        return None
    return target


def _lock_path(file_id: str):
    return import_preview_dir().resolve() / f".{file_id}.lock"


def _acquire(file_id: str) -> bool:
    lock = _thread_lock(file_id)
    if not lock.acquire(blocking=False):
        return False
    path = _lock_path(file_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and time.time() - path.stat().st_mtime < _STALE_SECONDS:
            lock.release()
            return False
        path.write_text(file_id, encoding="utf-8")
    except OSError:
        lock.release()
        raise
    return True


def _release(file_id: str) -> None:
    path = _lock_path(file_id)
    try:
        if path.is_file():
            path.unlink()
    finally:
        _thread_lock(file_id).release()


def _record(file_id: str) -> dict:
    if not valid_file_id(file_id):
        raise KnowledgeFileError("没有这个上传文件。", 404)
    record = get_uploaded_record(file_id)
    if record is None:
        raise KnowledgeFileError("没有这个上传文件。", 404)
    record.pop("stored_name", None)
    return record


def _read_json(path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreviewFailure("候选知识或预演结果无法读取。") from exc
    if not isinstance(payload, dict):
        raise PreviewFailure("结果不是 JSON 对象。")
    return payload


def _write(file_id: str, document: dict) -> None:
    directory = import_preview_dir().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    target = (directory / f"{file_id}.json").resolve()
    if target.parent != directory:
        raise KnowledgeFileError("入图预演不能写到预演目录之外。", 500)
    temporary = directory / f".{file_id}.{uuid.uuid4().hex}.partial"
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)


def _load_graph() -> tuple[dict, str]:
    if not GRAPH_FILE.is_file():
        raise PreviewFailure("没有找到正式知识图谱，不能生成入图预演。")
    try:
        raw = GRAPH_FILE.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreviewFailure("正式知识图谱无法读取，没有生成入图预演。") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("nodes"), list) or not isinstance(payload.get("edges"), list):
        raise PreviewFailure("正式知识图谱缺少节点或关系，没有生成入图预演。")
    return payload, hashlib.sha256(raw).hexdigest()


def _public_evidence(item: dict) -> dict:
    copied = {}
    for key in _EVIDENCE_KEYS:
        if key in item:
            copied[key] = item[key]
    return copied


def _evidence_ok(item: dict) -> bool:
    return bool(
        item.get("file_id")
        and item.get("section_id")
        and isinstance(item.get("locator"), dict)
        and str(item.get("quote") or "").strip()
        and item.get("sha256")
    )


class _Graph:
    def __init__(self, payload: dict) -> None:
        self.nodes = {}
        self.by_type_label: dict[tuple[str, str], list[str]] = {}
        self.parents: dict[str, list[str]] = {}
        self.edges: dict[tuple[str, str, str], str] = {}
        for node in payload["nodes"]:
            if not isinstance(node, dict):
                continue
            data = node.get("data") if isinstance(node.get("data"), dict) else {}
            node_id = str(node.get("id") or "")
            if not node_id:
                continue
            self.nodes[node_id] = data
            key = (str(data.get("type") or ""), _norm(data.get("label")))
            self.by_type_label.setdefault(key, []).append(node_id)
        for edge in payload["edges"]:
            if not isinstance(edge, dict):
                continue
            data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
            relation = str(data.get("relation") or "")
            source = str(edge.get("source") or "")
            target = str(edge.get("target") or "")
            edge_id = str(edge.get("id") or "")
            if source and target and relation:
                self.edges[(source, target, relation)] = edge_id
            if relation == "HAS_COMPONENT" and source and target:
                self.parents.setdefault(target, []).append(source)

    def ids_for(self, node_type: str, label: str) -> list[str]:
        return list(self.by_type_label.get((node_type, _norm(label)), []))

    def label(self, node_id: str) -> str:
        return str(self.nodes[node_id].get("label") or "")

    def parent_labels(self, node_id: str) -> list[str]:
        return [self.label(source) for source in self.parents.get(node_id, []) if source in self.nodes]


def _owners(entity_id: str, relations: list[dict]) -> list[str]:
    found = []
    for relation in relations:
        if relation.get("target") != entity_id or relation.get("relation_type") not in _OWNER_RELATIONS:
            continue
        label = str(relation.get("source_label") or "")
        if label and label not in found:
            found.append(label)
    return found


def _attributes(entity: dict) -> dict:
    raw = entity.get("attributes") if isinstance(entity.get("attributes"), dict) else {}
    return {
        "value": str(raw.get("value") or ""),
        "unit": str(raw.get("unit") or ""),
        "scope": str(raw.get("scope") or ""),
    }


def _entity_plan(entity: dict, *, status: str, reason: str, graph_node_id: str = "", rejected: list[dict] | None = None) -> dict:
    return {
        "item_type": "entity",
        "candidate_entity_id": entity["entity_id"],
        "label": entity["label"],
        "entity_type": entity["entity_type"],
        "scope": _attributes(entity)["scope"],
        "attributes": _attributes(entity),
        "status": status,
        "reason": reason,
        "graph_node_id": graph_node_id,
        "graph_label": "",
        "graph_type": "",
        "matched_parent": "",
        "rejected_same_name_nodes": rejected or [],
        "import_allowed": status == "planned_new",
        "evidence": [_public_evidence(item) for item in entity.get("evidence") or [] if isinstance(item, dict)],
        "in_graph": False,
    }


def _fill_graph_fields(plan: dict, graph: _Graph) -> None:
    node_id = plan["graph_node_id"]
    if not node_id or node_id not in graph.nodes:
        return
    data = graph.nodes[node_id]
    plan["graph_label"] = str(data.get("label") or "")
    plan["graph_type"] = str(data.get("type") or "")
    parents = graph.parent_labels(node_id)
    plan["matched_parent"] = parents[0] if len(parents) == 1 else ""


def _reject(graph: _Graph, node_ids: list[str], reason: str) -> list[dict]:
    rejected = []
    for node_id in node_ids:
        data = graph.nodes[node_id]
        rejected.append(
            {
                "id": node_id,
                "label": str(data.get("label") or ""),
                "type": str(data.get("type") or ""),
                "device_label": str(data.get("device_label") or ""),
                "parent_labels": graph.parent_labels(node_id),
                "value": str(data.get("value") or ""),
                "unit": str(data.get("unit") or ""),
                "applicability": str(data.get("applicability") or ""),
                "reason": reason,
            }
        )
    return rejected


def _classify_entities(graph: _Graph, entities: list[dict], relations: list[dict]) -> dict[str, dict]:
    states = {}
    for entity in entities:
        entity_id = str(entity.get("entity_id") or "")
        entity_type = str(entity.get("entity_type") or "")
        label = str(entity.get("label") or "")
        owners = _owners(entity_id, relations)
        scope = _attributes(entity)["scope"]
        if scope and scope not in owners:
            owners = [scope, *owners] if entity_type in {"component", "parameter", "fault"} else owners
        if entity_type == "parameter":
            states[entity_id] = _classify_parameter(graph, entity, owners)
            continue
        if not entity.get("graph_type"):
            plan = _entity_plan(entity, status="review", reason="正式图谱没有这个实体类型，不能新建同类型节点。")
            states[entity_id] = {"kind": "review", "plan": plan}
            continue
        if entity.get("certainty") == "uncertain":
            plan = _entity_plan(entity, status="review", reason="实体来自不确定表述，不能当作已确认节点。")
            states[entity_id] = {"kind": "review", "plan": plan}
            continue
        hits = graph.ids_for(entity_type, label)
        if entity_type == "component" and owners:
            narrowed = [
                node_id
                for node_id in hits
                if any(_norm(parent) == _norm(owner) for owner in owners for parent in graph.parent_labels(node_id))
            ]
            if len(narrowed) == 1:
                plan = _entity_plan(
                    entity,
                    status="matched",
                    reason="类型、名称和直接上级都一致，只匹配这一处已有部件。",
                    graph_node_id=narrowed[0],
                    rejected=_reject(graph, [node_id for node_id in hits if node_id != narrowed[0]], "同名部件的直接上级不同，没有合并。"),
                )
                _fill_graph_fields(plan, graph)
                states[entity_id] = {"kind": "matched", "plan": plan, "node_id": narrowed[0]}
                continue
            if len(narrowed) > 1:
                plan = _entity_plan(
                    entity,
                    status="blocked",
                    reason="同一上级下仍有多个同名部件，不能合并成一个节点。",
                    rejected=_reject(graph, narrowed, "同名且上级相同，仍不能唯一确定。"),
                )
                states[entity_id] = {"kind": "blocked", "plan": plan}
                continue
            if hits:
                plan = _entity_plan(
                    entity,
                    status="blocked",
                    reason="已有同名部件属于其他上级，不能合并，也不能自动新建一个忽略差异的节点。",
                    rejected=_reject(graph, hits, "名称相同，但所属上级不同。"),
                )
                states[entity_id] = {"kind": "blocked", "plan": plan}
                continue
        if len(hits) == 1 and (not owners or entity_type == "device"):
            plan = _entity_plan(
                entity,
                status="matched",
                reason="类型和名称在正式图谱中唯一。",
                graph_node_id=hits[0],
            )
            _fill_graph_fields(plan, graph)
            states[entity_id] = {"kind": "matched", "plan": plan, "node_id": hits[0]}
            continue
        if len(hits) > 1:
            plan = _entity_plan(
                entity,
                status="blocked",
                reason="同名节点不止一个，候选没有足够的设备或部件范围，不能合并。",
                rejected=_reject(graph, hits, "只靠名称不能区分。"),
            )
            states[entity_id] = {"kind": "blocked", "plan": plan}
            continue
        if _only_unmapped(entity_id, relations):
            plan = _entity_plan(entity, status="review", reason="该名称只出现在尚未映射的关系中，先不计划新建正式节点。")
            states[entity_id] = {"kind": "review", "plan": plan}
            continue
        plan = _entity_plan(entity, status="planned_new", reason="正式图谱中没有相同类型和名称的节点。")
        states[entity_id] = {"kind": "new", "plan": plan}
    return states


def _only_unmapped(entity_id: str, relations: list[dict]) -> bool:
    related = [item for item in relations if entity_id in {item.get("source"), item.get("target")}]
    return bool(related) and all(not item.get("mapped_to_graph") for item in related)


def _classify_parameter(graph: _Graph, entity: dict, owners: list[str]) -> dict:
    attrs = _attributes(entity)
    label = str(entity.get("label") or "")
    same_name = graph.ids_for("parameter", label)
    if len(owners) != 1:
        plan = _entity_plan(entity, status="blocked", reason="参数没有唯一所属设备或部件，不能绑定到已有限值。")
        return {"kind": "blocked", "plan": plan}
    owner = owners[0]
    same_device = [node_id for node_id in same_name if _norm(graph.nodes[node_id].get("device_label")) == _norm(owner)]
    others = [node_id for node_id in same_name if node_id not in same_device]
    if len(same_device) > 1:
        plan = _entity_plan(
            entity,
            status="blocked",
            reason="同一设备下有多个同名参数，不能自动选择。",
            rejected=_reject(graph, same_device, "同设备同名参数不唯一。"),
        )
        return {"kind": "blocked", "plan": plan}
    if len(same_device) == 1:
        node_id = same_device[0]
        data = graph.nodes[node_id]
        graph_value = str(data.get("value") or "")
        graph_unit = str(data.get("unit") or "")
        same_value = _norm(graph_value) == _norm(attrs["value"]) and (not graph_unit or not attrs["unit"] or _norm(graph_unit) == _norm(attrs["unit"]))
        rejected = _reject(graph, others, "其他设备的同名参数没有套用。")
        applicability = str(data.get("applicability") or "").strip()
        if not same_value:
            plan = _entity_plan(
                entity,
                status="conflict",
                reason=(
                    f"设备「{owner}」已有参数「{label}」，正式数值是「{graph_value}」，"
                    f"候选数值是「{attrs['value']}{attrs['unit']}」，不能互相覆盖。"
                    f"适用范围：{applicability or '正式节点未记录适用范围'}。"
                ),
                rejected=rejected,
            )
            return {"kind": "conflict", "plan": plan}
        if applicability:
            plan = _entity_plan(
                entity,
                status="review",
                reason=f"数值相同，但正式参数适用范围是「{applicability}」，候选没有给出适用范围，不能直接绑定。",
                graph_node_id=node_id,
                rejected=rejected,
            )
            _fill_graph_fields(plan, graph)
            return {"kind": "review", "plan": plan}
        plan = _entity_plan(entity, status="matched", reason="设备、参数名称和数值一致。", graph_node_id=node_id, rejected=rejected)
        _fill_graph_fields(plan, graph)
        return {"kind": "matched", "plan": plan, "node_id": node_id}
    rejected = _reject(graph, others, "所属设备不同，不能套用该限值。")
    if others:
        plan = _entity_plan(
            entity,
            status="conflict",
            reason=f"参数「{label}」属于「{owner}」，不能套用到其他设备的同名参数。候选也没有适用范围，不能自动新增绑定。",
            rejected=rejected,
        )
        return {"kind": "conflict", "plan": plan}
    plan = _entity_plan(entity, status="review", reason="候选参数没有适用范围，不能自动绑定到设备。")
    return {"kind": "review", "plan": plan}


def _relation_plan(relation: dict, *, status: str, reason: str, graph_edge_id: str = "") -> dict:
    evidence = [_public_evidence(item) for item in relation.get("evidence") or [] if isinstance(item, dict)]
    return {
        "item_type": "relation",
        "candidate_relation_id": relation.get("relation_id"),
        "relation_type": relation.get("relation_type"),
        "graph_relation": relation.get("graph_relation"),
        "source_label": relation.get("source_label"),
        "target_label": relation.get("target_label"),
        "source_entity_type": relation.get("source_entity_type"),
        "target_entity_type": relation.get("target_entity_type"),
        "direction": relation.get("direction") or "",
        "causal_status": relation.get("causal_status") or "",
        "certainty": relation.get("certainty"),
        "status": status,
        "reason": reason,
        "graph_edge_id": graph_edge_id,
        "import_allowed": status == "planned_new",
        "evidence": evidence,
        "in_graph": False,
    }


def _classify_relations(graph: _Graph, relations: list[dict], states: dict[str, dict]) -> dict[str, list[dict]]:
    buckets = {"new": [], "duplicate": [], "review": [], "blocked": []}
    for relation in relations:
        evidence = [item for item in relation.get("evidence") or [] if isinstance(item, dict)]
        if not evidence or any(not _evidence_ok(item) for item in evidence):
            buckets["blocked"].append(_relation_plan(relation, status="blocked", reason="关系缺少文件、段落、位置或原文证据，不能入图。"))
            continue
        if relation.get("relation_type") in _CAUSE_RELATIONS or relation.get("direction") == "cause_to_effect":
            if relation.get("direction") != "cause_to_effect" or relation.get("source_entity_type") != "cause":
                buckets["blocked"].append(_relation_plan(relation, status="blocked", reason="因果方向不是原因指向结果，不能入图。"))
                continue
            reason = "方向是原因指向结果，但仍是未验证候选，不能写成正式因果链。"
            if relation.get("certainty") == "uncertain":
                reason = "原文含有不确定表述，方向是原因指向结果，不能升级为确定因果关系。"
            plan = _relation_plan(relation, status="review", reason=reason)
            buckets["review"].append(plan)
            buckets["blocked"].append(_relation_plan(relation, status="blocked", reason=reason))
            continue
        if relation.get("certainty") != "explicit" or not relation.get("mapped_to_graph") or relation.get("graph_relation") not in _MAPPED_RELATIONS:
            buckets["review"].append(_relation_plan(relation, status="review", reason="这条关系不能映射到正式图谱的关系类型，需要人工审核。"))
            continue
        source = states.get(str(relation.get("source")))
        target = states.get(str(relation.get("target")))
        if source is None or target is None:
            buckets["blocked"].append(_relation_plan(relation, status="blocked", reason="关系端点不在候选实体中，不能入图。"))
            continue
        if source["kind"] not in {"matched", "new"} or target["kind"] not in {"matched", "new"}:
            buckets["blocked"].append(_relation_plan(relation, status="blocked", reason="关系端点还不能唯一对应图谱节点，不能新增关系。"))
            continue
        official = str(relation.get("graph_relation"))
        if source["kind"] == "matched" and target["kind"] == "matched":
            edge_id = graph.edges.get((source["node_id"], target["node_id"], official), "")
            if edge_id:
                buckets["duplicate"].append(_relation_plan(relation, status="duplicate", reason="正式图谱已经有这条关系，不重复新增。", graph_edge_id=edge_id))
                continue
        buckets["new"].append(_relation_plan(relation, status="planned_new", reason="两端已经明确，正式图谱中还没有这条关系。"))
    return buckets


def _build(record: dict, candidates: dict, graph_payload: dict, graph_sha: str) -> dict:
    graph = _Graph(graph_payload)
    entities = [item for item in candidates.get("entities") or [] if isinstance(item, dict)]
    relations = [item for item in candidates.get("relations") or [] if isinstance(item, dict)]
    states = _classify_entities(graph, entities, relations)
    relation_buckets = _classify_relations(graph, relations, states)
    matches = []
    new_nodes = []
    conflicts = []
    reviews = []
    blocked = []
    for entity in entities:
        state = states[str(entity.get("entity_id"))]
        plan = state["plan"]
        kind = state["kind"]
        if kind == "matched":
            matches.append(plan)
        elif kind == "new":
            new_nodes.append(plan)
        elif kind == "conflict":
            conflicts.append(plan)
            blocked.append(dict(plan, status="blocked", import_allowed=False, reason=plan["reason"]))
        elif kind == "blocked":
            blocked.append(plan)
            conflicts.append(plan)
        else:
            reviews.append(plan)
    document = {
        "schema_version": SCHEMA_VERSION,
        "file_id": record["file_id"],
        "status": "previewed",
        "created_at": _now(),
        "source_sha256": record["sha256"],
        "graph_fingerprint": {
            "node_count": len(graph_payload["nodes"]),
            "relation_count": len(graph_payload["edges"]),
            "sha256": graph_sha,
        },
        "existing_node_matches": sorted(matches, key=lambda item: (item["entity_type"], item["label"], item["scope"], item["graph_node_id"])),
        "new_nodes": sorted(new_nodes, key=lambda item: (item["entity_type"], item["label"], item["scope"])),
        "new_relations": sorted(relation_buckets["new"], key=lambda item: item["candidate_relation_id"]),
        "duplicate_relations": sorted(relation_buckets["duplicate"], key=lambda item: item["candidate_relation_id"]),
        "conflicts": sorted(conflicts, key=lambda item: (item.get("label") or item.get("source_label") or "", item.get("scope", ""), item.get("candidate_entity_id") or "")),
        "review_required": sorted(reviews + relation_buckets["review"], key=lambda item: item.get("candidate_entity_id") or item.get("candidate_relation_id") or ""),
        "blocked_items": sorted(blocked + relation_buckets["blocked"], key=lambda item: item.get("candidate_entity_id") or item.get("candidate_relation_id") or ""),
        "summary": {},
        "in_graph": False,
        "notice": NOTICE,
    }
    document["summary"] = {
        "matched_nodes": len(document["existing_node_matches"]),
        "planned_new_nodes": len(document["new_nodes"]),
        "planned_new_relations": len(document["new_relations"]),
        "duplicate_relations": len(document["duplicate_relations"]),
        "conflicts": len(document["conflicts"]),
        "review_required": len(document["review_required"]),
        "blocked_items": len(document["blocked_items"]),
    }
    return document


def _candidates_for(record: dict) -> dict:
    if record["processing_status"] != CLEANED_STATUS:
        raise KnowledgeFileError("只有已清洗的文件才能做入图预演。", 409)
    path = _extracted_path(record["file_id"])
    if path is None:
        raise KnowledgeFileError("还没有候选知识，不能生成入图预演。", 409)
    document = _read_json(path)
    if document.get("file_id") != record["file_id"]:
        raise PreviewFailure("候选知识不属于这个文件，没有生成入图预演。")
    if document.get("extraction_status") != "extracted":
        raise KnowledgeFileError("候选知识没有抽取成功，不能生成入图预演。", 409)
    if document.get("source_sha256") != record["sha256"]:
        raise PreviewFailure("候选知识和上传文件的哈希不一致，没有覆盖已有预演。")
    if not isinstance(document.get("entities"), list) or not isinstance(document.get("relations"), list):
        raise PreviewFailure("候选知识缺少实体或关系，没有生成入图预演。")
    return document


def _empty(record: dict, message: str) -> dict:
    return {
        "file_id": record["file_id"],
        "status": "not_previewed",
        "error_message": message,
        "preview": None,
        "in_graph": False,
        "notice": NOTICE,
    }


def import_preview(file_id: str) -> dict:
    record = _record(file_id)
    path = _preview_path(file_id)
    if path is None:
        message = "还没有入图预演。"
        if record["processing_status"] != CLEANED_STATUS:
            message = "只有已清洗的文件才能做入图预演。"
        return _empty(record, message)
    try:
        document = _read_json(path)
    except PreviewFailure as exc:
        raise KnowledgeFileError(exc.message, 500) from exc
    if document.get("status") != "previewed":
        return _empty(record, str(document.get("error_message") or "入图预演没有成功。"))
    return document


def build_current_preview(record: dict) -> dict:
    """按当前清洗结果和正式图谱重新生成预演。不写文件。"""
    candidates = _candidates_for(record)
    graph_payload, graph_sha = _load_graph()
    document = _build(record, candidates, graph_payload, graph_sha)
    extracted = _extracted_path(record["file_id"])
    document["candidate_sha256"] = hashlib.sha256(extracted.read_bytes()).hexdigest() if extracted else ""
    return document


def preview_knowledge_import(file_id: str) -> dict:
    record = _record(file_id)
    if record["processing_status"] != CLEANED_STATUS:
        raise KnowledgeFileError("只有已清洗的文件才能做入图预演。", 409)
    if _extracted_path(file_id) is None:
        raise KnowledgeFileError("还没有候选知识，不能生成入图预演。", 409)
    if not _acquire(file_id):
        current = import_preview(file_id)
        if current.get("status") != "previewed":
            current["error_message"] = "正在生成入图预演，还没有结果。"
        return current
    try:
        previous = None
        path = _preview_path(file_id)
        if path is not None:
            try:
                loaded = _read_json(path)
            except PreviewFailure:
                loaded = None
            if isinstance(loaded, dict) and loaded.get("status") == "previewed":
                previous = loaded
        try:
            candidates = _candidates_for(record)
            graph_payload, graph_sha = _load_graph()
            document = _build(record, candidates, graph_payload, graph_sha)
            extracted = _extracted_path(record["file_id"])
            document["candidate_sha256"] = hashlib.sha256(extracted.read_bytes()).hexdigest() if extracted else ""
        except PreviewFailure as exc:
            if previous is not None:
                raise KnowledgeFileError(f"重新预演没有成功，已保留上一次结果。{exc.message}", 422) from exc
            raise KnowledgeFileError(exc.message, 422) from exc
        _write(file_id, document)
        return document
    finally:
        _release(file_id)
