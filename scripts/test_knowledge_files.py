"""验收知识文件目录。上传使用临时目录和临时数据库。"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

TEMP = Path(tempfile.mkdtemp(prefix="knowledge-files-"))
os.environ["KNOWLEDGE_FILES_DB"] = str(TEMP / "files.sqlite3")
os.environ["KNOWLEDGE_UPLOAD_DIR"] = str(TEMP / "uploads")
os.environ["KNOWLEDGE_CLEANED_DIR"] = str(TEMP / "cleaned")
os.environ["KNOWLEDGE_EXTRACTED_DIR"] = str(TEMP / "extracted")
os.environ["KNOWLEDGE_IMPORT_PREVIEW_DIR"] = str(TEMP / "previews")
os.environ["KNOWLEDGE_GRAPH_IMPORTS_DB"] = str(TEMP / "imports.sqlite3")
os.environ["STARTUP_TOOL_SELFTEST"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.knowledge_file_store import MAX_UPLOAD_BYTES, KnowledgeFileError, save_upload  # noqa: E402

CHECKS: list[bool] = []
EXCEL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
GRAPH = ROOT / "data" / "processed" / "graph" / "graph.json"
SQLITE = ROOT / "data" / "runtime" / "monitoring_events.sqlite3"
DEFAULT_PATHS = (
    ROOT / "data" / "runtime" / "knowledge_files.sqlite3",
    ROOT / "data" / "runtime" / "knowledge_uploads",
    ROOT / "data" / "runtime" / "knowledge_cleaned",
    ROOT / "data" / "runtime" / "knowledge_extracted",
    ROOT / "data" / "runtime" / "knowledge_import_previews",
    ROOT / "data" / "runtime" / "knowledge_graph_imports.sqlite3",
)
EXPECTED_XLSX = "1e9caeba1fe526f3ad1b5dd6445a9dbcc02fa22ec02273d9863ebc84e3e30f87"
OFFICIAL = ROOT / "data" / "official"
FORBIDDEN = ("清洗成功", "已加入图谱", "stored_name", "source_path")


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append(bool(ok))
    print(("PASS" if ok else "FAIL"), name, detail)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def office_bytes(marker: str) -> bytes:
    buffer = io.BytesIO()
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        f'<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.{marker}.document.main+xml"/>'
        "</Types>"
    )
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", xml)
    return buffer.getvalue()


def main() -> None:
    before = {
        "excel": file_hash(EXCEL),
        "graph": file_hash(GRAPH),
        "sqlite": file_hash(SQLITE) if SQLITE.is_file() else None,
        "official": {path.name: file_hash(path) for path in OFFICIAL.glob("*.docx") if not path.name.startswith("~$")},
        "defaults": [path.exists() for path in DEFAULT_PATHS],
    }
    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    modules = ast.parse((BACKEND / "app" / "services" / "knowledge_file_store.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(modules):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.update(node.module.split("."))
    banned = imported & {"deepseek", "requests", "httpx", "monitoring_events", "anomaly_detection", "diagnostic_bridge"}
    check("文件模块没有导入 DeepSeek、监测或异常检测", not banned, str(sorted(banned)))
    check("单文件上限是 50MB", MAX_UPLOAD_BYTES == 50 * 1024 * 1024)

    client = TestClient(app)
    listed = client.get("/api/knowledge/files")
    body = listed.json()
    names = [item["original_filename"] for item in body["files"] if item["source_type"] == "existing_knowledge"]
    disk_names = sorted(path.name for path in OFFICIAL.glob("*.docx") if not path.name.startswith("~$"))
    check("接口返回六份实际存在的原有资料", listed.status_code == 200 and names == disk_names and len(names) == 6, str(names))
    check(
        "原有资料没有被标成待清洗或已入图",
        all(item["processing_status"] == "official_catalog" and item["in_graph"] is False for item in body["files"] if item["source_type"] == "existing_knowledge"),
    )

    chinese = "轴承磨损记录.txt".encode()
    payload = "这是一条待清洗的维修记录。".encode()
    uploaded = client.post(
        "/api/knowledge/files",
        params={"filename": "轴承磨损记录.txt"},
        content=payload,
        headers={"Content-Type": "application/octet-stream"},
    )
    saved = uploaded.json()
    text = uploaded.text
    check("中文文件名上传成功并返回 file_id", uploaded.status_code == 200 and isinstance(saved.get("file_id"), str) and len(saved["file_id"]) == 32, saved.get("file_id", ""))
    check("文本上传后完成清洗且未入图", saved.get("processing_status") == "cleaned" and saved.get("in_graph") is False and saved.get("participates_in_qa") is False)
    check("响应没有清洗成功、已入图或磁盘路径", uploaded.status_code == 200 and all(word not in text for word in FORBIDDEN) and "knowledge_uploads" not in text)
    stored = TEMP / "uploads" / saved["file_id"]
    check("磁盘文件使用标识名且内容一致", stored.is_file() and stored.read_bytes() == payload and "轴承" not in stored.name)

    again = client.get("/api/knowledge/files")
    found = [item for item in again.json()["files"] if item["file_id"] == saved["file_id"]]
    check("文件列表能查到新文件和中文名", len(found) == 1 and found[0]["original_filename"] == "轴承磨损记录.txt" and found[0]["extension"] == "txt")

    connection = sqlite3.connect(os.environ["KNOWLEDGE_FILES_DB"])
    row = connection.execute("SELECT original_filename, processing_status FROM knowledge_files WHERE file_id = ?", (saved["file_id"],)).fetchone()
    connection.close()
    check("重新打开数据库后记录仍在", row == ("轴承磨损记录.txt", "cleaned"), str(row))

    too_big = client.post(
        "/api/knowledge/files",
        params={"filename": "too-big.txt"},
        content=b"x",
        headers={"Content-Type": "application/octet-stream", "Content-Length": str(MAX_UPLOAD_BYTES + 1)},
    )
    rejected_size = False
    try:
        save_upload("too-big.txt", b"x" * (MAX_UPLOAD_BYTES + 1))
    except KnowledgeFileError as exc:
        rejected_size = exc.status_code == 413 and "50MB" in exc.message
    check("超过 50MB 被拒绝", rejected_size and (too_big.status_code in {200, 413}), f"header={too_big.status_code} body={rejected_size}")
    traversal = client.post(
        "/api/knowledge/files",
        params={"filename": "../secret.txt"},
        content=b"no",
        headers={"Content-Type": "application/octet-stream"},
    )
    executable = client.post(
        "/api/knowledge/files",
        params={"filename": "tool.exe"},
        content=b"MZ",
        headers={"Content-Type": "application/octet-stream"},
    )
    disguised = client.post(
        "/api/knowledge/files",
        params={"filename": "notes.exe.txt"},
        content="文本".encode(),
        headers={"Content-Type": "application/octet-stream"},
    )
    fake_pdf = client.post(
        "/api/knowledge/files",
        params={"filename": "manual.pdf"},
        content=b"MZ\x90\x00",
        headers={"Content-Type": "application/octet-stream"},
    )
    bad_json = client.post(
        "/api/knowledge/files",
        params={"filename": "bad.json"},
        content=b"{",
        headers={"Content-Type": "application/octet-stream"},
    )
    good_pdf = client.post(
        "/api/knowledge/files",
        params={"filename": "说明.pdf"},
        content=b"%PDF-1.4\n",
        headers={"Content-Type": "application/octet-stream"},
    )
    good_docx = client.post(
        "/api/knowledge/files",
        params={"filename": "规程.docx"},
        content=office_bytes("wordprocessingml"),
        headers={"Content-Type": "application/octet-stream"},
    )
    wrong_xlsx = client.post(
        "/api/knowledge/files",
        params={"filename": "表.xlsx"},
        content=office_bytes("wordprocessingml"),
        headers={"Content-Type": "application/octet-stream"},
    )
    check("路径穿越被拒绝", traversal.status_code == 422 and "路径" in traversal.json()["message"], traversal.text)
    check("可执行文件和伪装扩展名被拒绝", executable.status_code == 422 and disguised.status_code == 422, f"{executable.status_code}/{disguised.status_code}")
    check("内容和扩展名不一致被拒绝", fake_pdf.status_code == 422 and bad_json.status_code == 422 and wrong_xlsx.status_code == 422)
    check(
        "PDF 和无法解析的 DOCX 已保存但没有伪造成功",
        good_pdf.status_code == 200
        and good_docx.status_code == 200
        and good_pdf.json()["processing_status"] == "cleaning_failed"
        and "暂未完成" in good_pdf.json()["error_message"]
        and good_docx.json()["processing_status"] == "cleaning_failed"
        and good_pdf.json()["in_graph"] is False,
    )
    check("用户上传不会变成原有知识库", all(item["source_type"] == "user_upload" for item in (good_pdf.json(), good_docx.json(), saved)))

    final_graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    check("图谱仍是 252 个节点和 464 条关系", len(final_graph["nodes"]) == 252 and len(final_graph["edges"]) == 464 and len(graph["nodes"]) == 252)
    after_official = {path.name: file_hash(path) for path in OFFICIAL.glob("*.docx") if not path.name.startswith("~$")}
    check("官方 Word 和 Excel 没有变化", before["official"] == after_official and file_hash(EXCEL) == EXPECTED_XLSX and file_hash(GRAPH) == before["graph"])
    check("监测事件库没有被写入", (file_hash(SQLITE) if SQLITE.is_file() else None) == before["sqlite"])
    check(
        "测试没有创建正式上传目录或数据库",
        [path.exists() for path in DEFAULT_PATHS] == before["defaults"],
    )
    listed_text = again.text + uploaded.text
    check("列表和上传结果都没有待清洗之外的成功入图表述", "已加入图谱" not in listed_text and "清洗成功" not in listed_text)

    print(f"{sum(CHECKS)}/{len(CHECKS)} passed")
    if not all(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
