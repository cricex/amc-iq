import { UserCheck } from 'lucide-react'
import { READY_FOR_PI_REVIEW, screeningStatus } from '../sentiment'
import type { Eligibility, Reviewer } from '../types'
import { StatusChip } from './StatusChip'

interface CaseReviewCardProps {
  eligibility: Eligibility
  reviewers: Reviewer[]
}

const reviewerStatusLabel: Record<Reviewer['status'], string> = {
  assigned: 'Assigned',
  pending: 'Pending',
  complete: 'Complete',
}

function initials(name: string): string {
  return name
    .replace(/^Dr\.?\s+/i, '')
    .split(/\s+/)
    .map((part) => part[0])
    .slice(0, 2)
    .join('')
    .toUpperCase()
}

/** Operational case status for the action rail: screening readiness, PI review state, and the named
 * coordinator + PI with their review status. Uses its own class names (no shared pill styling). */
export function CaseReviewCard({ eligibility, reviewers }: CaseReviewCardProps) {
  const screening = screeningStatus(eligibility.assessment)
  const pi = reviewers.find((reviewer) => /pi|investigator|sign/i.test(reviewer.role))

  return (
    <section className="rail-card">
      <div className="rail-card-head">
        <UserCheck size={14} strokeWidth={2.4} aria-hidden="true" />
        Review status
      </div>
      <div className="rail-status-chips">
        <StatusChip meta={screening} />
        {pi && pi.status !== 'complete' ? <StatusChip meta={READY_FOR_PI_REVIEW} /> : null}
      </div>
      <ul className="rail-reviewers">
        {reviewers.map((reviewer) => (
          <li key={reviewer.owner.id}>
            <span className="rail-avatar" aria-hidden="true">
              {initials(reviewer.owner.display)}
            </span>
            <div className="rail-reviewer-info">
              <span className="rail-reviewer-role">{reviewer.role}</span>
              <span className="rail-reviewer-name">{reviewer.owner.display}</span>
            </div>
            <span className={`rail-reviewer-state state-${reviewer.status}`}>
              {reviewerStatusLabel[reviewer.status]}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}
