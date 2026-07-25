import { AlertTriangle } from 'lucide-react'
import { blockerTone } from '../sentiment'
import type { Criterion } from '../types'
import { StatusChip } from './StatusChip'

interface BlockerCardsProps {
  missingData: string[]
  criteria: Criterion[]
}

interface Blocker {
  title: string
  note: string
  severity: 'review' | 'hard'
}

/** Derives high-visibility blocker cards: not-met criteria are hard stops (red), open data items
 * from missingData are review blockers (amber). */
function deriveBlockers(missingData: string[], criteria: Criterion[]): Blocker[] {
  const hard: Blocker[] = criteria
    .filter((criterion) => criterion.status === 'not_met')
    .map((criterion) => ({ title: criterion.text, note: 'Fails a required trial criterion.', severity: 'hard' }))

  const review: Blocker[] = missingData.map((item) => {
    const [head, ...rest] = item.split(/\s*\(/)
    const note = rest.length ? `(${rest.join('(')}` : 'Required before formal screening.'
    return { title: head.trim(), note, severity: 'review' as const }
  })

  return [...hard, ...review]
}

export function BlockerCards({ missingData, criteria }: BlockerCardsProps) {
  const blockers = deriveBlockers(missingData, criteria)
  return (
    <div className="assessment-section">
      <div className="assessment-section-head">
        <AlertTriangle size={14} strokeWidth={2.4} aria-hidden="true" />
        Open issues
      </div>
      {blockers.length === 0 ? (
        <p className="muted">No screening blockers identified.</p>
      ) : (
        <div className="blocker-cards">
          {blockers.map((blocker, index) => {
            const meta = blockerTone(blocker.severity)
            const Glyph = meta.Icon
            return (
              <div key={`${blocker.severity}-${index}`} className={`blocker-card tone-${meta.tone}`}>
                <Glyph className="blocker-glyph" size={17} strokeWidth={2.2} aria-hidden="true" />
                <div className="blocker-body">
                  <div className="blocker-top">
                    <span className="blocker-title">{blocker.title}</span>
                    <StatusChip meta={meta} />
                  </div>
                  <span className="blocker-note">{blocker.note}</span>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
