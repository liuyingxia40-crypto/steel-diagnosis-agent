"""振动分析算法测试。信号由数学公式生成，是算法测试信号，不是官方设备数据。"""

import inspect
import math
from pathlib import Path

from app.services.vibration_analysis import analyze_vibration

MODULE_PATH = Path(__file__).resolve().parents[1] / "backend" / "app" / "services" / "vibration_analysis.py"
CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append(ok)
    print(("PASS" if ok else "FAIL"), name, detail)


def sine_wave(frequency, sampling_rate, count, amplitude=1.0):
    return [
        amplitude * math.sin(2 * math.pi * frequency * index / sampling_rate)
        for index in range(count)
    ]


print("信号类型：算法测试信号。不是官方设备波形。")
samples = sine_wave(50, 1000, 1000)
result = analyze_vibration(samples, 1000, unit="mm/s")
peaks = (result.get("fft") or {}).get("peaks") or []
main_peak = peaks[0]["frequency_hz"] if peaks else None
resolution = (result.get("fft") or {}).get("frequency_resolution_hz")
print("A_PEAK", main_peak, "resolution", resolution, "peaks", len(peaks))
check(
    "A 主频接近 50 Hz",
    result.get("ok") is True and main_peak is not None and abs(main_peak - 50) <= resolution,
    f"peak={main_peak} resolution={resolution}",
)

expected_rms = 1 / math.sqrt(2)
expected_crest = math.sqrt(2)
print(
    "B_METRICS",
    "mean",
    result.get("mean"),
    "rms",
    result.get("rms"),
    "peak",
    result.get("peak_absolute"),
    "pp",
    result.get("peak_to_peak"),
    "crest",
    result.get("crest_factor"),
)
check("B 单位保持 mm/s", result.get("unit") == "mm/s")
check("B RMS 接近 1/sqrt(2)", abs(result["rms"] - expected_rms) < 1e-9, str(result["rms"]))
check("B 峰值接近 1", abs(result["peak_absolute"] - 1) < 1e-9, str(result["peak_absolute"]))
check("B 峰峰值接近 2", abs(result["peak_to_peak"] - 2) < 1e-9, str(result["peak_to_peak"]))
check("B 峰值因子接近 sqrt(2)", abs(result["crest_factor"] - expected_crest) < 1e-9, str(result["crest_factor"]))
check("B 正弦 Pearson 峭度接近 1.5", abs(result["kurtosis_pearson"] - 1.5) < 1e-9, str(result["kurtosis_pearson"]))

zeros = analyze_vibration([0] * 32, 1000)
constant = analyze_vibration([2] * 32, 1000)
print("C_ZERO", zeros.get("ok"), zeros.get("rms"), zeros.get("crest_factor"), zeros.get("kurtosis_pearson"), (zeros.get("fft") or {}).get("peaks"))
print("C_CONSTANT", constant.get("ok"), constant.get("mean"), constant.get("crest_factor"), constant.get("kurtosis_pearson"), (constant.get("fft") or {}).get("peaks"))
check(
    "C 全零信号不报错且峭度为空",
    zeros.get("ok") is True and zeros.get("rms") == 0 and zeros.get("crest_factor") is None and zeros.get("kurtosis_pearson") is None and (zeros.get("fft") or {}).get("peaks") == [],
)
check(
    "C 恒定信号不报错且峭度为空",
    constant.get("ok") is True and constant.get("mean") == 2 and constant.get("kurtosis_pearson") is None and (constant.get("fft") or {}).get("peaks") == [],
)

cases = {
    "采样频率缺失": analyze_vibration(samples),
    "采样频率为零": analyze_vibration(samples, 0),
    "采样频率为负": analyze_vibration(samples, -10),
    "采样频率为文本": analyze_vibration(samples, "1000"),
    "空数据": analyze_vibration([], 1000),
    "字符串样本": analyze_vibration(["1", "2", "3", "4", "5", "6", "7", "8"], 1000),
    "NaN": analyze_vibration([0, 1, float("nan"), 0, 1, 0, 1, 0], 1000),
    "Infinity": analyze_vibration([0, 1, float("inf"), 0, 1, 0, 1, 0], 1000),
}
for name, outcome in cases.items():
    print("D", name, outcome.get("ok"), outcome.get("error"), "rms", outcome.get("rms"))
    check(
        "D 拒绝 " + name,
        outcome.get("ok") is False and outcome.get("rms") is None and outcome.get("fft") is None,
        outcome.get("error"),
    )

unknown_unit = analyze_vibration(samples, 1000)
check("未知单位被明确标记", unknown_unit.get("unit") == "未知")

source = MODULE_PATH.read_text(encoding="utf-8")
source_ok = (
    "deepseek" not in source.casefold()
    and "openai" not in source.casefold()
    and "轴承磨损" not in source
    and "转子不平衡" not in source
    and "齿轮异常" not in source
    and "润滑故障" not in source
)
result_ok = (
    result["conclusions"]["faults"] == []
    and result["conclusions"]["alarms"] == []
    and result["conclusions"]["probabilities"] == []
)
modules = set(inspect.getmodule(analyze_vibration).__dict__)
import_ok = "openai" not in modules and "requests" not in modules
check("E 不调用 DeepSeek 且不生成故障或报警结论", source_ok and result_ok and import_ok)

print("SUMMARY", sum(1 for item in CHECKS if item), "/", len(CHECKS))
raise SystemExit(0 if all(CHECKS) else 1)
