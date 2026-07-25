import { CalendarClock, Droplet, FlaskConical } from 'lucide-react'
import type { Trial } from '../types'

interface TrialCardProps {
  trial: Trial
  crclDate: string
}

export function TrialCard({ trial, crclDate }: TrialCardProps) {
  return (
    <section className="trial-card card">
      <div className="trial-main">
        <span className="eyebrow">Trial</span>
        <h2 className="trial-name">
          <FlaskConical size={18} strokeWidth={2.2} aria-hidden="true" />
          {trial.humanName || trial.short}
        </h2>
        <p className="trial-meta">
          Registry ID: <strong>{trial.id}</strong>
          <span className="dot-sep" aria-hidden="true">
            &middot;
          </span>
          Status: <strong>{trial.status}</strong>
        </p>
        <p className="trial-meta muted">
          {[trial.phase, trial.sponsor ? `Sponsor: ${trial.sponsor}` : '']
            .filter(Boolean)
            .join('  \u00b7  ')}
        </p>
      </div>
      {trial.keyIssue ? (
        <div className="trial-issue">
          <span className="eyebrow">
            <Droplet size={13} strokeWidth={2.2} aria-hidden="true" />
            Key issue
          </span>
          <p>{trial.keyIssue}</p>
        </div>
      ) : null}
      <div className="trial-updated">
        <span className="eyebrow">
          <CalendarClock size={13} strokeWidth={2.2} aria-hidden="true" />
          Latest data
        </span>
        <dl className="trial-data-list">
          <div>
            <dt>Patient data</dt>
            <dd>{crclDate}</dd>
          </div>
          {trial.latestProtocol ? (
            <div>
              <dt>Protocol data</dt>
              <dd>{trial.latestProtocol}</dd>
            </div>
          ) : null}
          <div>
            <dt>Environment</dt>
            <dd className="muted">Synthetic sandbox</dd>
          </div>
        </dl>
      </div>
    </section>
  )
}
