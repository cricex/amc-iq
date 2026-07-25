import { assessmentTone, toneIcon } from '../sentiment'
import type { Eligibility } from '../types'

interface StatusBannerProps {
  eligibility: Eligibility
  intent?: string
}

const HEADLINE: Record<Eligibility['assessment'], string> = {
  eligible: 'Meets trial criteria',
  likely_eligible_pending: 'Potential match, not yet confirmed eligible',
  not_eligible: 'Does not meet trial criteria',
  indeterminate: 'Needs human review',
}

/** For non-eligibility questions the banner headline reflects the QUESTION, not the match verdict,
 * so a data-gaps or workflow question never reads as "Potential match". The match status stays as
 * supporting context in the sub-line, and the tone color still reflects the true assessment. */
const INTENT_HEADLINE: Record<string, string> = {
  data_gaps: 'Data readiness for this case',
  workflow: 'Recommended next action and owner',
  evidence: 'Evidence packet for review',
  protocol: 'Protocol and exclusion interpretation',
}

/** Colored short-answer banner at the top of the assessment. External-context questions render an
 * informational blue banner so they never read as an eligibility decision. */
export function StatusBanner({ eligibility, intent }: StatusBannerProps) {
  if (intent === 'external_context') {
    const Icon = toneIcon('blue')
    return (
      <div className="status-banner banner-blue">
        <Icon className="banner-icon" size={20} strokeWidth={2.4} aria-hidden="true" />
        <div className="banner-text">
          <span className="banner-headline">External context only</span>
          <span className="banner-sub">Not used by itself to determine AMC trial eligibility.</span>
        </div>
      </div>
    )
  }

  const tone = assessmentTone(eligibility.assessment)
  const Icon = toneIcon(tone)
  const intentHeadline = intent ? INTENT_HEADLINE[intent] : undefined
  const headline = intentHeadline ?? HEADLINE[eligibility.assessment]
  const sub = intentHeadline ? `Trial-readiness status: ${eligibility.label}` : eligibility.label
  return (
    <div className={`status-banner banner-${tone}`}>
      <Icon className="banner-icon" size={20} strokeWidth={2.4} aria-hidden="true" />
      <div className="banner-text">
        <span className="banner-headline">{headline}</span>
        <span className="banner-sub">{sub}</span>
      </div>
    </div>
  )
}
