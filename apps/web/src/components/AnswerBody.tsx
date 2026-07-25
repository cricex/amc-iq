import type { ReactNode } from 'react'

/** Minimal, dependency-free renderer for the agent's markdown-ish answer text: supports headings
 * (#, ##, ###), bold (**text**), and bullet / numbered lists. Everything else renders as paragraphs. */

function renderInline(text: string): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*)/g)
  return parts.map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={index}>{part.slice(2, -2)}</strong>
    }
    return <span key={index}>{part}</span>
  })
}

export function AnswerBody({ text }: { text: string }) {
  const lines = text.split(/\r?\n/)
  const blocks: ReactNode[] = []
  let list: string[] = []
  let listType: 'ul' | 'ol' | null = null

  const flushList = () => {
    if (!list.length || !listType) return
    const items = list.map((item, index) => <li key={index}>{renderInline(item)}</li>)
    blocks.push(
      listType === 'ol' ? <ol key={blocks.length}>{items}</ol> : <ul key={blocks.length}>{items}</ul>,
    )
    list = []
    listType = null
  }

  for (const raw of lines) {
    const line = raw.trim()
    if (!line) {
      flushList()
      continue
    }
    const bullet = line.match(/^[-*]\s+(.*)$/)
    const numbered = line.match(/^\d+\.\s+(.*)$/)
    const heading = line.match(/^#{1,4}\s+(.*)$/)
    if (bullet) {
      if (listType && listType !== 'ul') flushList()
      listType = 'ul'
      list.push(bullet[1])
    } else if (numbered) {
      if (listType && listType !== 'ol') flushList()
      listType = 'ol'
      list.push(numbered[1])
    } else if (heading) {
      flushList()
      blocks.push(
        <p key={blocks.length} className="answer-heading">
          {renderInline(heading[1])}
        </p>,
      )
    } else {
      flushList()
      blocks.push(<p key={blocks.length}>{renderInline(line)}</p>)
    }
  }
  flushList()

  return <div className="answer-body">{blocks}</div>
}
