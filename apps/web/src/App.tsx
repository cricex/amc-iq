import { useEffect, useRef, useState } from 'react'
import {
  ask,
  askStream,
  decideStreamFailure,
  getFabricStatus,
  RequestError,
  shouldUseSampleFallback,
} from './api/client'
import './App.css'
import { AuthenticationError, useAuthentication } from './authentication'
import { ActionRail } from './components/ActionRail'
import { AssessmentCard } from './components/AssessmentCard'
import { AssessmentFailureCard } from './components/AssessmentFailureCard'
import { BuildingAssessment } from './components/BuildingAssessment'
import { EmptyState } from './components/EmptyState'
import { EvidencePacket } from './components/EvidencePacket'
import { IQActivityBar } from './components/IQActivityBar'
import { PatientSnapshot } from './components/PatientSnapshot'
import { QuestionLibrary } from './components/QuestionLibrary'
import { SideNav } from './components/SideNav'
import { TopBar } from './components/TopBar'
import { TrialCard } from './components/TrialCard'
import { heroQuestion, sampleResult } from './sampleResult'
import { sourceLabels, sourceOrder, sourceRetrieving } from './sourceMeta'
import type { AskResult, FabricStatus, SourceMapEntry, StreamEvent, TraceStep } from './types'

function createIdleSourceMap(status: SourceMapEntry['status']): SourceMapEntry[] {
  return sourceOrder.map((source) => ({
    source,
    label: sourceLabels[source],
    status,
    queries: [],
    citations: [],
    retrieving: sourceRetrieving[source],
  }))
}

const SECTION_IDS = ['assessment', 'patient', 'evidence', 'workflow', 'sources', 'settings']

