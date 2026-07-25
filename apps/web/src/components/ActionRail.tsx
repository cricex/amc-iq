import { ShieldCheck } from 'lucide-react'
import type { AskResult, TraceStep } from '../types'
import { AssessmentSteps } from './AssessmentSteps'
import { BottomLineCard } from './BottomLineCard'
import { CaseReviewCard } from './CaseReviewCard'
import { NextActionCard } from './NextActionCard'
import { ScopeCard } from './ScopeCard'

interface ActionRailProps {
  result: AskResult
  trace: TraceStep[]
}

/** Intents whose primary output is a governed action; these show the Next Action checklist. */
const ACTION_INTENTS = new Set(['eligibility', 'screening', 'workflow', 'data_gaps'])

/** The 40% executive/clinical action column. Adapts to the question intent: external context shows a
 * scope card instead of a task panel, other intents show Bottom Line + Next Action + Review Status. */
export function ActionRail({ result, trace }: ActionRailProps) {
  const intent = result.intent ?? 'eligibility'
  const isExternal = intent === 'external_context'

  return (
    <aside className="action-rail" id="workflow">
      {isExternal ? (
        <>
          <ScopeCard scope={result.scope ?? ''} />
          <BottomLineCard text={result.bottomLine ?? ''} />
        </>
      ) : (
        <>
          <BottomLineCard text={result.bottomLine ?? ''} />
          {ACTION_INTENTS.has(intent) ? <NextActionCard action={result.nextAction} /> : null}
          <CaseReviewCard eligibility={result.eligibility} reviewers={result.reviewers} />
        </>
      )}

      <AssessmentSteps trace={trace} />

      <section className="rail-card safety-notice">
        <div className="rail-card-head">
          <ShieldCheck size={14} strokeWidth={2.4} aria-hidden="true" />
          Safety
        </div>
        <p className="muted">
          Synthetic AMC sandbox. No PHI. Not clinical decision support. Requires clinician and PI review.
        </p>
      </section>
    </aside>
  )
}
