import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.services.deepseek import NO_EVIDENCE_ANSWER, DeepSeekError, ask, ask_with_tools
from app.services.diagnostic_bridge import analyze_record_for_diagnosis
from app.services.knowledge_cleaner import clean_uploaded_file, cleaned_preview
from app.services.knowledge_extractor import extract_knowledge, extracted_preview
from app.services.knowledge_graph_import_preview import import_preview, preview_knowledge_import
from app.services.knowledge_file_store import (
    MAX_UPLOAD_BYTES,
    KnowledgeFileError,
    list_knowledge_files,
    save_upload,
)
from app.services.knowledge_file_tree import list_knowledge_tree, preview_knowledge_file
from app.services.knowledge_graph import GraphFileError, load_graph
from app.services.knowledge_graph_import import apply_import, import_result, merge_imported_graph
from app.services.knowledge_pipeline import process_knowledge_file, process_result
from app.services.knowledge_physical_validator import validate_research_physics
from app.services.knowledge_reasoning_context import EntityIdentityError, find_cross_knowledge_evidence
from app.services.knowledge_research_reasoner import research_knowledge_relationship
from app.services.knowledge_search import SearchIndexError, search, tokenize
from app.services.knowledge_view import ViewFileError, load_view
from app.services.automatic_monitoring import (
    AutomaticMonitoringError,
    recover_abandoned_tasks,
    shutdown_automatic_monitoring,
    start_automatic_monitoring,
    stop_automatic_monitoring,
    task_status,
)
from app.services.monitoring_events import (
    MonitoringEventError,
    list_deviation_record_ids,
    list_monitoring_events,
    process_monitoring_record,
)
from app.services.diagnosis_history import DiagnosisHistoryError, compare_assessments, get_assessment, list_assessments
from app.services.fault_revision import reassess_record
from app.services.diagnostic_evidence import DiagnosticEvidenceError, list_evidence, register_evidence
from app.services.followup_tasks import FollowupTaskError, generate_followup_tasks, list_followup_tasks, sync_followup_tasks
from app.services.supplementary_measurement import (
    SupplementaryMeasurementError,
    attach_synchronous_csv,
    attach_vibration_csv,
    list_for_record,
    list_synchronous_for_record,
    revalidate_record,
)
from app.services.official_dataset import (
    MAX_LIMIT,
    DatasetError,
    get_dataset_summary,
    get_device_records,
    get_observation,
    list_devices,
    search_records,
)
from app.services.vibration_analysis import analyze_vibration
from app.services.vibration_csv import MAX_CSV_BYTES, VibrationCsvError, read_vibration_column


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """只在服务进程启动时做本地工具自检，不在导入模块或处理请求时执行。"""
    from app.services.startup_tool_selftest import run_startup_selftest

    recover_abandoned_tasks()
    run_startup_selftest()
    try:
        yield
    finally:
        shutdown_automatic_monitoring()


app = FastAPI(
    title="钢铁设备故障预测与根因诊断智能体",
    description="工业设备智能诊断原型系统的后端 API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class HealthResponse(BaseModel):
    status: str
    message: str


class SystemInfoResponse(BaseModel):
    project_name: str
    backend_status: str
    service: str
    version: str


class ErrorResponse(BaseModel):
    success: bool = False
    message: str


class ResearchEvidenceRequest(BaseModel):
    entity_a: str = Field(min_length=1, max_length=80)
    entity_b: str = Field(min_length=1, max_length=80)
    max_hops: int = Field(default=3, ge=1, le=3)
    entity_a_id: str | None = Field(default=None, max_length=128)
    entity_b_id: str | None = Field(default=None, max_length=128)
    entity_a_scope: str | None = Field(default=None, max_length=80)
    entity_b_scope: str | None = Field(default=None, max_length=80)

    @field_validator("entity_a", "entity_b")
    @classmethod
    def entity_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("请提供知识实体名称")
        return text

    @field_validator("entity_a_id", "entity_b_id", "entity_a_scope", "entity_b_scope")
    @classmethod
    def blank_identity_is_absent(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


class ResearchReasonRequest(ResearchEvidenceRequest):
    """研究推理接受名称、可选节点身份和跳数。额外字段不能授予外发权限。"""

    model_config = ConfigDict(extra="ignore")


class ResearchValidateRequest(BaseModel):
    """物理校验只接受两个名称、可选节点身份和可选记录编号。客户端不能提交校验结论。"""

    model_config = ConfigDict(extra="ignore")
    entity_a: str = Field(min_length=1, max_length=80)
    entity_b: str = Field(min_length=1, max_length=80)
    entity_a_id: str | None = Field(default=None, max_length=128)
    entity_b_id: str | None = Field(default=None, max_length=128)
    entity_a_scope: str | None = Field(default=None, max_length=80)
    entity_b_scope: str | None = Field(default=None, max_length=80)
    record_id: str | None = Field(default=None, max_length=80)

    @field_validator("entity_a", "entity_b")
    @classmethod
    def entity_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("请提供知识实体名称")
        return text

    @field_validator("entity_a_id", "entity_b_id", "entity_a_scope", "entity_b_scope", "record_id")
    @classmethod
    def empty_optional_is_absent(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)

    @field_validator("question")
    @classmethod
    def question_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("请输入问题")
        return text


class SourceItem(BaseModel):
    original_document: str
    document: str
    section: str
    locator: str


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceItem]


OFFICIAL_DIR = Path(__file__).resolve().parents[2] / "data" / "official"
UNCONFIRMED_DOCUMENT = "无法确认"
OFFICIAL_EXCEL_NAME = "钢铁设备数据集_开发集.xlsx"
_ANOMALY_STATUSES = {"偏离基线", "未发现显著偏离", "基线不足", "无法判定"}
_ANALYSIS_LEVELS = {
    "statistical_deviation": "统计偏离。这只说明指标相对历史基线的偏离，不是某种故障已经发生。",
    "diagnostic_clue": "诊断线索。下面只记录实际核对到的部分线索，不是 possible_match，也不能认定具体故障。",
    "possible_match": "可能匹配。这不是确诊。",
    "insufficient_evidence": "证据不足，不能形成可靠的故障候选。",
    "unable_to_evaluate": "无法评估。现有数据或工况不足以判断，不能写成设备正常。",
}
_RECORD_ID_PATTERN = re.compile(r"REC-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+")
_DATASET_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_ANALYSIS_NOTE = (
    "这是官方开发集历史记录的辅助分析，不是真实传感器实时监控，"
    "也不是故障确诊、故障概率、严重程度或正式报警。"
)
_ANSWER_LABELS = frozenset(
    {
        "设备状态",
        "故障类型",
        "故障严重度",
        "故障位置",
        "诊断置信度",
        "频谱描述",
        "根因分析",
        "建议措施",
        "优先级",
        "影响程度",
    }
)
_DEVICE_CODE_PATTERN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2,4}-\d{2}(?![A-Za-z0-9])")
_ANOMALY_MARKERS = ("异常", "偏离", "指标", "振动", "温度", "基线", "统计", "变化", "运行")
_FAULT_NAMES = ("轴承磨损", "转子不平衡", "齿轮异常", "润滑故障")
_OVERCLAIM_WORDS = ("已确诊", "确诊为", "可以确诊", "故障概率", "严重程度", "严重度", "正式报警", "已报警")
_NORMAL_CLAIMS = ("确认正常", "设备正常", "运行正常", "没有异常", "不存在异常", "未见异常")


def original_document(markdown_name: str) -> str:
    if not markdown_name.endswith(".md"):
        return UNCONFIRMED_DOCUMENT
    candidate = markdown_name[: -len(".md")] + ".docx"
    path = OFFICIAL_DIR / candidate
    if candidate.startswith("~$") or not path.is_file():
        return UNCONFIRMED_DOCUMENT
    return candidate


def source_item(hit: dict) -> SourceItem:
    document = str(hit.get("document", ""))
    return SourceItem(
        original_document=original_document(document),
        document=document,
        section=str(hit.get("section", "")),
        locator=str(hit.get("locator", "")),
    )


def confirmed_word_document(name: str) -> str:
    filename = Path(name).name
    if not filename.endswith(".docx") or filename.startswith("~$"):
        return UNCONFIRMED_DOCUMENT
    if not (OFFICIAL_DIR / filename).is_file():
        return UNCONFIRMED_DOCUMENT
    return filename


