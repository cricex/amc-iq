import { useEffect, useMemo, useState } from 'react'
import {
  ChevronDown,
  ClipboardCheck,
  Database,
  FileText,
  Globe,
  LayoutGrid,
  ScrollText,
  ShieldAlert,
  Workflow,
  type LucideIcon,
} from 'lucide-react'
import { questionCategories, questionLibrary, type QuestionCategory } from '../questionLibrary'

interface QuestionLibraryProps {
  question: string
  isLoading: boolean
  onQuestionChange: (question: string) => void
  onAsk: () => void
}

const categoryIcon: Record<QuestionCategory, LucideIcon> = {
  All: LayoutGrid,
  'Trial Eligibility': ClipboardCheck,
  'Screening Check': ShieldAlert,
  Evidence: FileText,
  Workflow: Workflow,
  Protocol: ScrollText,
  'Data Gaps': Database,
  'External Context': Globe,
}

const MAX_LEN = 2000
const OPEN_STORAGE_KEY = 'amciq.questionBankOpen'

function readInitialOpen(): boolean {
  if (typeof window === 'undefined') return false
  try {
    return window.localStorage.getItem(OPEN_STORAGE_KEY) === 'true'
  } catch {
    // localStorage can throw (privacy mode, blocked third-party storage, enterprise lockdown).
    return false
  }
}

export function QuestionLibrary({ question, isLoading, onQuestionChange, onAsk }: QuestionLibraryProps) {
  const [activeCategory, setActiveCategory] = useState<QuestionCategory>('All')
  const [isOpen, setIsOpen] = useState<boolean>(readInitialOpen)

  useEffect(() => {
    if (typeof window !== 'undefined') {
      try {
        window.localStorage.setItem(OPEN_STORAGE_KEY, String(isOpen))
      } catch {
        // Persisting the preference is best-effort; ignore quota/security errors.
      }
    }
  }, [isOpen])

  const visible = useMemo(
    () =>
      activeCategory === 'All'
        ? questionLibrary
        : questionLibrary.filter((item) => item.category === activeCategory),
    [activeCategory],
  )

  return (
    <section className="question-library card" aria-label="Question library">
      <button
        type="button"
        className="card-header question-library-toggle"
        aria-expanded={isOpen}
        aria-controls="question-library-body"
        onClick={() => setIsOpen((value) => !value)}
      >
        <span className="eyebrow">Question library</span>
        <span className="question-library-toggle-meta">
          <span className="muted">{isOpen ? 'Click an example, then edit and run' : 'Show examples'}</span>
          <ChevronDown
            size={16}
            strokeWidth={2.4}
            aria-hidden="true"
            className={isOpen ? 'question-library-chevron open' : 'question-library-chevron'}
          />
        </span>
      </button>

      {isOpen ? (
        <div id="question-library-body">
          <div className="category-tabs" role="tablist">
            {questionCategories.map((category) => {
              const Icon = categoryIcon[category]
              return (
                <button
                  key={category}
                  type="button"
                  role="tab"
                  aria-selected={activeCategory === category}
                  className={activeCategory === category ? 'category-tab active' : 'category-tab'}
                  onClick={() => setActiveCategory(category)}
                >
                  <Icon size={14} strokeWidth={2.2} aria-hidden="true" />
                  {category}
                </button>
              )
            })}
          </div>

          <div className="question-chips">
            {visible.map((item) => (
              <button
                key={item.short}
                type="button"
                className={question === item.prompt ? 'question-chip active' : 'question-chip'}
                onClick={() => onQuestionChange(item.prompt)}
              >
                <span className="chip-category">{item.category}</span>
                <span className="chip-text">{item.short}</span>
              </button>
            ))}
          </div>
        </div>
      ) : null}

      <form
        className="prompt-area"
        onSubmit={(event) => {
          event.preventDefault()
          onAsk()
        }}
      >
        <textarea
          id="prompt"
          value={question}
          maxLength={MAX_LEN}
          onChange={(event) => onQuestionChange(event.target.value)}
          rows={3}
          placeholder="Ask a trial readiness question, or expand the question library for examples."
        />
        <div className="prompt-actions">
          <span className="char-count">
            {question.length}/{MAX_LEN}
          </span>
          <button type="submit" className="run-button" disabled={isLoading || !question.trim()}>
            {isLoading ? 'Running assessment...' : 'Run assessment'}
          </button>
        </div>
      </form>
    </section>
  )
}
