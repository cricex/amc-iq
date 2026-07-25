import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { Criterion, Eligibility } from '../types'
import { AssessmentCard } from './AssessmentCard'

const eligibility: Eligibility = {
  assessment: 'likely_eligible_pending',
  label: 'Likely eligible pending review',
  confidence: 'medium',
}

const criteria: Criterion[] = [
  {
    text: 'Documented EGFR exon 20 insertion',
    status: 'met',
    evidenceRefs: [],
  },
  {
    text: 'Creatinine clearance >= 50 mL/min',
    status: 'uncertain',
    evidenceRefs: ['r3'],
  },
  {
    text: 'Prior platinum exclusion requires PI review',
    status: 'uncertain',
    evidenceRefs: ['r2', 'r4'],
  },
]

describe('AssessmentCard intent focus', () => {
  it('shows only unresolved criteria for screening questions', () => {
    const markup = renderToStaticMarkup(
      <AssessmentCard
        eligibility={eligibility}
        answer="Two blockers remain."
        intent="screening"
        criteria={criteria}
        missingData={[]}
      />,
    )

    expect(markup).not.toContain('Documented EGFR exon 20 insertion')
    expect(markup).toContain('Creatinine clearance')
    expect(markup).toContain('Prior platinum exclusion')
  })

  it('shows only protocol interpretation criteria for protocol questions', () => {
    const markup = renderToStaticMarkup(
      <AssessmentCard
        eligibility={eligibility}
        answer="PI interpretation is required."
        intent="protocol"
        criteria={criteria}
        missingData={[]}
      />,
    )

    expect(markup).toContain('Prior platinum exclusion')
    expect(markup).not.toContain('Creatinine clearance')
    expect(markup).not.toContain('Documented EGFR exon 20 insertion')
  })
})