def graph_source_item(ref: dict) -> SourceItem:
    document = str(ref.get("document", ""))
    return SourceItem(
        original_document=confirmed_word_document(document),
        document=document,
        section=str(ref.get("section", "")),
        locator=str(ref.get("locator", "")),
    )


def _source_key(item: SourceItem) -> tuple[str, str, str, str]:
    return (item.original_document, item.document, item.section, item.locator)


def _append_source(
    sources: list[SourceItem],
    seen: set[tuple[str, str, str, str]],
    item: SourceItem,
) -> None:
    key = _source_key(item)
    if key in seen:
        return
    seen.add(key)
    sources.append(item)


_GENERIC_QUESTION_TOKENS = {
    "观测",
    "观察",
    "监测",
    "判断",
    "分析",
    "可能",
    "官方",
    "资料",
    "根据",
    "故障",
    "诊断",
    "特征",
    "什么",
    "哪些",
}


def question_tokens(question: str) -> list[str]:
    return [
        token
        for token in tokenize(question)
        if len(token) >= 2 and token not in _GENERIC_QUESTION_TOKENS
    ]


def mentions_question(text: str, question: str, tokens: list[str]) -> bool:
    if tokens:
        return any(token in text for token in tokens)
    stripped = question.strip()
    return len(stripped) >= 2 and stripped in text


def _fault_judgment(question: str) -> bool:
    text = question.strip()
    if any(marker in text for marker in ("观测到", "观察到", "监测到")):
        return True
    return "故障" in text and any(marker in text for marker in ("判断", "可能", "是什么", "哪种"))


def _diagnosis_features(result: dict, question: str, tokens: list[str]) -> list[dict]:
    if result.get("status") != "possible_match":
        return []
    matches = result.get("matches")
    if not isinstance(matches, list):
        return []
    found = []
    for match in matches:
        if not isinstance(match, dict) or match.get("confirmed") is True:
            continue
        fault = str(match.get("fault") or "").strip()
        features = match.get("matched_features")
        if not fault or not isinstance(features, list):
            continue
        for feature in features:
            if not isinstance(feature, dict):
                continue
            text = str(feature.get("text") or feature.get("official_text") or "").strip()
            document = str(feature.get("document") or "").strip()
            section = str(feature.get("section") or "").strip()
            locator = str(feature.get("locator") or "").strip()
            if not text or not document or not section or not locator:
                continue
            haystack = "\n".join(
                [fault, text, str(feature.get("description") or ""), str(feature.get("code") or "")]
            )
            observed = feature.get("observed")
            quoted = False
            if isinstance(observed, list):
                quoted = any(
                    isinstance(item, str) and len(item.strip()) >= 4 and item.strip() in question
                    for item in observed
                )
            if not quoted and not mentions_question(haystack, question, tokens):
                continue
            found.append(feature)
    return found


def _record_ids(question: str) -> list[str]:
    return _RECORD_ID_PATTERN.findall(question)


def _device_codes(question: str) -> list[str]:
    return [code for code in _DEVICE_CODE_PATTERN.findall(question) if not code.startswith("REC-")]


def _record_anomaly_question(question: str) -> bool:
    return bool(_record_ids(question)) and any(marker in question for marker in _ANOMALY_MARKERS)


def _device_only_anomaly_question(question: str) -> bool:
    return not _record_ids(question) and bool(_device_codes(question)) and any(
        marker in question for marker in _ANOMALY_MARKERS
    )


def _record_id_from_arguments(arguments: object) -> str:
    parsed = arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return ""
    if not isinstance(parsed, dict):
        return ""
    value = parsed.get("record_id")
    if not isinstance(value, str):
        return ""
    return value.strip()


