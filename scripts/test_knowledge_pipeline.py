"""验收上传后的知识处理全流程。使用临时目录和临时数据库。"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

TEMP = Path(tempfile.mkdtemp(prefix="knowledge-pipeline-"))
os.environ["KNOWLEDGE_FILES_DB"] = str(TEMP / "files.sqlite3")
os.environ["KNOWLEDGE_UPLOAD_DIR"] = str(TEMP / "uploads")
os.environ["KNOWLEDGE_CLEANED_DIR"] = str(TEMP / "cleaned")
os.environ["KNOWLEDGE_EXTRACTED_DIR"] = str(TEMP / "extracted")
os.environ["KNOWLEDGE_IMPORT_PREVIEW_DIR"] = str(TEMP / "previews")
os.environ["KNOWLEDGE_GRAPH_IMPORTS_DB"] = str(TEMP / "imports.sqlite3")
os.environ["STARTUP_TOOL_SELFTEST"] = "0"
os.environ.pop("KNOWLEDGE_PIPELINE_FAIL_STAGE", None)
os.environ.pop("KNOWLEDGE_PIPELINE_PAUSE_STAGE", None)
os.environ.pop("KNOWLEDGE_IMPORT_INJECT_ERROR", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.knowledge_file_store import KnowledgeFileError, save_upload  # noqa: E402
from app.services.knowledge_pipeline import pipeline_pause, process_knowledge_file  # noqa: E402

CHECKS: list[bool] = []
EXCEL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
GRAPH = ROOT / "data" / "processed" / "graph" / "graph.json"
SQLITE = ROOT / "data" / "runtime" / "monitoring_events.sqlite3"
OFFICIAL = ROOT / "data" / "official"
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


def stage_names(body: dict) -> list[str]:
    return [item["stage"] for item in body["pipeline"]["stages"]]


def wait_stage(file_id: str, stage: str) -> bool:
    deadline = time.time() + 8
    while time.time() < deadline:
        connection = sqlite3.connect(os.environ["KNOWLEDGE_FILES_DB"])
        row = connection.execute(
            "SELECT stage, pipeline_status FROM knowledge_processes WHERE file_id = ? ORDER BY started_at DESC LIMIT 1",
            (file_id,),
        ).fetchone()
        connection.close()
        if row is not None and row[0] == stage and row[1] == "running":
            return True
        time.sleep(0.05)
    return False


def main() -> None:
    before = {
        "excel": file_hash(EXCEL),
        "graph": file_hash(GRAPH),
        "sqlite": file_hash(SQLITE) if SQLITE.is_file() else None,
        "official": {path.name: file_hash(path) for path in OFFICIAL.glob("*.docx") if not path.name.startswith("~$")},
        "defaults": [path.exists() for path in DEFAULTS],
    }
    original = json.loads(GRAPH.read_text(encoding="utf-8"))
    source = (BACKEND / "app" / "services" / "knowledge_pipeline.py").read_text(encoding="utf-8")
    modules = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(modules):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.update(node.module.split("."))
    banned = imported & {"deepseek", "requests", "httpx", "monitoring_events", "anomaly_detection", "diagnostic_bridge", "agent_tools"}
    check("流程模块没有调用 DeepSeek 或五个工具", not banned and "sk-" not in source, str(sorted(banned)))

    client = TestClient(app)
    uploaded = upload(client, "自动流程.txt", SAMPLE.encode())
    body = uploaded.json()
    file_id = body.get("file_id", "")
    pipeline = body.get("pipeline") or {}
    counts = pipeline.get("counts") or {}
    extracted_path = TEMP / "extracted" / f"{file_id}.json"
    preview_path = TEMP / "previews" / f"{file_id}.json"
    cleaned_path = TEMP / "cleaned" / f"{file_id}.json"
    extracted = json.loads(extracted_path.read_text(encoding="utf-8")) if extracted_path.is_file() else {}
    preview = json.loads(preview_path.read_text(encoding="utf-8")) if preview_path.is_file() else {}
    summary = preview.get("summary") or {}
    imported_body = pipeline.get("import_result") or {}
    check(
        "上传后自动完成清洗、抽取、预演和安全入图",
        uploaded.status_code == 200
        and body.get("processing_status") == "cleaned"
        and pipeline.get("pipeline_status") == "completed"
        and stage_names(body) == ["cleaning", "extracting", "previewing", "importing"]
        and all(item["status"] == "completed" for item in pipeline.get("stages", []))
        and pipeline["stages"][0]["reused"] is False
        and counts.get("entity_count") == len(extracted.get("entities") or [])
        and counts.get("relation_count") == len(extracted.get("relations") or [])
        and counts.get("matched_node_count") == summary.get("matched_nodes")
        and counts.get("duplicate_relation_count") == summary.get("duplicate_relations")
        and counts.get("conflict_count") == summary.get("conflicts")
        and counts.get("review_count") == summary.get("review_required")
        and counts.get("blocked_count") == summary.get("blocked_items")
        and counts.get("new_node_count") == imported_body.get("imported_node_count")
        and counts.get("new_relation_count") == imported_body.get("imported_relation_count")
        and counts.get("new_node_count") == 2
        and counts.get("new_relation_count") == 2
        and pipeline.get("knowledge_added") is True
        and pipeline.get("duration_ms", 0) >= 0
        and isinstance(pipeline.get("process_id"), str)
        and len(pipeline.get("process_id") or "") == 32,
        uploaded.text[:800],
    )
    relations = extracted.get("relations") or []
    check(
        "候选关系没有被流程标成已在图谱中",
        extracted.get("in_graph") is False
        and relations
        and all(item.get("in_graph") is False for item in relations)
        and body.get("in_graph") is False
        and pipeline.get("in_graph") is False,
    )
    reasons = "\n".join(item["reason"] for item in imported_body.get("rejections") or [])
    check("不确定因果关系没有入图", "不能升级为确定因果关系" in reasons or "未验证" in reasons)
    check("参数冲突和归属不明没有入图", "388A" in reasons and "不能互相覆盖" in reasons and "不能合并" in reasons)

    connection = sqlite3.connect(os.environ["KNOWLEDGE_GRAPH_IMPORTS_DB"])
    stored = connection.execute("SELECT label FROM import_nodes").fetchall()
    connection.close()
    graph = client.get("/api/knowledge/graph").json()
    original_ids = {node["id"] for node in original["nodes"]}
    added = [node for node in graph["nodes"] if node["id"] not in original_ids]
    by_original = {node["id"]: node for node in original["nodes"]}
    same_original = all(by_original[node["id"]] == {key: node[key] for key in ("id", "data")} for node in graph["nodes"] if node["id"] in original_ids)
    device_ids = {node["id"] for node in original["nodes"] if node["data"]["type"] == "device" and node["data"]["label"] in {"棒线主轧机电机", "棒线冷却水循环泵"}}
    check(
        "查询接口能看到新增知识且原节点不变",
        len(stored) == 2
        and {row[0] for row in stored} == {"车间新增护罩"}
        and len(graph["nodes"]) == 254
        and len(graph["edges"]) == 466
        and len(added) == 2
        and {node["parentId"] for node in added} == device_ids
        and same_original
        and all(node["data"]["label"] != "试验密封圈" and node["data"]["label"] != "另一护罩" for node in added),
    )

    cleaned_bytes = cleaned_path.read_bytes()
    upload_bytes = (TEMP / "uploads" / file_id).read_bytes()
    again = client.post(f"/api/knowledge/files/{file_id}/process")
    again_body = again.json()
    again_nodes = sqlite3.connect(os.environ["KNOWLEDGE_GRAPH_IMPORTS_DB"]).execute("SELECT COUNT(*) FROM import_nodes").fetchone()[0]
    check(
        "重复处理不重复清洗，也不重复创建节点",
        again.status_code == 200
        and again_body["pipeline"]["pipeline_status"] == "completed"
        and again_body["pipeline"]["knowledge_added"] is False
        and again_body["pipeline"]["counts"]["new_node_count"] == 0
        and again_body["pipeline"]["counts"]["new_relation_count"] == 0
        and again_body["pipeline"]["stages"][0]["reused"] is True
        and cleaned_path.read_bytes() == cleaned_bytes
        and (TEMP / "uploads" / file_id).read_bytes() == upload_bytes
        and again_nodes == 2
        and "没有重复创建" in again_body["pipeline"]["message"],
        again.text[:500],
    )

    other = upload(client, "第二份证据.txt", "棒线主轧机电机包含车间新增护罩".encode())
    evidence = sqlite3.connect(os.environ["KNOWLEDGE_GRAPH_IMPORTS_DB"]).execute(
        "SELECT file_id FROM import_evidence WHERE quote = ?",
        ("棒线主轧机电机包含车间新增护罩",),
    ).fetchall()
    motor_parent = next(node["parentId"] for node in added if node["data"]["device_label"] == "棒线主轧机电机")
    motor_edges = [
        edge
        for edge in client.get("/api/knowledge/graph").json()["edges"]
        if edge["id"].startswith("import-edge-") and edge["source"] == motor_parent
    ]
    check(
        "第二份文件只补充证据，不新建关系",
        other.status_code == 200
        and other.json()["pipeline"]["knowledge_added"] is False
        and other.json()["pipeline"]["import_result"]["status"] == "evidence_recorded"
        and other.json()["pipeline"]["import_result"]["new_evidence_count"] == 1
        and len(evidence) == 2
        and len({row[0] for row in evidence}) == 2
        and len(motor_edges) == 1
        and len(motor_edges[0]["data"]["source_refs"]) == 2,
        other.text[:500],
    )

    placeholder = upload(client, "占位设备.txt", "某设备包含试验密封圈".encode())
    placeholder_pipeline = placeholder.json().get("pipeline") or {}
    placeholder_nodes = sqlite3.connect(os.environ["KNOWLEDGE_GRAPH_IMPORTS_DB"]).execute(
        "SELECT COUNT(*) FROM import_nodes WHERE label = ?",
        ("试验密封圈",),
    ).fetchone()[0]
    check(
        "没有可入图知识时完成流程但不伪造成功",
        placeholder.status_code == 200
        and placeholder_pipeline.get("pipeline_status") == "completed"
        and placeholder_pipeline.get("outcome") != "knowledge_added"
        and placeholder_pipeline.get("knowledge_added") is False
        and placeholder_pipeline.get("counts", {}).get("new_node_count") == 0
        and placeholder_pipeline.get("import_result", {}).get("applied") is False
        and placeholder_pipeline.get("import_result", {}).get("status") == "nothing_imported"
        and "没有" in placeholder_pipeline.get("message", "")
        and placeholder_nodes == 0,
        placeholder.text[:500],
    )

    os.environ["KNOWLEDGE_PIPELINE_FAIL_STAGE"] = "extracting"
    failed = upload(client, "抽取中断.txt", "棒线主轧机电机包含中断护罩".encode())
    os.environ.pop("KNOWLEDGE_PIPELINE_FAIL_STAGE", None)
    failed_body = failed.json()
    failed_id = failed_body.get("file_id", "")
    check(
        "抽取失败时保留原始文件和清洗结果，后续阶段不成功",
        failed.status_code == 500
        and failed_body.get("processing_status") == "cleaned"
        and failed_body.get("pipeline", {}).get("pipeline_status") == "failed"
        and failed_body.get("pipeline", {}).get("failure_stage") == "extracting"
        and "extracting" in failed_body.get("pipeline", {}).get("error_message", "")
        and "previewing" not in stage_names(failed_body)
        and "importing" not in stage_names(failed_body)
        and (TEMP / "uploads" / failed_id).is_file()
        and (TEMP / "cleaned" / f"{failed_id}.json").is_file()
        and not (TEMP / "extracted" / f"{failed_id}.json").exists(),
        failed.text[:500],
    )

    reread = TestClient(app).get(f"/api/knowledge/files/{file_id}/process-result")
    history = sqlite3.connect(os.environ["KNOWLEDGE_FILES_DB"]).execute(
        "SELECT process_id, new_node_count FROM knowledge_processes WHERE file_id = ? ORDER BY started_at",
        (file_id,),
    ).fetchall()
    check(
        "重新读取仍能查到之前的处理结果",
        reread.status_code == 200
        and reread.json()["pipeline"]["process_id"] == again_body["pipeline"]["process_id"]
        and reread.json()["pipeline"]["counts"]["new_node_count"] == 0
        and (pipeline["process_id"], 2) in history
        and (again_body["pipeline"]["process_id"], 0) in history,
        str(history),
    )

    separate_clean = client.post(f"/api/knowledge/files/{file_id}/clean")
    separate_extract = client.post(f"/api/knowledge/files/{file_id}/extract")
    separate_preview = client.post(f"/api/knowledge/files/{file_id}/preview-import")
    separate_apply = client.post(f"/api/knowledge/files/{file_id}/apply-import")
    separate_nodes = sqlite3.connect(os.environ["KNOWLEDGE_GRAPH_IMPORTS_DB"]).execute("SELECT COUNT(*) FROM import_nodes WHERE label = ?", ("车间新增护罩",)).fetchone()[0]
    check(
        "单独清洗、抽取、预演和入图接口仍可用",
        separate_clean.status_code == 200
        and separate_clean.json()["processing_status"] == "cleaned"
        and separate_extract.status_code == 200
        and separate_extract.json()["extraction_status"] == "extracted"
        and separate_preview.status_code == 200
        and separate_preview.json()["status"] == "previewed"
        and separate_apply.status_code == 200
        and separate_apply.json()["imported_node_count"] == 0
        and separate_nodes == 2,
        separate_apply.text[:300],
    )

    saved = save_upload("过期预演.txt", "棒线主轧机电机包含过期护罩".encode())
    os.environ["KNOWLEDGE_PIPELINE_PAUSE_STAGE"] = "importing"
    pipeline_pause.clear()
    holder: dict = {}

    def run_stale() -> None:
        holder["result"] = process_knowledge_file(saved["file_id"])

    thread = threading.Thread(target=run_stale)
    thread.start()
    reached = wait_stage(saved["file_id"], "importing")
    stale_preview = TEMP / "previews" / f"{saved['file_id']}.json"
    if stale_preview.is_file():
        preview_body = json.loads(stale_preview.read_text(encoding="utf-8"))
        preview_body["graph_fingerprint"]["sha256"] = "0" * 64
        stale_preview.write_text(json.dumps(preview_body, ensure_ascii=False), encoding="utf-8")
    pipeline_pause.set()
    thread.join(25)
    os.environ.pop("KNOWLEDGE_PIPELINE_PAUSE_STAGE", None)
    stale_result = holder.get("result") or {}
    stale_nodes = sqlite3.connect(os.environ["KNOWLEDGE_GRAPH_IMPORTS_DB"]).execute(
        "SELECT COUNT(*) FROM import_nodes WHERE label = ?",
        ("过期护罩",),
    ).fetchone()[0]
    check(
        "过期预演被拒绝，已完成阶段仍保留",
        reached
        and stale_result.get("pipeline", {}).get("pipeline_status") == "failed"
        and stale_result.get("pipeline", {}).get("failure_stage") == "importing"
        and stale_result.get("pipeline", {}).get("status_code") == 409
        and "重新预演" in stale_result.get("pipeline", {}).get("error_message", "")
        and stale_nodes == 0
        and (TEMP / "uploads" / saved["file_id"]).is_file()
        and (TEMP / "cleaned" / f"{saved['file_id']}.json").is_file()
        and (TEMP / "extracted" / f"{saved['file_id']}.json").is_file(),
        json.dumps(stale_result.get("pipeline", {}), ensure_ascii=False)[:500],
    )

    os.environ["KNOWLEDGE_IMPORT_INJECT_ERROR"] = "1"
    injected = upload(client, "事务失败.txt", "棒线主轧机电机包含事务护罩".encode())
    os.environ.pop("KNOWLEDGE_IMPORT_INJECT_ERROR", None)
    injected_nodes = sqlite3.connect(os.environ["KNOWLEDGE_GRAPH_IMPORTS_DB"]).execute(
        "SELECT COUNT(*) FROM import_nodes WHERE label = ?",
        ("事务护罩",),
    ).fetchone()[0]
    kept_nodes = sqlite3.connect(os.environ["KNOWLEDGE_GRAPH_IMPORTS_DB"]).execute(
        "SELECT COUNT(*) FROM import_nodes WHERE label = ?",
        ("车间新增护罩",),
    ).fetchone()[0]
    check(
        "入图事务失败不会留下不完整节点",
        injected.status_code == 500
        and injected.json()["pipeline"]["failure_stage"] == "importing"
        and "不完整" in injected.json()["pipeline"]["error_message"]
        and injected_nodes == 0
        and kept_nodes == 2
        and (TEMP / "cleaned" / f"{injected.json()['file_id']}.json").is_file(),
        injected.text[:400],
    )

    concurrent_saved = save_upload("并发.txt", "棒线主轧机电机包含并发护罩".encode())
    os.environ["KNOWLEDGE_PIPELINE_PAUSE_STAGE"] = "extracting"
    pipeline_pause.clear()
    concurrent_holder: dict = {}

    def run_concurrent() -> None:
        concurrent_holder["result"] = process_knowledge_file(concurrent_saved["file_id"])

    concurrent_thread = threading.Thread(target=run_concurrent)
    concurrent_thread.start()
    concurrent_reached = wait_stage(concurrent_saved["file_id"], "extracting")
    concurrent_error = None
    try:
        process_knowledge_file(concurrent_saved["file_id"])
    except KnowledgeFileError as exc:
        concurrent_error = exc
    pipeline_pause.set()
    concurrent_thread.join(25)
    os.environ.pop("KNOWLEDGE_PIPELINE_PAUSE_STAGE", None)
    check(
        "同一文件不能同时跑两遍流程",
        concurrent_reached
        and concurrent_error is not None
        and concurrent_error.status_code == 409
        and "不能同时" in concurrent_error.message
        and concurrent_holder.get("result", {}).get("pipeline", {}).get("pipeline_status") == "completed",
        "" if concurrent_error is None else concurrent_error.message,
    )

    missing = client.post("/api/knowledge/files/abcdefabcdefabcdefabcdefabcdefab/process")
    with_path = client.post(
        f"/api/knowledge/files/{file_id}/process",
        json={"path": r"D:\工作\steel-diagnosis-agent\data\processed\graph\graph.json"},
    )
    check(
        "不存在的文件不能处理，请求里的路径不会被当成服务器文件",
        missing.status_code == 404
        and with_path.status_code == 200
        and with_path.json()["file_id"] == file_id
        and file_hash(GRAPH) == before["graph"],
    )

    final_graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    official_after = {path.name: file_hash(path) for path in OFFICIAL.glob("*.docx") if not path.name.startswith("~$")}
    check(
        "基础图谱文件仍是 252 个节点和 464 条关系",
        len(final_graph["nodes"]) == 252
        and len(final_graph["edges"]) == 464
        and file_hash(GRAPH) == before["graph"],
    )
    check(
        "官方资料、Excel、监测库和正式运行目录没有变化",
        official_after == before["official"]
        and file_hash(EXCEL) == EXPECTED_XLSX
        and (file_hash(SQLITE) if SQLITE.is_file() else None) == before["sqlite"]
        and [path.exists() for path in DEFAULTS] == before["defaults"],
    )
    print("PIPELINE_RESULT")
    print(json.dumps({
        "pipeline_status": pipeline.get("pipeline_status"),
        "outcome": pipeline.get("outcome"),
        "message": pipeline.get("message"),
        "counts": counts,
        "import_status": imported_body.get("status"),
        "applied": imported_body.get("applied"),
    }, ensure_ascii=False))
    print(f"{sum(CHECKS)}/{len(CHECKS)} passed")
    if not all(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
