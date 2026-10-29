"""验收知识文件树。运行目录使用临时路径，不改正式图谱。"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

TEMP = Path(tempfile.mkdtemp(prefix="knowledge-tree-"))
os.environ["KNOWLEDGE_FILES_DB"] = str(TEMP / "files.sqlite3")
os.environ["KNOWLEDGE_UPLOAD_DIR"] = str(TEMP / "uploads")
os.environ["KNOWLEDGE_CLEANED_DIR"] = str(TEMP / "cleaned")
os.environ["KNOWLEDGE_EXTRACTED_DIR"] = str(TEMP / "extracted")
os.environ["KNOWLEDGE_IMPORT_PREVIEW_DIR"] = str(TEMP / "previews")
os.environ["KNOWLEDGE_GRAPH_IMPORTS_DB"] = str(TEMP / "imports.sqlite3")
os.environ["STARTUP_TOOL_SELFTEST"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

CHECKS: list[bool] = []
GRAPH = ROOT / "data" / "processed" / "graph" / "graph.json"
ANSWER_LABELS = ("设备状态", "故障类型", "故障严重度", "故障位置", "诊断置信度", "频谱描述", "根因分析", "建议措施", "优先级", "影响程度")
PAGE = ROOT / "frontend" / "src" / "components" / "KnowledgeFileDirectory.tsx"


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append(bool(ok))
    print(("PASS" if ok else "FAIL"), name, detail)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def walk(nodes: list[dict]) -> list[dict]:
    found = []
    for node in nodes:
        found.append(node)
        found.extend(walk(node.get("children") or []))
    return found


def main() -> None:
    before = file_hash(GRAPH)
    upload_dir = TEMP / "uploads"
    upload_dir.mkdir()
    (upload_dir / ".env").write_text("DEEPSEEK_API_KEY=sk-should-not-appear", encoding="utf-8")
    (upload_dir / "research_release_grants.json").write_text("{}", encoding="utf-8")
    (upload_dir / "notes.sqlite3").write_bytes(b"sqlite")
    client = TestClient(app)
    files = client.get("/api/knowledge/files")
    tree = client.get("/api/knowledge/tree").json()
    nodes = walk(tree["roots"])
    paths = [item["path"] for item in nodes]
    names = {item["name"] for item in nodes}
    page = PAGE.read_text(encoding="utf-8")
    check(
        "原有文件接口存在，失败时不再把空目录说成没有资料",
        files.status_code == 200
        and len(files.json()["files"]) >= 6
        and "/api/knowledge/tree" in page
        and "没有读到原有知识资料" not in page
        and "未找到该接口" not in page,
        f"files={files.status_code}",
    )
    check(
        "白名单目录按实际结构列出",
        {"data", "data/official", "data/official_dataset", "data/processed", "data/processed/graph", "data/processed/markdown", "data/processed/rag"} <= set(paths)
        and sum(item.startswith("data/official/") and item.endswith(".docx") for item in paths) == 6
        and any(item.startswith("data/official_dataset/") and item.endswith(".xlsx") for item in paths)
        and "data/processed/graph/graph.json" in paths
        and "data/processed/manifest.json" in paths
        and "data/processed/rag/index.json" in paths
        and sum(item.startswith("data/processed/markdown/") and item.endswith(".md") for item in paths) == 6,
        "、".join(item["path"] for item in tree["roots"]),
    )
    check(
        "敏感文件和目录外路径不会出现",
        ".env" not in names
        and "research_release_grants.json" not in names
        and not any(item.endswith(".sqlite3") for item in paths)
        and "sk-should-not-appear" not in json.dumps(tree, ensure_ascii=False),
    )
    escaped = client.get("/api/knowledge/tree/preview", params={"path": "data/official/../../.env"})
    missing = client.get("/api/knowledge/tree/preview", params={"path": "data/official/不存在的文件.txt"})
    check(
        "越界路径被拒绝，缺少文件不会说成接口不存在",
        escaped.status_code == 400
        and escaped.json()["message"] == "路径不在知识目录范围内。"
        and missing.status_code == 404
        and missing.json()["message"] == "没有找到这个知识文件。",
        escaped.json()["message"],
    )
    markdown = next(item for item in paths if item.startswith("data/processed/markdown/") and item.endswith(".md"))
    preview = client.get("/api/knowledge/tree/preview", params={"path": markdown}).json()
    graph_preview = client.get("/api/knowledge/tree/preview", params={"path": "data/processed/graph/graph.json"}).json()
    docx = next(item for item in paths if item.startswith("data/official/") and item.endswith(".docx"))
    docx_preview = client.get("/api/knowledge/tree/preview", params={"path": docx}).json()
    workbook = next(item for item in paths if item.startswith("data/official_dataset/") and item.endswith(".xlsx"))
    sheet = client.get("/api/knowledge/tree/preview", params={"path": workbook}).json()
    sheet_text = json.dumps(sheet, ensure_ascii=False)
    check(
        "Markdown 能预览，Excel 只返回元信息",
        preview["preview_kind"] == "text"
        and len(preview["text"]) > 20
        and sheet["preview_kind"] == "metadata"
        and sheet["text"] == ""
        and "不读取表内观测内容" in sheet["notice"]
        and all(label not in sheet_text for label in ANSWER_LABELS)
        and graph_preview["preview_kind"] == "text"
        and graph_preview["truncated"] is True
        and '"node_count": 252' in graph_preview["text"]
        and '"edge_count": 464' in graph_preview["text"]
        and len(graph_preview["text"]) < 8000
        and docx_preview["preview_kind"] == "text"
        and len(docx_preview["text"]) > 20,
        markdown,
    )
    uploaded = client.post(
        "/api/knowledge/files",
        params={"filename": "目录测试.txt"},
        content="这是目录树测试记录。".encode("utf-8"),
        headers={"Content-Type": "application/octet-stream"},
    )
    uploaded_body = uploaded.json()
    file_id = uploaded_body.get("file_id", "")
    refreshed = client.get("/api/knowledge/tree").json()
    refreshed_paths = [item["path"] for item in walk(refreshed["roots"])]
    blocked = client.post(
        "/api/knowledge/files",
        params={"filename": "不能清洗.pdf"},
        content=b"%PDF-1.4\n",
        headers={"Content-Type": "application/octet-stream"},
    )
    blocked_body = blocked.json()
    after_blocked = [item["path"] for item in walk(client.get("/api/knowledge/tree").json()["roots"])]
    check(
        "上传后能在上传和清洗目录看到结果，不能清洗的文件没有入图",
        uploaded.status_code == 200
        and f"data/runtime/knowledge_uploads/{file_id}" in refreshed_paths
        and f"data/runtime/knowledge_cleaned/{file_id}.json" in refreshed_paths
        and uploaded_body["in_graph"] is False
        and blocked.status_code in {200, 422}
        and blocked_body["pipeline"]["pipeline_status"] == "failed"
        and blocked_body["pipeline"]["knowledge_added"] is False
        and blocked_body["in_graph"] is False
        and f"data/runtime/knowledge_cleaned/{blocked_body['file_id']}.json" not in after_blocked,
        file_id,
    )
    text_preview = client.get("/api/knowledge/tree/preview", params={"path": f"data/runtime/knowledge_uploads/{file_id}"}).json()
    check(
        "上传文本可以预览且对应原始文件名",
        "这是目录树测试记录。" in text_preview["text"] and text_preview["related_filename"] == "目录测试.txt",
        text_preview.get("notice", ""),
    )
    check(
        "正式图谱没有被改写",
        file_hash(GRAPH) == before and len(json.loads(GRAPH.read_text(encoding="utf-8"))["nodes"]) == 252 and len(json.loads(GRAPH.read_text(encoding="utf-8"))["edges"]) == 464,
    )
    print("TREE_ROOTS")
    print("、".join(item["path"] for item in tree["roots"]))
    print(f"{sum(CHECKS)}/{len(CHECKS)} passed")
    if not all(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
