import {
  Activity,
  AlertTriangle,
  CalendarClock,
  CheckCircle2,
  CircleDashed,
  Dna,
  Droplet,
  FlaskConical,
  HeartPulse,
  Info,
  Stamp,
  Stethoscope,
  XCircle,
  type LucideIcon,
} from 'lucide-react'
import type { CriterionStatus, EligibilityAssessment } from './types'

/** The five-tone clinical sentiment system used across chips, banners, and cards.
 * green = met/favorable, amber = needs review/conditional, red = not met/blocked,
 * blue = informational/external context, gray = neutral metadata. */
export type Tone = 'green' | 'amber' | 'red' | 'blue' | 'gray'

export interface ToneMeta {
  tone: Tone
  label: string
  Icon: LucideIcon
}

const TONE_ICON: Record<Tone, LucideIcon> = {
  green: CheckCircle2,
  amber: AlertTriangle,
  red: XCircle,
  blue: Info,
  gray: CircleDashed,
}

export function toneIcon(tone: Tone): LucideIcon {
  return TONE_ICON[tone] ?? CircleDashed
}

const CRITERION_TONE: Record<CriterionStatus, ToneMeta> = {
  met: { tone: 'green', label: 'MET', Icon: CheckCircle2 },
  uncertain: { tone: 'amber', label: 'NEEDS REVIEW', Icon: AlertTriangle },
  not_met: { tone: 'red', label: 'NOT MET CURRENTLY', Icon: XCircle },
}

export function criterionTone(status: CriterionStatus): ToneMeta {
  return CRITERION_TONE[status] ?? { tone: 'gray', label: String(status).toUpperCase(), Icon: CircleDashed }
}

const ASSESSMENT_TONE: Record<EligibilityAssessment, Tone> = {
  eligible: 'green',
  likely_eligible_pending: 'amber',
  not_eligible: 'red',
  indeterminate: 'gray',
}

export function assessmentTone(assessment: EligibilityAssessment): Tone {
  return ASSESSMENT_TONE[assessment] ?? 'gray'
}

export type BlockerSeverity = 'review' | 'hard'

export function blockerTone(severity: BlockerSeverity): ToneMeta {
  return severity === 'hard'
    ? { tone: 'red', label: 'NOT MET CURRENTLY', Icon: XCircle }
    : { tone: 'amber', label: 'NEEDS REVIEW', Icon: AlertTriangle }
}

export const INFO_ONLY: ToneMeta = { tone: 'blue', label: 'INFO ONLY', Icon: Info }
export const TASK_DRAFTED: ToneMeta = { tone: 'gray', label: 'TASK DRAFTED', Icon: CircleDashed }
export const NOT_SUBMITTED: ToneMeta = { tone: 'gray', label: 'NOT SUBMITTED', Icon: CircleDashed }
export const READY_FOR_PI_REVIEW: ToneMeta = { tone: 'amber', label: 'READY FOR PI REVIEW', Icon: Stamp }

/** Operational screening state derived from the eligibility assessment. */
export function screeningStatus(assessment: EligibilityAssessment): ToneMeta {
  if (assessment === 'eligible') return { tone: 'green', label: 'READY FOR SCREENING', Icon: CheckCircle2 }
  if (assessment === 'not_eligible') return { tone: 'red', label: 'NOT ELIGIBLE', Icon: XCircle }
  return { tone: 'amber', label: 'NOT READY FOR SCREENING', Icon: AlertTriangle }
}

export const CALENDAR_ICON = CalendarClock

/** Best-fit clinical glyph for a criterion, chosen by keyword. Decorative scan aid only. */
export function criterionGlyph(text: string): LucideIcon {
  const t = text.toLowerCase()
  if (/crcl|renal|kidney|creatinine/.test(t)) return Droplet
  if (/egfr|exon|biomarker|genomic|mutation/.test(t)) return Dna
  if (/ecog|performance status/.test(t)) return HeartPulse
  if (/platinum|chemo|therapy|treatment|drug/.test(t)) return FlaskConical
  if (/diagnosis|histology|stage|nsclc/.test(t)) return Stethoscope
  return Activity
}