def _format_number(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    text = f"{number:.6f}".rstrip("0").rstrip(".")
    if "." not in text:
        text += ".0"
    return text


def _value_with_unit(value: object, unit: object) -> str:
    text = _format_number(value)
    if text is None:
        return "无"
    if isinstance(unit, str) and unit.strip():
        return f"{text} {unit.strip()}"
    return f"{text}（表头未标注单位，不补充单位）"


def _negated(text: str, word: str) -> bool:
    start = 0
    found = False
    while True:
        index = text.find(word, start)
        if index < 0:
            return found
        found = True
        window = text[max(0, index - 8) : index]
        if not any(flag in window for flag in ("不是", "不能", "不可", "无法", "并非", "没有", "未", "不会", "勿", "不得", "并不")):
            return False
        start = index + len(word)


def _fault_overclaim(text: str) -> bool:
    for word in (*_FAULT_NAMES, *_OVERCLAIM_WORDS):
        if word in text and not _negated(text, word):
            return True
    return False


def _status_contradiction(text: str, statuses: set[str]) -> bool:
    if not (statuses & _ANOMALY_STATUSES):
        return False
    for claim in _NORMAL_CLAIMS:
        if claim in text and not _negated(text, claim):
            return True
    return False


def _valid_observation(result: dict, question: str) -> bool:
    if result.get("official_record") is not True or result.get("data_source") != "official_dataset":
        return False
    if result.get("confirmed_fault") is True:
        return False
    record_id = result.get("record_id")
    device_id = result.get("device_id")
    observed_at = result.get("observation_time")
    status = result.get("detection_status")
    if not all(isinstance(item, str) and item.strip() for item in (record_id, device_id, observed_at, status)):
        return False
    if status not in _ANOMALY_STATUSES or record_id not in question:
        return False
    metrics = result.get("analyzed_metrics")
    if not isinstance(metrics, list):
        return False
    if status in {"偏离基线", "未发现显著偏离"} and not metrics:
        return False
    return True


def _metric_line(item: dict) -> str:
    parts = [f"{item.get('field')}：当前值 {_value_with_unit(item.get('current_value'), item.get('unit'))}"]
    if item.get("median") is not None:
        parts.append(f"历史中位数 {_value_with_unit(item.get('median'), item.get('unit'))}")
    if item.get("mad") is not None:
        parts.append(f"MAD {_value_with_unit(item.get('mad'), item.get('unit'))}")
    score = _format_number(item.get("robust_z"))
    if score is not None:
        parts.append(f"Robust Z-score {score}")
    status = item.get("metric_status")
    if isinstance(status, str) and status:
        parts.append(f"状态 {status}")
    reason = item.get("reason")
    if isinstance(reason, str) and reason.strip():
        parts.append(reason.strip())
    note = item.get("note")
    if isinstance(note, str) and note.strip():
        parts.append(note.strip())
    return "，".join(parts)


def _status_note(status: str) -> str:
    if status == "偏离基线":
        return "上述独立指标相对历史基线出现统计偏离。这不是故障确诊，也不是官方报警。"
    if status == "未发现显著偏离":
        return "未发现显著偏离是有效的统计检测结果，不是资料检索失败，也不能据此确认设备正常。"
    if status == "基线不足":
        return "历史基线不足，无法进行可靠比较，不能写成设备正常。"
    return "当前结果无法判定。必须保留下面的真实原因，不能写成设备正常，也不能写成设备故障。"


def _format_observation(result: dict) -> str:
    status = str(result.get("detection_status") or "")
    threshold = result.get("experimental_threshold")
    limit = None
    if isinstance(threshold, dict):
        limit = _format_number(threshold.get("robust_z_abs"))
    lines = [
        f"数据来源：{OFFICIAL_EXCEL_NAME}",
        f"设备编号：{result.get('device_id')}",
        f"记录编号：{result.get('record_id')}",
        f"采集时间：{result.get('observation_time')}",
        f"检测状态：{status}",
        (
            f"统计基线：历史样本 {result.get('history_sample_count')} 条，"
            f"基线方法 {result.get('baseline_method')}，比较当前值、历史中位数、MAD 和 Robust Z-score。"
        ),
    ]
    if limit is not None:
        lines.append(f"实验性判据：绝对 Robust Z-score 大于 {limit}。这不是官方报警限值。")
    metrics = result.get("analyzed_metrics")
    selected = []
    if isinstance(metrics, list):
        for item in metrics:
            if not isinstance(item, dict) or item.get("counts_as_independent_evidence") is False:
                continue
            if item.get("metric_status") in {"偏离基线", "基线不足", "无法判定"}:
                selected.append(item)
    compared = []
    if isinstance(metrics, list):
        for item in metrics:
            if isinstance(item, dict) and item.get("counts_as_independent_evidence") is not False:
                field = item.get("field")
                if isinstance(field, str) and field:
                    compared.append(field)
    if selected:
        lines.append("统计偏离与无法比较的指标：")
        lines.extend(f"- {_metric_line(item)}" for item in selected)
    elif status == "未发现显著偏离":
        shown = "、".join(compared) if compared else "工具返回的独立指标"
        lines.append(f"已比较指标：{shown}。没有独立指标超过实验性统计判据。")
    lines.append(_status_note(status))
    warnings = result.get("warnings")
    if isinstance(warnings, list):
        for warning in warnings:
            if isinstance(warning, str) and warning.strip():
                lines.append(f"检测说明：{warning.strip()}")
    return "\n".join(lines)


def _failed_observation_message(record_id: str, error: str) -> str:
    if record_id and _DEVICE_CODE_PATTERN.fullmatch(record_id):
        return (
            f"「{record_id}」是设备编号，不能当作记录编号。"
            "请提供官方数据集中的具体记录编号，不能自动选择最新或随机记录。"
        )
    if record_id and "没有找到" in error:
        return f"没有找到记录编号「{record_id}」。不能改用其他记录，也不能编造观测数据。"
    if "记录编号" in error:
        return error
    return "统计偏离检查没有返回可核对的官方记录，不能编造观测数据。"


def _observation_evidence(tool_results: object, tool_calls: object, question: str) -> dict:
    calls_by_id: dict[str, dict] = {}
    if isinstance(tool_calls, list):
        for call in tool_calls:
            if isinstance(call, dict):
                calls_by_id[str(call.get("id") or "")] = call
    accepted = []
    rejected: list[str] = []
    rows = tool_results if isinstance(tool_results, list) else []
    for item in rows:
        if not isinstance(item, dict):
            continue
        result = item.get("result")
        name = item.get("name") or (result.get("tool") if isinstance(result, dict) else None)
        if name != "detect_anomaly":
            continue
        call = calls_by_id.get(str(item.get("id") or ""))
        requested = _record_id_from_arguments(call.get("arguments") if isinstance(call, dict) else None)
        if requested and requested not in question:
            rejected.append(f"记录编号「{requested}」不是用户提供的编号，不能改用这条记录。")
            continue
        if not isinstance(result, dict) or result.get("ok") is not True:
            error = str(result.get("error") or "") if isinstance(result, dict) else ""
            rejected.append(_failed_observation_message(requested, error))
            continue
        returned = result.get("record_id")
        if requested and returned != requested:
            rejected.append("统计偏离检查返回的记录编号与请求不一致，不能改用其他记录。")
            continue
        if not _valid_observation(result, question):
            rejected.append("统计偏离检查没有返回可核对的官方记录，不能编造观测数据。")
            continue
        accepted.append(result)
    if accepted:
        return {
            "kind": "accepted",
            "message": "",
            "text": "\n\n".join(_format_observation(result) for result in accepted),
            "observations": accepted,
        }
    if rejected:
        return {"kind": "rejected", "message": rejected[0], "text": "", "observations": []}
    if _record_anomaly_question(question):
        record_id = _record_ids(question)[0]
        return {
            "kind": "rejected",
            "message": (
                f"没有实际执行记录编号「{record_id}」的统计偏离检查，"
                "不能假装已经检测，也不能改用其他记录。"
            ),
            "text": "",
            "observations": [],
        }
    if _device_only_anomaly_question(question):
        code = _device_codes(question)[0]
        return {
            "kind": "rejected",
            "message": (
                f"「{code}」是设备编号，不能当作记录编号。"
                "请提供官方数据集中的具体记录编号，不能自动选择最新或随机记录。"
            ),
            "text": "",
            "observations": [],
        }
    return {"kind": "none", "message": "", "text": "", "observations": []}


def _full_record_analysis_question(question: str) -> bool:
    return "完整分析" in question and bool(_record_ids(question) or _device_codes(question))


def _analysis_calls(tool_results: object, tool_calls: object) -> list[tuple[str, dict]]:
    calls_by_id: dict[str, dict] = {}
    if isinstance(tool_calls, list):
        for call in tool_calls:
            if isinstance(call, dict):
                calls_by_id[str(call.get("id") or "")] = call
    found = []
    rows = tool_results if isinstance(tool_results, list) else []
    for item in rows:
        if not isinstance(item, dict):
            continue
        result = item.get("result")
        name = item.get("name") or (result.get("tool") if isinstance(result, dict) else None)
        if name != "analyze_equipment_record":
            continue
        call = calls_by_id.get(str(item.get("id") or ""))
        requested = _record_id_from_arguments(call.get("arguments") if isinstance(call, dict) else None)
        found.append((requested, result if isinstance(result, dict) else {}))
    return found


def _valid_record_analysis(result: dict, question: str) -> bool:
    if result.get("ok") is not True or result.get("confirmed_fault") is True or result.get("confirmed") is True:
        return False
    record_id = result.get("record_id")
    device_id = result.get("device_id")
    collected_at = result.get("collected_at")
    level = result.get("diagnosis_level")
    status = result.get("detection_status")
    observation = result.get("observation")
    detected = result.get("statistical_deviation")
    if not all(isinstance(item, str) and item.strip() for item in (record_id, device_id, collected_at, level, status)):
        return False
    if record_id not in question or level not in _ANALYSIS_LEVELS or status not in _ANOMALY_STATUSES:
        return False
    if not isinstance(observation, dict) or not isinstance(observation.get("values"), dict):
        return False
    if observation.get("record_id") != record_id or observation.get("device_id") != device_id:
        return False
    if not isinstance(detected, dict) or detected.get("detection_status") != status:
        return False
    if not isinstance(result.get("diagnostic_clues"), list) or not isinstance(result.get("candidate_faults"), list):
        return False
    if not isinstance(result.get("anomalous_metrics"), list):
        return False
    return True


def _clue_source(clue: dict) -> SourceItem | None:
    if clue.get("partial_condition_met") is not True or clue.get("complete_condition_met") is True:
        return None
    document = str(clue.get("document") or "")
    section = str(clue.get("related_fault_section") or "")
    line_number = clue.get("line_number")
    if not document.endswith(".md") or not section or isinstance(line_number, bool) or not isinstance(line_number, int):
        return None
    return SourceItem(
        original_document=original_document(document),
        document=document,
        section=section,
        locator=f"第{line_number}行",
    )


def _format_clue(clue: dict) -> str:
    fields = []
    for item in clue.get("observed_fields") or []:
        if isinstance(item, dict):
            fields.append(f"{item.get('field')} {_value_with_unit(item.get('value'), item.get('unit'))}")
    section = str(clue.get("related_fault_section") or "")
    line_number = clue.get("line_number")
    text = (
        f"部分诊断线索：{section}，第{line_number}行。"
        f"实际观测：{'；'.join(fields) if fields else '工具未返回观测数值'}。"
        f"核对方法：{clue.get('method') or '工具未返回核对方法。'}"
        f"{clue.get('unverified_reason') or ''}"
        "这只是部分线索，不是 possible_match，不能据此认定具体故障。"
    )
    if "转子不平衡" in section:
        text += "不能认定转子不平衡。"
    return text


def _format_record_analysis(result: dict) -> tuple[str, list[SourceItem]]:
    detected = result.get("statistical_deviation") if isinstance(result.get("statistical_deviation"), dict) else {}
    metrics = detected.get("analyzed_metrics") if isinstance(detected.get("analyzed_metrics"), list) else []
    anomalous = set(result.get("anomalous_metrics") or [])
    selected = [
        item
        for item in metrics
        if isinstance(item, dict)
        and item.get("counts_as_independent_evidence") is not False
        and (item.get("field") in anomalous or item.get("metric_status") in {"基线不足", "无法判定"})
    ]
    level = str(result.get("diagnosis_level") or "")
    status = str(result.get("detection_status") or "")
    lines = [
        "官方记录完整分析（Excel 观测不是 Word 引用）：",
        f"数据来源：{result.get('data_source') or OFFICIAL_EXCEL_NAME}",
        f"设备编号：{result.get('device_id')}",
        f"记录编号：{result.get('record_id')}",
        f"采集时间：{result.get('collected_at')}",
        f"统计检测状态：{status}",
        f"诊断等级：{level}。{_ANALYSIS_LEVELS[level]}",
        (
            f"统计基线：历史样本 {detected.get('history_sample_count')} 条，"
            f"基线方法 {detected.get('baseline_method')}。"
        ),
    ]
    threshold = detected.get("experimental_threshold")
    if isinstance(threshold, dict):
        limit = _format_number(threshold.get("robust_z_abs"))
        if limit is not None:
            lines.append(f"实验性判据：绝对 Robust Z-score 大于 {limit}。这不是官方报警限值，也不能证明具体故障。")
    if selected:
        lines.append("偏离基线或无法比较的指标：")
        lines.extend(f"- {_metric_line(item)}" for item in selected)
    elif status == "未发现显著偏离":
        lines.append("已完成统计比较，没有独立指标超过实验性统计判据。这不是资料检索失败。")
    elif status == "基线不足":
        lines.append("历史样本不足，无法进行可靠比较，不能认定设备正常。")
    lines.append(_status_note(status))
    clues = [item for item in result.get("diagnostic_clues") or [] if isinstance(item, dict)]
    sources: list[SourceItem] = []
    seen: set[tuple[str, str, str, str]] = set()
    if clues:
        lines.append("实际可验证的诊断线索：")
        for clue in clues:
            if clue.get("partial_condition_met") is not True:
                continue
            lines.append(f"- {_format_clue(clue)}")
            source = _clue_source(clue)
            if source is not None:
                _append_source(sources, seen, source)
    else:
        lines.append("没有核对到可单独成立的诊断线索。")
    missing = [item for item in result.get("missing_data") or [] if isinstance(item, str) and item.strip()]
    if missing:
        lines.append("当前无法验证的诊断条件：" + "、".join(missing) + "。")
    reasons = [
        item
        for item in result.get("insufficient_reasons") or []
        if isinstance(item, str) and item.strip()
    ]
    if reasons:
        lines.append("证据不足原因：")
        lines.extend(f"- {item}" for item in reasons[:8])
    candidates = [
        item
        for item in result.get("candidate_faults") or []
        if isinstance(item, dict) and item.get("confirmed") is False and item.get("fault")
    ]
    if level == "possible_match" and result.get("diagnose_fault_called") is True and candidates:
        lines.append("候选故障只表示可能匹配，不是确诊：")
        lines.extend(f"- {item.get('fault')}：{item.get('note') or '不是确诊。'}" for item in candidates)
    else:
        lines.append("候选故障：空。缺少完整证据，不能给出故障候选，也不能认定轴承磨损、转子不平衡或润滑故障。")
    if any(field in anomalous for field in ("包络谱峰值(g)", "BPFO幅值(g)", "BPFI幅值(g)", "BSF幅值(g)")):
        lines.append("包络谱峰值或轴承特征频率幅值的统计偏离，不能认定轴承磨损。")
    if any("温度" in str(field) for field in anomalous):
        lines.append("轴承温度的统计偏离，不能认定润滑故障。")
    return "\n".join(lines), sources


def _record_analysis_evidence(tool_results: object, tool_calls: object, question: str) -> dict:
    if not _full_record_analysis_question(question):
        return {"kind": "none", "message": "", "text": "", "sources": []}
    accepted: list[dict] = []
    rejected: list[str] = []
    for requested, result in _analysis_calls(tool_results, tool_calls):
        if requested and requested not in question:
            rejected.append(f"记录编号「{requested}」不是用户提供的编号，不能改用这条记录。")
            continue
        if result.get("ok") is not True:
            rejected.append(_failed_observation_message(requested, str(result.get("error") or "")))
            continue
        returned = result.get("record_id")
        if requested and returned != requested:
            rejected.append("完整分析返回的记录编号与请求不一致，不能改用其他记录。")
            continue
        if not _valid_record_analysis(result, question):
            rejected.append("完整分析没有返回可核对的官方记录，不能编造观测或诊断。")
            continue
        accepted.append(result)
    if accepted:
        texts = []
        sources: list[SourceItem] = []
        seen: set[tuple[str, str, str, str]] = set()
        for result in accepted:
            text, result_sources = _format_record_analysis(result)
            texts.append(text)
            for source in result_sources:
                _append_source(sources, seen, source)
        return {"kind": "accepted", "message": "", "text": "\n\n".join(texts), "sources": sources}
    if rejected:
        return {"kind": "rejected", "message": rejected[0], "text": "", "sources": []}
    record_ids = _record_ids(question)
    if record_ids:
        return {
            "kind": "rejected",
            "message": (
                f"没有实际执行记录编号「{record_ids[0]}」的完整分析，"
                "不能假装已经分析，也不能改用其他记录。"
            ),
            "text": "",
            "sources": [],
        }
    code = _device_codes(question)[0]
    return {
        "kind": "rejected",
        "message": (
            f"「{code}」是设备编号，不能当作记录编号。"
            "请提供官方数据集中的具体记录编号后再做完整分析。"
        ),
        "text": "",
        "sources": [],
    }


def _has_diagnosis_support(tool_results: object, question: str) -> bool:
    tokens = question_tokens(question)
    if not isinstance(tool_results, list):
        return False
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        result = item.get("result")
        if not isinstance(result, dict) or result.get("ok") is not True:
            continue
        name = item.get("name") or result.get("tool")
        if name == "diagnose_fault" and _diagnosis_features(result, question, tokens):
            return True
    return False


def _compose_observation_answer(answer: object, observation: dict, diagnosis_supported: bool) -> str:
    observations = observation.get("observations")
    statuses = {
        str(item.get("detection_status") or "")
        for item in observations
        if isinstance(item, dict)
    } if isinstance(observations, list) else set()
    text = answer.strip() if isinstance(answer, str) else ""
    allowed_ids = {
        str(item.get("record_id") or "")
        for item in observations
        if isinstance(item, dict)
    } if isinstance(observations, list) else set()
    if any(record_id not in allowed_ids for record_id in _record_ids(text)):
        text = "以下只保留用户给出的记录编号所对应的检测结果。"
    if not diagnosis_supported and (_fault_overclaim(text) or _status_contradiction(text, statuses)):
        text = "以下只解释已经执行的统计偏离检查，不能据此确认故障或正式报警。"
    elif diagnosis_supported:
        text = (
            text
            + "\n\n统计偏离本身不能单独确认故障；可能故障只依据已经执行的官方特征对照。"
        ).strip()
    if not text:
        text = "以下是已经执行的统计偏离检查结果。"
    return text + "\n\n官方开发集观测证据（不是 Word 文档引用）：\n" + str(observation.get("text") or "")


def evidence_from_tools(tool_results: object, question: str) -> tuple[bool, bool, list[SourceItem]]:
    executed = False
    supported = False
    sources: list[SourceItem] = []
    seen: set[tuple[str, str, str, str]] = set()
    tokens = question_tokens(question)
    judgment = _fault_judgment(question)
    if not isinstance(tool_results, list):
        return executed, supported, sources
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        result = item.get("result")
        if not isinstance(result, dict):
            continue
        executed = True
        if result.get("ok") is not True:
            continue
        name = item.get("name") or result.get("tool")
        if name == "search_knowledge":
            if judgment:
                continue
            rows = result.get("results")
            if not isinstance(rows, list):
                continue
            usable = []
            relevant = False
            for row in rows:
                if not isinstance(row, dict) or not str(row.get("text", "")).strip():
                    continue
                usable.append(row)
                haystack = str(row.get("section", "")) + "\n" + str(row.get("text", ""))
                if mentions_question(haystack, question, tokens):
                    relevant = True
            if not relevant:
                continue
            supported = True
            for row in usable:
                _append_source(sources, seen, source_item(row))
        elif name == "get_equipment_relations":
            if judgment or result.get("ambiguous") is True:
                continue
            relations = result.get("relations")
            if not isinstance(relations, list):
                continue
            node_name = str((result.get("node") or {}).get("name") or "")
            for relation in relations:
                if not isinstance(relation, dict):
                    continue
                if not relation.get("relation") or not relation.get("source_id") or not relation.get("target_id"):
                    continue
                names = "\n".join(
                    [
                        node_name,
                        str(relation.get("source_name") or ""),
                        str(relation.get("target_name") or ""),
                    ]
                )
                if not mentions_question(names, question, tokens):
                    continue
                supported = True
                refs = relation.get("source_refs")
                if not isinstance(refs, list):
                    continue
                for ref in refs:
                    if isinstance(ref, dict):
                        _append_source(sources, seen, graph_source_item(ref))
        elif name == "diagnose_fault":
            features = _diagnosis_features(result, question, tokens)
            if not features:
                continue
            supported = True
            for feature in features:
                _append_source(sources, seen, source_item(feature))
    return executed, supported, sources


def _published_fault_answer(outcome: dict) -> str:
    report = outcome.get("fault_report") if isinstance(outcome, dict) else None
    if not isinstance(report, dict) or report.get("applicable") is not True:
        return ""
    answer = report.get("public_answer")
    return answer.strip() if isinstance(answer, str) else ""


def grounded_fallback(question: str) -> ChatResponse:
    if _fault_judgment(question):
        return ChatResponse(answer=NO_EVIDENCE_ANSWER, sources=[])
    try:
        found = search(question, 5)
    except SearchIndexError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    hits = found.get("results")
    materials = [
        item
        for item in hits
        if isinstance(item, dict) and str(item.get("text", "")).strip()
    ] if isinstance(hits, list) else []
    if not materials:
        return ChatResponse(answer=NO_EVIDENCE_ANSWER, sources=[])
    try:
        answer = ask(question, materials)
    except DeepSeekError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    sources: list[SourceItem] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in materials:
        _append_source(sources, seen, source_item(item))
    return ChatResponse(answer=answer, sources=sources)


def error_body(message: str) -> dict[str, str | bool]:
    return ErrorResponse(message=message).model_dump()


def validation_message(exc: RequestValidationError) -> str:
    for error in exc.errors():
        message = error.get("msg")
        if isinstance(message, str) and message.startswith("Value error, "):
            return message.removeprefix("Value error, ")
        location = error.get("loc") or ()
        if location and location[-1] == "question":
            return "请输入问题"
        if location and location[-1] == "q":
            return "请输入检索内容"
        if location and location[-1] == "top_k":
            return "返回条数必须在 1 到 20 之间"
        if location and location[-1] == "device_id":
            return "请提供有效的设备编号 device_id。"
        if location and location[-1] == "limit":
            return "limit 必须是 1 到 100 的整数。"
        if location and location[-1] in {"start_time", "end_time"}:
            return "时间参数必须是 YYYY-MM-DD HH:MM:SS。"
        if location and location[-1] == "record_id":
            return "请提供有效的记录编号。"
        if location and location[-1] == "session_id":
            return "请提供有效的会话标识 session_id。"
        if location and location[-1] == "filename":
            return "请提供有效的文件名。"
        if location and location[-1] == "interval_seconds":
            return "回放间隔必须是 0 到 60 秒。"
        if location and location[-1] == "task_id":
            return "请提供有效的任务编号。"
    return "请求参数不正确"


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    if isinstance(exc.detail, str) and exc.detail != "Not Found":
        message = exc.detail
    elif exc.status_code == 404:
        message = "未找到该接口"
    else:
        message = "请求处理失败"
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(message),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=error_body(validation_message(exc)),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content=error_body("服务器内部错误"),
    )


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", message="系统运行正常")


