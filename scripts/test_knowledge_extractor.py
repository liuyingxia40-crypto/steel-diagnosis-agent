"""验收已清洗文件的候选知识抽取。使用临时目录，不写入正式资料。"""

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

TEMP = Path(tempfile.mkdtemp(prefix="knowledge-extract-"))
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
OFFICIAL = ROOT / "data" / "official"
EXPECTED_XLSX = "1e9caeba1fe526f3ad1b5dd6445a9dbcc02fa22ec02273d9863ebc84e3e30f87"
OFFICIAL_RELATIONS = {
    "CONNECTS_TO",
    "HAS_ACTION",
    "HAS_COMPONENT",
    "HAS_FAULT",
    "HAS_FEATURE",
    "HAS_PARAMETER",
    "OCCURS_ON",
    "SUPPORTED_BY",
}
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
        "某设备包含轴承",
        "轴承磨损具有高频冲击特征",
        "齿轮裂纹、轴承磨损",
        "轴承温升可能由润滑不足引起",
        "设备主电机的额定电流为120A",
        "部件轴承的温度限值为80°C",
        "轴承磨损的处置措施为补充润滑脂",
        "今天车间负荷较高，需要继续观察",
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


def relation(document: dict, source: str, target: str) -> dict | None:
    found = [
        item
        for item in document["relations"]
        if item["source_label"] == source and item["target_label"] == target
    ]
    return found[0] if len(found) == 1 else None


