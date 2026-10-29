"""读取本机知识图谱文件，供前端可视化使用。"""

from __future__ import annotations

import json
from pathlib import Path

GRAPH_FILE = Path(__file__).resolve().parents[3] / "data" / "processed" / "graph" / "graph.json"


class GraphFileError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def load_graph() -> dict[str, list]:
    if not GRAPH_FILE.is_file():
        raise GraphFileError(
            "未找到本地知识图谱文件 data/processed/graph/graph.json。",
            404,
        )
    try:
        payload = json.loads(GRAPH_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise GraphFileError("知识图谱文件不是有效的 JSON。", 500) from None
    except OSError:
        raise GraphFileError("读取知识图谱文件失败。", 500) from None
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("nodes"), list)
        or not isinstance(payload.get("edges"), list)
    ):
        raise GraphFileError("知识图谱文件缺少 nodes 或 edges。", 500)
    return {"nodes": payload["nodes"], "edges": payload["edges"]}