@app.get("/api/system-info", response_model=SystemInfoResponse)
def system_info() -> SystemInfoResponse:
    return SystemInfoResponse(
        project_name="钢铁设备故障预测与根因诊断智能体",
        backend_status="running",
        service="fastapi",
        version=app.version,
    )


@app.post("/api/ai/chat", response_model=ChatResponse)
def ai_chat(payload: ChatRequest) -> ChatResponse:
    try:
        outcome = ask_with_tools(payload.question)
    except DeepSeekError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    if not isinstance(outcome, dict):
        raise HTTPException(status_code=502, detail="DeepSeek 没有返回有效回答，请稍后重试。")
    tool_results = outcome.get("tool_results")
    tool_calls = outcome.get("tool_calls")
    analysis = _record_analysis_evidence(tool_results, tool_calls, payload.question)
    if analysis["kind"] == "accepted":
        return ChatResponse(answer=str(analysis.get("text") or ""), sources=list(analysis.get("sources") or []))
    if analysis["kind"] == "rejected":
        message = str(analysis.get("message") or "没有可核对的官方记录，不能编造完整分析结果。")
        return ChatResponse(answer=message, sources=[])
    executed, supported, sources = evidence_from_tools(tool_results, payload.question)
    observation = _observation_evidence(tool_results, tool_calls, payload.question)
    fault_text = _published_fault_answer(outcome)
    if observation["kind"] == "accepted" and not fault_text:
        return ChatResponse(
            answer=_compose_observation_answer(
                outcome.get("answer"),
                observation,
                _has_diagnosis_support(tool_results, payload.question),
            ),
            sources=sources,
        )
    if fault_text and observation["kind"] != "rejected":
        report = outcome.get("fault_report") if isinstance(outcome.get("fault_report"), dict) else {}
        fault_sources = sources if report.get("hypotheses") and supported else []
        return ChatResponse(answer=fault_text, sources=fault_sources)
    if observation["kind"] == "rejected":
        message = str(observation.get("message") or "没有可核对的官方观测记录，不能编造检测结果。")
        model_answer = outcome.get("answer")
        text = model_answer.strip() if isinstance(model_answer, str) else ""
        invented = any(record_id not in payload.question for record_id in _record_ids(text))
        if supported and text and not invented:
            return ChatResponse(answer=text + "\n\n" + message, sources=sources)
        return ChatResponse(answer=message, sources=[])
    if not executed:
        return grounded_fallback(payload.question)
    if not supported:
        return ChatResponse(answer=NO_EVIDENCE_ANSWER, sources=[])
    answer = outcome.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        return ChatResponse(answer=NO_EVIDENCE_ANSWER, sources=[])
    return ChatResponse(answer=answer.strip(), sources=sources)


