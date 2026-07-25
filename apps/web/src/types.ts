export type IQSource = 'foundry' | 'fabric' | 'work' | 'web'

export type SourceStatus =
  | 'idle'
  | 'queued'
  | 'searching'
  | 'retrieved'
  | 'needs_review'
  | 'complete'
  | 'failed'

export type Confidence = 'low' | 'medium' | 'high'

export type EligibilityAssessment =
  | 'eligible'
  | 'likely_eligible_pending'
  | 'not_eligible'
  | 'indeterminate'

export type CriterionStatus = 'met' | 'uncertain' | 'not_met'

export type TraceStatus = 'completed' | 'in_progress' | 'pending' | 'queued'

export type ReviewerStatus = 'assigned' | 'pending' | 'complete'

export interface AskRequest {
  question: string
  patientId?: string
  trialId?: string
}

export type FabricState = 'Active' | 'Paused' | 'Pausing' | 'Resuming' | 'Unknown'

export interface FabricStatus {
  state: FabricState
  capacityName: string
  portalUrl: string
  detail?: string | null
}

export interface Person {
  id: string
  display: string
  role: string
}

export interface Patient {
  id: string
  mrn: string
  display: string
  age: number
  sex: string
  ecog: number
  diagnosis: string
  stage: string
  biomarkers: string[]
  crcl: number
  crclDate: string
}

export interface Eligibility {
  assessment: EligibilityAssessment
  label: string
  confidence: Confidence
}

export interface Trial {
  id: string
  short: string
  status: string
  humanName: string
  title: string
  phase: string
  sponsor: string
  keyIssue: string
  latestProtocol?: string
}

export interface Criterion {
  text: string
  status: CriterionStatus
  evidenceRefs: string[]
}

export interface Evidence {
  refId: string
  source: IQSource
  title: string
  snippet: string
  url: string | null
  sourceType: string
}

export interface NextAction {
  text: string
  steps?: string[]
  owner: Person
  taskType: string
  taskId: string
  taskStatus: string
  due: string
}

export interface HumanReview {
  owner: Person
  reason: string
}

export interface Reviewer {
  role: string
  owner: Person
  status: ReviewerStatus
}

export interface SourceMapEntry {
  source: IQSource
  label: string
  status: SourceStatus
  queries: string[]
  citations: string[]
  durationMs?: number
  summary?: string
  retrieving?: string
  evidenceCount?: number
  evidenceNoun?: string
}

export interface TraceStep {
  step: string
  source?: IQSource
  detail: string
  ts: number
  status?: TraceStatus
}

export interface AskResult {
  question: string
  answer?: string
  answerRefs?: string[]
  intent?: string
  scope?: string
  bottomLine?: string
  patient: Patient
  eligibility: Eligibility
  trial: Trial
  criteria: Criterion[]
  evidence: Evidence[]
  missingData: string[]
  nextAction: NextAction
  humanReview: HumanReview
  reviewers: Reviewer[]
  unavailableSources: string[]
  sourceMap: SourceMapEntry[]
  trace: TraceStep[]
  agentDriven?: boolean
  mode: 'mock' | 'live'
  disclaimer: string
}

export interface PlanEvent {
  steps: string[]
}

export interface SourceQueryEvent {
  source: IQSource
  label: string
  query: string
  retrieving?: string
  status: 'searching'
}

export interface SourceResultEvent {
  source: IQSource
  status: SourceStatus
  summary?: string
  citations: string[]
  durationMs?: number
  retrieving?: string
  evidenceCount?: number
  evidenceNoun?: string
}

export interface TokenEvent {
  text: string
}

export interface AgentActivityEvent {
  phase: 'tool_start' | 'tool_done' | 'answer_delta'
  source?: IQSource
  query?: string
  text?: string
}

export interface FinalEvent {
  result: AskResult
}

export interface ErrorEventPayload {
  message: string
}

export type StreamEvent =
  | { type: 'plan'; data: PlanEvent }
  | { type: 'source_query'; data: SourceQueryEvent }
  | { type: 'source_result'; data: SourceResultEvent }
  | { type: 'token'; data: TokenEvent }
  | { type: 'agent_activity'; data: AgentActivityEvent }
  | { type: 'final'; data: FinalEvent }
  | { type: 'error'; data: ErrorEventPayload }
