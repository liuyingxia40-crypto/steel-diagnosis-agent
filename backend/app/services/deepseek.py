import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

from app.services.agent_tools import TOOLS

logger = logging.getLogger(__name__)

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-flash"
TIMEOUT_SECONDS = 60.0
NO_EVIDENCE_ANSWER = "当前官方知识库没有检索到相关依据，暂时无法依据现有官方资料给出诊断结论。"
SYSTEM_PROMPT = (
    "你是钢铁设备故障诊断助手。只能依据用户消息中给出的官方知识资料回答。"
    "资料中明确记载的内容作为事实说明。"
    "资料没有直接写明、需要据此作出的判断，必须标明这是推断。"
    "不得编造参数限值、故障原因、处置措施或官方来源。"
    "资料不足以支持结论时，说明现有官方资料不能支持该结论。"
    "使用中文回答。"
)


class DeepSeekError(Exception):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _api_key() -> str:
    load_dotenv(ENV_PATH, override=True)
    return os.environ.get("DEEPSEEK_API_KEY", "").strip()


def build_messages(question: str, contexts: list[dict]) -> list[dict[str, str]]:
    blocks = []
    for index, item in enumerate(contexts, 1):
        blocks.append(
            "\n".join(
                [
                    f"[{index}] 文档：{item.get('document', '')}",
                    f"章节：{item.get('section', '')}",
                    f"位置：{item.get('locator', '')}",
                    "原文：",
                    str(item.get("text", "")),
                ]
            )
        )
    evidence = "\n\n".join(blocks)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"用户问题：\n{question}\n\n官方知识资料：\n{evidence}",
        },
    ]


def ask(question: str, contexts: list[dict] | None = None) -> str:
    materials = list(contexts or [])
    if not materials:
        return NO_EVIDENCE_ANSWER
    api_key = _api_key()
    if not api_key:
        raise DeepSeekError(
            "未配置 DeepSeek API Key。请在 backend/.env 中填写 DEEPSEEK_API_KEY 后重新发送。",
            status_code=500,
        )

    client = OpenAI(
        api_key=api_key,
        base_url=BASE_URL,
        timeout=TIMEOUT_SECONDS,
        max_retries=0,
    )
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=build_messages(question, materials),
            extra_body={"thinking": {"type": "disabled"}},
            max_tokens=1024,
        )
    except AuthenticationError:
        logger.warning("DeepSeek authentication failed")
        raise DeepSeekError(
            "DeepSeek API Key 无效或未授权，请检查 backend/.env 中的密钥。",
            status_code=401,
        ) from None
    except RateLimitError:
        logger.warning("DeepSeek rate limit reached")
        raise DeepSeekError("DeepSeek 请求过于频繁，请稍后再试。", status_code=429) from None
    except APITimeoutError:
        logger.warning("DeepSeek request timed out")
        raise DeepSeekError("连接 DeepSeek 超时，请稍后重试。", status_code=504) from None
    except APIConnectionError:
        logger.warning("DeepSeek connection failed")
        raise DeepSeekError("无法连接 DeepSeek 服务，请检查网络后重试。", status_code=502) from None
    except APIStatusError as exc:
        logger.warning("DeepSeek returned HTTP %s", exc.status_code)
        raise DeepSeekError(_status_message(exc.status_code), status_code=_public_status(exc.status_code)) from None
    except Exception:
        logger.warning("DeepSeek request failed with an unexpected error")
        raise DeepSeekError("调用 DeepSeek 时发生未知错误，请稍后重试。", status_code=502) from None
    finally:
        client.close()

    content = ""
    if response.choices:
        content = response.choices[0].message.content or ""
    if not content.strip():
        raise DeepSeekError("DeepSeek 没有返回有效回答，请稍后重试。", status_code=502)
    return content.strip()


def complete_chat(system: str, user: str) -> str:
    """单轮文本补全。不调用工具，也不读取知识文件。"""
    system_text = system.strip() if isinstance(system, str) else ""
    user_text = user.strip() if isinstance(user, str) else ""
    if not system_text or not user_text:
        raise DeepSeekError("研究请求缺少可发送的内容。", status_code=422)
    api_key = _api_key()
    if not api_key:
        raise DeepSeekError(
            "未配置 DeepSeek API Key。请在 backend/.env 中填写 DEEPSEEK_API_KEY 后重新发送。",
            status_code=500,
        )
    client = OpenAI(
        api_key=api_key,
        base_url=BASE_URL,
        timeout=TIMEOUT_SECONDS,
        max_retries=0,
    )
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_text},
                {"role": "user", "content": user_text},
            ],
            extra_body={"thinking": {"type": "disabled"}},
            max_tokens=1024,
        )
    except Exception as exc:
        _raise_api_error(exc)
        raise
    finally:
        client.close()
    content = ""
    if response.choices:
        content = response.choices[0].message.content or ""
    if not content.strip():
        raise DeepSeekError("DeepSeek 没有返回有效回答，请稍后重试。", status_code=502)
    return content.strip()