@app.get("/api/knowledge/graph")
def knowledge_graph() -> dict[str, list]:
    try:
        return merge_imported_graph(load_graph())
    except GraphFileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    except KnowledgeFileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None


@app.get("/api/knowledge/view")
def knowledge_view() -> dict:
    try:
        return load_view()
    except ViewFileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None


@app.get("/api/knowledge/files")
def knowledge_files() -> dict:
    """列出原有六份知识资料和用户上传的文件。不读取正文，也不修改图谱。"""
    try:
        return list_knowledge_files()
    except KnowledgeFileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None


@app.get("/api/knowledge/tree")
def knowledge_tree() -> dict:
    """返回白名单知识目录。目录不存在时列入 missing_roots，不伪造文件。"""
    try:
        return list_knowledge_tree()
    except KnowledgeFileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None


@app.get("/api/knowledge/tree/preview")
def knowledge_tree_preview(path: str = Query(min_length=1, max_length=300)) -> dict:
    """预览白名单内的单个知识文件。不接受工作区之外的路径。"""
    try:
        return preview_knowledge_file(path)
    except KnowledgeFileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None


@app.post("/api/knowledge/files")
async def upload_knowledge_file(
    request: Request,
    filename: str = Query(min_length=1, max_length=180),
) -> dict:
    """接收原始二进制文件。文件名使用查询参数，由浏览器做 URL 编码。"""
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/octet-stream":
        raise HTTPException(status_code=415, detail="请使用 Content-Type: application/octet-stream 上传原始文件。")
    declared = request.headers.get("content-length", "").strip()
    if declared:
        try:
            declared_size = int(declared)
        except ValueError:
            raise HTTPException(status_code=400, detail="Content-Length 不是有效数字。") from None
        if declared_size > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="单个文件不能超过 50MB。")
    payload = await request.body()
    try:
        saved = save_upload(filename, payload)
        return _pipeline_response(process_knowledge_file(saved["file_id"]))
    except KnowledgeFileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None


@app.get("/api/knowledge/files/{file_id}/cleaned")
def knowledge_file_cleaned(file_id: str) -> dict:
    """返回清洗状态和有限预览。未完成时不返回虚构的 JSON。"""
    try:
        return cleaned_preview(file_id)
    except KnowledgeFileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None


