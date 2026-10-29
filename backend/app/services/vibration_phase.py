"""计算同一同步文件中两路振动的 1X 相位差。

相位差是两个通道 FFT 仓相角之差。它不是相对于键相的绝对相位，
也不能代替 KB-02「1X相位稳定，幅值随负荷变化」。
"""

from __future__ import annotations

import math

from app.services.vibration_analysis import MIN_SAMPLES, analyze_vibration, fft_components

KB02_PHASE_CONDITION = "1X相位稳定，幅值随负荷变化"
KB02_PHASE_DOCUMENT = "KB-02_故障模式诊断知识库.md"
KB02_PHASE_LINE = 36
_MIN_CYCLES = 4


def rotation_target_hz(values: dict) -> tuple[float | None, str]:
    """用官方记录的转速和转频确定 1X。两者矛盾时不另选频率。"""
    frequency = _number(values.get("转频(Hz)"))
    rpm = _number(values.get("转速(rpm)"))
    if frequency is None and rpm is None:
        return None, "记录没有转速或转频，不能确定 1X。"
    if frequency is not None and rpm is not None:
        expected = rpm / 60
        tolerance = 0.5 * (10 ** (-_decimal_places(frequency)))
        if abs(frequency - expected) > tolerance:
            return None, "记录中的转速与转频不一致，不能用来确定 1X。"
        return frequency, ""
    if frequency is not None:
        return frequency, ""
    return rpm / 60 if rpm is not None else None, ""


def interchannel_phase(channel_a: list[float], channel_b: list[float], sampling_rate: float, target_hz: float, name_a: str, name_b: str) -> dict:
    """在两路样本长度、采样率和时间基准已经相同的前提下计算相位差。"""
    empty = _empty(name_a, name_b, target_hz)
    if len(channel_a) != len(channel_b):
        empty["reason"] = "两路样本长度不同，不能计算通道间相位差。"
        return empty
    if len(channel_a) < MIN_SAMPLES:
        empty["reason"] = "采样不足，不能计算通道间相位差。"
        return empty
    if not math.isfinite(target_hz) or target_hz <= 0:
        empty["reason"] = "没有可用的 1X 频率。"
        return empty
    duration = len(channel_a) / sampling_rate
    cycles = duration * target_hz
    if cycles < _MIN_CYCLES:
        empty["reason"] = "信号长度不足，1X 周期太少，不能输出可靠相位。"
        empty["cycles"] = cycles
        return empty
    if target_hz >= sampling_rate / 2:
        empty["reason"] = "1X 不低于奈奎斯特频率，不能输出可靠相位。"
        return empty
    first = _channel_component(channel_a, sampling_rate, target_hz)
    second = _channel_component(channel_b, sampling_rate, target_hz)
    empty["frequency_resolution_hz"] = first.get("frequency_resolution_hz")
    empty["fft_length"] = first.get("fft_length")
    empty["cycles"] = cycles
    empty["channels"] = [
        _public_channel(name_a, first),
        _public_channel(name_b, second),
    ]
    if first.get("ok") is not True or second.get("ok") is not True:
        empty["reason"] = first.get("reason") or second.get("reason") or "没有有效 1X 峰"
        return empty
    difference = _wrap_degrees(math.degrees(second["phase_rad"] - first["phase_rad"]))
    empty.update(
        {
            "phase_reliable": True,
            "interchannel_phase_computed": True,
            "phase_difference_deg": difference,
            "reason": "缺少键相参考。通道间 1X 相位差不是相对于键相的绝对相位，也不能把 KB-02 相位条款标成已满足。",
            "quality": "两路 1X 都落在转频对应的频率仓内，并且是各自频谱的主峰。相位差已按同一 Hann 窗和同一 FFT 长度计算。",
        }
    )
    return empty


