import { sourceClass, sourceIcon } from '../sourceMeta'
import { statusLabel } from '../sourceMeta'
import type { SourceMapEntry } from '../types'

interface IQActivityBarProps {
  entries: SourceMapEntry[]
}

function isActive(status: SourceMapEntry['status']): boolean {
  return status === 'queued' || status === 'searching'
}

export function IQActivityBar({ entries }: IQActivityBarProps) {
  return (
    <section className="iq-activity" id="sources" aria-label="IQ layer activity">
      <div className="iq-activity-head">
        <span className="eyebrow">IQ layer activity</span>
        <span className="muted">Four context providers running in parallel</span>
      </div>
      <div className="iq-activity-grid">
        {entries.map((entry) => (
          <div
            key={entry.source}
            className={`iq-chip ${sourceClass(entry.source)} status-${entry.status}`}
          >
            <div className="iq-chip-head">
              <span className="iq-chip-name">
                {(() => {
                  const Icon = sourceIcon[entry.source]
                  return <Icon size={15} strokeWidth={2.2} aria-hidden="true" />
                })()}
                {entry.label}
              </span>
              <span className={`iq-state ${isActive(entry.status) ? 'pulsing' : ''}`}>
                {statusLabel(entry.status)}
              </span>
            </div>
            <p className="iq-chip-retrieving">{entry.retrieving ?? 'Preparing retrieval intent'}</p>
            <div className="iq-chip-foot">
              {entry.status === 'failed' ? (
                <span className="iq-count failed">Unavailable</span>
              ) : (entry.evidenceCount ?? entry.citations.length) > 0 ? (
                <a
                  className="iq-count"
                  href="#evidence"
                  onClick={(event) => {
                    event.preventDefault()
                    document.getElementById('evidence')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                  }}
                >
                  {(entry.evidenceCount ?? entry.citations.length) || 0} {entry.evidenceNoun ?? 'citations'}
                </a>
              ) : (
                <span className="iq-count muted">{statusLabel(entry.status)}</span>
              )}
              {entry.durationMs ? <span className="iq-latency">{Math.round(entry.durationMs)} ms</span> : null}
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
