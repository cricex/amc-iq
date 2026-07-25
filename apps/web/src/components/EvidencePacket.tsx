import { sourceClass, sourceLabels } from '../sourceMeta'
import type { Criterion, Evidence } from '../types'
import { evidenceHref, sourceTypeLabel } from './evidencePresentation'

interface EvidencePacketProps {
  evidence: Evidence[]
  criteria: Criterion[]
  intent?: string
}

interface Row {
  finding: string
  source: Evidence['source']
  evidence: string
  sourceType: string
  impact: string
  href: string | undefined
}

function impactFor(criterion: Criterion | undefined, item: Evidence): string {
  if (criterion) {
    if (criterion.status === 'met') return 'Meets criterion'
    if (criterion.status === 'not_met') return 'Does not meet criterion'
    if (/crcl/i.test(criterion.text)) return 'Below protocol threshold'
    if (/platinum/i.test(criterion.text)) return 'Requires PI interpretation'
    return 'Requires review'
  }
  if (item.source === 'work') return 'Supports trial follow up'
  if (item.source === 'web') return 'External context'
  return 'Supporting evidence'
}

function findingFor(criterion: Criterion | undefined, item: Evidence): string {
  if (criterion) return criterion.text
  if (item.source === 'work') return 'Care team workflow context'
  if (item.source === 'web') return 'External treatment landscape'
  return item.title
}

export function EvidencePacket({ evidence, criteria, intent = 'eligibility' }: EvidencePacketProps) {
  const showCriterionFindings = !['workflow', 'external_context'].includes(intent)
  const rows: Row[] = evidence.map((item) => {
    const criterion = showCriterionFindings
      ? criteria.find((c) => c.evidenceRefs.includes(item.refId))
      : undefined
    return {
      finding: findingFor(criterion, item),
      source: item.source,
      evidence: item.title,
      sourceType: sourceTypeLabel(item.sourceType),
      impact: impactFor(criterion, item),
      href: evidenceHref(item.url),
    }
  })

  return (
    <section className="evidence-card card" id="evidence">
      <div className="card-header">
        <span className="eyebrow">Evidence packet</span>
        <span className="muted">{evidence.length} sources</span>
      </div>
      <div className="evidence-scroll">
        <table className="evidence-table">
          <thead>
            <tr>
              <th>Finding</th>
              <th>Source</th>
              <th>Evidence</th>
              <th>Impact</th>
              <th>Link</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={`${row.finding}-${index}`}>
                <td className="cell-finding">{row.finding}</td>
                <td>
                  <span className={`source-tag ${sourceClass(row.source)}`}>{sourceLabels[row.source]}</span>
                </td>
                <td className="cell-evidence">
                  <span className="evidence-title">{row.evidence}</span>
                  <span className="evidence-source-type">{row.sourceType}</span>
                </td>
                <td className="cell-impact">{row.impact}</td>
                <td>
                  {row.href ? (
                    <a
                      href={row.href}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="evidence-link"
                      aria-label={`Open ${row.evidence}`}
                    >
                      Open source
                    </a>
                  ) : (
                    <span className="muted">Not linked</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
