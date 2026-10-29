"""历史记录检索接口测试。只读官方数据，不修改 Excel。"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
TEMP = Path(tempfile.mkdtemp(prefix="history-search-"))
os.environ["MONITORING_EVENTS_DB"] = str(TEMP / "events.sqlite3")
os.environ["STARTUP_TOOL_SELFTEST"] = "0"

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.official_dataset import search_records

EXCEL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
RESULTS: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append(bool(ok))
    print(("PASS" if ok else "FAIL"), name, detail)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    before = file_hash(EXCEL)
    mm = search_records(device_id="MM-01", page=1, page_size=20)
    check(
        "按 MM-01 能找到对应记录",
        mm["found"] is True and mm["returned_count"] == 20 and all(item["device_id"] == "MM-01" for item in mm["records"]),
        f"matched={mm['matched_count']} page={mm['page']}",
    )
    named = search_records(q="主轧机电机", page=1, page_size=20)
    check(
        "按主轧机电机能搜索设备",
        named["found"] is True and all("主轧机电机" in item["equipment_category"] for item in named["records"]),
        f"matched={named['matched_count']}",
    )
    target = search_records(q="REC-MA-20260403-00346", page=1, page_size=20)
    check(
        "不输入完整设备也能打开目标记录",
        target["found"] is True
        and any(item["record_id"] == "REC-MA-20260403-00346" for item in target["records"])
        and all(item.get("analysis_status") in {"尚未分析", "未分析", "偏离基线"} for item in target["records"]),
        [item["record_id"] for item in target["records"]],
    )
    page1 = search_records(device_id="MM-01", page=1, page_size=20)
    page2 = search_records(device_id="MM-01", page=2, page_size=20)
    ids1 = {item["record_id"] for item in page1["records"]}
    ids2 = {item["record_id"] for item in page2["records"]}
    check("分页不重叠", page1["returned_count"] == 20 and page2["returned_count"] == 20 and not ids1.intersection(ids2))
    timed = search_records(device_id="MM-01", start_time="2026-04-03 11:00:00", end_time="2026-04-03 12:00:00", page=1, page_size=20)
    check(
        "时间筛选正常",
        timed["found"] is True and all("2026-04-03 11:" in item["collected_at"] or "2026-04-03 12:" in item["collected_at"] for item in timed["records"]),
        timed["matched_count"],
    )
    same_time = [item for item in mm["records"] if item.get("same_timestamp_count", 1) > 1]
    if same_time:
        stamp = same_time[0]["collected_at"]
        group = [item for item in mm["records"] if item["collected_at"] == stamp]
        check("相同时间不同记录不合并", len({item["record_id"] for item in group}) == len(group) and len(group) >= 1, stamp)
    else:
        # force check around known duplicates if present in matched full set
        wide = search_records(device_id="MM-01", page=1, page_size=100)
        stamped = {}
        for item in wide["records"]:
            stamped.setdefault(item["collected_at"], []).append(item["record_id"])
        dup = next((ids for ids in stamped.values() if len(ids) > 1), None)
        check("相同时间不同记录不合并", dup is None or len(set(dup)) == len(dup), dup)
    unanalyzed = all(item.get("analysis_status") == "尚未分析" for item in mm["records"])
    check("尚未分析记录不标为正常", unanalyzed and all(item.get("analysis_status") != "正常" for item in mm["records"]))
    empty_status = search_records(analysis_status="no_significant_deviation", page=1, page_size=20)
    check(
        "未发现显著偏离筛选不伪造结果",
        empty_status["found"] is False and "尚未分析" in empty_status["status_filter_note"],
        empty_status["status_filter_note"][:40],
    )

    main_module.ask_with_tools = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no model"))
    main_module.ask = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no model"))
    client = TestClient(app)
    response = client.get("/api/dataset/records/search", params={"q": "MM-01", "page": 1, "page_size": 20})
    body = response.json()
    check(
        "HTTP 检索接口可用",
        response.status_code == 200 and body.get("returned_count", 0) > 0 and body["records"][0].get("device_name"),
        response.status_code,
    )
    obs = client.get("/api/dataset/observations/REC-MA-20260403-00346")
    check("目标观测仍可读取", obs.status_code == 200 and obs.json()["record"]["record_id"] == "REC-MA-20260403-00346")
    after = file_hash(EXCEL)
    check("正式 Excel 未变化", before == after, after)
    print("RESULT", f"passed={sum(1 for item in RESULTS if item)}", f"failed={sum(1 for item in RESULTS if not item)}")
    if not all(RESULTS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
