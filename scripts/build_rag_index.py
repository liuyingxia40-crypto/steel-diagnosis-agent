"""从本地 Markdown 生成知识检索索引。

不修改 Markdown、知识图谱和 Word，不访问网络。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.knowledge_search import DOCUMENTS, MARKDOWN_DIR, INDEX_PATH, write_index


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    before = {name: sha256(MARKDOWN_DIR / name) for name in DOCUMENTS}
    graph = ROOT / "data" / "processed" / "graph" / "graph.json"
    graph_before = sha256(graph) if graph.is_file() else ""
    payload = write_index()
    after = {name: sha256(MARKDOWN_DIR / name) for name in DOCUMENTS}
    graph_after = sha256(graph) if graph.is_file() else ""
    if before != after or graph_before != graph_after:
        raise RuntimeError("生成索引时改动了 Markdown 或正式图谱。")
    print(f"chunks={payload['chunk_count']}")
    print(f"index={INDEX_PATH}")


if __name__ == "__main__":
    main()
