"""在已有图谱和已保存候选中查找交叉证据。

不调用模型，不新增关系，也不把普通相邻关系写成因果链。
"""

from __future__ import annotations

import json
from collections import defaultdict, deque

from app.services.knowledge_document_evidence import collect_document_evidence
from app.services.knowledge_file_store import KnowledgeFileError, extracted_dir
from app.services.knowledge_graph import load_graph
from app.services.knowledge_graph_import import merge_imported_graph

BUSINESS_RELATIONS = frozenset(
    {
        "HAS_COMPONENT",
        "HAS_FAULT",
        "HAS_FEATURE",
        "HAS_PARAMETER",
        "HAS_ACTION",
        "CONNECTS_TO",
        "OCCURS_ON",
    }
)
CITATION_RELATIONS = frozenset({"SUPPORTED_BY"})
NAVIGATION_RELATIONS = frozenset({"UI_GROUP"})
MAX_HOPS = 3
MAX_PATHS = 20
MAX_STATES = 4000


class EntityIdentityError(Exception):
    """节点编号、名称或归属对不上。"""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def find_cross_knowledge_evidence(
    entity_a: str,
    entity_b: str,
    max_hops: int = 3,
    *,
    graph: dict | None = None,
    entity_a_id: str | None = None,
    entity_b_id: str | None = None,
    entity_a_scope: str | None = None,
    entity_b_scope: str | None = None,
) -> dict:
    """查找两个名称在现有知识中的匹配、相邻关系和有限跳路径。

    提供 node_id 时只使用该节点。只给同名名称时保留全部匹配，不自动选第一个。
    """
    left_name = entity_a.strip()
    right_name = entity_b.strip()
    hops = max(1, min(int(max_hops), MAX_HOPS))
    payload = graph if graph is not None else merge_imported_graph(load_graph())
    nodes = [node for node in payload.get("nodes") or [] if isinstance(node, dict) and isinstance(node.get("data"), dict)]
    edges = [edge for edge in payload.get("edges") or [] if isinstance(edge, dict) and isinstance(edge.get("data"), dict)]
    by_id = {str(node["id"]): node for node in nodes if node.get("id")}
    left_named = _matches(left_name, nodes, edges, by_id)
    right_named = _matches(right_name, nodes, edges, by_id)
    left_selected = _resolve_match(left_name, entity_a_id, entity_a_scope, left_named, edges, by_id)
    right_selected = _resolve_match(right_name, entity_b_id, entity_b_scope, right_named, edges, by_id)
    left_matches = [left_selected] if left_selected else left_named
    right_matches = [right_selected] if right_selected else right_named
    names = {left_name, right_name}
    candidates = _unverified_candidates(names)
    gaps: list[str] = []
    left_state = "selected" if left_selected else _match_state(left_name, left_named, gaps)
    right_state = "selected" if right_selected else _match_state(right_name, right_named, gaps)
    gaps.extend(_selection_notes(left_name, left_selected, left_named))
    gaps.extend(_selection_notes(right_name, right_selected, right_named))
    shared: list[dict] = []
    paths: list[dict] = []
    truncated = False
    excluded = _excluded_notes(left_matches, right_matches, edges)
    gaps.extend(excluded)
    if _resolved(left_state) and _resolved(right_state) and left_matches[0]["node_id"] != right_matches[0]["node_id"]:
        shared = _shared_neighbors(left_matches[0], right_matches[0], edges, by_id)
        paths, truncated = _paths(left_matches[0]["node_id"], right_matches[0]["node_id"], edges, by_id, hops)
        if not paths:
            gaps.append("没有找到现有知识支持的关联路径。")
    elif _resolved(left_state) and _resolved(right_state) and left_matches[0]["node_id"] == right_matches[0]["node_id"]:
        gaps.append("两个名称对应同一个已有节点，没有另外搜索因果链。")
    evidence = _supporting_evidence(left_matches, right_matches, paths)
    for item in evidence:
        if not item["location_complete"]:
            gaps.append("有关系缺少文档、章节或位置，没有编造定位。")
            break
    if any(not item["quote_available"] for item in evidence):
        gaps.append("已有图谱关系没有保存可逐字核对的原文片段。")
    if _missing_measurement(left_matches, right_matches, by_id):
        gaps.append("这两个节点没有保存测量指标或物理约束，不能补充物理结论。")
    if candidates:
        gaps.append("存在未验证候选关系，只放在待验证假设中，没有并入事实路径。")
    try:
        documents = collect_document_evidence(
            left_name,
            right_name,
            left_in_graph=bool(left_matches),
            right_in_graph=bool(right_matches),
        )
    except (OSError, KnowledgeFileError):
        documents = {
            "warnings": ["文档检索没有完成，没有把失败写成证据。"],
            "document_evidence": _empty_documents(),
            "cross_document_evidence": [],
            "cross_document_truncated": False,
        }
    gaps.extend(documents.get("warnings") or [])
    document_evidence = documents["document_evidence"]
    cross_evidence = documents["cross_document_evidence"]
    document_found = bool(document_evidence["entity_a"] or document_evidence["entity_b"] or cross_evidence)
    if not paths and document_found:
        gaps.append("未找到已入图的关联路径，但检索到可供进一步研究的文档证据。")
    elif not paths and not document_found:
        gaps.append("文档中也没有检索到可用证据。")
    if any(not item["direct_mention"] for item in document_evidence["entity_a"] + document_evidence["entity_b"]):
        gaps.append("有文档只因关键词相关被检索到，没有直接出现查询名称，不能当成已经建立的图谱关系。")
    for name, matches, items in ((left_name, left_matches, document_evidence["entity_a"]), (right_name, right_matches, document_evidence["entity_b"])):
        if not matches and items:
            gaps.append(f"「{name}」没有图谱节点。检索到的原文只作为文档证据，不是已经建立的图谱关系。")
    summary = _summary(left_name, right_name, left_state, right_state, shared, paths, candidates, truncated, document_found)
    return {
        "entity_a": left_name,
        "entity_b": right_name,
        "max_hops": hops,
        "path_limit": MAX_PATHS,
        "paths_truncated": truncated,
        "entity_a_matches": left_matches,
        "entity_b_matches": right_matches,
        "entity_a_identity": _identity_view(left_selected or (left_named[0] if len(left_named) == 1 else None)),
        "entity_b_identity": _identity_view(right_selected or (right_named[0] if len(right_named) == 1 else None)),
        "entity_a_other_matches": _other_matches(left_named, left_selected),
        "entity_b_other_matches": _other_matches(right_named, right_selected),
        "shared_neighbors": shared,
        "connection_paths": paths,
        "supporting_evidence": evidence,
        "unverified_candidates": candidates,
        "document_evidence": document_evidence,
        "cross_document_evidence": cross_evidence,
        "evidence_gaps": _unique(gaps),
        "summary": summary,
        "causal_conclusion": False,
    }