function App() {
  const { getAccessToken } = useAuthentication()
  const [question, setQuestion] = useState(heroQuestion)
  const [result, setResult] = useState<AskResult | null>(null)
  const [sourceMap, setSourceMap] = useState<SourceMapEntry[]>(createIdleSourceMap('idle'))
  const [trace, setTrace] = useState<TraceStep[]>([])
  const [statusMessage, setStatusMessage] = useState('Select or edit a question, then run the assessment.')
  const [isLoading, setIsLoading] = useState(false)
  const [darkMode, setDarkMode] = useState(false)
  const [activeSection, setActiveSection] = useState('assessment')
  const [reasoningDraft, setReasoningDraft] = useState('')
  const [fabricStatus, setFabricStatus] = useState<FabricStatus | null>(null)
  const [assessmentFailure, setAssessmentFailure] = useState<string | null>(null)
  const traceTimers = useRef<number[]>([])

  useEffect(() => {
    document.body.classList.toggle('dark', darkMode)
  }, [darkMode])

  useEffect(() => {
    let active = true
    const poll = async () => {
      const status = await getFabricStatus()
      if (active) setFabricStatus(status)
    }
    void poll()
    const interval = window.setInterval(() => void poll(), 30000)
    return () => {
      active = false
      window.clearInterval(interval)
    }
  }, [])

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio)
        if (visible[0]) setActiveSection(visible[0].target.id)
      },
      { rootMargin: '-40% 0px -50% 0px', threshold: [0, 0.25, 0.5] },
    )
    for (const id of SECTION_IDS) {
      const element = document.getElementById(id)
      if (element) observer.observe(element)
    }
    return () => observer.disconnect()
  }, [])

  function clearTraceTimers() {
    for (const timer of traceTimers.current) window.clearTimeout(timer)
    traceTimers.current = []
  }

  function revealPlanSteps(steps: string[]) {
    clearTraceTimers()
    setTrace([])
    for (const [index, step] of steps.entries()) {
      const timer = window.setTimeout(() => {
        setTrace((current) => [...current, { step, detail: 'Planning source queries and evidence checks.', ts: index, status: 'in_progress' }])
      }, index * 220)
      traceTimers.current.push(timer)
    }
  }

  function handleStreamEvent(event: StreamEvent) {
    switch (event.type) {
      case 'plan':
        setSourceMap(createIdleSourceMap('queued'))
        revealPlanSteps(event.data.steps)
        break
      case 'source_query':
        setSourceMap((current) =>
          current.map((entry) =>
            entry.source === event.data.source
              ? { ...entry, status: 'searching', queries: [event.data.query], retrieving: event.data.retrieving ?? entry.retrieving }
              : entry,
          ),
        )
        break
      case 'source_result':
        setSourceMap((current) =>
          current.map((entry) =>
            entry.source === event.data.source
              ? {
                  ...entry,
                  status: event.data.status,
                  citations: event.data.citations,
                  durationMs: event.data.durationMs,
                  summary: event.data.summary,
                  retrieving: event.data.retrieving ?? entry.retrieving,
                  evidenceCount: event.data.evidenceCount,
                  evidenceNoun: event.data.evidenceNoun,
                }
              : entry,
          ),
        )
        break
      case 'token':
        break
      case 'agent_activity':
        if (event.data.phase === 'answer_delta') {
          setReasoningDraft((current) => current + (event.data.text ?? ''))
        }
        break
      case 'final':
        clearTraceTimers()
        setAssessmentFailure(null)
        setResult(event.data.result)
        setSourceMap(event.data.result.sourceMap)
        setTrace(event.data.result.trace)
        setStatusMessage(`Live ${event.data.result.mode} assessment rendered.`)
        break
      case 'error':
        setStatusMessage(`Stream warning: ${event.data.message}`)
        break
      default:
        break
    }
  }

  async function handleAsk() {
    const patientId = result?.patient.id ?? sampleResult.patient.id
    const trialId = result?.trial.id ?? sampleResult.trial.id
    clearTraceTimers()
    setIsLoading(true)
    setResult(null)
    setSourceMap(createIdleSourceMap('queued'))
    setTrace([])
    setReasoningDraft('')
    setAssessmentFailure(null)
    setStatusMessage('Dispatching the IQ layers in parallel...')

    let streamReturnedFinal = false
    let streamHadProgress = false
    let accessToken: string | null
    try {
      accessToken = await getAccessToken()
    } catch (error) {
      setSourceMap(createIdleSourceMap('idle'))
      setStatusMessage(
        error instanceof AuthenticationError
          ? error.message
          : 'Microsoft Entra sign-in or token acquisition failed.',
      )
      setIsLoading(false)
      return
    }

    try {
      await askStream(
        question,
        (event) => {
          streamHadProgress = true
          if (event.type === 'final') streamReturnedFinal = true
          handleStreamEvent(event)
        },
        accessToken,
        patientId,
        trialId,
      )
      if (!streamReturnedFinal) {
        throw new Error('Stream closed before final result')
      }
    } catch (streamError) {
      if (streamReturnedFinal) {
        setStatusMessage('Assessment complete.')
        return
      }
      const streamFailureDecision = decideStreamFailure(streamError)
      if (streamFailureDecision === 'validation') {
        setSourceMap(createIdleSourceMap('idle'))
        setStatusMessage(streamError instanceof Error ? streamError.message : 'The assessment request was invalid.')
        return
      }
      if (streamFailureDecision === 'terminal-composition') {
        clearTraceTimers()
        const message = streamError instanceof Error ? streamError.message : 'The assessment could not be composed.'
        setAssessmentFailure(message)
        setStatusMessage(`Retrieval completed, but the assessment could not be composed: ${message}`)
        return
      }
      try {
        const fallback = await ask(question, accessToken, patientId, trialId)
        setAssessmentFailure(null)
        setResult(fallback)
        setSourceMap(fallback.sourceMap)
        setTrace(fallback.trace)
        setStatusMessage('Streaming unavailable; rendered non-streaming result.')
      } catch (askError) {
        // Any backend HTTP response is authoritative, including 5xx service failures. Only a real
        // transport/connectivity failure falls back to the bundled sample case.
        if (askError instanceof RequestError) {
          if (!streamHadProgress) setSourceMap(createIdleSourceMap('idle'))
          if (streamHadProgress) setAssessmentFailure(askError.message)
          setStatusMessage(
            streamHadProgress
              ? `Retrieval completed, but the assessment could not be composed: ${askError.message}`
              : askError.message,
          )
        } else if (shouldUseSampleFallback(askError)) {
          setAssessmentFailure(null)
          setResult(sampleResult)
          setSourceMap(sampleResult.sourceMap)
          setTrace(sampleResult.trace)
          setStatusMessage('Backend unreachable; showing the bundled sample case.')
          console.warn('Using bundled sample fallback.', { streamError, askError })
        }
      }
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="layout">
      <SideNav activeId={activeSection} darkMode={darkMode} onToggleDarkMode={() => setDarkMode((value) => !value)} />

      <div className="main-col">
        <TopBar mode={result?.mode} fabricStatus={fabricStatus} />

        <div className="status-strip">
          <span className={isLoading ? 'loading-dot' : 'ready-dot'} aria-hidden="true" />
          <span>{statusMessage}</span>
        </div>

        <QuestionLibrary question={question} isLoading={isLoading} onQuestionChange={setQuestion} onAsk={handleAsk} />

        <IQActivityBar entries={sourceMap} />

        {result ? (
          <>
            <TrialCard trial={result.trial} crclDate={result.patient.crclDate} />

            <div className="assessment-zone">
              <div className="assessment-main">
                <AssessmentCard
                  eligibility={result.eligibility}
                  answer={result.answer}
                  intent={result.intent}
                  criteria={result.criteria}
                  missingData={result.missingData}
                />

                <PatientSnapshot patient={result.patient} criteria={result.criteria} />

                <EvidencePacket
                  evidence={result.evidence}
                  criteria={result.criteria}
                  intent={result.intent}
                />
              </div>

              <ActionRail result={result} trace={trace} />
            </div>
          </>
        ) : isLoading ? (
          <BuildingAssessment sources={sourceMap} draft={reasoningDraft} />
        ) : assessmentFailure ? (
          <AssessmentFailureCard message={assessmentFailure} onRetry={handleAsk} />
        ) : (
          <EmptyState isLoading={false} />
        )}

        <footer className="app-footer" id="settings">
          <p>{(result?.disclaimer ?? 'Synthetic data. No PHI. Not clinical decision support.')} Always follow institutional policies and clinical judgement.</p>
        </footer>
      </div>
    </div>
  )
}

export default App
