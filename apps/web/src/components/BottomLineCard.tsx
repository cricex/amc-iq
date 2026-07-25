import { Flag } from 'lucide-react'

/** Concise closing verdict, shown as the top card of the action rail. */
export function BottomLineCard({ text }: { text: string }) {
  if (!text) return null
  return (
    <section className="rail-card">
      <div className="rail-card-head">
        <Flag size={14} strokeWidth={2.4} aria-hidden="true" />
        Bottom line
      </div>
      <p className="bottom-line-text">{text}</p>
    </section>
  )
}