@app.post("/api/knowledge/files/{file_id}/process")
def knowledge_file_process(file_id: str):
    """对已保存文件重新执行清洗、抽取、预演和安全入图。不接收文件路径。"""
    try:
        return _pipeline_response(process_knowledge_file(file_id))
    except KnowledgeFileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None


@app.get("/api/knowledge/files/{file_id}/process-result")
def knowledge_file_process_result(file_id: str) -> dict:
    """读取最近一次全流程结果。没有处理记录时不返回虚构入图。"""
    try:
        return process_result(file_id)
    except KnowledgeFileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None


@app.post("/api/knowledge/files/{file_id}/clean")
def knowledge_file_clean(file_id: str) -> dict:
    """对同一个已保存文件再次清洗。不接收文件路径。"""
    try:
        return clean_uploaded_file(file_id)
    except KnowledgeFileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None


@app.post("/api/knowledge/files/{file_id}/extract")
def knowledge_file_extract(file_id: str) -> dict:
    """从已清洗 JSON 抽取候选知识。不修改正式图谱，也不接收文件路径。"""
    try:
        return extract_knowledge(file_id)
    except KnowledgeFileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None


@app.get("/api/knowledge/files/{file_id}/extracted")
def knowledge_file_extracted(file_id: str) -> dict:
    """返回候选知识的有限预览。未抽取时不返回虚构实体或关系。"""
    try:
        return extracted_preview(file_id)
    except KnowledgeFileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None


@app.post("/api/knowledge/files/{file_id}/preview-import")
def knowledge_file_preview_import(file_id: str) -> dict:
    """生成入图预演。只读正式图谱，不写入节点或关系。"""
    try:
        return preview_knowledge_import(file_id)
    except KnowledgeFileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None


@app.get("/api/knowledge/files/{file_id}/preview-import")
def knowledge_file_import_preview(file_id: str) -> dict:
    """读取已经生成的入图预演。没有预演时不返回虚构匹配。"""
    try:
        return import_preview(file_id)
    except KnowledgeFileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None


@app.post("/api/knowledge/files/{file_id}/apply-import")
def knowledge_file_apply_import(file_id: str) -> dict:
    """按安全规则把预演中的明确包含关系写入运行时数据库。不改基础图谱文件。"""
    try:
        return apply_import(file_id)
    except KnowledgeFileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None


@app.get("/api/knowledge/files/{file_id}/import-result")
def knowledge_file_import_result(file_id: str) -> dict:
    """读取该文件最近一次入图结果。没有入图时不返回虚构新增。"""
    try:
        return import_result(file_id)
    except KnowledgeFileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None


def _research_identity(body: ResearchEvidenceRequest) -> dict[str, str | None]:
    return {
        "entity_a_id": body.entity_a_id,
        "entity_b_id": body.entity_b_id,
        "entity_a_scope": body.entity_a_scope,
        "entity_b_scope": body.entity_b_scope,
    }


@app.post("/api/knowledge/research/evidence")
def knowledge_research_evidence(body: ResearchEvidenceRequest) -> dict:
    """只读取已保存的图谱、候选知识和本地原文，不生成新关系。"""
    try:
        return find_cross_knowledge_evidence(body.entity_a, body.entity_b, body.max_hops, **_research_identity(body))
    except EntityIdentityError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from None
    except GraphFileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None


@app.post("/api/knowledge/research/reason")
def knowledge_research_reason(body: ResearchReasonRequest) -> dict:
    """结合已保存证据整理研究报告。请求参数不能授权把资料发给外部模型。"""
    try:
        return research_knowledge_relationship(body.entity_a, body.entity_b, body.max_hops, **_research_identity(body))
    except EntityIdentityError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from None
    except GraphFileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None


@app.post("/api/knowledge/research/validate")
def knowledge_research_validate(body: ResearchValidateRequest) -> dict:
    """用已保存证据和官方观测重算可检验的数值条件。"""
    try:
        return validate_research_physics(
            body.entity_a,
            body.entity_b,
            body.record_id,
            entity_a_id=body.entity_a_id,
            entity_b_id=body.entity_b_id,
            entity_a_scope=body.entity_a_scope,
            entity_b_scope=body.entity_b_scope,
        )
    except EntityIdentityError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from None
    except GraphFileError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None


@app.get("/api/knowledge/search")
def knowledge_search(
    q: str = Query(min_length=1, max_length=200),
    top_k: int = Query(default=5, ge=1, le=20),
) -> dict:
    try:
        return search(q, top_k)
    except SearchIndexError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None


def _pipeline_response(result: dict):
    status_code = int(result.get("pipeline", {}).get("status_code") or 200)
    if status_code == 200:
        return result
    return JSONResponse(status_code=status_code, content=result)


def _sampling_rate_hz(value: str | None) -> float:
    if value is None or not value.strip():
        raise HTTPException(status_code=422, detail="请提供采样频率 sampling_rate，单位是 Hz。")
    try:
        rate = float(value.strip())
    except ValueError:
        raise HTTPException(status_code=422, detail="sampling_rate 必须是大于 0 的数值，单位是 Hz。") from None
    if rate != rate or rate in {float("inf"), float("-inf")} or rate <= 0:
        raise HTTPException(status_code=422, detail="sampling_rate 必须是大于 0 的有限数值，单位是 Hz。")
    return rate


@app.post("/api/vibration/analyze")
async def vibration_analyze(
    request: Request,
    sampling_rate: str | None = Query(default=None),
    column: str = Query(default="value"),
    unit: str | None = Query(default=None),
) -> dict:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "text/csv":
        raise HTTPException(status_code=415, detail="请使用 Content-Type: text/csv 上传 CSV 文本。")
    rate = _sampling_rate_hz(sampling_rate)
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_CSV_BYTES:
            raise HTTPException(status_code=413, detail="CSV 请求体超过 5MB。")
        chunks.append(chunk)
    try:
        values = read_vibration_column(b"".join(chunks), column)
    except VibrationCsvError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    unit_text = unit.strip() if isinstance(unit, str) else ""
    outcome = analyze_vibration(values, rate, unit_text or None)
    if outcome.get("ok") is not True:
        raise HTTPException(status_code=422, detail=str(outcome.get("error") or "振动数据无法分析。"))
    outcome["data_source"] = "uploaded_csv"
    outcome["column"] = column.strip()
    outcome["analysis_note"] = "这是信号分析结果，不是故障确诊。"
    return outcome


@app.post("/api/dataset/records/{record_id}/supplementary-vibration")
async def attach_record_vibration(
    record_id: str,
    request: Request,
    device_id: str = Query(min_length=1),
    measured_at: str = Query(min_length=1),
    sensor_position: str = Query(min_length=1),
    sampling_rate: str = Query(min_length=1),
    unit: str = Query(min_length=1),
    file_name: str = Query(min_length=1),
    data_origin: str = Query(min_length=1),
    column: str = Query(default="value"),
) -> dict:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "text/csv":
        raise HTTPException(status_code=415, detail="请使用 Content-Type: text/csv 上传 CSV 文本。")
    rate = _sampling_rate_hz(sampling_rate)
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_CSV_BYTES:
            raise HTTPException(status_code=413, detail="CSV 请求体超过 5MB。")
        chunks.append(chunk)
    try:
        result = attach_vibration_csv(
            record_id,
            device_id,
            measured_at,
            sensor_position,
            rate,
            unit,
            file_name,
            b"".join(chunks),
            column=column,
            data_origin=data_origin,
        )
    except SupplementaryMeasurementError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    if result.get("ok") is not True:
        raise HTTPException(status_code=int(result.get("status_code") or 422), detail=str(result.get("error") or "补充测量没有保存。"))
    try:
        result["followup_tasks"] = sync_followup_tasks(record_id)["tasks"]
    except FollowupTaskError as exc:
        result["followup_error"] = exc.message
    return result


@app.get("/api/dataset/records/{record_id}/supplementary-vibration")
def list_record_vibration(record_id: str) -> dict:
    try:
        measurements = list_for_record(record_id)
        synchronous = list_synchronous_for_record(record_id)
    except SupplementaryMeasurementError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    return {
        "record_id": record_id,
        "measurements": measurements,
        "synchronous_measurements": synchronous,
        "official_excel_modified": False,
        "written_to_graph": False,
    }


