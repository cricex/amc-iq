import { FileText } from 'lucide-react'
import type { Criterion, Eligibility } from '../types'
import { AnswerBody } from './AnswerBody'
import { BlockerCards } from './BlockerCards'
import { CriteriaFindings } from './CriteriaFindings'
import { StatusBanner } from './StatusBanner'

interface AssessmentCardProps {
  eligibility: Eligibility
  answer?: string
  intent?: string
  criteria: Criterion[]
  missingData: string[]
}

type SectionKey = 'summary' | 'criteria' | 'blockers' | 'narrative'

/** Which structured sections each question intent renders, in order. Structured cards come before the
 * detailed narrative. External context omits criteria/blockers so it never reads as an eligibility
 * decision. Bottom line and next action live in the action rail, not here. */
const SECTIONS_BY_INTENT: Record<string, SectionKey[]> = {
  eligibility: ['summary', 'criteria', 'blockers', 'narrative'],
  screening: ['summary', 'blockers', 'criteria', 'narrative'],
  protocol: ['summary', 'criteria', 'blockers', 'narrative'],
  data_gaps: ['summary', 'blockers', 'narrative'],
  workflow: ['summary', 'narrative'],
  evidence: ['summary', 'criteria', 'narrative'],
  external_context: ['summary', 'narrative'],
}

const confidenceLabel: Record<Eligibility['confidence'], string> = {
  low: 'Low',
  medium: 'Medium',
  high: 'High',
}

function stripMarkers(line: string): string {
  return line
    .replace(/^#{1,6}\s+/, '')
    .replace(/^>\s+/, '')
    .replace(/^[-*+]\s+/, '')
    .replace(/^\d+[.)]\s+/, '')
    .replace(/\*\*/g, '')
    .replace(/`/g, '')
    .trim()
}

/** Extract the first one or two prose sentences from the agent's markdown answer. Strips leading
 * markdown markers (headings, quotes, list bullets, ordered-list numbers) and inline emphasis so a
 * bold or bulleted opener still yields a visible lead, and splits sentences without breaking decimals
 * like "50.2 mL/min". Falls back to the first non-empty line so the card is never blank. */
function leadSummary(text: string): string {
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
  const prose = lines
    .map(stripMarkers)
    .find((line) => line.length >= 20 && /\s/.test(line) && !/^-{3,}$/.test(line))
  const lead = prose ?? (lines.length ? stripMarkers(lines[0]) : '')
  if (!lead) return ''
  const sentences = lead.match(/.+?[.!?](?=\s|$)/g)
  if (!sentences) return lead
  return sentences.slice(0, 2).join(' ').trim()
}

function criteriaForIntent(criteria: Criterion[], intent: string): Criterion[] {
  if (intent === 'screening') {
    return criteria.filter((criterion) => criterion.status !== 'met')
  }
  if (intent === 'protocol') {
    return criteria.filter((criterion) =>
      /prior|platinum|therapy|exclusion|consent|amendment/i.test(criterion.text),
    )
  }
  return criteria
}

export function AssessmentCard({
  eligibility,
  answer,
  intent = 'eligibility',
  criteria,
  missingData,
}: AssessmentCardProps) {
  const sections = SECTIONS_BY_INTENT[intent] ?? SECTIONS_BY_INTENT.eligibility
  const summary = answer ? leadSummary(answer) : ''
  const focusedCriteria = criteriaForIntent(criteria, intent)

  const renderSection = (key: SectionKey) => {
    switch (key) {
      case 'summary':
        return answer ? (
          summary ? (
            <p key="summary" className="assessment-summary">
              {summary}
            </p>
          ) : null
        ) : (
          <p key="summary" className="assessment-fallback">
            Run an assessment to see the agent respond to your question using the IQ layers.
          </p>
        )
      case 'criteria':
        return <CriteriaFindings key="criteria" criteria={focusedCriteria} />
      case 'blockers':
        return <BlockerCards key="blockers" missingData={missingData} criteria={criteria} />
      case 'narrative':
        return answer ? (
          <details key="narrative" className="assessment-narrative">
            <summary>
              <FileText size={14} strokeWidth={2.2} aria-hidden="true" />
              Detailed assessment
            </summary>
            <AnswerBody text={answer} />
          </details>
        ) : null
      default:
        return null
    }
  }

  return (
    <section className="assessment-card-main card" id="assessment">
      <div className="assessment-head">
        <span className="assessment-eyebrow">Assessment</span>
        <span className={`confidence-pill confidence-${eligibility.confidence}`}>
          Confidence: {confidenceLabel[eligibility.confidence]}
        </span>
      </div>

      <StatusBanner eligibility={eligibility} intent={intent} />

      {sections.map(renderSection)}
    </section>
  )
}
