"""把当前记录里已经出现的证据缺口整理成补测任务。

只引用本条记录的诊断线索、未验证假设和 L2 状态。
不把知识库里的全部测量项推荐给每台设备，也不把上传或数值结果写成确诊。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

_RECORD_ID = re.compile(r"REC-[A-Za-z0-9-]+")
PENDING = "待补测"
UPLOADED = "已上传待校验"
INSUFFICIENT = "已校验但条件不足"
EVIDENCE = "已获得可用证据"
CLOSED = "已关闭"
_DIRECT = "直接缺口"
_RELATED = "同一条款未核对条件"
_PRIORITY_NOTE = "这是规则上的建议顺序，不是工业风险等级，也没有估计采集成本。"

_CATALOG = (
    {
        "section": "转子不平衡",
        "item_id": "dominant_1x",
        "feature_code": "dominant_frequency",
        "title": "1X是否为频谱主导峰",
        "measurement_item": "覆盖转频及邻近频率的振动时序",
        "data_type": "单通道振动时序 CSV",
        "upload_supported": True,
        "official_check_id": "dominant_1x",
        "supplement_check_id": "supplement_dominant_1x",
        "verifiable_condition": "补充信号的主峰是否靠近这条记录的转频。算出或反对该条件也不是故障确诊。",
        "purpose": "当前转子不平衡条款要求核对 1X 是否为主导峰。记录里的 1X 幅值不能证明主导峰。",
    },
    {
        "section": "转子不平衡",
        "item_id": "second_harmonic_ratio",
        "feature_code": "harmonic_2x",
        "title": "2X与1X比值",
        "measurement_item": "同一振动信号中的 1X 和 2X 频率峰",
        "data_type": "单通道振动时序 CSV",
        "upload_supported": True,
        "official_check_id": "second_harmonic_ratio",
        "supplement_check_id": "supplement_second_harmonic_ratio",
        "verifiable_condition": "同一补充信号内能否同时找到 1X 和 2X 并计算比值。没有故障阈值。",
        "purpose": "当前转子不平衡条款列有 2X 与 1X 的比例。官方记录没有 2X 幅值。",
    },
    {
        "section": "转子不平衡",
        "item_id": "phase_stability",
        "feature_code": "phase",
        "title": "相位",
        "measurement_item": "带键相参考的 1X 相位稳定性，以及负荷变化下的幅值",
        "data_type": "同步多通道振动 CSV；键相通道目前仍缺失",
        "upload_supported": True,
        "upload_mode": "synchronous",
        "official_check_id": "phase_stability",
        "supplement_check_id": "supplement_phase",
        "evidence_check_id": "supplement_interchannel_phase",
        "verifiable_condition": "KB-02 要求「1X相位稳定，幅值随负荷变化」。通道间相位差不是键相绝对相位，也不能单独满足该条款。",
        "purpose": "当前转子不平衡条款写了相位。可上传同步 X/Y 作为新增测量证据，但没有键相和负荷序列时不能关闭该补测任务。",
    },
    {
        "section": "轴承磨损",
        "item_id": "waveform_statistics",
        "feature_code": "kurtosis",
        "title": "峭度与峰值因子",
        "measurement_item": "原始振动波形",
        "data_type": "单通道振动时序 CSV",
        "upload_supported": True,
        "official_check_id": "waveform_statistics",
        "supplement_check_id": "supplement_waveform_statistics",
        "verifiable_condition": "补充波形能否算出 Pearson 峭度和峰值因子。没有冲击故障阈值。",
        "purpose": "轴承磨损条款中的峭度和峰值因子都需要原始波形。只有该条款被当前线索或假设引用时才建议补测。",
    },
    {
        "section": "轴承磨损",
        "item_id": "bearing_harmonics",
        "feature_code": "envelope_spectrum",
        "title": "包络谱谐波形态",
        "measurement_item": "BPFO、BPFI 或 BSF 及其谐波的包络谱",
        "data_type": "带轴承几何参数的包络谱",
        "upload_supported": False,
        "official_check_id": "bpfo_harmonics",
        "supplement_check_id": "supplement_bearing_harmonics",
        "verifiable_condition": "包络谱是否出现对应特征频率及其谐波。频率峰不能代替这项判断。",
        "purpose": "轴承磨损条款要求包络谱谐波，不是单个 BPFO 幅值。当前算法不能从振动 CSV 编造谐波族。",
    },
    {
        "section": "齿轮异常",
        "item_id": "gear_sidebands",
        "feature_code": "sidebands",
        "title": "啮合频率边带",
        "measurement_item": "啮合频率附近的边带频谱",
        "data_type": "边带频谱",
        "upload_supported": False,
        "official_check_id": "",
        "supplement_check_id": "",
        "verifiable_condition": "1X 调制边带是否密集。振动总幅值不能证明边带。",
        "purpose": "齿轮异常条款写了边带。没有齿轮线索或齿轮假设时，不向其他设备推荐这项测量。",
    },
    {
        "section": "润滑故障",
        "item_id": "friction_noise",
        "feature_code": "noise",
        "title": "金属摩擦异音",
        "measurement_item": "声音记录",
        "data_type": "声音记录",
        "upload_supported": False,
        "official_check_id": "",
        "supplement_check_id": "",
        "verifiable_condition": "是否存在金属摩擦异音。振动 CSV 不能代替声音。",
        "purpose": "润滑故障条款写了异音。没有润滑线索或润滑假设时，不推荐这项测量。",
    },
)

_CREATE_TASKS = """
CREATE TABLE IF NOT EXISTS followup_tasks (
    task_id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    condition_key TEXT NOT NULL,
    title TEXT NOT NULL,
    measurement_item TEXT NOT NULL,
    purpose TEXT NOT NULL,
    missing_evidence TEXT NOT NULL,
    knowledge_ref TEXT NOT NULL,
    data_type TEXT NOT NULL,
    verifiable_condition TEXT NOT NULL,
    priority TEXT NOT NULL,
    priority_basis TEXT NOT NULL,
    status TEXT NOT NULL,
    gap_class TEXT NOT NULL,
    upload_supported INTEGER NOT NULL,
    official_check_id TEXT NOT NULL,
    supplement_check_id TEXT NOT NULL,
    official_check_status TEXT NOT NULL,
    supplement_check_status TEXT NOT NULL,
    measurement_ids_json TEXT NOT NULL,
    condition_text TEXT NOT NULL,
    section TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(record_id, item_id, condition_key)
)
"""
_CREATE_EVENTS = """
CREATE TABLE IF NOT EXISTS followup_task_events (
    event_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""


class FollowupTaskError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def database_path() -> Path:
    override = os.environ.get("FOLLOWUP_TASK_DB", "").strip()
    root = Path(__file__).resolve().parents[3]
    path = Path(override) if override else root / "data" / "runtime" / "followup_tasks.sqlite3"
    return _assert_runtime_location(path)


def list_followup_tasks(record_id: str) -> list[dict]:
    identity = _identity(record_id)
    connection = _open_readonly()
    if connection is None:
        return []
    try:
        rows = connection.execute(
            "SELECT * FROM followup_tasks WHERE record_id = ?",
            (identity,),
        ).fetchall()
        events = connection.execute(
            "SELECT * FROM followup_task_events WHERE record_id = ? ORDER BY created_at, rowid",
            (identity,),
        ).fetchall()
    finally:
        connection.close()
    grouped: dict[str, list[dict]] = {}
    for event in events:
        grouped.setdefault(event["task_id"], []).append(_public_event(event))
    tasks = [_public_task(row, grouped.get(row["task_id"], [])) for row in rows]
    rank = {_DIRECT: 0, _RELATED: 1}
    return sorted(tasks, key=lambda item: (rank.get(str(item["priority"]), 9), str(item["item_id"])))


def generate_followup_tasks(record_id: str) -> dict:
    """按当前真实线索和 L2 结果追加或更新任务。重复调用不复制待办。"""
    identity = _identity(record_id)
    analysis, physics, hypotheses = _evidence(identity)
    plan = _plan(analysis, physics, hypotheses)
    if not plan["eligible"]:
        return _empty_plan(identity, analysis, plan["eligibility_reason"])
    saved, created = _save_plan(identity, str(analysis["observation"]["device_id"]), plan, physics)
    return _response(identity, str(analysis["observation"]["device_id"]), plan, saved, created)


def sync_followup_tasks(record_id: str, physics: dict | None = None) -> dict:
    """用已有 L2 更新任务状态。没有任务库时不创建，也不把上传当成完成。"""
    identity = _identity(record_id)
    if not database_path().is_file():
        return {"ok": True, "record_id": identity, "tasks": [], "updated": False, "confirmed": False, "written_to_graph": False}
    existing = list_followup_tasks(identity)
    if not existing:
        return {"ok": True, "record_id": identity, "tasks": [], "updated": False, "confirmed": False, "written_to_graph": False}
    report = physics if isinstance(physics, dict) and physics.get("physical_checks") else None
    if report is None:
        from app.services.knowledge_physical_validator import validate_record_physics

        report = validate_record_physics(identity)
    measurements = _measurements(identity)
    _apply_report(identity, existing, report, measurements)
    return {
        "ok": True,
        "record_id": identity,
        "tasks": list_followup_tasks(identity),
        "updated": True,
        "confirmed": False,
        "written_to_graph": False,
        "official_excel_modified": False,
    }


def _evidence(record_id: str) -> tuple[dict, dict, list[dict]]:
    from app.services.diagnostic_bridge import analyze_record_for_diagnosis
    from app.services.knowledge_physical_validator import validate_record_physics

    analysis = analyze_record_for_diagnosis(record_id)
    if analysis.get("ok") is not True:
        raise FollowupTaskError(str(analysis.get("error") or "没有读到这条记录，不能生成补测任务。"), 422)
    observation = analysis.get("observation") if isinstance(analysis.get("observation"), dict) else {}
    if not str(observation.get("device_id") or "").strip():
        raise FollowupTaskError("记录没有设备编号，不能生成补测任务。", 422)
    physics = validate_record_physics(record_id)
    return analysis, physics, _hypotheses(record_id)


def _hypotheses(record_id: str) -> list[dict]:
    from app.services.diagnosis_history import latest_state

    state = latest_state(record_id) or {}
    rows = []
    for item in state.get("hypotheses") or []:
        if not isinstance(item, dict) or item.get("compound"):
            continue
        if item.get("verification_status") == "被证据否定":
            continue
        faults = item.get("faults") if isinstance(item.get("faults"), list) else []
        if faults:
            rows.append(item)
    return rows


def _plan(analysis: dict, physics: dict, hypotheses: list[dict]) -> dict:
    detected = analysis.get("statistical_deviation") if isinstance(analysis.get("statistical_deviation"), dict) else {}
    clues = [item for item in analysis.get("diagnostic_clues") or [] if isinstance(item, dict) and item.get("partial_condition_met") is True]
    sections = _active_sections(clues, hypotheses)
    eligible = detected.get("detection_status") == "偏离基线" or bool(clues) or bool(hypotheses)
    if not eligible:
        return {
            "eligible": False,
            "eligibility_reason": "当前记录没有统计异常、诊断线索或未验证假设，不生成补测建议。",
            "tasks": [],
            "already_calculated": [],
            "not_applicable": [],
        }
    checks = {item.get("check_id"): item for item in physics.get("physical_checks") or [] if isinstance(item, dict)}
    features = {item.get("feature_code"): item for item in analysis.get("feature_checks") or [] if isinstance(item, dict)}
    tasks = []
    not_applicable = []
    for spec in _CATALOG:
        feature = features.get(spec["feature_code"])
        if spec["section"] not in sections or feature is None:
            not_applicable.append(
                {
                    "item_id": spec["item_id"],
                    "title": spec["title"],
                    "section": spec["section"],
                    "gap_class": "not_applicable",
                    "reason": f"KB-02「{spec['section']}」没有被当前诊断线索或未验证假设引用，不把「{spec['title']}」推荐给这条记录。",
                }
            )
            continue
        official = checks.get(spec["official_check_id"]) if spec["official_check_id"] else None
        if official is not None and official.get("status") == "not_applicable":
            not_applicable.append(
                {
                    "item_id": spec["item_id"],
                    "title": spec["title"],
                    "section": spec["section"],
                    "gap_class": "not_applicable",
                    "reason": official.get("limit") or "当前设备或假设不适用该条款。",
                }
            )
            continue
        if official is not None and official.get("status") == "consistent":
            continue
        tasks.append(_task_from_spec(spec, feature, official, clues, hypotheses))
    tasks.extend(_direct_gaps(clues))
    temperature = checks.get("kb02_temperature_threshold")
    if temperature is not None and temperature.get("status") == "not_applicable":
        not_applicable.append(
            {
                "item_id": "kb02_temperature_threshold",
                "title": "KB-02 温度数值子句",
                "section": "润滑故障",
                "gap_class": "not_applicable",
                "reason": "当前没有润滑或温升对象，不套用 KB-02 温度数值子句。",
            }
        )
    already = []
    for check_id, title in (("speed_frequency", "转速与转频"), ("xy_vibration", "X向与Y向振动")):
        check = checks.get(check_id)
        if check is not None and check.get("status") == "consistent":
            already.append(
                {
                    "check_id": check_id,
                    "title": title,
                    "gap_class": "already_calculated",
                    "status": "consistent",
                    "reason": f"{title}已经由现有记录算出，状态是一致。不需要再把它列为补测，也不能据此确诊。",
                }
            )
    if not tasks and sections:
        reason = "当前线索或假设没有留下仍缺少测量的适用条款。"
    elif not tasks:
        reason = "有统计偏离，但没有诊断线索或未验证假设指向具体知识条款，所以不推荐测量项目。"
    else:
        reason = "只为当前线索或未验证假设引用的条款生成补测建议。"
    return {
        "eligible": True,
        "eligibility_reason": reason,
        "tasks": tasks,
        "already_calculated": already,
        "not_applicable": not_applicable,
        "sections": sorted(sections),
    }


def _task_from_spec(spec: dict, feature: dict, official: dict | None, clues: list[dict], hypotheses: list[dict]) -> dict:
    clue = next((item for item in clues if item.get("feature_code") == spec["feature_code"]), None)
    priority = _DIRECT if clue is not None else _RELATED
    if clue is not None:
        basis = f"诊断线索已经引用「{feature.get('official_condition')}」，而这项测量仍然缺失。{_PRIORITY_NOTE}"
    else:
        names = "、".join(fault for item in hypotheses for fault in item.get("faults") or [])
        source = f"未验证假设「{names}」" if names else "同一条当前线索所属的知识条款"
        basis = f"{source}引用了「{spec['section']}」，该项官方条件尚未核对。建议顺序低于线索里点名的缺口。{_PRIORITY_NOTE}"
    missing = feature.get("unverified_reason") or (official or {}).get("limit") or "官方记录没有这项测量。"
    return {
        "item_id": spec["item_id"],
        "condition_key": f"{spec['section']}:{spec['feature_code']}",
        "section": spec["section"],
        "title": spec["title"],
        "measurement_item": spec["measurement_item"],
        "purpose": spec["purpose"],
        "missing_evidence": str(missing),
        "knowledge_ref": f"{feature.get('document')} 第{feature.get('line_number')}行：{feature.get('official_condition')}",
        "data_type": spec["data_type"],
        "verifiable_condition": spec["verifiable_condition"],
        "priority": priority,
        "priority_basis": basis,
        "upload_supported": bool(spec["upload_supported"]),
        "upload_mode": spec.get("upload_mode") or ("single" if spec["upload_supported"] else "unsupported"),
        "official_check_id": spec["official_check_id"],
        "supplement_check_id": spec["supplement_check_id"],
        "evidence_check_id": spec.get("evidence_check_id") or "",
        "gap_class": "missing_measurement",
    }


def _direct_gaps(clues: list[dict]) -> list[dict]:
    found = []
    for clue in clues:
        if clue.get("feature_code") != "amplitude":
            continue
        text = f"{clue.get('official_condition') or ''}{clue.get('unverified_reason') or ''}"
        if "轴向" not in text:
            continue
        found.append(
            {
                "item_id": "axial_eccentricity",
                "condition_key": "转子不平衡:axial_eccentricity",
                "section": "转子不平衡",
                "title": "轴向偏心测量",
                "measurement_item": "轴向偏心或轴向位移",
                "purpose": "这条未完成线索写明径向 X>Y 之外还要轴向侧偏心。当前记录没有轴向偏心测量。",
                "missing_evidence": str(clue.get("unverified_reason") or "当前数据没有轴向偏心测量。"),
                "knowledge_ref": f"{clue.get('document')} 第{clue.get('line_number')}行：{clue.get('official_condition')}",
                "data_type": "轴向位移或偏心量",
                "verifiable_condition": "轴向侧是否偏心。振动速度 CSV 不能代替这项几何测量。",
                "priority": _DIRECT,
                "priority_basis": f"当前诊断线索直接写明缺少轴向偏心，所以建议先补这项。{_PRIORITY_NOTE}",
                "upload_supported": True,
                "upload_mode": "structured",
                "official_check_id": "",
                "supplement_check_id": "supplement_axial_eccentricity",
                "evidence_check_id": "supplement_axial_eccentricity",
                "gap_class": "missing_measurement",
            }
        )
    return found


def _active_sections(clues: list[dict], hypotheses: list[dict]) -> set[str]:
    sections = set()
    for clue in clues:
        section = str(clue.get("related_fault_section") or "").split(" / ", 1)[0].strip()
        if section:
            sections.add(section)
    known = {item["section"] for item in _CATALOG}
    for hypothesis in hypotheses:
        for fault in hypothesis.get("faults") or []:
            name = str(fault).strip()
            if name in known:
                sections.add(name)
            elif "润滑" in name:
                sections.add("润滑故障")
            elif "齿轮" in name:
                sections.add("齿轮异常")
    return sections


def _save_plan(record_id: str, device_id: str, plan: dict, physics: dict) -> tuple[list[dict], int]:
    measurements = _measurements(record_id)
    connection = _connect(database_path())
    created = 0
    try:
        connection.execute("BEGIN IMMEDIATE")
        owner = connection.execute(
            "SELECT device_id FROM followup_tasks WHERE record_id = ? LIMIT 1",
            (record_id,),
        ).fetchone()
        if owner is not None and owner["device_id"] != device_id:
            raise FollowupTaskError("这条记录的补测任务已经属于其他设备。")
        desired_keys = set()
        for task in plan["tasks"]:
            desired_keys.add((task["item_id"], task["condition_key"]))
            row = connection.execute(
                "SELECT * FROM followup_tasks WHERE record_id = ? AND item_id = ? AND condition_key = ?",
                (record_id, task["item_id"], task["condition_key"]),
            ).fetchone()
            if row is None:
                _insert_task(connection, record_id, device_id, task, physics, measurements)
                created += 1
            else:
                _refresh_row(connection, row, task, physics, measurements)
        _close_absent(connection, record_id, desired_keys)
        connection.execute("COMMIT")
    except FollowupTaskError:
        _rollback(connection)
        raise
    except sqlite3.Error as exc:
        _rollback(connection)
        raise FollowupTaskError(f"补测任务没有写入：{exc}") from exc
    finally:
        connection.close()
    return list_followup_tasks(record_id), created


def _insert_task(connection: sqlite3.Connection, record_id: str, device_id: str, task: dict, physics: dict, measurements: list[dict]) -> None:
    decision = _decide(task, PENDING, physics, measurements)
    now = _now()
    task_id = uuid.uuid4().hex
    connection.execute(
        """
        INSERT INTO followup_tasks (
            task_id, record_id, device_id, item_id, condition_key, title, measurement_item, purpose,
            missing_evidence, knowledge_ref, data_type, verifiable_condition, priority, priority_basis,
            status, gap_class, upload_supported, official_check_id, supplement_check_id,
            official_check_status, supplement_check_status, measurement_ids_json, condition_text,
            section, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            record_id,
            device_id,
            task["item_id"],
            task["condition_key"],
            task["title"],
            task["measurement_item"],
            task["purpose"],
            task["missing_evidence"],
            task["knowledge_ref"],
            task["data_type"],
            task["verifiable_condition"],
            task["priority"],
            task["priority_basis"],
            decision["status"],
            decision["gap_class"],
            1 if task["upload_supported"] else 0,
            task["official_check_id"],
            task["supplement_check_id"],
            decision["official_check_status"],
            decision["supplement_check_status"],
            _dumps(decision["measurement_ids"]),
            decision["condition_text"],
            task["section"],
            now,
            now,
        ),
    )
    previous = ""
    for step in decision["events"]:
        _event(connection, task_id, record_id, previous, step["status"], step["reason"])
        previous = step["status"]


def _refresh_row(connection: sqlite3.Connection, row: sqlite3.Row, task: dict, physics: dict, measurements: list[dict]) -> None:
    decision = _decide(task, row["status"], physics, measurements)
    connection.execute(
        """
        UPDATE followup_tasks SET
            title = ?, measurement_item = ?, purpose = ?, missing_evidence = ?, knowledge_ref = ?,
            data_type = ?, verifiable_condition = ?, priority = ?, priority_basis = ?, status = ?,
            gap_class = ?, upload_supported = ?, official_check_status = ?, supplement_check_status = ?,
            measurement_ids_json = ?, condition_text = ?, updated_at = ?
        WHERE task_id = ?
        """,
        (
            task["title"],
            task["measurement_item"],
            task["purpose"],
            task["missing_evidence"],
            task["knowledge_ref"],
            task["data_type"],
            task["verifiable_condition"],
            task["priority"],
            task["priority_basis"],
            decision["status"],
            decision["gap_class"],
            1 if task["upload_supported"] else 0,
            decision["official_check_status"],
            decision["supplement_check_status"],
            _dumps(decision["measurement_ids"]),
            decision["condition_text"],
            _now(),
            row["task_id"],
        ),
    )
    previous = row["status"]
    for step in decision["events"]:
        if step["status"] == previous:
            continue
        _event(connection, row["task_id"], row["record_id"], previous, step["status"], step["reason"])
        previous = step["status"]


def _apply_report(record_id: str, tasks: list[dict], physics: dict, measurements: list[dict]) -> None:
    connection = _connect(database_path())
    try:
        connection.execute("BEGIN IMMEDIATE")
        for task in tasks:
            if task["status"] == CLOSED:
                continue
            row = connection.execute("SELECT * FROM followup_tasks WHERE task_id = ?", (task["task_id"],)).fetchone()
            if row is None:
                continue
            decision = _decide(task, row["status"], physics, measurements)
            connection.execute(
                """
                UPDATE followup_tasks SET
                    status = ?, gap_class = ?, official_check_status = ?, supplement_check_status = ?,
                    measurement_ids_json = ?, condition_text = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (
                    decision["status"],
                    decision["gap_class"],
                    decision["official_check_status"],
                    decision["supplement_check_status"],
                    _dumps(decision["measurement_ids"]),
                    decision["condition_text"],
                    _now(),
                    row["task_id"],
                ),
            )
            previous = row["status"]
            for step in decision["events"]:
                if step["status"] == previous:
                    continue
                _event(connection, row["task_id"], record_id, previous, step["status"], step["reason"])
                previous = step["status"]
        connection.execute("COMMIT")
    except sqlite3.Error as exc:
        _rollback(connection)
        raise FollowupTaskError(f"补测任务状态没有更新：{exc}") from exc
    finally:
        connection.close()


def _close_absent(connection: sqlite3.Connection, record_id: str, desired: set[tuple[str, str]]) -> None:
    rows = connection.execute("SELECT * FROM followup_tasks WHERE record_id = ?", (record_id,)).fetchall()
    for row in rows:
        if (row["item_id"], row["condition_key"]) in desired or row["status"] != PENDING:
            continue
        connection.execute(
            "UPDATE followup_tasks SET status = ?, gap_class = ?, condition_text = ?, updated_at = ? WHERE task_id = ?",
            (CLOSED, "not_applicable", "当前线索或假设不再引用该条款，未开始的补测任务关闭。已有证据没有删除。", _now(), row["task_id"]),
        )
        _event(connection, row["task_id"], record_id, PENDING, CLOSED, "当前线索或假设不再引用该条款，任务关闭。这不是测量已经完成。")


def _decide(task: dict, current: str, physics: dict, measurements: list[dict]) -> dict:
    checks = {item.get("check_id"): item for item in physics.get("physical_checks") or [] if isinstance(item, dict)}
    official = checks.get(task.get("official_check_id")) if task.get("official_check_id") else None
    supplement = checks.get(task.get("supplement_check_id")) if task.get("supplement_check_id") else None
    evidence_id = task.get("evidence_check_id") or ("supplement_interchannel_phase" if task.get("item_id") == "phase_stability" else "")
    evidence = checks.get(evidence_id) if evidence_id else None
    official_status = str(official.get("status") or "") if official else ""
    supplement_status = str(supplement.get("status") or "") if supplement else ""
    mode = str(
        task.get("upload_mode")
        or (
            "synchronous"
            if task.get("item_id") == "phase_stability"
            else ("structured" if task.get("item_id") == "axial_eccentricity" else ("single" if task.get("upload_supported") else "unsupported"))
        )
    )
    if mode == "synchronous":
        ids = [
            str(item.get("measurement_id") or item.get("evidence_id") or "")
            for item in measurements
            if (item.get("measurement_id") or item.get("evidence_id")) and item.get("channel_mode") == "synchronous"
        ]
    elif mode == "structured":
        ids = [
            str(item.get("evidence_id") or "")
            for item in measurements
            if item.get("evidence_id") and item.get("evidence_kind") == "axial_eccentricity"
        ]
    elif mode == "single":
        ids = [
            str(item.get("measurement_id") or "")
            for item in measurements
            if item.get("measurement_id") and item.get("channel_mode") != "synchronous" and not item.get("evidence_kind")
        ]
    else:
        ids = [str(item.get("measurement_id") or item.get("evidence_id") or "") for item in measurements if item.get("measurement_id") or item.get("evidence_id")]
    supported = bool(task.get("upload_supported"))
    if task.get("item_id") == "axial_eccentricity":
        return _decide_axial(task, current, official_status, supplement, ids, measurements)
    if not supported:
        seen = f"已看到振动 CSV {'、'.join(ids)}，但它不能提供{task.get('data_type')}。" if ids else "还没有可用于这项的测量。"
        checked = f"补充检查 {task.get('supplement_check_id')} 仍是{_status_label(supplement_status)}。" if supplement_status else ""
        text = f"官方记录仍然缺少「{task.get('title')}」。{seen}{checked}任务保持待补测，不能把单通道 CSV 标成已完成。"
        return _decision(current if current in {PENDING, CLOSED} else PENDING, "missing_measurement", [], text, official_status, supplement_status, [])
    if mode == "synchronous":
        return _decide_phase(task, current, official_status, supplement, evidence, ids)
    if not ids:
        text = f"官方检查 {task.get('official_check_id') or task.get('title')} 是{_status_label(official_status)}。还没有合法关联的补充测量。任务状态：待补测。"
        return _decision(PENDING, "missing_measurement", [], text, official_status, supplement_status, [])
    if supplement_status in {"consistent", "contradicted"}:
        effect = "支持该数值条件" if supplement_status == "consistent" else "反对该数值条件"
        text = (
            f"补充前，官方检查 {task.get('official_check_id')} 是{_status_label(official_status)}。"
            f"补充后，{task.get('supplement_check_id')} 为 {supplement_status}，{effect}。"
            "官方原检查没有被改写成已有数据，这也不是故障确诊。"
        )
        reason = f"L2 补充检查 {task.get('supplement_check_id')} 为 {supplement_status}。上传本身没有被当成完成。"
        return _decision(EVIDENCE, "evidence_obtained", ids, text, official_status, supplement_status, _steps(current, EVIDENCE, ids, reason))
    if supplement_status == "insufficient_data":
        limit = str(supplement.get("limit") or "补充数据仍不足以核对这项条件。") if supplement else "补充数据仍不足以核对这项条件。"
        text = (
            f"补充前，官方检查 {task.get('official_check_id')} 是{_status_label(official_status)}。"
            f"补充后，{task.get('supplement_check_id')} 仍是数据不足：{limit}"
        )
        reason = f"L2 补充检查仍是数据不足：{limit}"
        return _decision(INSUFFICIENT, "supplement_insufficient", ids, text, official_status, supplement_status, _steps(current, INSUFFICIENT, ids, reason))
    text = "补充测量已关联，但对应校验还没有给出可用结果。任务停在已上传待校验，不能把上传当成完成。"
    return _decision(UPLOADED, "calculable_pending", ids, text, official_status, supplement_status, _steps(current, UPLOADED, ids, "振动 CSV 已合法关联，校验结果尚未可用。"))


def _decide_axial(task: dict, current: str, official_status: str, supplement: dict | None, ids: list[str], measurements: list[dict]) -> dict:
    supplement_status = str(supplement.get("status") or "") if supplement else ""
    wrong = []
    for item in measurements:
        kind = item.get("evidence_kind")
        if kind in {"axial_displacement", "axial_vibration"}:
            wrong.append(str(item.get("evidence_id") or ""))
        elif item.get("measurement_id") and not kind:
            wrong.append(str(item.get("measurement_id") or ""))
    wrong = [item for item in wrong if item]
    if ids and supplement_status == "consistent":
        text = (
            "已登记轴向偏心或轴向跳动，测量项目、单位、方法和参考基准齐全。"
            "这只表示轴向偏心测量已经获得，不能把轴向位移或轴向振动当成同一物理量，"
            "也不能把 KB-02 第38行整句条款标成已满足，更不是故障确诊。"
        )
        return _decision(EVIDENCE, "evidence_obtained", ids, text, official_status, supplement_status, _steps(current, EVIDENCE, ids, "轴向偏心结构化证据已通过登记校验。"))
    if wrong and not ids:
        labels = "、".join(item for item in wrong if item)
        text = f"官方记录仍然缺少「轴向偏心测量」。已看到其他测量 {labels}，但轴向位移、轴向振动或 X/Y 振动不能代替轴向偏心。任务保持待补测。"
        return _decision(PENDING, "missing_measurement", [], text, official_status, supplement_status, [])
    text = "官方记录仍然缺少「轴向偏心测量」。还没有带方法和参考基准的轴向偏心结构化证据。任务保持待补测。"
    return _decision(PENDING, "missing_measurement", [], text, official_status, supplement_status, [])


def _decide_phase(task: dict, current: str, official_status: str, supplement: dict | None, evidence: dict | None, ids: list[str]) -> dict:
    supplement_status = str(supplement.get("status") or "") if supplement else ""
    evidence_status = str(evidence.get("status") or "") if evidence else ""
    computed = bool((evidence or {}).get("computed", {}).get("interchannel_phase_computed")) if isinstance(evidence, dict) else False
    if not ids:
        text = (
            f"官方检查 {task.get('official_check_id')} 是{_status_label(official_status)}。"
            "还没有同步多通道测量。单通道不能生成通道间相位。缺少键相参考时也不能生成绝对相位。"
        )
        return _decision(PENDING, "missing_measurement", [], text, official_status, supplement_status, [])
    if supplement_status == "consistent" and (supplement or {}).get("computed", {}).get("reference_matches_clause") is True:
        text = "KB-02 相位条款所需的参考和稳定性已经核对。这仍然不是故障确诊。"
        return _decision(EVIDENCE, "evidence_obtained", ids, text, official_status, supplement_status, _steps(current, EVIDENCE, ids, "相位条款所需参考已匹配。"))
    difference = (evidence or {}).get("computed", {}).get("phase_difference_deg") if isinstance(evidence, dict) else None
    if computed and evidence_status == "consistent":
        text = (
            f"补充前，官方检查 {task.get('official_check_id')} 是{_status_label(official_status)}。"
            f"补充后已算出通道间 1X 相位差 {difference}°，但缺少键相参考，不能生成绝对相位，"
            "也不能把 KB-02「1X相位稳定，幅值随负荷变化」标成已满足。任务停在已校验但条件不足。"
        )
        reason = "已有通道间相位差证据，但与 KB-02 相位条款的参考定义不一致。"
        return _decision(INSUFFICIENT, "reference_mismatch", ids, text, official_status, supplement_status, _steps(current, INSUFFICIENT, ids, reason))
    limit = str((supplement or evidence or {}).get("limit") or "同步数据仍不足以给出可靠相位。")
    text = (
        f"补充前，官方检查 {task.get('official_check_id')} 是{_status_label(official_status)}。"
        f"补充后仍不能完成相位条款：{limit}"
    )
    return _decision(INSUFFICIENT, "supplement_insufficient", ids, text, official_status, supplement_status, _steps(current, INSUFFICIENT, ids, limit))


def _steps(current: str, target: str, measurement_ids: list[str], reason: str) -> list[dict]:
    if current == target:
        return []
    labels = "、".join(measurement_ids)
    uploaded_reason = f"振动 CSV {labels} 已合法关联。上传本身不是校验完成。"
    if current == PENDING and target in {INSUFFICIENT, EVIDENCE}:
        return [{"status": UPLOADED, "reason": uploaded_reason}, {"status": target, "reason": reason}]
    if current == UPLOADED and target in {INSUFFICIENT, EVIDENCE}:
        return [{"status": target, "reason": reason}]
    if current == PENDING and target == UPLOADED:
        return [{"status": UPLOADED, "reason": uploaded_reason}]
    return [{"status": target, "reason": reason}]


def _decision(status: str, gap_class: str, measurement_ids: list[str], text: str, official_status: str, supplement_status: str, events: list[dict]) -> dict:
    if status == PENDING and not events:
        events = [{"status": PENDING, "reason": "根据当前诊断线索或未验证假设生成。官方记录缺少这项测量。这不是故障确诊。"}]
    return {
        "status": status,
        "gap_class": gap_class,
        "measurement_ids": measurement_ids,
        "condition_text": text,
        "official_check_status": official_status,
        "supplement_check_status": supplement_status,
        "events": events,
    }


def _measurements(record_id: str) -> list[dict]:
    from app.services.diagnostic_evidence import DiagnosticEvidenceError, list_evidence
    from app.services.supplementary_measurement import SupplementaryMeasurementError, list_for_record, list_synchronous_for_record

    rows = []
    try:
        rows.extend(list_for_record(record_id))
        rows.extend(list_synchronous_for_record(record_id))
    except SupplementaryMeasurementError:
        pass
    try:
        rows.extend(list_evidence(record_id))
    except DiagnosticEvidenceError:
        pass
    return [item for item in rows if isinstance(item, dict)]


def _response(record_id: str, device_id: str, plan: dict, tasks: list[dict], created: int) -> dict:
    return {
        "ok": True,
        "record_id": record_id,
        "device_id": device_id,
        "eligible": True,
        "eligibility_reason": plan["eligibility_reason"],
        "tasks": tasks,
        "already_calculated": plan["already_calculated"],
        "not_applicable": plan["not_applicable"],
        "created_count": created,
        "confirmed": False,
        "written_to_graph": False,
        "causal_conclusion": False,
        "official_excel_modified": False,
        "hypothesis_created": False,
    }


def _empty_plan(record_id: str, analysis: dict, reason: str) -> dict:
    observation = analysis.get("observation") if isinstance(analysis.get("observation"), dict) else {}
    return {
        "ok": True,
        "record_id": record_id,
        "device_id": str(observation.get("device_id") or ""),
        "eligible": False,
        "eligibility_reason": reason,
        "tasks": list_followup_tasks(record_id),
        "already_calculated": [],
        "not_applicable": [],
        "created_count": 0,
        "confirmed": False,
        "written_to_graph": False,
        "causal_conclusion": False,
        "official_excel_modified": False,
        "hypothesis_created": False,
    }


def _public_task(row: sqlite3.Row, events: list[dict]) -> dict:
    return {
        "task_id": row["task_id"],
        "record_id": row["record_id"],
        "device_id": row["device_id"],
        "item_id": row["item_id"],
        "condition_key": row["condition_key"],
        "title": row["title"],
        "measurement_item": row["measurement_item"],
        "purpose": row["purpose"],
        "missing_evidence": row["missing_evidence"],
        "knowledge_ref": row["knowledge_ref"],
        "data_type": row["data_type"],
        "verifiable_condition": row["verifiable_condition"],
        "priority": row["priority"],
        "priority_basis": row["priority_basis"],
        "status": row["status"],
        "gap_class": row["gap_class"],
        "upload_supported": bool(row["upload_supported"]),
        "upload_mode": (
            "synchronous"
            if row["item_id"] == "phase_stability" and bool(row["upload_supported"])
            else ("structured" if row["item_id"] == "axial_eccentricity" and bool(row["upload_supported"]) else ("single" if bool(row["upload_supported"]) else "unsupported"))
        ),
        "official_check_id": row["official_check_id"],
        "supplement_check_id": row["supplement_check_id"],
        "official_check_status": row["official_check_status"],
        "supplement_check_status": row["supplement_check_status"],
        "measurement_ids": _loads(row["measurement_ids_json"], []),
        "condition_text": row["condition_text"],
        "section": row["section"],
        "events": events,
        "confirmed": False,
        "written_to_graph": False,
        "supports_fault": False,
    }


def _public_event(row: sqlite3.Row) -> dict:
    return {
        "from_status": row["from_status"],
        "to_status": row["to_status"],
        "reason": row["reason"],
        "created_at": row["created_at"],
    }


def _event(connection: sqlite3.Connection, task_id: str, record_id: str, previous: str, status: str, reason: str) -> None:
    connection.execute(
        """
        INSERT INTO followup_task_events (event_id, task_id, record_id, from_status, to_status, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (uuid.uuid4().hex, task_id, record_id, previous, status, reason, _now()),
    )


def _status_label(status: str) -> str:
    return {
        "consistent": "一致",
        "contradicted": "矛盾",
        "insufficient_data": "数据不足",
        "not_applicable": "不适用",
    }.get(status, "没有结果")


def _identity(record_id: str) -> str:
    identity = record_id.strip() if isinstance(record_id, str) else ""
    if _RECORD_ID.fullmatch(identity) is None:
        raise FollowupTaskError("记录编号格式不正确。")
    return identity


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str, fallback: object) -> object:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return fallback
    return parsed if isinstance(parsed, list) else fallback


def _open_readonly() -> sqlite3.Connection | None:
    path = database_path()
    if not path.is_file():
        return None
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def _connect(path: Path) -> sqlite3.Connection:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=5, isolation_level=None)
    except sqlite3.Error as exc:
        raise FollowupTaskError(f"补测任务数据库无法打开：{exc}") from exc
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute(_CREATE_TASKS)
        connection.execute(_CREATE_EVENTS)
    except sqlite3.Error as exc:
        connection.close()
        raise FollowupTaskError(f"补测任务数据库无法初始化：{exc}") from exc
    return connection


def _rollback(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error:
        return


def _assert_runtime_location(path: Path) -> Path:
    root = Path(__file__).resolve().parents[3]
    resolved = path if path.is_absolute() else root / path
    try:
        resolved = resolved.resolve()
    except OSError as exc:
        raise FollowupTaskError(f"补测任务数据库无法打开：{exc}") from exc
    forbidden = (
        (root / "data" / "official").resolve(),
        (root / "data" / "official_dataset").resolve(),
        (root / "data" / "processed").resolve(),
    )
    if any(resolved == base or base in resolved.parents for base in forbidden):
        raise FollowupTaskError("补测任务不能写入官方数据目录。")
    return resolved
