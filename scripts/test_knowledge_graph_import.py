"""验收安全入图和运行时图谱合并。使用临时库，不改正式图谱文件。"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

TEMP = Path(tempfile.mkdtemp(prefix="knowledge-import-"))
os.environ["KNOWLEDGE_FILES_DB"] = str(TEMP / "files.sqlite3")
os.environ["KNOWLEDGE_UPLOAD_DIR"] = str(TEMP / "uploads")
os.environ["KNOWLEDGE_CLEANED_DIR"] = str(TEMP / "cleaned")
os.environ["KNOWLEDGE_EXTRACTED_DIR"] = str(TEMP / "extracted")
os.environ["KNOWLEDGE_IMPORT_PREVIEW_DIR"] = str(TEMP / "previews")
os.environ["KNOWLEDGE_GRAPH_IMPORTS_DB"] = str(TEMP / "imports.sqlite3")
os.environ["STARTUP_TOOL_SELFTEST"] = "0"
os.environ.pop("KNOWLEDGE_IMPORT_INJECT_ERROR", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.knowledge_file_store import save_upload  # noqa: E402
from app.services.knowledge_graph_import import activate_batch, deactivate_batch  # noqa: E402

CHECKS: list[bool] = []
EXCEL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
GRAPH = ROOT / "data" / "processed" / "graph" / "graph.json"
SQLITE = ROOT / "data" / "runtime" / "monitoring_events.sqlite3"
OFFICIAL = ROOT / "data" / "official"
EXPECTED_XLSX = "1e9caeba1fe526f3ad1b5dd6445a9dbcc02fa22ec02273d9863ebc84e3e30f87"
DEFAULT_DB = ROOT / "data" / "runtime" / "knowledge_graph_imports.sqlite3"
SAMPLE = "\n".join(
    [
        "棒线主轧机电机包含车间新增护罩",
        "棒线冷却水循环泵包含车间新增护罩",
        "棒线主轧机电机包含定子",
        "棒线主轧机电机包含外圈",
        "设备棒线主轧机电机的额定电流为120A",
        "设备主电机的额定电流为120A",
        "润滑不足导致轴承温升",
        "轴承温升可能由润滑不足引起",
        "某设备包含试验密封圈",
        "主电机包含另一护罩",
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


def prepare(client: TestClient, filename: str, content: str) -> str:
    saved = save_upload(filename, content.encode())
    cleaned = client.post(f"/api/knowledge/files/{saved['file_id']}/clean")
    extracted = client.post(f"/api/knowledge/files/{saved['file_id']}/extract")
    preview = client.post(f"/api/knowledge/files/{saved['file_id']}/preview-import")
    if cleaned.status_code != 200 or extracted.status_code != 200 or preview.status_code != 200:
        raise RuntimeError(cleaned.text + extracted.text + preview.text)
    return saved["file_id"]


def main() -> None:
    before = {
        "excel": file_hash(EXCEL),
        "graph": file_hash(GRAPH),
        "sqlite": file_hash(SQLITE) if SQLITE.is_file() else None,
        "official": {path.name: file_hash(path) for path in OFFICIAL.glob("*.docx") if not path.name.startswith("~$")},
        "default_db": DEFAULT_DB.exists(),
    }
    original = json.loads(GRAPH.read_text(encoding="utf-8"))
    source = (BACKEND / "app" / "services" / "knowledge_graph_import.py").read_text(encoding="utf-8")
    modules = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(modules):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.update(node.module.split("."))
    banned = imported & {"deepseek", "requests", "httpx", "monitoring_events", "anomaly_detection", "diagnostic_bridge", "agent_tools"}
    check("入图模块没有调用 DeepSeek 或五个工具", not banned and "sk-" not in source, str(sorted(banned)))

    client = TestClient(app)
    file_id = prepare(client, "安全入图.txt", SAMPLE)
    first = client.post(f"/api/knowledge/files/{file_id}/apply-import")
    body = first.json()
    check(
        "明确的设备包含新部件能够入图",
        first.status_code == 200 and body["applied"] is True and body["imported_node_count"] == 2 and body["imported_relation_count"] == 2,
        first.text[:500],
    )
    connection = sqlite3.connect(os.environ["KNOWLEDGE_GRAPH_IMPORTS_DB"])
    stored_nodes = connection.execute("SELECT node_id, parent_id, label FROM import_nodes ORDER BY label, parent_id").fetchall()
    evidence = connection.execute("SELECT COUNT(*) FROM import_evidence").fetchone()[0]
    connection.close()
    check("新增节点写入临时数据库", len(stored_nodes) == 2 and {row[2] for row in stored_nodes} == {"车间新增护罩"} and evidence == 2)

    graph = client.get("/api/knowledge/graph").json()
    original_ids = {node["id"] for node in original["nodes"]}
    merged_ids = {node["id"] for node in graph["nodes"]}
    added = [node for node in graph["nodes"] if node["id"] not in original_ids]
    by_original = {node["id"]: node for node in original["nodes"]}
    same_original = all(by_original[node["id"]] == {key: node[key] for key in ("id", "data")} for node in graph["nodes"] if node["id"] in original_ids)
    check(
        "查询接口合并新增内容且原节点不变",
        len(graph["nodes"]) == 254
        and len(graph["edges"]) == 466
        and original_ids <= merged_ids
        and len(added) == 2
        and same_original,
    )
    parents = {node["parentId"] for node in added}
    device_ids = {node["id"] for node in original["nodes"] if node["data"]["type"] == "device" and node["data"]["label"] in {"棒线主轧机电机", "棒线冷却水循环泵"}}
    check(
        "两个同名护罩分别挂到两台设备，没有循环",
        parents == device_ids
        and len({node["id"] for node in added}) == 2
        and all(node["data"]["type"] == "component" and node["data"]["category"] == "equipment" for node in added)
        and all(node["parentId"] != node["id"] and node["id"] not in parents for node in added)
        and all(node["data"]["source_refs"][0]["source_type"] == "user_upload" for node in added),
    )
    second = client.post(f"/api/knowledge/files/{file_id}/apply-import")
    again_nodes = sqlite3.connect(os.environ["KNOWLEDGE_GRAPH_IMPORTS_DB"]).execute("SELECT COUNT(*) FROM import_nodes").fetchone()[0]
    check("同一文件重复提交不创建重复节点", second.status_code == 200 and second.json()["repeated"] is True and again_nodes == 2)

    other_id = prepare(client, "第二份证据.txt", "棒线主轧机电机包含车间新增护罩")
    other = client.post(f"/api/knowledge/files/{other_id}/apply-import")
    evidence_rows = sqlite3.connect(os.environ["KNOWLEDGE_GRAPH_IMPORTS_DB"]).execute(
        "SELECT file_id, quote FROM import_evidence WHERE quote = ?",
        ("棒线主轧机电机包含车间新增护罩",),
    ).fetchall()
    motor_edges = [
        edge
        for edge in client.get("/api/knowledge/graph").json()["edges"]
        if edge["id"].startswith("import-edge-") and edge["source"] == next(node["parentId"] for node in added if node["data"]["device_label"] == "棒线主轧机电机")
    ]
    check(
        "同一关系保留两份证据且不重复建边",
        other.status_code == 200
        and other.json()["applied"] is False
        and other.json()["imported_relation_count"] == 0
        and other.json()["new_evidence_count"] == 1
        and len(evidence_rows) == 2
        and len({row[0] for row in evidence_rows}) == 2
        and len(motor_edges) == 1
        and len(motor_edges[0]["data"]["source_refs"]) == 2,
    )
    reasons = "\n".join(item["reason"] for item in body["rejections"])
    check("未验证因果、冲突参数、占位名称和泛称都没有入图", "不能升级为确定因果关系" in reasons or "未验证" in reasons)
    check("占位名称和泛称被拒绝", "占位" in reasons and "泛称" in reasons)
    check("电流限值冲突被拒绝", "388A" in reasons and "不能互相覆盖" in reasons)
    check("归属不明的外圈被拒绝", "不能合并" in reasons)
    added_labels = {node["data"]["label"] for node in added}
    check("占位设备和泛称设备下没有自动新增部件", "试验密封圈" not in added_labels and "另一护罩" not in added_labels)

    placeholder_id = prepare(client, "占位设备.txt", "某设备包含试验密封圈")
    placeholder = client.post(f"/api/knowledge/files/{placeholder_id}/apply-import")
    check(
        "全部不符合规则时导入零条且不伪造成功",
        placeholder.status_code == 200
        and placeholder.json()["applied"] is False
        and placeholder.json()["status"] == "nothing_imported"
        and placeholder.json()["imported_node_count"] == 0,
    )

    stale_id = prepare(client, "过期预演.txt", "棒线主轧机电机包含过期护罩")
    preview_path = TEMP / "previews" / f"{stale_id}.json"
    preview_body = json.loads(preview_path.read_text(encoding="utf-8"))
    preview_body["graph_fingerprint"]["sha256"] = "0" * 64
    preview_path.write_text(json.dumps(preview_body, ensure_ascii=False), encoding="utf-8")
    stale = client.post(f"/api/knowledge/files/{stale_id}/apply-import")
    stale_nodes = sqlite3.connect(os.environ["KNOWLEDGE_GRAPH_IMPORTS_DB"]).execute(
        "SELECT COUNT(*) FROM import_nodes WHERE label = ?",
        ("过期护罩",),
    ).fetchone()[0]
    check("旧预演被拒绝且没有写入", stale.status_code == 409 and "重新预演" in stale.json()["message"] and stale_nodes == 0)

    fail_id = prepare(client, "事务失败.txt", "棒线主轧机电机包含事务护罩")
    os.environ["KNOWLEDGE_IMPORT_INJECT_ERROR"] = "1"
    failed = client.post(f"/api/knowledge/files/{fail_id}/apply-import")
    os.environ.pop("KNOWLEDGE_IMPORT_INJECT_ERROR", None)
    failed_nodes = sqlite3.connect(os.environ["KNOWLEDGE_GRAPH_IMPORTS_DB"]).execute(
        "SELECT COUNT(*) FROM import_nodes WHERE label = ?",
        ("事务护罩",),
    ).fetchone()[0]
    kept = sqlite3.connect(os.environ["KNOWLEDGE_GRAPH_IMPORTS_DB"]).execute("SELECT COUNT(*) FROM import_nodes").fetchone()[0]
    check("事务失败不会留下不完整节点，已有入图仍在", failed.status_code == 500 and failed_nodes == 0 and kept == 2)

    missing = client.post("/api/knowledge/files/abcdefabcdefabcdefabcdefabcdefab/apply-import")
    pending = save_upload("未抽取.txt", "棒线主轧机电机包含车间新增护罩".encode())
    pending_apply = client.post(f"/api/knowledge/files/{pending['file_id']}/apply-import")
    check("不存在或未抽取的文件不能入图", missing.status_code == 404 and pending_apply.status_code == 409)

    result = client.get(f"/api/knowledge/files/{file_id}/import-result")
    check("可以查询实际入图结果", result.status_code == 200 and result.json()["imported_node_count"] == 2 and result.json()["applied"] is True)

    first_batch = body["batch_id"]
    deactivate_batch(first_batch)
    after_stop = client.get("/api/knowledge/graph").json()
    stopped_labels = [node["data"]["device_label"] for node in after_stop["nodes"] if node["data"].get("label") == "车间新增护罩"]
    activate_batch(first_batch)
    restored = client.get("/api/knowledge/graph").json()
    check(
        "按批次停用时保留另一份文件仍支持的关系",
        stopped_labels == ["棒线主轧机电机"] and len([node for node in restored["nodes"] if node["id"] not in original_ids]) == 2,
    )

    official_after = {path.name: file_hash(path) for path in OFFICIAL.glob("*.docx") if not path.name.startswith("~$")}
    check("基础图谱文件仍是 252 个节点和 464 条关系", len(original["nodes"]) == 252 and len(original["edges"]) == 464 and file_hash(GRAPH) == before["graph"])
    check(
        "官方资料、Excel、监测库和正式入图库没有变化",
        official_after == before["official"]
        and file_hash(EXCEL) == EXPECTED_XLSX
        and (file_hash(SQLITE) if SQLITE.is_file() else None) == before["sqlite"]
        and DEFAULT_DB.exists() == before["default_db"],
    )
    print("IMPORT_RESULT")
    print(json.dumps({key: body[key] for key in ("status", "imported_node_count", "imported_relation_count", "existing_relation_count", "review_count", "rejected_count", "message")}, ensure_ascii=False))
    print("REJECTIONS")
    print(json.dumps(body["rejections"], ensure_ascii=False, indent=2)[:4000])
    print(f"{sum(CHECKS)}/{len(CHECKS)} passed")
    if not all(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
