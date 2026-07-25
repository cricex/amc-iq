import { AlertTriangle, RefreshCw } from 'lucide-react'

interface AssessmentFailureCardProps {
  message: string
  onRetry: () => void
}

export function AssessmentFailureCard({ message, onRetry }: AssessmentFailureCardProps) {
  return (
    <section className="assessment-failure card" id="assessment" role="alert" aria-live="assertive">
      <div className="assessment-failure-icon" aria-hidden="true">
        <AlertTriangle size={24} strokeWidth={2.2} />
      </div>
      <div className="assessment-failure-body">
        <span className="eyebrow">Assessment incomplete</span>
        <h2>The final assessment could not be composed</h2>
        <p className="assessment-failure-message">{message}</p>
        <p className="assessment-failure-guidance">
          Completed source activity remains visible above. Confirm the assessment services are available, then retry.
        </p>
        <button type="button" className="assessment-retry-button" onClick={onRetry}>
          <RefreshCw size={16} strokeWidth={2.3} aria-hidden="true" />
          Retry assessment
        </button>
      </div>
    </section>
  )
}
