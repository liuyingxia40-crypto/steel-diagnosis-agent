"""一维振动信号的时域指标和 FFT 频谱。

只计算信号指标。不判断故障，不访问外部模型接口，也不换算不同的物理单位。
当前环境没有 numpy 或 scipy，因此 FFT 使用标准库实现。
"""

from __future__ import annotations

import math

MIN_SAMPLES = 8
MAX_SAMPLES = 65536
MAX_PEAKS = 5
PEAK_RELATIVE_THRESHOLD = 0.05
_ANALYSIS_NOTE = "本结果只包含信号指标，不判断故障、报警阈值、概率或严重程度。"


def analyze_vibration(samples, sampling_rate=None, unit=None) -> dict:
    """分析一维振动采样。幅值保持输入单位，频率单位为 Hz。"""
    parsed_unit, unit_error = _parse_unit(unit)
    if unit_error:
        return _failure(unit_error, parsed_unit)
    rate, rate_error = _parse_sampling_rate(sampling_rate)
    if rate_error:
        return _failure(rate_error, parsed_unit, sampling_rate=rate)
    values, sample_error = _parse_samples(samples)
    if sample_error:
        count = len(values) if values is not None else None
        return _failure(sample_error, parsed_unit, sampling_rate=rate, sample_count=count)
    count = len(values)
    if count > MAX_SAMPLES:
        return _failure(
            f"样本数量超过 {MAX_SAMPLES}。当前环境没有 numpy，不能继续分析更长的数据。",
            parsed_unit,
            sampling_rate=rate,
            sample_count=count,
        )
    if count < MIN_SAMPLES:
        return _failure(
            f"样本数量不足，至少需要 {MIN_SAMPLES} 个采样点。",
            parsed_unit,
            sampling_rate=rate,
            sample_count=count,
        )

    duration = count / rate
    mean = sum(values) / count
    rms = math.sqrt(sum(value * value for value in values) / count)
    maximum = max(values)
    minimum = min(values)
    peak_absolute = max(abs(value) for value in values)
    peak_to_peak = maximum - minimum
    second_moment = sum((value - mean) ** 2 for value in values) / count
    notes = []

    if rms == 0:
        crest_factor = None
        notes.append("均方根为零，峰值因子无法定义。")
    else:
        crest_factor = peak_absolute / rms

    if second_moment == 0:
        kurtosis = None
        notes.append("方差为零，Pearson 峭度无法定义。这里不计算超额峭度。")
    else:
        fourth_moment = sum((value - mean) ** 4 for value in values) / count
        kurtosis = fourth_moment / (second_moment * second_moment)

    spectrum = _spectrum(values, mean, rate)
    return {
        "ok": True,
        "error": None,
        "unit": parsed_unit,
        "unit_note": "幅值使用输入信号的原始单位，没有换算。频率单位是 Hz。",
        "sample_count": count,
        "sampling_rate_hz": rate,
        "duration_seconds": duration,
        "mean": mean,
        "rms": rms,
        "peak_absolute": peak_absolute,
        "peak_to_peak": peak_to_peak,
        "crest_factor": crest_factor,
        "kurtosis_pearson": kurtosis,
        "kurtosis_type": "pearson",
        "kurtosis_note": "这是四阶中心矩除以方差平方，不是超额峭度。",
        "fft": spectrum,
        "notes": notes,
        "conclusions": {
            "faults": [],
            "alarms": [],
            "probabilities": [],
            "note": _ANALYSIS_NOTE,
        },
    }


def _parse_unit(unit) -> tuple[str, str | None]:
    if unit is None:
        return "未知", None
    if not isinstance(unit, str):
        return "未知", "unit 必须是文本；未知单位请留空。"
    text = unit.strip()
    if not text:
        return "未知", None
    return text, None


def _parse_sampling_rate(sampling_rate) -> tuple[float | None, str | None]:
    if sampling_rate is None:
        return None, "sampling_rate 必填，单位是 Hz。"
    if isinstance(sampling_rate, bool) or not isinstance(sampling_rate, (int, float)):
        return None, "sampling_rate 必须是大于 0 的数值，单位是 Hz。"
    if not math.isfinite(sampling_rate) or sampling_rate <= 0:
        return None, "sampling_rate 必须是大于 0 的有限数值，单位是 Hz。"
    return float(sampling_rate), None


def _parse_samples(samples) -> tuple[list[float] | None, str | None]:
    if isinstance(samples, (str, bytes)) or not isinstance(samples, (list, tuple)):
        return None, "samples 必须是一维数值列表。"
    if len(samples) == 0:
        return [], "采样数据为空。"
    values = []
    for item in samples:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None, "采样数据包含字符串或其他非数值，不能参与计算。"
        if not math.isfinite(item):
            return None, "采样数据包含 NaN 或 Infinity，不能参与计算。"
        values.append(float(item))
    return values, None


def _failure(
    message: str,
    unit: str,
    sampling_rate: float | None = None,
    sample_count: int | None = None,
) -> dict:
    return {
        "ok": False,
        "error": message,
        "unit": unit,
        "unit_note": "幅值使用输入信号的原始单位，没有换算。频率单位是 Hz。",
        "sample_count": sample_count,
        "sampling_rate_hz": sampling_rate,
        "duration_seconds": None,
        "mean": None,
        "rms": None,
        "peak_absolute": None,
        "peak_to_peak": None,
        "crest_factor": None,
        "kurtosis_pearson": None,
        "kurtosis_type": "pearson",
        "kurtosis_note": "这是四阶中心矩除以方差平方，不是超额峭度。计算不能成立时为空。",
        "fft": None,
        "notes": [message],
        "conclusions": {
            "faults": [],
            "alarms": [],
            "probabilities": [],
            "note": _ANALYSIS_NOTE,
        },
    }


