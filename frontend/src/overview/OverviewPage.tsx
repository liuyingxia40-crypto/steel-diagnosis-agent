import { useState } from "react"
import { DataStatusBar } from "./DataStatusBar"
import { DEMO_LINE } from "./demoData"
import { OverviewHeader } from "./OverviewHeader"
import { PhysicalSystemPanel } from "./PhysicalSystemPanel"
import { ScadaPanel } from "./ScadaPanel"
import type { ViewSelection } from "./model"
import "./overview.css"

export function OverviewPage() {
  const [selection, setSelection] = useState<ViewSelection>({ type: "overview" })

  return (
    <main className="overview">
      <OverviewHeader />
      <div className="ov-workspace">
        <PhysicalSystemPanel line={DEMO_LINE} selection={selection} onSelect={setSelection} />
        <ScadaPanel
          line={DEMO_LINE}
          selection={selection}
          onOverview={() => setSelection({ type: "overview" })}
        />
      </div>
      <DataStatusBar line={DEMO_LINE} />
    </main>
  )
}
