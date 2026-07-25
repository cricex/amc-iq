import type { FabricStatus } from '../types'
import { FabricStatusChip } from './FabricStatusChip'

interface TopBarProps {
  mode?: 'mock' | 'live'
  fabricStatus?: FabricStatus | null
}

export function TopBar({ mode, fabricStatus }: TopBarProps) {
  return (
    <header className="top-bar" id="assessment-top">
      <div className="top-bar-title">
        <h1>Precision Oncology Trial Readiness</h1>
        <p>Synthetic AMC sandbox. No PHI. Requires human review.</p>
      </div>
      <div className="top-bar-meta">
        <FabricStatusChip status={fabricStatus ?? null} />
        {mode ? (
          <span className={mode === 'live' ? 'mode-pill live' : 'mode-pill mock'}>
            {mode === 'live' ? 'Live IQ layers' : 'Sample data'}
          </span>
        ) : null}
      </div>
    </header>
  )
}