def _resolved(state: str) -> bool:
    return state in {"unique", "selected"}


def _resolve_match(name: str, node_id: str | None, scope: str | None, name_matches: list[dict], edges: list[dict], by_id: dict[str, dict]) -> dict | None:
    identity = node_id.strip() if isinstance(node_id, str) else ""
    if not identity:
        return None
    node = by_id.get(identity)
    if node is None:
        raise EntityIdentityError(f"没有找到节点「{identity}」。")
    label = str(node["data"].get("label") or "")
    actual_scope, _parent = _scope(node, edges, by_id)
    if label != name:
        raise EntityIdentityError(f"节点「{identity}」的名称是「{label}」，与提交的「{name}」不一致。")
    submitted = scope.strip() if isinstance(scope, str) else ""
    if submitted and submitted != actual_scope:
        shown = actual_scope or "归属未标明"
        raise EntityIdentityError(f"节点「{identity}」的归属是「{shown}」，与提交的「{submitted}」不一致。")
    chosen = next((item for item in name_matches if item["node_id"] == identity), None)
    if chosen is None:
        raise EntityIdentityError(f"节点「{identity}」不能作为「{name}」使用。")
    return chosen


def _selection_notes(name: str, selected: dict | None, named: list[dict]) -> list[str]:
    if selected is None or len(named) <= 1:
        return []
    scope = selected.get("scope") or "归属未标明"
    return [
        f"「{name}」另有同名节点，本次只使用归属「{scope}」的节点「{selected['node_id']}」，没有合并。",
        f"文档检索仍按名称「{name}」进行，没有把同名节点的原文分配到本次选定的归属。",
    ]


def _identity_view(match: dict | None) -> dict | None:
    if match is None:
        return None
    return {
        "node_id": match["node_id"],
        "label": match["label"],
        "entity_type": match["entity_type"],
        "scope": match["scope"],
        "parent_id": match["parent_id"],
    }


