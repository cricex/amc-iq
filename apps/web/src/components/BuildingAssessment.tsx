import { useEffect, useRef } from 'react'
import type { SourceMapEntry } from '../types'

interface BuildingAssessmentProps {
  sources: SourceMapEntry[]
  draft: string
}

type StepState = 'done' | 'active' | 'pending'

const STEPS = [
  { title: 'Interpreting the clinical question', detail: 'Identifying the decision points the question is asking about.' },
  { title: 'Running parallel IQ retrieval', detail: 'Sending context requests to Foundry IQ, Fabric IQ, Work IQ, and Web IQ at the same time.' },
  { title: 'Matching patient facts to protocol criteria', detail: 'Mapping diagnosis, biomarker, ECOG, renal function, and prior therapy against the trial.' },
  { title: 'Detecting blockers and uncertainty', detail: 'Flagging values below threshold and language that needs human interpretation.' },
  { title: 'Preparing governed next action', detail: 'Drafting the recommended next action, PI review request, and evidence packet.' },
  { title: 'Composing final assessment', detail: 'Producing a cited, human reviewed recommendation.' },
]

export function BuildingAssessment({ sources, draft }: BuildingAssessmentProps) {
  const draftRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (draftRef.current) draftRef.current.scrollTop = draftRef.current.scrollHeight
  }, [draft])

  const retrievalStarted = sources.some((s) => s.status === 'searching' || s.status === 'complete' || s.status === 'failed')
  const retrievalDone = sources.length > 0 && sources.every((s) => s.status === 'complete' || s.status === 'failed')
  const drafting = draft.trim().length > 0

  const phase: 'retrieving' | 'synthesizing' | 'composing' = drafting
    ? 'composing'
    : retrievalDone
      ? 'synthesizing'
      : 'retrieving'

  function stateFor(index: number): StepState {
    switch (index) {
      case 0:
        return retrievalStarted ? 'done' : 'active'
      case 1:
        return phase === 'retrieving' ? 'active' : 'done'
      case 2:
      case 3:
      case 4:
        return phase === 'composing' ? 'done' : phase === 'synthesizing' ? 'active' : 'pending'
      case 5:
        return phase === 'composing' ? 'active' : 'pending'
      default:
        return 'pending'
    }
  }

  const cleanDraft = draft.replace(/\*\*/g, '').replace(/^#{1,4}\s+/gm, '')

  return (
    <section className="building-assessment card" aria-live="polite">
      <div className="building-head">
        <span className="building-title">
          Building Assessment
          <span className="thinking-dots" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
        </span>
        <span className="muted">Turning retrieved context into a governed assessment</span>
      </div>

      <ol className="building-steps">
        {STEPS.map((step, index) => {
          const state = stateFor(index)
          return (
            <li key={step.title} className={`building-step step-${state}`}>
              <span className="building-dot" aria-hidden="true">
                {state === 'done' ? '\u2713' : ''}
              </span>
              <div className="building-step-body">
                <span className="building-step-title">{step.title}</span>
                {state !== 'pending' ? <span className="building-step-detail muted">{step.detail}</span> : null}
              </div>
            </li>
          )
        })}
      </ol>

      {cleanDraft ? (
        <div className="building-draft" ref={draftRef}>
          {cleanDraft}
          <span className="reasoning-caret" aria-hidden="true" />
        </div>
      ) : null}
    </section>
  )
}
