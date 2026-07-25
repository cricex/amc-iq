import { CircleCheck, CircleHelp, Loader, PauseCircle } from 'lucide-react'
import type { FabricState, FabricStatus } from '../types'

interface FabricStatusChipProps {
  status: FabricStatus | null
}

interface StateMeta {
  tone: 'live' | 'paused' | 'transition' | 'unknown'
  label: string
  Icon: typeof CircleCheck
  spin?: boolean
}

const stateMeta: Record<FabricState, StateMeta> = {
  Active: { tone: 'live', label: 'Fabric running', Icon: CircleCheck },
  Paused: { tone: 'paused', label: 'Fabric paused', Icon: PauseCircle },
  Resuming: { tone: 'transition', label: 'Fabric resuming', Icon: Loader, spin: true },
  Pausing: { tone: 'transition', label: 'Fabric pausing', Icon: Loader, spin: true },
  Unknown: { tone: 'unknown', label: 'Fabric state unknown', Icon: CircleHelp },
}

/** Read-only indicator of the backing Fabric F64 capacity state, so users know at a glance whether a
 *  live assessment will work. Green when Active, red when Paused, amber while transitioning, grey when
 *  unknown. Links to the Fabric workspace so users can resume the capacity themselves if needed. */
export function FabricStatusChip({ status }: FabricStatusChipProps) {
  if (!status) return null
  const meta = stateMeta[status.state] ?? stateMeta.Unknown
  const { Icon } = meta
  const title = status.detail ?? `${meta.label}${status.capacityName ? ` (${status.capacityName})` : ''}`

  return (
    <a
      className={`fabric-chip fabric-${meta.tone}`}
      href={status.portalUrl || undefined}
      target="_blank"
      rel="noreferrer"
      title={title}
      aria-label={title}
    >
      <Icon size={13} strokeWidth={2.5} aria-hidden="true" className={meta.spin ? 'fabric-spin' : undefined} />
      {meta.label}
    </a>
  )
}