@app.post("/api/dataset/records/{record_id}/synchronous-vibration")
async def attach_record_synchronous_vibration(
    record_id: str,
    request: Request,
    device_id: str = Query(min_length=1),
    measured_at: str = Query(min_length=1),
    sampling_rate: str = Query(min_length=1),
    unit: str = Query(min_length=1),
    file_name: str = Query(min_length=1),
    data_origin: str = Query(min_length=1),
    channels: str = Query(min_length=2),
    sync_method: str = Query(default="shared_time_column"),
    time_base: str = Query(default="relative_seconds"),
) -> dict:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "text/csv":
        raise HTTPException(status_code=415, detail="请使用 Content-Type: text/csv 上传 CSV 文本。")
    try:
        channel_rows = json.loads(channels)
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="通道描述必须是 JSON 数组。") from None
    if not isinstance(channel_rows, list):
        raise HTTPException(status_code=422, detail="通道描述必须是 JSON 数组。")
    rate = _sampling_rate_hz(sampling_rate)
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_CSV_BYTES:
            raise HTTPException(status_code=413, detail="CSV 请求体超过 5MB。")
        chunks.append(chunk)
    try:
        result = attach_synchronous_csv(
            record_id,
            device_id,
            measured_at,
            rate,
            unit,
            file_name,
            b"".join(chunks),
            channel_rows,
            sync_method=sync_method,
            time_base=time_base,
            data_origin=data_origin,
        )
    except SupplementaryMeasurementError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    if result.get("ok") is not True:
        raise HTTPException(status_code=int(result.get("status_code") or 422), detail=str(result.get("error") or "同步振动测量没有保存。"))
    try:
        result["followup_tasks"] = sync_followup_tasks(record_id)["tasks"]
    except FollowupTaskError as exc:
        result["followup_error"] = exc.message
    return result


@app.post("/api/dataset/records/{record_id}/physical-revalidation")
def revalidate_record_vibration(record_id: str) -> dict:
    try:
        result = revalidate_record(record_id)
        result["followup_tasks"] = sync_followup_tasks(record_id, result.get("report"))["tasks"]
        return result
    except SupplementaryMeasurementError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    except FollowupTaskError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None


@app.get("/api/dataset/records/{record_id}/diagnostic-evidence")
def list_record_diagnostic_evidence(record_id: str) -> dict:
    try:
        evidence = list_evidence(record_id)
    except DiagnosticEvidenceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    return {
        "record_id": record_id,
        "evidence": evidence,
        "confirmed": False,
        "written_to_graph": False,
        "official_excel_modified": False,
    }


@app.post("/api/dataset/records/{record_id}/diagnostic-evidence")
async def create_record_diagnostic_evidence(record_id: str, request: Request) -> dict:
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="请提交 JSON 结构化证据。") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="请提交 JSON 结构化证据。")
    try:
        result = register_evidence(record_id, payload)
        result["followup_tasks"] = sync_followup_tasks(record_id)["tasks"]
        return result
    except DiagnosticEvidenceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    except FollowupTaskError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None


@app.get("/api/dataset/records/{record_id}/followup-tasks")
def list_record_followup_tasks(record_id: str) -> dict:
    try:
        tasks = list_followup_tasks(record_id)
    except FollowupTaskError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    return {
        "record_id": record_id,
        "tasks": tasks,
        "confirmed": False,
        "written_to_graph": False,
        "official_excel_modified": False,
    }


@app.post("/api/dataset/records/{record_id}/followup-tasks")
def create_record_followup_tasks(record_id: str) -> dict:
    try:
        return generate_followup_tasks(record_id)
    except FollowupTaskError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None


@app.post("/api/dataset/records/{record_id}/fault-reassessment")
def reassess_record_fault(record_id: str) -> dict:
    try:
        result = reassess_record(record_id)
    except (SupplementaryMeasurementError, DiagnosisHistoryError) as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    if result.get("ok") is not True:
        raise HTTPException(status_code=422, detail=str(result.get("error") or "故障重新评估没有完成。"))
    return result


@app.get("/api/dataset/records/{record_id}/diagnosis-history")
def list_record_diagnosis_history(record_id: str) -> dict:
    try:
        assessments = list_assessments(record_id)
    except DiagnosisHistoryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    return {
        "record_id": record_id,
        "assessments": assessments,
        "official_excel_modified": False,
        "written_to_graph": False,
    }


@app.get("/api/dataset/records/{record_id}/diagnosis-history/compare")
def compare_record_diagnosis_history(
    record_id: str,
    before: str = Query(default=""),
    after: str = Query(default=""),
) -> dict:
    try:
        return compare_assessments(record_id, before, after)
    except DiagnosisHistoryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None


@app.get("/api/dataset/records/{record_id}/diagnosis-history/{assessment_id}")
def read_record_diagnosis_history(record_id: str, assessment_id: str) -> dict:
    try:
        assessment = get_assessment(record_id, assessment_id)
    except DiagnosisHistoryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    if assessment is None:
        raise HTTPException(status_code=404, detail="没有找到这次评估，或者它不属于这条记录。")
    return assessment


def _dataset_token(value: object, label: str) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise HTTPException(status_code=422, detail=f"请提供{label}。")
    if "/" in text or "\\" in text or ".." in text or "\x00" in text:
        raise HTTPException(status_code=422, detail=f"{label}不能用于读取服务器文件。")
    if _DATASET_TOKEN.fullmatch(text) is None:
        raise HTTPException(status_code=422, detail=f"{label}格式不正确。")
    return text


def _without_answer_labels(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _without_answer_labels(item)
            for key, item in value.items()
            if key not in _ANSWER_LABELS
        }
    if isinstance(value, list):
        return [_without_answer_labels(item) for item in value]
    return value


def _dataset_result(loader, *args, **kwargs) -> dict:
    try:
        result = loader(*args, **kwargs)
    except DatasetError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from None
    if not isinstance(result, dict):
        raise HTTPException(status_code=500, detail="官方数据查询没有返回有效结果。")
    cleaned = _without_answer_labels(result)
    if not isinstance(cleaned, dict):
        raise HTTPException(status_code=500, detail="官方数据查询没有返回有效结果。")
    return cleaned


def _optional_time(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


@app.get("/api/dataset/summary")
def dataset_summary() -> dict:
    return _dataset_result(get_dataset_summary)


@app.get("/api/dataset/devices")
def dataset_devices() -> dict:
    return _dataset_result(list_devices)


@app.get("/api/dataset/records")
def dataset_records(
    device_id: str = Query(min_length=1, max_length=64),
    start_time: str | None = Query(default=None, max_length=32),
    end_time: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=MAX_LIMIT, ge=1, le=MAX_LIMIT),
) -> dict:
    """返回白名单观测记录。采集间隔不是原始波形采样频率，相同时间的记录也不合并。"""
    return _dataset_result(
        get_device_records,
        _dataset_token(device_id, "设备编号"),
        start_time=_optional_time(start_time),
        end_time=_optional_time(end_time),
        limit=limit,
    )


@app.get("/api/dataset/records/search")
def dataset_records_search(
    q: str | None = Query(default=None, max_length=120),
    device_id: str | None = Query(default=None, max_length=64),
    start_time: str | None = Query(default=None, max_length=32),
    end_time: str | None = Query(default=None, max_length=32),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_LIMIT),
    analysis_status: str = Query(default="all", max_length=40),
) -> dict:
    """统一历史记录检索。支持设备中文名称、设备编号、记录编号和时间分页。不修改 Excel。"""
    token = None
    if device_id is not None and str(device_id).strip():
        token = _dataset_token(device_id, "设备编号")
    status = str(analysis_status or "all").strip()
    deviation_ids: set[str] = set()
    try:
        if status in {"all", "deviation"}:
            deviation_ids = list_deviation_record_ids(token)
    except MonitoringEventError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from None
    return _dataset_result(
        search_records,
        q=q,
        device_id=token,
        start_time=_optional_time(start_time),
        end_time=_optional_time(end_time),
        page=page,
        page_size=page_size,
        analysis_status=status,
        known_deviation_ids=deviation_ids,
    )