MAX_TOOL_ROUNDS = 3
TOOL_SYSTEM_PROMPT = (
    "你是钢铁设备故障诊断助手。只能依据工具真实执行后返回的内容回答。"
    "search_knowledge 用于查询官方故障知识、诊断特征、规程或专家案例。"
    "get_equipment_relations 用于查询设备、部件及其关系。"
    "diagnose_fault 用于用户已经提供可核实的故障特征、并要求匹配可能故障时使用。"
    "调用 diagnose_fault 时，observed_features 只能填写用户已经给出的特征，必须原样使用，不得改写或补充。"
    "用户没有提供任何实际观测特征时，不得自行编造特征后调用 diagnose_fault。"
    "detect_anomaly 只在用户提供记录编号、并且只要求检查统计异常、异常指标或历史比较时使用。"
    "analyze_equipment_record 在用户提供记录编号，并要求完整分析设备运行状态、异常指标、诊断线索或可能故障时使用。"
    "用户明确要求对某条记录做完整分析时，优先调用 analyze_equipment_record，不要再调用 detect_anomaly，因为完整分析内部已经包含统计偏离检查。"
    "用户只要求统计异常、没有要求完整分析时，只调用 detect_anomaly，不要调用 analyze_equipment_record。"
    "record_id 必须来自用户实际提供的记录编号，不得猜测、构造或随机选择。"
    "即使用户给出的记录编号可能不存在，也要调用与问题对应的记录工具，不能改用其他记录。"
    "如果用户只提供设备编号，例如 MM-01，应说明需要具体记录编号，不能把设备编号当作 record_id，也不能自动选择最新或随机记录。"
    "询问设备部件或关系时只使用 get_equipment_relations，不要调用 detect_anomaly 或 analyze_equipment_record。"
    "用户给出的是观测特征而不是记录编号时，使用 diagnose_fault，不要把记录分析工具当作文字特征诊断工具。"
    "可以解释 detect_anomaly 返回的指标、历史中位数、MAD、Robust Z-score 和检测状态。"
    "统计偏离不等于设备故障，更不等于正式设备报警。"
    "Robust Z-score 绝对值大于 3.5 是实验性统计判据，不是官方报警限值。"
    "基线不足时必须说明无法进行可靠比较，不能写成设备正常。"
    "工况不可比、MAD 为 0 或其他无法判定情况，必须保留工具返回的真实原因。"
    "只有真实观测支持、并且工具标明完整条件已经满足时，才能形成候选故障。"
    "diagnostic_clue 只代表部分线索，不代表 possible_match。possible_match 也不是确诊。"
    "不得将 Robust Z-score、BPFO 幅值升高、X 大于 Y 等单独指标直接转换成某种已经发生的故障。"
    "缺失关键数据时，必须说明无法验证哪些诊断条件，不能补造峭度、相位、2X 幅值或频谱形态。"
    "不得编造故障严重程度、故障概率、根因或正式报警。"
    "不得编造故障类型、严重度、故障概率、故障原因、处置措施。"
    "用户进一步询问可能故障时，只能结合其他工具和用户确实提供的观测特征分析，不能单凭统计偏离或部分线索推断故障。"
    "不得获取或推测官方 Excel 已隔离的答案标签。"
    "问题需要多类资料时，可以组合调用这些工具，但观测数据和文档依据要分开说明。"
    "用户询问根因、复合故障或可能故障时，按需要组合已注册工具；统计偏离、部分线索和 possible_match 都不是确诊。"
    "证据不足时说明无法判断，不得编造测量值、文档引用、设备关系或因果链。"
    "diagnose_fault 返回的 possible_match 只能解释为可能匹配，不能写成已确诊、已确定或必然是该故障。"
    "只有工具真实执行并返回的内容才可以作为证据。没有实际执行 detect_anomaly 或 analyze_equipment_record 时，不能假装已经检测或完成完整分析。"
    "不得编造故障概率、严重程度、报警阈值、故障原因或处置建议。"
    "资料中明确记载的内容作为事实说明。"
    "资料没有直接写明、需要据此作出的判断，必须标明这是推断。"
    "工具没有返回依据时，说明现有官方资料不能支持该结论。"
    "使用中文回答。"
)
TOOL_LIMIT_NOTICE = (
    "工具调用已达到 3 轮上限。请只根据前面已经返回的工具结果回答。"
    "此后再提出的工具调用不会执行，不能假定这些工具已经执行，也不能编造其返回内容。"
)