def _channel_component(samples: list[float], sampling_rate: float, target_hz: float) -> dict:
    spectrum = fft_components(samples, sampling_rate)
    analysis = analyze_vibration(samples, sampling_rate, None)
    if spectrum.get("ok") is not True or analysis.get("ok") is not True:
        return {"ok": False, "reason": "没有有效 1X 峰", "frequency_1x_hz": None, "amplitude_1x": None}
    resolution = spectrum["frequency_resolution_hz"]
    bins = spectrum["bins"]
    nearest = min(bins, key=lambda item: abs(item["frequency_hz"] - target_hz))
    if abs(nearest["frequency_hz"] - target_hz) > resolution / 2:
        return {"ok": False, "reason": "没有有效 1X 峰", "frequency_resolution_hz": resolution, "fft_length": spectrum["fft_length"], "frequency_1x_hz": nearest["frequency_hz"], "amplitude_1x": nearest["amplitude"]}
    peaks = ((analysis.get("fft") or {}).get("peaks") or []) if isinstance(analysis.get("fft"), dict) else []
    matched = [peak for peak in peaks if abs(float(peak.get("frequency_hz") or 0) - target_hz) <= resolution / 2]
    if not matched:
        return {"ok": False, "reason": "没有有效 1X 峰", "frequency_resolution_hz": resolution, "fft_length": spectrum["fft_length"], "frequency_1x_hz": nearest["frequency_hz"], "amplitude_1x": nearest["amplitude"]}
    dominant = peaks[0] if peaks else None
    amplitude = float(matched[0].get("amplitude") or 0)
    dominant_amplitude = float(dominant.get("amplitude") or 0) if isinstance(dominant, dict) else 0
    if dominant_amplitude <= 0 or amplitude < dominant_amplitude * 0.5:
        return {"ok": False, "reason": "没有有效 1X 峰", "frequency_resolution_hz": resolution, "fft_length": spectrum["fft_length"], "frequency_1x_hz": nearest["frequency_hz"], "amplitude_1x": amplitude}
    return {
        "ok": True,
        "reason": "",
        "frequency_resolution_hz": resolution,
        "fft_length": spectrum["fft_length"],
        "frequency_1x_hz": nearest["frequency_hz"],
        "amplitude_1x": nearest["amplitude"],
        "phase_rad": nearest["phase_rad"],
        "bin_index": nearest["bin_index"],
    }


def _empty(name_a: str, name_b: str, target_hz: float) -> dict:
    return {
        "phase_reliable": False,
        "interchannel_phase_computed": False,
        "phase_attempted": True,
        "phase_difference_deg": None,
        "absolute_phase_deg": None,
        "from_channel": name_a,
        "to_channel": name_b,
        "reference_type": "interchannel",
        "keyphasor_present": False,
        "keyphasor_valid": False,
        "reference_matches_clause": False,
        "load_sequence_present": False,
        "stability_observed": False,
        "clause_satisfied": False,
        "kb02_condition": KB02_PHASE_CONDITION,
        "kb02_document": KB02_PHASE_DOCUMENT,
        "kb02_line": KB02_PHASE_LINE,
        "target_1x_hz": target_hz,
        "frequency_resolution_hz": None,
        "fft_length": None,
        "cycles": None,
        "channels": [],
        "reason": "没有有效 1X 峰",
        "quality": "相位差没有达到可靠计算条件。",
        "supports_fault": False,
        "values_merged": False,
    }


def _public_channel(name: str, component: dict) -> dict:
    return {
        "name": name,
        "frequency_1x_hz": component.get("frequency_1x_hz"),
        "amplitude_1x": component.get("amplitude_1x"),
        "reliable_1x": component.get("ok") is True,
    }


def _wrap_degrees(value: float) -> float:
    wrapped = (value + 180) % 360 - 180
    if wrapped == -180:
        return 180
    return wrapped


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _decimal_places(value: float) -> int:
    text = format(value, "f").rstrip("0").rstrip(".")
    if "." not in text:
        return 0
    return len(text.split(".", 1)[1])