def _other_matches(named: list[dict], selected: dict | None) -> list[dict]:
    if selected is None:
        return []
    return [_identity_view(item) for item in named if item["node_id"] != selected["node_id"]]


def _matches(name: str, nodes: list[dict], edges: list[dict], by_id: dict[str, dict]) -> list[dict]:
    found = []
    for node in nodes:
        if str(node["data"].get("label") or "") != name:
            continue
        node_id = str(node["id"])
        scope, parent_id = _scope(node, edges, by_id)
        found.append(
            {
                "node_id": node_id,
                "label": name,
                "entity_type": str(node["data"].get("type") or ""),
                "scope": scope,
                "parent_id": parent_id,
                "origin": "runtime_import" if node_id.startswith("import-") else "base_graph",
                "fact_class": "known_fact",
                "business_neighbors": _neighbors(node_id, edges, by_id),
                "source_refs": _evidence(node["data"].get("source_refs"), "runtime_import" if node_id.startswith("import-") else "base_graph"),
            }
        )
    return sorted(found, key=lambda item: (item["scope"], item["node_id"]))


def _scope(node: dict, edges: list[dict], by_id: dict[str, dict]) -> tuple[str, str]:
    parent_id = str(node.get("parentId") or "")
    if parent_id and parent_id in by_id:
        return str(by_id[parent_id]["data"].get("label") or ""), parent_id
    parents = []
    for edge in edges:
        if edge.get("target") == node.get("id") and edge["data"].get("relation") == "HAS_COMPONENT" and edge.get("source") in by_id:
            parents.append(by_id[str(edge["source"])])
    if len(parents) == 1:
        return str(parents[0]["data"].get("label") or ""), str(parents[0]["id"])
    return "", ""


def _neighbors(node_id: str, edges: list[dict], by_id: dict[str, dict]) -> list[dict]:
    rows = []
    for edge in edges:
        relation = str(edge["data"].get("relation") or "")
        if relation not in BUSINESS_RELATIONS:
            continue
        if edge.get("source") == node_id:
            other_id = str(edge.get("target"))
            traversal = "along_arrow"
        elif edge.get("target") == node_id:
            other_id = str(edge.get("source"))
            traversal = "against_arrow"
        else:
            continue
        other = by_id.get(other_id)
        if other is None:
            continue
        scope, _parent = _scope(other, edges, by_id)
        rows.append(
            {
                "node_id": other_id,
                "label": str(other["data"].get("label") or ""),
                "entity_type": str(other["data"].get("type") or ""),
                "scope": scope,
                "relation": relation,
                "direction": "source_to_target",
                "traversal": traversal,
                "edge_id": str(edge.get("id") or ""),
                "fact_class": "known_fact",
                "causal_conclusion": False,
            }
        )
    return rows


def _match_state(name: str, matches: list[dict], gaps: list[str]) -> str:
    if not matches:
        gaps.append(f"没有找到「{name}」。")
        return "missing"
    if len(matches) > 1:
        scopes = "、".join(item["scope"] or "归属未标明" for item in matches)
        gaps.append(f"「{name}」对应 {len(matches)} 个节点（{scopes}），没有擅自合并或选择。")
        return "ambiguous"
    return "unique"


def _shared_neighbors(left: dict, right: dict, edges: list[dict], by_id: dict[str, dict]) -> list[dict]:
    left_neighbors = {item["node_id"]: item for item in left["business_neighbors"]}
    shared = []
    for item in right["business_neighbors"]:
        other = left_neighbors.get(item["node_id"])
        if other is None or item["node_id"] in {left["node_id"], right["node_id"]}:
            continue
        shared.append(
            {
                "node_id": item["node_id"],
                "label": item["label"],
                "entity_type": item["entity_type"],
                "scope": item["scope"],
                "link_to_a": {"relation": other["relation"], "traversal": other["traversal"], "edge_id": other["edge_id"]},
                "link_to_b": {"relation": item["relation"], "traversal": item["traversal"], "edge_id": item["edge_id"]},
                "fact_class": "known_fact",
                "causal_conclusion": False,
                "note": "共同邻居只说明两边都有真实相邻关系，不能据此认定因果关系。",
            }
        )
    return shared