def main() -> None:
    before = {
        "excel": file_hash(EXCEL),
        "graph": file_hash(GRAPH),
        "sqlite": file_hash(SQLITE) if SQLITE.is_file() else None,
        "official": {path.name: file_hash(path) for path in OFFICIAL.glob("*.docx") if not path.name.startswith("~$")},
        "defaults": [path.exists() for path in DEFAULTS],
    }
    source = (BACKEND / "app" / "services" / "knowledge_extractor.py").read_text(encoding="utf-8")
    modules = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(modules):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.update(node.module.split("."))
    banned = imported & {
        "deepseek",
        "requests",
        "httpx",
        "monitoring_events",
        "anomaly_detection",
        "diagnostic_bridge",
        "agent_tools",
    }
    check("抽取模块没有调用 DeepSeek、监测或智能体工具", not banned and "sk-" not in source, str(sorted(banned)))

    client = TestClient(app)
    uploaded = upload(client, "轴承维修记录.txt", SAMPLE.encode())
    saved = uploaded.json()
    cleaned_path = TEMP / "cleaned" / f"{saved['file_id']}.json"
    cleaned_before = cleaned_path.read_bytes()
    cleaned = json.loads(cleaned_before.decode("utf-8"))
    extracted = client.post(f"/api/knowledge/files/{saved['file_id']}/extract")
    body = extracted.json()
    document_path = TEMP / "extracted" / f"{saved['file_id']}.json"
    document = json.loads(document_path.read_text(encoding="utf-8")) if document_path.is_file() else {}
    contains = relation(document, "某设备", "轴承") if document else None
    contains_section = next(item for item in cleaned["sections"] if item["text"] == "某设备包含轴承")
    check(
        "明确的包含表述抽出设备和部件关系",
        extracted.status_code == 200
        and contains is not None
        and contains["relation_type"] == "HAS_COMPONENT"
        and contains["graph_relation"] == "HAS_COMPONENT"
        and contains["mapped_to_graph"] is True
        and contains["certainty"] == "explicit"
        and contains["in_graph"] is False
        and contains["evidence"][0]["quote"] == "某设备包含轴承"
        and contains["evidence"][0]["locator"] == contains_section["locator"]
        and contains["evidence"][0]["sha256"] == saved["sha256"]
        and contains["evidence"][0]["original_filename"] == "轴承维修记录.txt",
        extracted.text[:400],
    )
    feature = relation(document, "轴承磨损", "高频冲击") if document else None
    feature_section = next(item for item in cleaned["sections"] if "高频冲击" in item["text"])
    check(
        "故障特征关系保留清洗位置",
        feature is not None
        and feature["relation_type"] == "HAS_FEATURE"
        and feature["graph_relation"] == "HAS_FEATURE"
        and feature["evidence"][0]["locator"] == feature_section["locator"]
        and feature["evidence"][0]["section_id"] == feature_section["section_id"],
    )
    labels = {item["label"] for item in document.get("entities", [])}
    endpoints = {(item["source_label"], item["target_label"]) for item in document.get("relations", [])}
    check(
        "并列故障名称没有编造因果关系",
        "齿轮裂纹" not in labels and ("齿轮裂纹", "轴承磨损") not in endpoints and ("轴承磨损", "齿轮裂纹") not in endpoints,
    )
    cause = relation(document, "润滑不足", "轴承温升") if document else None
    check(
        "可能引起保持不确定且方向是原因指向结果",
        cause is not None
        and cause["source_entity_type"] == "cause"
        and cause["target_entity_type"] == "fault"
        and cause["direction"] == "cause_to_effect"
        and cause["causal_status"] == "unverified"
        and cause["certainty"] == "uncertain"
        and cause["mapped_to_graph"] is False
        and cause["graph_relation"] is None
        and cause["relation_type"] not in OFFICIAL_RELATIONS
        and "可能" in cause["evidence"][0]["quote"]
        and cause["review_status"] == "needs_review"
        and relation(document, "轴承温升", "润滑不足") is None,
    )
    action = relation(document, "轴承磨损", "补充润滑脂") if document else None
    parameter = relation(document, "主电机", "额定电流") if document else None
    component_parameter = relation(document, "轴承", "温度限值") if document else None
    check(
        "处置和部件参数没有强行改成不匹配的正式关系",
        action is not None
        and action["relation_type"] != "HAS_ACTION"
        and action["graph_relation"] is None
        and parameter is not None
        and parameter["graph_relation"] == "HAS_PARAMETER"
        and parameter["target_label"] == "额定电流"
        and component_parameter is not None
        and component_parameter["graph_relation"] is None,
    )
    check(
        "自由文本被标记为需要后续语义抽取",
        any("需要后续语义抽取" in item for item in document.get("warnings", []))
        and not any("继续观察" in item["label"] for item in document["entities"]),
    )
    first_ids = [item["entity_id"] for item in document["entities"]]
    again = client.post(f"/api/knowledge/files/{saved['file_id']}/extract")
    second = json.loads(document_path.read_text(encoding="utf-8"))
    check(
        "重复抽取不生成重复候选",
        again.status_code == 200
        and [item["entity_id"] for item in second["entities"]] == first_ids
        and len(first_ids) == len(set(first_ids))
        and [item["relation_id"] for item in second["relations"]] == [item["relation_id"] for item in document["relations"]]
        and cleaned_path.read_bytes() == cleaned_before,
    )

    pending = save_upload("待清洗.txt", "某设备包含轴承".encode())
    pending_extract = client.post(f"/api/knowledge/files/{pending['file_id']}/extract")
    missing = client.post("/api/knowledge/files/abcdefabcdefabcdefabcdefabcdefab/extract")
    failed = upload(client, "说明.pdf", b"%PDF-1.4\n")
    failed_body = failed.json()
    failed_extract = client.post(f"/api/knowledge/files/{failed_body['file_id']}/extract")
    check(
        "不存在或未清洗的文件不能抽取",
        pending_extract.status_code == 409
        and not (TEMP / "extracted" / f"{pending['file_id']}.json").exists()
        and missing.status_code == 404
        and failed_extract.status_code == 409
        and failed_body["processing_status"] == "cleaning_failed"
        and not (TEMP / "extracted" / f"{failed_body['file_id']}.json").exists()
        and (TEMP / "uploads" / failed_body["file_id"]).is_file(),
    )

    preview = client.get(f"/api/knowledge/files/{saved['file_id']}/extracted")
    preview_body = preview.json()
    preview_relation = relation(preview_body["preview"], "某设备", "轴承") if preview_body.get("preview") else None
    check(
        "查询接口返回候选和原文位置且不含磁盘路径",
        preview.status_code == 200
        and preview_relation is not None
        and preview_relation["evidence"][0]["locator"] == contains_section["locator"]
        and preview_body["in_graph"] is False
        and preview_body["notice"] == "候选知识尚未加入知识图谱。"
        and str(TEMP) not in preview.text
        and "stored_name" not in preview.text
        and "source_path" not in preview.text,
    )
    reopened = json.loads(document_path.read_text(encoding="utf-8"))
    check("重新打开抽取文件后候选仍在", relation(reopened, "某设备", "轴承") is not None)

    table_file = upload(client, "原因对照.csv", "故障,原因\n齿轮裂纹,安装偏差\n轴承磨损,润滑不足\n".encode())
    table_saved = table_file.json()
    table_extract = client.post(f"/api/knowledge/files/{table_saved['file_id']}/extract")
    table_doc = json.loads((TEMP / "extracted" / f"{table_saved['file_id']}.json").read_text(encoding="utf-8"))
    first_cause = relation(table_doc, "安装偏差", "齿轮裂纹")
    second_cause = relation(table_doc, "润滑不足", "轴承磨损")
    crossed = relation(table_doc, "安装偏差", "轴承磨损")
    check(
        "表格按本行表头抽取，没有跨行拼接原因",
        table_extract.status_code == 200
        and first_cause is not None
        and second_cause is not None
        and crossed is None
        and "润滑不足" not in first_cause["evidence"][0]["quote"]
        and first_cause["direction"] == "cause_to_effect"
        and first_cause["source_entity_type"] == "cause"
        and first_cause["evidence"][0]["locator"]["index"] != second_cause["evidence"][0]["locator"]["index"],
    )
    causal = upload(
        client,
        "因果方向.txt",
        "润滑不足导致轴承温升\n轴承温升由润滑不足引起\n轴承温升可能由润滑不足引起\n齿轮裂纹与轴承磨损同时出现\n".encode(),
    )
    causal_id = causal.json()["file_id"]
    causal_extract = client.post(f"/api/knowledge/files/{causal_id}/extract")
    causal_doc = json.loads((TEMP / "extracted" / f"{causal_id}.json").read_text(encoding="utf-8"))
    explicit_causes = [
        item
        for item in causal_doc["relations"]
        if item["source_label"] == "润滑不足" and item["target_label"] == "轴承温升" and item["certainty"] == "explicit"
    ]
    uncertain_causes = [
        item
        for item in causal_doc["relations"]
        if item["source_label"] == "润滑不足" and item["target_label"] == "轴承温升" and item["certainty"] == "uncertain"
    ]
    quotes = {entry["quote"] for entry in explicit_causes[0]["evidence"]} if explicit_causes else set()
    check(
        "导致和由引起都从原因指向结果，并列出现不建立因果",
        causal_extract.status_code == 200
        and len(explicit_causes) == 1
        and explicit_causes[0]["direction"] == "cause_to_effect"
        and explicit_causes[0]["causal_status"] == "unverified"
        and explicit_causes[0]["graph_relation"] is None
        and quotes == {"润滑不足导致轴承温升", "轴承温升由润滑不足引起"}
        and len(uncertain_causes) == 1
        and uncertain_causes[0]["certainty"] == "uncertain"
        and "可能" in uncertain_causes[0]["evidence"][0]["quote"]
        and not any(item["source_label"] in {"齿轮裂纹", "轴承磨损"} and item["target_label"] in {"齿轮裂纹", "轴承磨损"} for item in causal_doc["relations"]),
    )

    long_text = "".join(f"泵{index:02d}包含叶轮{index:02d}\n" for index in range(1, 26)).encode()
    long_file = upload(client, "多部件.txt", long_text)
    long_id = long_file.json()["file_id"]
    long_extract = client.post(f"/api/knowledge/files/{long_id}/extract")
    long_preview = long_extract.json()
    long_stored = json.loads((TEMP / "extracted" / f"{long_id}.json").read_text(encoding="utf-8"))
    check(
        "接口只返回有限预览，完整候选仍保存在文件中",
        long_preview["preview_truncated"] is True
        and len(long_preview["preview"]["relations"]) == 20
        and len(long_stored["relations"]) == 25
        and long_preview["relation_count"] == 25,
    )

    broken_record = save_upload("损坏.txt", "某设备包含轴承".encode())
    broken_clean = client.post(f"/api/knowledge/files/{broken_record['file_id']}/clean")
    broken_id = broken_record["file_id"]
    check("损坏样本只清洗、不走自动抽取", broken_clean.status_code == 200 and not (TEMP / "extracted" / f"{broken_id}.json").exists())
    (TEMP / "cleaned" / f"{broken_id}.json").write_text("{", encoding="utf-8")
    broken_extract = client.post(f"/api/knowledge/files/{broken_id}/extract")
    broken_get = client.get(f"/api/knowledge/files/{broken_id}/extracted")
    check(
        "抽取失败给出原因且不伪造成功",
        broken_extract.status_code == 422
        and broken_get.json()["extraction_status"] == "extraction_failed"
        and broken_get.json()["preview"] is None
        and broken_get.json()["error_message"],
    )
    (TEMP / "cleaned" / f"{saved['file_id']}.json").write_text("{", encoding="utf-8")
    kept = client.post(f"/api/knowledge/files/{saved['file_id']}/extract")
    kept_doc = json.loads(document_path.read_text(encoding="utf-8"))
    check(
        "重新抽取失败时保留上一次候选",
        kept.status_code == 422
        and "已保留上一次结果" in kept.json()["message"]
        and [item["relation_id"] for item in kept_doc["relations"]] == [item["relation_id"] for item in document["relations"]],
    )

    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    official_after = {path.name: file_hash(path) for path in OFFICIAL.glob("*.docx") if not path.name.startswith("~$")}
    check("图谱仍是 252 个节点和 464 条关系", len(graph["nodes"]) == 252 and len(graph["edges"]) == 464 and file_hash(GRAPH) == before["graph"])
    check("官方 Word、Excel 和监测库没有变化", official_after == before["official"] and file_hash(EXCEL) == EXPECTED_XLSX and (file_hash(SQLITE) if SQLITE.is_file() else None) == before["sqlite"])
    check("测试没有创建正式抽取目录", [path.exists() for path in DEFAULTS] == before["defaults"])
    print("SAMPLE_JSON")
    print(json.dumps(document, ensure_ascii=False, indent=2))
    print(f"{sum(CHECKS)}/{len(CHECKS)} passed")
    if not all(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