@app.get("/api/dataset/observations/{record_id}")
def dataset_observation(record_id: str) -> dict:
    identity = _dataset_token(record_id, "记录编号")
    result = _dataset_result(get_observation, identity)
    record = result.get("record")
    if result.get("found") is not True or not isinstance(record, dict):
        raise HTTPException(status_code=404, detail=f"没有找到记录编号「{identity}」。")
    return result


def _analysis_ready(result: dict, record_id: str) -> bool:
    observation = result.get("observation")
    deviation = result.get("statistical_deviation")
    clues = result.get("diagnostic_clues")
    candidates = result.get("candidate_faults")
    status = deviation.get("detection_status") if isinstance(deviation, dict) else None
    return (
        isinstance(observation, dict)
        and observation.get("record_id") == record_id
        and isinstance(observation.get("device_id"), str)
        and bool(observation.get("device_id"))
        and isinstance(observation.get("collected_at"), str)
        and bool(observation.get("collected_at"))
        and isinstance(deviation, dict)
        and deviation.get("record_id") == record_id
        and isinstance(status, str)
        and bool(status)
        and isinstance(clues, list)
        and isinstance(candidates, list)
        and result.get("diagnosis_level") in _ANALYSIS_LEVELS
        and result.get("confirmed") is not True
        and all(not isinstance(item, dict) or item.get("confirmed") is not True for item in candidates)
    )


@app.get("/api/dataset/analysis/{record_id}")
def dataset_analysis(record_id: str) -> dict:
    """返回已有记录分析函数的结果。不调用 DeepSeek，也不改写诊断等级。"""
    identity = _dataset_token(record_id, "记录编号")
    result = _dataset_result(analyze_record_for_diagnosis, identity)
    if result.get("ok") is not True:
        message = str(result.get("error") or f"没有找到记录编号「{identity}」。")
        status_code = 404 if "没有找到记录编号" in message else 422
        raise HTTPException(status_code=status_code, detail=message)
    if not _analysis_ready(result, identity):
        raise HTTPException(status_code=500, detail="记录分析没有返回可核对的观测结果。")
    result["analysis_scope"] = "official_development_set_history"
    result["analysis_note"] = _ANALYSIS_NOTE
    return result


class MonitoringProcessRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=80)
    record_id: str = Field(min_length=1, max_length=80)

    @field_validator("session_id")
    @classmethod
    def session_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("请提供有效的会话标识 session_id。")
        return text

    @field_validator("record_id")
    @classmethod
    def record_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("请提供有效的记录编号。")
        return text


@app.post("/api/monitoring/process-record")
def monitoring_process_record(payload: MonitoringProcessRequest) -> dict:
    """重新分析一条官方记录，并只在确实偏离基线时写入当前回放会话。"""
    session_id = _dataset_token(payload.session_id, "会话标识")
    record_id = _dataset_token(payload.record_id, "记录编号")
    try:
        result = process_monitoring_record(session_id, record_id)
    except MonitoringEventError as exc:
        raise HTTPException(status_code=500, detail=exc.message) from None
    if result.get("ok") is not True:
        message = str(result.get("error") or f"没有找到记录编号「{record_id}」。")
        status_code = 404 if "没有找到记录编号" in message else 422
        raise HTTPException(status_code=status_code, detail=message)
    analysis = result.get("analysis")
    if not isinstance(analysis, dict) or not _analysis_ready(analysis, record_id):
        raise HTTPException(status_code=500, detail="记录分析没有返回可核对的观测结果。")
    if result.get("saved") is True and result.get("duplicate") is True:
        raise HTTPException(status_code=500, detail="监测事件保存状态不一致。")
    if (result.get("saved") is True or result.get("duplicate") is True) and not isinstance(result.get("event"), dict):
        raise HTTPException(status_code=500, detail="监测事件没有写入数据库。")
    if result.get("saved") is not True and result.get("duplicate") is not True and result.get("event") is not None:
        raise HTTPException(status_code=500, detail="监测事件保存状态不一致。")
    analysis = dict(analysis)
    analysis["analysis_scope"] = "official_development_set_history"
    analysis["analysis_note"] = _ANALYSIS_NOTE
    body = {
        "session_id": session_id,
        "record_id": record_id,
        "analysis": analysis,
        "event": result.get("event"),
        "saved": result.get("saved") is True,
        "duplicate": result.get("duplicate") is True,
        "reminder_required": result.get("reminder_required") is True,
    }
    cleaned = _without_answer_labels(body)
    if not isinstance(cleaned, dict):
        raise HTTPException(status_code=500, detail="监测事件没有返回有效结果。")
    return cleaned


@app.get("/api/monitoring/events")
def monitoring_events(
    session_id: str | None = Query(default=None, max_length=80),
    device_id: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict:
    """查询已经写入的历史回放统计偏离事件。不分析、也不新增事件。"""
    session = _dataset_token(session_id, "会话标识") if isinstance(session_id, str) and session_id.strip() else None
    device = _dataset_token(device_id, "设备编号") if isinstance(device_id, str) and device_id.strip() else None
    try:
        result = list_monitoring_events(session, device, limit)
    except MonitoringEventError as exc:
        raise HTTPException(status_code=500, detail=exc.message) from None
    cleaned = _without_answer_labels(result)
    if not isinstance(cleaned, dict):
        raise HTTPException(status_code=500, detail="监测事件没有查询成功。")
    return cleaned


class AutomaticMonitoringStartRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=80)
    interval_seconds: float = Field(default=1.0, ge=0, le=60)

    @field_validator("device_id")
    @classmethod
    def device_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("请提供有效的设备编号 device_id。")
        return text


class AutomaticMonitoringStopRequest(BaseModel):
    device_id: str | None = Field(default=None, max_length=80)
    task_id: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def has_target(self) -> "AutomaticMonitoringStopRequest":
        device = self.device_id.strip() if isinstance(self.device_id, str) else ""
        task = self.task_id.strip() if isinstance(self.task_id, str) else ""
        if not device and not task:
            raise ValueError("请提供要停止的设备编号或任务编号。")
        return self


def _automatic_result(loader, *args, **kwargs) -> dict | list:
    try:
        result = loader(*args, **kwargs)
    except AutomaticMonitoringError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None
    except MonitoringEventError as exc:
        raise HTTPException(status_code=500, detail=exc.message) from None
    cleaned = _without_answer_labels(result)
    if isinstance(cleaned, dict):
        return cleaned
    if isinstance(cleaned, list):
        return {"tasks": cleaned}
    raise HTTPException(status_code=500, detail="自动监测没有返回有效状态。")


@app.post("/api/monitoring/auto/start")
def monitoring_auto_start(payload: AutomaticMonitoringStartRequest) -> dict:
    """按历史记录启动后台回放分析。不连接实时传感器。"""
    device_id = _dataset_token(payload.device_id, "设备编号")
    result = _automatic_result(start_automatic_monitoring, device_id, payload.interval_seconds)
    if not isinstance(result, dict):
        raise HTTPException(status_code=500, detail="自动监测没有返回有效状态。")
    return result


@app.post("/api/monitoring/auto/stop")
def monitoring_auto_stop(payload: AutomaticMonitoringStopRequest) -> dict:
    """停止指定自动监测任务。"""
    device_id = _dataset_token(payload.device_id, "设备编号") if isinstance(payload.device_id, str) and payload.device_id.strip() else None
    task_id = _dataset_token(payload.task_id, "任务编号") if isinstance(payload.task_id, str) and payload.task_id.strip() else None
    result = _automatic_result(stop_automatic_monitoring, device_id, task_id)
    if not isinstance(result, dict):
        raise HTTPException(status_code=500, detail="自动监测没有返回有效状态。")
    return result


@app.get("/api/monitoring/auto/status")
def monitoring_auto_status(
    device_id: str | None = Query(default=None, max_length=80),
    task_id: str | None = Query(default=None, max_length=80),
) -> dict:
    """查询自动监测的实际状态。没有后台线程时，不会把任务显示为运行中。"""
    device = _dataset_token(device_id, "设备编号") if isinstance(device_id, str) and device_id.strip() else None
    task = _dataset_token(task_id, "任务编号") if isinstance(task_id, str) and task_id.strip() else None
    result = _automatic_result(task_status, device, task)
    if not isinstance(result, dict):
        raise HTTPException(status_code=500, detail="自动监测没有返回有效状态。")
    return result
