import { Compass } from 'lucide-react'
import { INFO_ONLY } from '../sentiment'
import { StatusChip } from './StatusChip'

interface ScopeCardProps {
  scope: string
}

/** External-context questions get a scope card instead of an action/task panel, so the answer never
 * reads as an eligibility decision. */
export function ScopeCard({ scope }: ScopeCardProps) {
  return (
    <section className="rail-card rail-card-info">
      <div className="rail-card-head">
        <Compass size={14} strokeWidth={2.4} aria-hidden="true" />
        Scope
        <StatusChip meta={INFO_ONLY} />
      </div>
      <p className="bottom-line-text">
        {scope || 'External public context only. Not used by itself to determine AMC trial eligibility.'}
      </p>
      <dl className="rail-meta">
        <div>
          <dt>What it does not decide</dt>
          <dd>AMC trial eligibility or the screening decision.</dd>
        </div>
        <div>
          <dt>Suggested next step</dt>
          <dd>Use this context to inform the institutional criteria and PI review.</dd>
        </div>
      </dl>
    </section>
  )
}