def _spectrum(values: list[float], mean: float, sampling_rate: float) -> dict:
    centered = [value - mean for value in values]
    if all(value == 0 for value in centered):
        return {
            "window": "hann",
            "dc_removed": True,
            "fft_length": None,
            "frequency_resolution_hz": None,
            "peaks": [],
            "amplitude_note": "去除直流后没有交流成分，不报告频率峰。",
        }
    window = _hann(len(centered))
    gain = sum(window)
    windowed = [value * weight for value, weight in zip(centered, window)]
    fft_length = _next_power_of_two(len(windowed))
    padded = windowed + [0.0] * (fft_length - len(windowed))
    transformed = _fft([complex(value, 0.0) for value in padded])
    resolution = sampling_rate / fft_length
    amplitudes = []
    frequencies = []
    last_bin = fft_length // 2
    for bin_index in range(1, last_bin):
        frequencies.append(bin_index * resolution)
        amplitudes.append(2 * abs(transformed[bin_index]) / gain)
    frequencies.append(last_bin * resolution)
    amplitudes.append(abs(transformed[last_bin]) / gain)
    return {
        "window": "hann",
        "dc_removed": True,
        "fft_length": fft_length,
        "frequency_resolution_hz": resolution,
        "peaks": _main_peaks(frequencies, amplitudes),
        "amplitude_note": "频率峰幅值是单边谱峰值，已按 Hann 窗的相干增益修正，单位与输入信号相同。",
    }


def _hann(length: int) -> list[float]:
    scale = 2 * math.pi / (length - 1)
    return [0.5 * (1 - math.cos(scale * index)) for index in range(length)]


def _next_power_of_two(length: int) -> int:
    size = 1
    while size < length:
        size *= 2
    return size


def _fft(values: list[complex]) -> list[complex]:
    count = len(values)
    reversed_index = 0
    for index in range(1, count):
        bit = count >> 1
        while reversed_index & bit:
            reversed_index ^= bit
            bit >>= 1
        reversed_index ^= bit
        if index < reversed_index:
            values[index], values[reversed_index] = values[reversed_index], values[index]
    length = 2
    while length <= count:
        angle = -2 * math.pi / length
        step = complex(math.cos(angle), math.sin(angle))
        half = length // 2
        for start in range(0, count, length):
            rotation = 1 + 0j
            for offset in range(half):
                even = values[start + offset]
                odd = values[start + offset + half] * rotation
                values[start + offset] = even + odd
                values[start + offset + half] = even - odd
                rotation *= step
        length *= 2
    return values


def fft_components(samples, sampling_rate) -> dict:
    """返回与 analyze_vibration 相同窗函数下的频率仓相位。

    相位只用于同一套处理下的通道间差值。单独一个仓的相位不是键相绝对相位。
    """
    rate, rate_error = _parse_sampling_rate(sampling_rate)
    values, sample_error = _parse_samples(samples)
    if rate_error or sample_error or values is None or rate is None:
        return {"ok": False, "error": rate_error or sample_error or "振动数据无法分析。", "bins": []}
    if len(values) < MIN_SAMPLES or len(values) > MAX_SAMPLES:
        return {"ok": False, "error": "样本数量不适合当前 FFT。", "bins": []}
    mean = sum(values) / len(values)
    centered = [value - mean for value in values]
    window = _hann(len(centered))
    gain = sum(window)
    if gain == 0 or all(value == 0 for value in centered):
        return {"ok": False, "error": "去除直流后没有交流成分。", "bins": []}
    windowed = [value * weight for value, weight in zip(centered, window)]
    fft_length = _next_power_of_two(len(windowed))
    padded = windowed + [0.0] * (fft_length - len(windowed))
    transformed = _fft([complex(value, 0.0) for value in padded])
    resolution = rate / fft_length
    bins = []
    last_bin = fft_length // 2
    for bin_index in range(1, last_bin + 1):
        component = transformed[bin_index]
        scale = 1 if bin_index == last_bin else 2
        bins.append(
            {
                "bin_index": bin_index,
                "frequency_hz": bin_index * resolution,
                "amplitude": scale * abs(component) / gain,
                "phase_rad": math.atan2(component.imag, component.real),
            }
        )
    return {
        "ok": True,
        "error": None,
        "fft_length": fft_length,
        "frequency_resolution_hz": resolution,
        "window": "hann",
        "bins": bins,
    }


def _main_peaks(frequencies: list[float], amplitudes: list[float]) -> list[dict]:
    if not amplitudes:
        return []
    maximum = max(amplitudes)
    if maximum <= 0:
        return []
    threshold = maximum * PEAK_RELATIVE_THRESHOLD
    peaks = []
    last = len(amplitudes) - 1
    for index, amplitude in enumerate(amplitudes):
        left = amplitudes[index - 1] if index > 0 else float("-inf")
        right = amplitudes[index + 1] if index < last else float("-inf")
        if amplitude >= left and amplitude >= right and amplitude >= threshold:
            peaks.append(
                {
                    "frequency_hz": frequencies[index],
                    "amplitude": amplitude,
                }
            )
    peaks.sort(key=lambda item: item["amplitude"], reverse=True)
    return peaks[:MAX_PEAKS]
