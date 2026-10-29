"""读取本机知识展示树，不改正式图谱。"""

from __future__ import annotations

import json
from pathlib import Path

VIEW_FILE = Path(__file__).resolve().parents[3] / "data" / "processed" / "graph" / "knowledge_view.json"


class ViewFileError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def load_view() -> dict:
    if not VIEW_FILE.is_file():
        raise ViewFileError(
            "未找到本地知识展示文件 data/processed/graph/knowledge_view.json。",
            404,
        )
    try:
        payload = json.loads(VIEW_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise ViewFileError("知识展示文件不是有效的 JSON。", 500) from None
    except OSError:
        raise ViewFileError("读取知识展示文件失败。", 500) from None
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("root_id"), str)
        or not isinstance(payload.get("nodes"), list)
    ):
        raise ViewFileError("知识展示文件缺少 root_id 或 nodes。", 500)
    return payload
