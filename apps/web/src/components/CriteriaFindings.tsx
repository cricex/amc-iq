import { ListChecks } from 'lucide-react'
import { criterionGlyph, criterionTone } from '../sentiment'
import type { Criterion } from '../types'
import { StatusChip } from './StatusChip'

interface CriteriaFindingsProps {
  criteria: Criterion[]
}

/** Renders each protocol criterion as a status-chip card (MET / NEEDS REVIEW / NOT MET CURRENTLY)
 * with a clinical glyph and links to the supporting evidence rows. */
export function CriteriaFindings({ criteria }: CriteriaFindingsProps) {
  if (!criteria.length) return null
  return (
    <div className="assessment-section">
      <div className="assessment-section-head">
        <ListChecks size={14} strokeWidth={2.4} aria-hidden="true" />
        Criteria match
      </div>
      <div className="criteria-findings">
        {criteria.map((criterion) => {
          const meta = criterionTone(criterion.status)
          const Glyph = criterionGlyph(criterion.text)
          return (
            <div key={criterion.text} className={`criterion-row tone-${meta.tone}`}>
              <Glyph className="criterion-glyph" size={17} strokeWidth={2} aria-hidden="true" />
              <div className="criterion-body">
                <div className="criterion-top">
                  <span className="criterion-text">{criterion.text}</span>
                  <StatusChip meta={meta} />
                </div>
                {criterion.evidenceRefs.length ? (
                  <span className="criterion-evidence">
                    Evidence:{' '}
                    {criterion.evidenceRefs.map((ref, index) => (
                      <span key={ref}>
                        {index > 0 ? ', ' : ''}
                        <a
                          href="#evidence"
                          onClick={(event) => {
                            event.preventDefault()
                            document
                              .getElementById('evidence')
                              ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                          }}
                        >
                          {ref}
                        </a>
                      </span>
                    ))}
                  </span>
                ) : null}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