def _paths(start: str, goal: str, edges: list[dict], by_id: dict[str, dict], hops: int) -> tuple[list[dict], bool]:
    adjacency: dict[str, list[tuple[str, dict, str]]] = defaultdict(list)
    for edge in edges:
        relation = str(edge["data"].get("relation") or "")
        if relation not in BUSINESS_RELATIONS:
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source not in by_id or target not in by_id:
            continue
        adjacency[source].append((target, edge, "along_arrow"))
        adjacency[target].append((source, edge, "against_arrow"))
    found: list[dict] = []
    truncated = False
    queue: deque[tuple[str, list[dict], set[str]]] = deque([(start, [], {start})])
    states = 0
    while queue:
        current, steps, seen = queue.popleft()
        states += 1
        if states > MAX_STATES:
            truncated = True
            break
        if len(steps) >= hops:
            continue
        for neighbor, edge, traversal in adjacency.get(current, []):
            if neighbor in seen:
                continue
            step = _step(current, neighbor, edge, traversal)
            next_steps = [*steps, step]
            if neighbor == goal:
                found.append(
                    {
                        "hops": len(next_steps),
                        "fact_class": "known_fact",
                        "causal_conclusion": False,
                        "steps": next_steps,
                    }
                )
                if len(found) >= MAX_PATHS:
                    return found, True
                continue
            queue.append((neighbor, next_steps, seen | {neighbor}))
    return found, truncated


def _step(current: str, neighbor: str, edge: dict, traversal: str) -> dict:
    origin = "runtime_import" if str(edge.get("id") or "").startswith("import-") else "base_graph"
    evidence = _evidence(edge["data"].get("source_refs"), origin)
    return {
        "edge_id": str(edge.get("id") or ""),
        "source_id": str(edge.get("source") or ""),
        "target_id": str(edge.get("target") or ""),
        "from_id": current,
        "to_id": neighbor,
        "relation": str(edge["data"].get("relation") or ""),
        "direction": "source_to_target",
        "traversal": traversal,
        "fact_class": "known_fact",
        "origin": origin,
        "causal_conclusion": False,
        "evidence": evidence,
    }


def _evidence(value: object, origin: str) -> list[dict]:
    if not isinstance(value, list):
        return []
    rows = []
    for item in value:
        if not isinstance(item, dict):
            continue
        document = item.get("document") if isinstance(item.get("document"), str) else ""
        section = item.get("section") if isinstance(item.get("section"), str) else ""
        locator = item.get("locator") if isinstance(item.get("locator"), str) else ""
        quote = item.get("quote") if isinstance(item.get("quote"), str) else ""
        file_id = item.get("file_id") if isinstance(item.get("file_id"), str) else ""
        rows.append(
            {
                "document": document,
                "file_id": file_id,
                "section_id": section if file_id else "",
                "section": section,
                "locator": locator,
                "quote": quote,
                "source_type": item.get("source_type") if isinstance(item.get("source_type"), str) and item.get("source_type") else ("user_upload" if file_id else "existing_knowledge"),
                "origin": origin,
                "location_complete": bool(document and section and locator),
                "quote_available": bool(quote),
                "fact_class": "known_fact",
            }
        )
    return rows


def _supporting_evidence(left: list[dict], right: list[dict], paths: list[dict]) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()

    def add(items: list[dict]) -> None:
        for item in items:
            marker = (item.get("file_id", ""), item.get("document", ""), item.get("section", ""), item.get("locator", ""), item.get("quote", ""))
            if marker in seen:
                continue
            seen.add(marker)
            rows.append(item)

    if len(left) == 1:
        add(left[0]["source_refs"])
    if len(right) == 1:
        add(right[0]["source_refs"])
    for path in paths:
        for step in path["steps"]:
            add(step["evidence"])
    return rows


def _excluded_notes(left: list[dict], right: list[dict], edges: list[dict]) -> list[str]:
    ids = {item["node_id"] for item in left + right}
    if len(left) == 1 and len(right) == 1:
        ids = {left[0]["node_id"], right[0]["node_id"]}
    notes = []
    kinds = set()
    for edge in edges:
        relation = str(edge["data"].get("relation") or "")
        if relation not in CITATION_RELATIONS and relation not in NAVIGATION_RELATIONS:
            continue
        if edge.get("source") in ids or edge.get("target") in ids:
            kinds.add(relation)
    if "SUPPORTED_BY" in kinds:
        notes.append("存在 SUPPORTED_BY 来源引用，没有把它当作工业关联或因果证据。")
    if "UI_GROUP" in kinds:
        notes.append("存在 UI_GROUP 界面导航，没有把它当作工业关联或因果证据。")
    return notes


