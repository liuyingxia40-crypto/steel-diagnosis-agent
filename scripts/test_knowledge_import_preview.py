"""验收候选知识的入图预演。使用临时目录，不写入正式图谱。"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

TEMP = Path(tempfile.mkdtemp(prefix="knowledge-preview-"))
os.environ["KNOWLEDGE_FILES_DB"] = str(TEMP / "files.sqlite3")
os.environ["KNOWLEDGE_UPLOAD_DIR"] = str(TEMP / "uploads")
os.environ["KNOWLEDGE_CLEANED_DIR"] = str(TEMP / "cleaned")
os.environ["KNOWLEDGE_EXTRACTED_DIR"] = str(TEMP / "extracted")
os.environ["KNOWLEDGE_IMPORT_PREVIEW_DIR"] = str(TEMP / "previews")
os.environ["KNOWLEDGE_GRAPH_IMPORTS_DB"] = str(TEMP / "imports.sqlite3")
os.environ["STARTUP_TOOL_SELFTEST"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.knowledge_file_store import save_upload  # noqa: E402

CHECKS: list[bool] = []
EXCEL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
GRAPH = ROOT / "data" / "processed" / "graph" / "graph.json"
SQLITE = ROOT / "data" / "runtime" / "monitoring_events.sqlite3"
EXPECTED_XLSX = "1e9caeba1fe526f3ad1b5dd6445a9dbcc02fa22ec02273d9863ebc84e3e30f87"
DEFAULTS = (
    ROOT / "data" / "runtime" / "knowledge_files.sqlite3",
    ROOT / "data" / "runtime" / "knowledge_uploads",
    ROOT / "data" / "runtime" / "knowledge_cleaned",
    ROOT / "data" / "runtime" / "knowledge_extracted",
    ROOT / "data" / "runtime" / "knowledge_import_previews",
    ROOT / "data" / "runtime" / "knowledge_graph_imports.sqlite3",
)
SAMPLE = "\n".join(
    [
        "棒线主轧机电机包含定子",
        "部件驱动端轴承包含外圈",
        "部件非驱动端轴承包含外圈",
        "棒线主轧机电机包含外圈",
        "某设备包含试验密封圈",
        "设备棒线主轧机电机的额定电流为120A",
        "设备棒线冷却水循环泵的轴承温度限值为90°C",
        "设备主电机的额定电流为120A",
        "润滑不足导致轴承温升",
        "轴承温升由润滑不足引起",
        "轴承温升可能由润滑不足引起",
        "齿轮裂纹与轴承磨损同时出现",
    ]
)


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append(bool(ok))
    print(("PASS" if ok else "FAIL"), name, detail)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def upload(client: TestClient, filename: str, content: bytes):
    return client.post(
        "/api/knowledge/files",
        params={"filename": filename},
        content=content,
        headers={"Content-Type": "application/octet-stream"},
    )


def without_time(document: dict) -> dict:
    copied = dict(document)
    copied.pop("created_at", None)
    return copied


def main() -> None:
    before = {
        "excel": file_hash(EXCEL),
        "graph": file_hash(GRAPH),
        "sqlite": file_hash(SQLITE) if SQLITE.is_file() else None,
        "defaults": [path.exists() for path in DEFAULTS],
    }
    source = (BACKEND / "app" / "services" / "knowledge_graph_import_preview.py").read_text(encoding="utf-8")
    modules = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(modules):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.update(node.module.split("."))
    banned = imported & {"deepseek", "requests", "httpx", "monitoring_events", "anomaly_detection", "diagnostic_bridge", "agent_tools"}
    check("预演模块没有调用 DeepSeek 或监测库", not banned and "sk-" not in source, str(sorted(banned)))

    client = TestClient(app)
    uploaded = upload(client, "入图预演样本.txt", SAMPLE.encode())
    saved = uploaded.json()
    extracted = client.post(f"/api/knowledge/files/{saved['file_id']}/extract")
    preview = client.post(f"/api/knowledge/files/{saved['file_id']}/preview-import")
    body = preview.json()
    cleaned = json.loads((TEMP / "cleaned" / f"{saved['file_id']}.json").read_text(encoding="utf-8"))
    matches = body.get("existing_node_matches") or []
    matched = {(item["entity_type"], item["label"], item["scope"]): item for item in matches}
    check(
        "已有设备和唯一部件能够匹配",
        extracted.status_code == 200
        and preview.status_code == 200
        and ("device", "棒线主轧机电机", "") in matched
        and ("component", "定子", "棒线主轧机电机") in matched
        and matched[("component", "定子", "棒线主轧机电机")]["matched_parent"] == "棒线主轧机电机",
        preview.text[:500],
    )
    new_labels = {(item["entity_type"], item["label"]) for item in body.get("new_nodes") or []}
    check("图谱中没有的实体生成新增计划", ("device", "某设备") in new_labels and ("component", "试验密封圈") in new_labels)
    outer = [item for item in matches if item["label"] == "外圈"]
    ambiguous = [item for item in body.get("blocked_items") or [] if item.get("label") == "外圈" and item.get("scope") == "棒线主轧机电机"]
    check(
        "不同上级的同名外圈不会合并",
        len(outer) == 2
        and len({item["graph_node_id"] for item in outer}) == 2
        and {item["matched_parent"] for item in outer} == {"驱动端轴承", "非驱动端轴承"}
        and len(ambiguous) == 1
        and not ambiguous[0]["graph_node_id"]
        and "不能合并" in ambiguous[0]["reason"],
    )
    conflicts = body.get("conflicts") or []
    motor_current = [item for item in conflicts if item.get("label") == "额定电流" and item.get("scope") == "棒线主轧机电机"]
    pump_limit = [item for item in conflicts if item.get("label") == "轴承温度限值" and item.get("scope") == "棒线冷却水循环泵"]
    other_motor = [item for item in conflicts if item.get("label") == "额定电流" and item.get("scope") == "主电机"]
    parameter_matches = [item for item in matches if item["entity_type"] == "parameter"]
    check(
        "不同设备的电流和温度限值不会互相套用",
        len(motor_current) == 1
        and "120A" in motor_current[0]["reason"]
        and "388A" in motor_current[0]["reason"]
        and len(pump_limit) == 1
        and "90°C" in pump_limit[0]["reason"]
        and any(item.get("device_label") == "棒线主轧机电机" for item in pump_limit[0]["rejected_same_name_nodes"])
        and len(other_motor) == 1
        and "不能套用" in other_motor[0]["reason"]
        and any(item.get("device_label") == "棒线主轧机电机" for item in other_motor[0]["rejected_same_name_nodes"])
        and not parameter_matches,
        json.dumps([item.get("reason") for item in conflicts], ensure_ascii=False)[:500],
    )
    duplicate = {(item["source_label"], item["target_label"], item["graph_relation"]) for item in body.get("duplicate_relations") or []}
    planned = {(item["source_label"], item["target_label"], item["graph_relation"]) for item in body.get("new_relations") or []}
    check(
        "已经存在的包含关系不重复新增",
        ("棒线主轧机电机", "定子", "HAS_COMPONENT") in duplicate
        and ("驱动端轴承", "外圈", "HAS_COMPONENT") in duplicate
        and ("非驱动端轴承", "外圈", "HAS_COMPONENT") in duplicate
        and ("棒线主轧机电机", "定子", "HAS_COMPONENT") not in planned,
    )
    reviews = [
        item
        for item in body.get("review_required") or []
        if item.get("source_label") == "润滑不足" and item.get("target_label") == "轴承温升" and item.get("certainty") == "uncertain"
    ]
    blocked_causes = [
        item
        for item in body.get("blocked_items") or []
        if item.get("source_label") == "润滑不足" and item.get("target_label") == "轴承温升"
    ]
    check(
        "不确定因果保持待审核且不能入图",
        len(reviews) == 1
        and reviews[0]["direction"] == "cause_to_effect"
        and reviews[0]["causal_status"] == "unverified"
        and reviews[0]["import_allowed"] is False
        and reviews[0]["graph_relation"] is None
        and "可能" in reviews[0]["evidence"][0]["quote"]
        and blocked_causes
        and all(item["import_allowed"] is False for item in blocked_causes),
    )
    explicit = [
        item
        for item in body.get("review_required") or []
        if item.get("source_label") == "润滑不足" and item.get("target_label") == "轴承温升" and item.get("certainty") == "explicit"
    ]
    reversed_cause = [
        item
        for item in (body.get("new_relations") or []) + (body.get("review_required") or []) + (body.get("duplicate_relations") or [])
        if item.get("source_label") == "轴承温升" and item.get("target_label") == "润滑不足"
    ]
    check(
        "明确因果也是原因指向结果，没有写成已验证因果链",
        len(explicit) == 1
        and explicit[0]["direction"] == "cause_to_effect"
        and explicit[0]["causal_status"] == "unverified"
        and explicit[0]["graph_relation"] is None
        and {item["quote"] for item in explicit[0]["evidence"]} == {"润滑不足导致轴承温升", "轴承温升由润滑不足引起"}
        and not reversed_cause
        and ("润滑不足", "轴承温升", None) not in planned,
    )
    sections = {item["section_id"]: item for item in cleaned["sections"]}
    evidenced = (body.get("new_relations") or []) + (body.get("duplicate_relations") or [])
    check(
        "准备入图的关系都有原文证据",
        evidenced
        and all(
            item["evidence"]
            and item["evidence"][0]["file_id"] == saved["file_id"]
            and item["evidence"][0]["section_id"] in sections
            and item["evidence"][0]["locator"] == sections[item["evidence"][0]["section_id"]]["locator"]
            and item["evidence"][0]["quote"]
            and item["evidence"][0]["quote"] in sections[item["evidence"][0]["section_id"]]["text"]
            and item["evidence"][0]["sha256"] == saved["sha256"]
            for item in evidenced
        )
        and ("某设备", "试验密封圈", "HAS_COMPONENT") in planned
        and not any(item.get("source_label") in {"齿轮裂纹", "轴承磨损"} for item in evidenced),
    )
    again = client.post(f"/api/knowledge/files/{saved['file_id']}/preview-import")
    check("重复预演的计划稳定", again.status_code == 200 and without_time(again.json()) == without_time(body))
    fetched = client.get(f"/api/knowledge/files/{saved['file_id']}/preview-import")
    check(
        "查询接口读到同一份预演且不含磁盘路径",
        fetched.status_code == 200
        and without_time(fetched.json()) == without_time(body)
        and fetched.json()["in_graph"] is False
        and fetched.json()["notice"] == "入图预演没有修改正式知识图谱。"
        and str(TEMP) not in fetched.text
        and "graph.json" not in fetched.text,
    )
    pending = save_upload("未抽取.txt", "棒线主轧机电机包含定子".encode())
    pending_preview = client.post(f"/api/knowledge/files/{pending['file_id']}/preview-import")
    missing = client.post("/api/knowledge/files/abcdefabcdefabcdefabcdefabcdefab/preview-import")
    pending_get = client.get(f"/api/knowledge/files/{pending['file_id']}/preview-import")
    check(
        "不存在或未抽取的文件不能生成假预演",
        pending_preview.status_code == 409
        and missing.status_code == 404
        and pending_get.json().get("preview") is None
        and "existing_node_matches" not in pending_get.json()
        and not (TEMP / "previews" / f"{pending['file_id']}.json").exists(),
    )
    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    check(
        "正式图谱仍是 252 个节点和 464 条关系且哈希不变",
        len(graph["nodes"]) == 252
        and len(graph["edges"]) == 464
        and file_hash(GRAPH) == before["graph"]
        and body["graph_fingerprint"]["node_count"] == 252
        and body["graph_fingerprint"]["relation_count"] == 464
        and body["graph_fingerprint"]["sha256"] == before["graph"],
    )
    check(
        "Excel、监测库和正式预演目录没有被写入",
        file_hash(EXCEL) == EXPECTED_XLSX
        and (file_hash(SQLITE) if SQLITE.is_file() else None) == before["sqlite"]
        and [path.exists() for path in DEFAULTS] == before["defaults"],
    )
    print("PREVIEW_SUMMARY")
    print(json.dumps(body["summary"], ensure_ascii=False))
    print("MATCHES")
    print(json.dumps([{k: item[k] for k in ("entity_type", "label", "scope", "graph_node_id", "matched_parent", "reason")} for item in matches], ensure_ascii=False, indent=2))
    print("NEW_NODES")
    print(json.dumps([{k: item[k] for k in ("entity_type", "label", "scope", "reason")} for item in body["new_nodes"]], ensure_ascii=False, indent=2))
    print("NEW_RELATIONS")
    print(json.dumps([{k: item[k] for k in ("source_label", "target_label", "graph_relation", "reason")} for item in body["new_relations"]], ensure_ascii=False, indent=2))
    print("DUPLICATES")
    print(json.dumps([{k: item[k] for k in ("source_label", "target_label", "graph_relation")} for item in body["duplicate_relations"]], ensure_ascii=False, indent=2))
    print("REVIEWS")
    print(json.dumps([{k: item.get(k) for k in ("label", "source_label", "target_label", "certainty", "direction", "reason")} for item in body["review_required"]], ensure_ascii=False, indent=2))
    print("BLOCKED")
    print(json.dumps([{k: item.get(k) for k in ("label", "scope", "source_label", "target_label", "reason")} for item in body["blocked_items"]], ensure_ascii=False, indent=2))
    print(f"{sum(CHECKS)}/{len(CHECKS)} passed")
    if not all(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