def ask_with_tools(question: str) -> dict:
    """让 DeepSeek 自行选择已注册工具。调用顺序由模型决定，结果再交回模型继续推理。"""
    from app.services.agent_tools import execute_tool
    from app.services.supervisor import SupervisorTraceError, run_supervisor, save_supervisor_trace

    api_key = _api_key()
    if not api_key:
        raise DeepSeekError(
            "未配置 DeepSeek API Key。请在 backend/.env 中填写 DEEPSEEK_API_KEY 后重新发送。",
            status_code=500,
        )

    client = OpenAI(
        api_key=api_key,
        base_url=BASE_URL,
        timeout=TIMEOUT_SECONDS,
        max_retries=0,
    )

    def complete(messages: list[dict], use_tools: bool):
        return _tool_completion(client, messages, use_tools)

    try:
        outcome = run_supervisor(
            question,
            complete,
            execute=execute_tool,
            max_rounds=MAX_TOOL_ROUNDS,
            system_prompt=TOOL_SYSTEM_PROMPT,
            limit_notice=TOOL_LIMIT_NOTICE,
        )
    except DeepSeekError:
        raise
    except Exception as exc:
        _raise_api_error(exc)
        raise
    finally:
        client.close()
    from app.services.fault_revision import run_closed_loop

    loop = run_closed_loop(
        str(outcome.get("question") or question),
        outcome.get("tool_results"),
        execute=execute_tool,
        physics=_research_physics,
        model_answer=str(outcome.get("answer") or ""),
        max_rounds=_revision_rounds(),
    )
    report = loop["fault_report"]
    outcome["fault_report"] = report
    outcome["revision"] = {"stop_reason": loop["stop_reason"], "rounds": loop["rounds"]}
    if loop["applicable"]:
        outcome["tool_results"] = loop["tool_results"]
        outcome["steps"] = list(outcome.get("steps") or []) + list(loop["extra_steps"])
    evidence = outcome.get("evidence") if isinstance(outcome.get("evidence"), dict) else {}
    evidence["fault_hypotheses"] = report.get("hypotheses") or []
    evidence["missing_capabilities"] = report.get("missing_capabilities") or []
    evidence["consistency"] = report.get("consistency") or {}
    evidence["revision_rounds"] = loop["rounds"]
    evidence["stop_reason"] = loop["stop_reason"]
    outcome["evidence"] = evidence
    try:
        saved = save_supervisor_trace(outcome)
        outcome["trace_saved"] = True
        outcome["trace_database"] = saved["database"]
    except SupervisorTraceError as exc:
        outcome["trace_saved"] = False
        outcome["trace_error"] = exc.message
    return outcome


def _revision_rounds() -> int:
    raw = os.environ.get("FAULT_REVISION_ROUNDS", "3").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 3
    return max(1, min(value, 3))


def _research_physics(entity_a: str, entity_b: str, record_id: str) -> dict:
    """复用现有 L2。失败时保留错误，不把异常写成校验通过。"""
    from app.services.knowledge_physical_validator import validate_record_physics, validate_research_physics

    try:
        if not str(entity_a or "").strip() and not str(entity_b or "").strip():
            result = validate_record_physics(record_id)
        else:
            result = validate_research_physics(entity_a, entity_b, record_id)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"L2 物理校验没有完成：{exc}",
            "causal_conclusion": False,
            "written_to_graph": False,
            "contradicted_checks": [],
            "missing_measurements": [],
            "physical_checks": [],
        }
    return result


def _tool_completion(client: OpenAI, messages: list[dict], use_tools: bool):
    options = {
        "model": MODEL,
        "messages": messages,
        "extra_body": {"thinking": {"type": "disabled"}},
        "max_tokens": 1024,
    }
    if use_tools:
        options["tools"] = TOOLS
        options["tool_choice"] = "auto"
    response = client.chat.completions.create(**options)
    if not response.choices:
        raise DeepSeekError("DeepSeek 没有返回有效回答，请稍后重试。", status_code=502)
    return response.choices[0].message


def _raise_api_error(exc: Exception) -> None:
    if isinstance(exc, AuthenticationError):
        logger.warning("DeepSeek authentication failed")
        raise DeepSeekError(
            "DeepSeek API Key 无效或未授权，请检查 backend/.env 中的密钥。",
            status_code=401,
        ) from None
    if isinstance(exc, RateLimitError):
        logger.warning("DeepSeek rate limit reached")
        raise DeepSeekError("DeepSeek 请求过于频繁，请稍后再试。", status_code=429) from None
    if isinstance(exc, APITimeoutError):
        logger.warning("DeepSeek request timed out")
        raise DeepSeekError("连接 DeepSeek 超时，请稍后重试。", status_code=504) from None
    if isinstance(exc, APIConnectionError):
        logger.warning("DeepSeek connection failed")
        raise DeepSeekError("无法连接 DeepSeek 服务，请检查网络后重试。", status_code=502) from None
    if isinstance(exc, APIStatusError):
        logger.warning("DeepSeek returned HTTP %s", exc.status_code)
        raise DeepSeekError(_status_message(exc.status_code), status_code=_public_status(exc.status_code)) from None
    logger.warning("DeepSeek request failed with an unexpected error")
    raise DeepSeekError("调用 DeepSeek 时发生未知错误，请稍后重试。", status_code=502) from None


def _status_message(status_code: int) -> str:
    if status_code == 401:
        return "DeepSeek API Key 无效或未授权，请检查 backend/.env 中的密钥。"
    if status_code == 402:
        return "DeepSeek 账户余额不足，请充值后再试。"
    if status_code == 429:
        return "DeepSeek 请求过于频繁，请稍后再试。"
    if status_code in {500, 503}:
        return "DeepSeek 服务暂时不可用，请稍后重试。"
    return "DeepSeek 请求失败，请稍后重试。"


def _public_status(status_code: int) -> int:
    if status_code in {401, 402, 429}:
        return status_code
    return 502
