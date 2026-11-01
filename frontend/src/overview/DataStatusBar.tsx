import type { LineSnapshot } from "./model"

export function DataStatusBar({ line }: { line: LineSnapshot }) {
  return (
    <footer className="ov-status-bar">
      <span>数据更新 {line.updatedAt}</span>
      <span>连接状态 未连接 PLC / 现场采集</span>
      <span>首页视图 v1</span>
    </footer>
  )
}
