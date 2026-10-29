import {
  AUTO_MODE_LABEL,
  AUTO_POLL_MS,
  autoHeadline,
  duplicateStartText,
  isMissingAutoTask,
  parseAutoTask,
  parseAutoTaskList,
  pickRestoredTask,
  shouldPollAuto,
  startRequestBody,
} from "../frontend/src/monitoring/autoMonitor.ts"

const results: boolean[] = []

function check(name: string, passed: boolean, detail = ""): void {
  results.push(passed)
  console.log(`${passed ? "PASS" : "FAIL"} ${name}${detail ? " | " + detail : ""}`)
}

const task = {
  task_id: "auto-abc",
  device_id: "CC-01",
  status: "运行中",
  current_record_id: "REC-CC-1",
  processed_count: 3,
  deviation_count: 1,
  total_count: 400,
  last_processed_at: "2026-10-27T15:00:00+08:00",
  error_message: "",
  monitoring_note: "没有连接实时传感器",
}

check("轮询间隔约为2秒", AUTO_POLL_MS === 2000)
check("模式名称是官方历史数据模拟监测", AUTO_MODE_LABEL === "官方历史数据模拟监测")
check("启动请求只包含设备和1秒间隔", JSON.stringify(startRequestBody("CC-01")) === '{"device_id":"CC-01","interval_seconds":1}')
check("运行中显示历史数据模拟监测中", autoHeadline("运行中", "") === "历史数据模拟监测中" && shouldPollAuto("运行中"))
check("停止后显示已停止", autoHeadline("已停止", "") === "已停止" && !shouldPollAuto("已停止"))
check("完成后显示已完成", autoHeadline("已结束", "") === "已完成" && !shouldPollAuto("已结束"))
check("失败时显示真实错误", autoHeadline("已出错", "记录分析失败") === "记录分析失败")
check("能解析状态字段", parseAutoTask(task)?.current_record_id === "REC-CC-1" && parseAutoTask(task)?.processed_count === 3)
check("404空任务不是失败", isMissingAutoTask(404, { success: false, message: "没有找到自动监测任务。" }))
check("409使用后端提示", duplicateStartText(409, { success: false, message: "这台设备已有自动监测任务正在运行。" }) === "这台设备已有自动监测任务正在运行。")
const restored = pickRestoredTask([
  { ...task, status: "已结束", task_id: "auto-old" },
  { ...task, status: "运行中", task_id: "auto-live", processed_count: 8 },
])
check("刷新后优先恢复仍在运行的任务", restored?.task_id === "auto-live" && restored.processed_count === 8)
check("任务列表可解析", parseAutoTaskList({ tasks: [task] })?.length === 1)

if (results.some((item) => !item)) {
  process.exit(1)
}
