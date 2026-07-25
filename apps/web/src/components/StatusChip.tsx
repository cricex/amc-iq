import type { ToneMeta } from '../sentiment'

interface StatusChipProps {
  meta: ToneMeta
  /** Optional label override; defaults to meta.label. */
  label?: string
}

/** Compact colored status chip (MET / NEEDS REVIEW / NOT MET CURRENTLY / INFO ONLY / TASK DRAFTED). */
export function StatusChip({ meta, label }: StatusChipProps) {
  const { tone, Icon } = meta
  return (
    <span className={`status-chip chip-${tone}`}>
      <Icon size={13} strokeWidth={2.5} aria-hidden="true" />
      {label ?? meta.label}
    </span>
  )
}
