import { Workflow } from 'lucide-react'
import type { TraceStep } from '../types'

interface AssessmentStepsProps {
  trace: TraceStep[]
}

const statusClass: Record<NonNullable<TraceStep['status']>, string> = {
  completed: 'step-completed',
  in_progress: 'step-in-progress',
  pending: 'step-pending',
  queued: 'step-queued',
}

export function AssessmentSteps({ trace }: AssessmentStepsProps) {
  const hasDetail = trace.some((step) => step.detail)
  return (
    <section className="rail-card">
      <div className="rail-card-head">
        <Workflow size={14} strokeWidth={2.4} aria-hidden="true" />
        Assessment steps
      </div>
      <ol className="steps-list compact">
        {trace.map((step) => (
          <li key={`${step.ts}-${step.step}`} className={`step-row ${statusClass[step.status ?? 'completed']}`}>
            <span className="step-dot" aria-hidden="true" />
            <span className="step-title">{step.step}</span>
          </li>
        ))}
      </ol>
      {hasDetail ? (
        <details className="steps-detail">
          <summary>Retrieval detail</summary>
          <ul className="steps-detail-list">
            {trace
              .filter((step) => step.detail)
              .map((step) => (
                <li key={`${step.ts}-detail`}>
                  <span className="steps-detail-title">{step.step}</span>
                  <span className="muted">{step.detail}</span>
                </li>
              ))}
          </ul>
        </details>
      ) : null}
    </section>
  )
}