def _missing_measurement(left: list[dict], right: list[dict], by_id: dict[str, dict]) -> bool:
    if len(left) != 1 or len(right) != 1:
        return False
    for match in (left[0], right[0]):
        data = by_id[match["node_id"]]["data"]
        if data.get("value") or data.get("unit") or data.get("applicability"):
            return False
    return True


def _unverified_candidates(names: set[str]) -> list[dict]:
    directory = extracted_dir()
    if not directory.is_dir():
        return []
    found = []
    for path in sorted(directory.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(document, dict) or document.get("extraction_status") != "extracted":
            continue
        for relation in document.get("relations") or []:
            if not isinstance(relation, dict):
                continue
            if relation.get("relation_type") != "CAUSE_CANDIDATE" and relation.get("causal_status") != "unverified":
                continue
            source_label = str(relation.get("source_label") or "")
            target_label = str(relation.get("target_label") or "")
            if source_label not in names and target_label not in names:
                continue
            evidence = _candidate_evidence(relation.get("evidence"), str(document.get("file_id") or ""))
            found.append(
                {
                    "source_label": source_label,
                    "target_label": target_label,
                    "relation_type": "CAUSE_CANDIDATE",
                    "direction": relation.get("direction") if isinstance(relation.get("direction"), str) else "",
                    "certainty": str(relation.get("certainty") or ""),
                    "causal_status": "unverified",
                    "fact_class": "unverified_candidate",
                    "causal_conclusion": False,
                    "in_graph": False,
                    "evidence": evidence,
                    "note": "这是未验证候选，不能当作已确认因果关系。",
                }
            )
    return found


def _candidate_evidence(value: object, file_id: str) -> list[dict]:
    if not isinstance(value, list):
        return []
    rows = []
    for item in value:
        if not isinstance(item, dict):
            continue
        locator = item.get("locator")
        if isinstance(locator, dict) and locator.get("type") == "line" and isinstance(locator.get("index"), int):
            locator_text = f"行 {locator['index']}"
        elif isinstance(locator, str):
            locator_text = locator
        else:
            locator_text = ""
        section_id = item.get("section_id") if isinstance(item.get("section_id"), str) else ""
        quote = item.get("quote") if isinstance(item.get("quote"), str) else ""
        rows.append(
            {
                "document": item.get("original_filename") if isinstance(item.get("original_filename"), str) else "",
                "file_id": item.get("file_id") if isinstance(item.get("file_id"), str) else file_id,
                "section_id": section_id,
                "section": section_id,
                "locator": locator_text,
                "quote": quote,
                "source_type": "user_upload",
                "origin": "candidate",
                "location_complete": bool(section_id and locator_text),
                "quote_available": bool(quote),
                "fact_class": "unverified_candidate",
            }
        )
    return rows


def _empty_documents() -> dict:
    return {
        "entity_a": [],
        "entity_b": [],
        "limit_per_entity": 5,
        "snippet_limit": 400,
        "truncated": {"entity_a": False, "entity_b": False},
        "external_model_allowed": False,
        "release_scope": "local_only",
    }


def _summary(
    left_name: str,
    right_name: str,
    left_state: str,
    right_state: str,
    shared: list[dict],
    paths: list[dict],
    candidates: list[dict],
    truncated: bool,
    document_found: bool,
) -> dict:
    if paths:
        text = f"找到 {len(paths)} 条现有知识关联路径。这些路径只表示已保存的关系，不是因果链。"
    else:
        text = "没有找到现有知识支持的关联路径。"
        if not _resolved(left_state) or not _resolved(right_state):
            text += "实体没有唯一对应到现有节点，没有生成关联路径。"
        if document_found:
            text += "未找到已入图的关联路径，但检索到可供进一步研究的文档证据。"
        else:
            text += "文档中也没有检索到可用证据。"
    if shared:
        text += "共同邻居不能认定二者有因果关系。"
    if candidates:
        text += "未验证候选单独列出。"
    if truncated:
        text += f"路径数量达到上限 {MAX_PATHS}，没有继续展开。"
    return {
        "text": text,
        "entity_a_status": left_state,
        "entity_b_status": right_state,
        "found_path": bool(paths),
        "shared_neighbor_count": len(shared),
        "unverified_candidate_count": len(candidates),
        "document_evidence_found": document_found,
        "causal_conclusion": False,
        "query": [left_name, right_name],
    }


def _unique(items: list[str]) -> list[str]:
    result = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result
