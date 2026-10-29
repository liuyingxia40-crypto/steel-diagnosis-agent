"""用模拟工具结果验证复合故障假设。不调用真实 DeepSeek，不写正式图谱。"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["STARTUP_TOOL_SELFTEST"] = "0"
os.environ["MONITORING_EVENTS_DB"] = str(Path(tempfile.mkdtemp(prefix="fault-hypothesis-")) / "events.sqlite3")
os.environ["SUPERVISOR_TRACE_DB"] = str(Path(tempfile.mkdtemp(prefix="fault-hypothesis-trace-")) / "traces.sqlite3")

from app.main import _published_fault_answer
from app.services.fault_hypothesis import assess_fault_hypotheses

GRAPH = ROOT / "data" / "processed" / "graph" / "graph.json"
EXCEL = ROOT / "data" / "official_dataset" / "钢铁设备数据集_开发集.xlsx"
PRODUCTION_DB = ROOT / "data" / "runtime" / "monitoring_events.sqlite3"
ENV_FILE = ROOT / "backend" / ".env"
PROTECTED = (
    GRAPH,
    EXCEL,
    PRODUCTION_DB,
    ENV_FILE,
    ROOT / "backend" / "app" / "services" / "knowledge_physical_validator.py",
    ROOT / "backend" / "app" / "services" / "agent_tools.py",
)
RESULTS: list[bool] = []


def file_hash(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(name: str, passed: bool, detail: str = "") -> None:
    RESULTS.append(passed)
    print(("PASS " if passed else "FAIL ") + name + ("" if not detail else " | " + detail))


def tool(name: str, result: dict) -> dict:
    return {"id": name, "name": name, "result": result}


def relation(source: str, kind: str, target: str) -> dict:
    return {"source_name": source, "relation": kind, "target_name": target, "source_id": "s", "target_id": "t"}


def diagnosis(*faults: str) -> dict:
    return {
        "ok": True,
        "tool": "diagnose_fault",
        "status": "possible_match",
        "confirmed": False,
        "equipment_name": None,
        "matches": [
            {
                "fault": fault,
                "confirmed": False,
                "matched_features": [{"document": "KB-02.md", "locator": f"第{index}行", "section": fault}],
            }
            for index, fault in enumerate(faults, start=1)
        ],
    }


def relations() -> dict:
    return {
        "ok": True,
        "tool": "get_equipment_relations",
        "ambiguous": False,
        "count": 4,
        "node": {"name": "棒线主轧机电机"},
        "relations": [
            relation("棒线主轧机电机", "HAS_COMPONENT", "驱动端轴承"),
            relation("棒线主轧机电机", "HAS_COMPONENT", "非驱动端轴承"),
            relation("棒线主轧机电机", "CONNECTS_TO", "棒线联轴器"),
            relation("棒线主轧机电机", "SUPPORTED_BY", "KB-01_设备知识图谱.docx"),
        ],
    }


def search() -> dict:
    return {
        "ok": True,
        "count": 1,
        "results": [{"document": "KB-02.md", "locator": "第3行", "section": "轴承", "text": "临时证据"}],
    }


def test_single_fault() -> None:
    report = assess_fault_hypotheses("这台设备的可能故障是什么？", [tool("search_knowledge", search()), tool("get_equipment_relations", relations()), tool("diagnose_fault", diagnosis("轴承磨损"))])
    hypothesis = report["hypotheses"][0]
    sources = {item["source"] for item in hypothesis["supporting_evidence"]}
    check(
        "单一故障与互补证据",
        len(report["hypotheses"]) == 1
        and hypothesis["compound"] is False
        and hypothesis["faults"] == ["轴承磨损"]
        and hypothesis["equipment"] == ["棒线主轧机电机"]
        and hypothesis["verification_status"] == "未验证"
        and hypothesis["written_to_graph"] is False
        and {"search_knowledge", "get_equipment_relations", "diagnose_fault"} <= sources
        and "KB-02.md 第1行" in hypothesis["source_locators"]
        and report["counts"]["相邻关系"] == 4
        and report["counts"]["部件"] == 2,
    )


def test_compound_fault() -> None:
    report = assess_fault_hypotheses("请判断是否存在复合故障。", [tool("diagnose_fault", diagnosis("轴承磨损", "润滑不良")), tool("get_equipment_relations", relations())])
    compound = report["hypotheses"][-1]
    check(
        "复合故障假设",
        len(report["hypotheses"]) == 3
        and compound["hypothesis_id"] == "C1"
        and compound["compound"] is True
        and compound["faults"] == ["轴承磨损", "润滑不良"]
        and compound["causal_conclusion"] is False
        and "不是因果链" in compound["basis"]
        and "未验证假设：3 条" in report["public_answer"],
    )


def test_support_and_opposition() -> None:
    anomaly = {"ok": True, "tool": "detect_anomaly", "record_id": "REC-TEMP-1", "detection_status": "未发现显著偏离"}
    report = assess_fault_hypotheses("观测到振动后，可能故障是什么？", [tool("diagnose_fault", diagnosis("轴承磨损")), tool("detect_anomaly", anomaly), tool("get_equipment_relations", relations())])
    hypothesis = report["hypotheses"][0]
    check(
        "支持证据与反对证据",
        hypothesis["supporting_evidence"]
        and hypothesis["opposing_evidence"]
        and hypothesis["verification_status"] == "待核查"
        and hypothesis["credibility"] == "降低"
        and any("未发现显著偏离" in item["detail"] for item in hypothesis["opposing_evidence"]),
    )


def test_ambiguous_names() -> None:
    called = []

    def physics(entity_a: str, entity_b: str, record_id: str) -> dict:
        called.append((entity_a, entity_b, record_id))
        return {}

    ambiguous = {
        "ok": True,
        "ambiguous": True,
        "count": 0,
        "relations": [],
        "candidates": [{"name": "驱动端轴承/外圈", "id": "A"}, {"name": "非驱动端轴承/外圈", "id": "B"}],
        "message": "找到 2 个可能的节点。",
    }
    anomaly = {"ok": True, "record_id": "REC-TEMP-2", "detection_status": "偏离基线"}
    report = assess_fault_hypotheses(
        "同名部件的根因可能是什么故障？",
        [tool("get_equipment_relations", ambiguous), tool("diagnose_fault", diagnosis("轴承磨损")), tool("detect_anomaly", anomaly)],
        physics=physics,
    )
    hypothesis = report["hypotheses"][0]
    check(
        "同名部件归属不明确",
        called == []
        and hypothesis["equipment"] == []
        and hypothesis["components"] == []
        and "驱动端轴承/外圈" not in hypothesis["equipment"]
        and hypothesis["verification_status"] == "待核查"
        and any("同名" in item for item in report["insufficient_evidence"]),
    )


def test_missing_measurements() -> None:
    analysis = {
        "ok": True,
        "record_id": "REC-TEMP-3",
        "diagnosis_level": "diagnostic_clue",
        "confirmed": False,
        "candidate_faults": [],
        "missing_data": ["相位", "2X幅值"],
        "detection_status": "偏离基线",
    }
    report = assess_fault_hypotheses("请给出这条记录的故障原因。", [tool("analyze_equipment_record", analysis)])
    text = report["public_answer"]
    check(
        "缺少测量条件",
        report["hypotheses"] == []
        and "无法判断" in text
        and any("相位" in item for item in report["missing_capabilities"])
        and any("2X幅值" in item for item in report["missing_capabilities"])
        and "3.017" not in text,
    )


def test_physical_contradiction() -> None:
    seen = []

    def physics(entity_a: str, entity_b: str, record_id: str) -> dict:
        seen.append((entity_a, entity_b, record_id))
        return {
            "overall_status": "numeric_contradiction",
            "contradicted_checks": ["speed_frequency"],
            "physical_checks": [{"check_id": "speed_frequency", "title": "转速与转频一致性", "status": "contradicted"}],
            "missing_measurements": ["相位"],
            "causal_conclusion": False,
            "written_to_graph": False,
        }

    anomaly = {"ok": True, "record_id": "REC-TEMP-4", "detection_status": "偏离基线"}
    report = assess_fault_hypotheses(
        "请判断复合故障是否成立。",
        [tool("get_equipment_relations", relations()), tool("diagnose_fault", diagnosis("轴承磨损")), tool("detect_anomaly", anomaly)],
        physics=physics,
    )
    hypothesis = report["hypotheses"][0]
    check(
        "物理约束矛盾",
        seen == [("棒线主轧机电机", "轴承磨损", "REC-TEMP-4")]
        and hypothesis["credibility"] == "降低"
        and hypothesis["verification_status"] == "待核查"
        and any("转速与转频一致性" in item["detail"] for item in hypothesis["opposing_evidence"])
        and "相位" in hypothesis["missing_measurements"]
        and report["physics"]["called"] is True,
    )


def test_no_knowledge() -> None:
    report = assess_fault_hypotheses(
        "齿根裂纹是不是复合故障的根因？",
        [
            tool("search_knowledge", {"ok": True, "count": 0, "results": []}),
            tool("diagnose_fault", {"ok": True, "status": "insufficient", "confirmed": False, "matches": [], "message": "证据不足，不能诊断。"}),
            tool("get_equipment_relations", {"ok": True, "ambiguous": False, "count": 0, "node": None, "relations": [], "message": "没有找到节点。"}),
        ],
        model_answer="因果链已经证实：齿根裂纹导致停机。",
    )
    check(
        "没有相关知识时拒绝编造",
        report["hypotheses"] == []
        and "无法判断" in report["public_answer"]
        and "齿根裂纹" not in report["public_answer"]
        and "已经证实" not in report["public_answer"]
        and report["written_to_graph"] is False,
    )


def test_model_cause_not_written() -> None:
    report = assess_fault_hypotheses(
        "请判断可能故障。",
        [tool("diagnose_fault", diagnosis("轴承磨损"))],
        model_answer="因果链：齿根裂纹导致轴承磨损，应写入图谱。",
    )
    names = [fault for item in report["hypotheses"] for fault in item["faults"]]
    check(
        "模型因果假设不进入图谱",
        names == ["轴承磨损"]
        and "齿根裂纹" not in names
        and all(item["written_to_graph"] is False for item in report["hypotheses"])
        and report["causal_conclusion"] is False
        and "应写入图谱" not in report["public_answer"],
    )


def test_counts_match_tools() -> None:
    report = assess_fault_hypotheses(
        "请总结可能故障和设备关系。",
        [tool("get_equipment_relations", relations()), tool("diagnose_fault", diagnosis("轴承磨损"))],
        model_answer="相邻关系共 9 条，部件共 3 个，假设共 4 条。",
    )
    answer = report["public_answer"]
    check(
        "最终回答数字与工具结果一致",
        report["consistency"]["ok"] is False
        and "相邻关系：4 条" in answer
        and "部件：2 个" in answer
        and "未验证假设：1 条" in answer
        and "共 9 条" not in answer
        and "共 3 个" not in answer
        and "共 4 条" not in answer,
    )


def test_chat_uses_checked_answer() -> None:
    report = assess_fault_hypotheses("可能故障是什么？", [tool("diagnose_fault", diagnosis("轴承磨损"))])
    published = _published_fault_answer({"answer": "共 9 条关系。", "fault_report": report})
    skipped = _published_fault_answer({"answer": "只查关系。", "fault_report": {"applicable": False, "public_answer": "不应使用"}})
    check("问答采用一致性答案", published.startswith("以下为未验证假设") and skipped == "")


def main() -> None:
    before = {path: file_hash(path) for path in PROTECTED}
    test_single_fault()
    test_compound_fault()
    test_support_and_opposition()
    test_ambiguous_names()
    test_missing_measurements()
    test_physical_contradiction()
    test_no_knowledge()
    test_model_cause_not_written()
    test_counts_match_tools()
    test_chat_uses_checked_answer()
    after = {path: file_hash(path) for path in PROTECTED}
    check("正式数据与 L2 实现未被修改", before == after)
    passed = sum(RESULTS)
    failed = len(RESULTS) - passed
    print(f"RESULT passed={passed} failed={failed}")
    raise SystemExit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
