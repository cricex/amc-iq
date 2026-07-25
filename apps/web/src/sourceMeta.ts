import { BrainCircuit, Database, Globe, Users, type LucideIcon } from 'lucide-react'
import type { IQSource, SourceStatus } from './types'

export const sourceLabels: Record<IQSource, string> = {
  foundry: 'Foundry IQ',
  fabric: 'Fabric IQ',
  work: 'Work IQ',
  web: 'Web IQ',
}

export const sourceIcon: Record<IQSource, LucideIcon> = {
  foundry: BrainCircuit,
  fabric: Database,
  work: Users,
  web: Globe,
}

export const sourceOrder: IQSource[] = ['foundry', 'fabric', 'work', 'web']

export const sourceTagline: Record<IQSource, string> = {
  foundry: 'Institutional knowledge',
  fabric: 'Patient and operational data',
  work: 'Care team and workflow',
  web: 'External evidence',
}

export const sourceRetrieving: Record<IQSource, string> = {
  foundry: 'Protocol criteria, genomics report, pathology, consent policy',
  fabric: 'Patient registry, CrCl trend, ECOG, treatment history',
  work: 'Open tasks, coordinator ownership, tumor board context',
  web: 'External trial registry and biomarker treatment context',
}

export function sourceClass(source: IQSource): string {
  return `source-${source}`
}

export const statusLabels: Record<SourceStatus, string> = {
  idle: 'Idle',
  queued: 'Queued',
  searching: 'Searching',
  retrieved: 'Retrieved',
  needs_review: 'Needs review',
  complete: 'Complete',
  failed: 'Failed',
}

export function statusLabel(status: SourceStatus): string {
  return statusLabels[status] ?? status
}

