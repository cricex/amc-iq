import {
  FileText,
  LayoutDashboard,
  Layers,
  Moon,
  Settings,
  Sun,
  User,
  Workflow,
  type LucideIcon,
} from 'lucide-react'

interface NavItem {
  id: string
  label: string
  Icon: LucideIcon
}

const navItems: NavItem[] = [
  { id: 'assessment', label: 'Overview', Icon: LayoutDashboard },
  { id: 'patient', label: 'Patient Summary', Icon: User },
  { id: 'evidence', label: 'Evidence', Icon: FileText },
  { id: 'workflow', label: 'Workflow', Icon: Workflow },
  { id: 'sources', label: 'Sources', Icon: Layers },
  { id: 'settings', label: 'Settings', Icon: Settings },
]

interface SideNavProps {
  activeId: string
  darkMode: boolean
  onToggleDarkMode: () => void
}

export function SideNav({ activeId, darkMode, onToggleDarkMode }: SideNavProps) {
  function handleClick(event: React.MouseEvent<HTMLAnchorElement>, id: string) {
    event.preventDefault()
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <nav className="side-nav" aria-label="Sections">
      <div className="side-nav-brand">
        <span className="brand-mark" aria-hidden="true">
          IQ
        </span>
        <span className="brand-name">AMC IQ</span>
      </div>
      <ul className="side-nav-list">
        {navItems.map((item) => (
          <li key={item.id}>
            <a
              href={`#${item.id}`}
              className={activeId === item.id ? 'nav-link active' : 'nav-link'}
              onClick={(event) => handleClick(event, item.id)}
            >
              <item.Icon size={16} strokeWidth={2.2} aria-hidden="true" />
              {item.label}
            </a>
          </li>
        ))}
      </ul>
      <button type="button" className="dark-toggle" onClick={onToggleDarkMode}>
        {darkMode ? <Sun size={15} strokeWidth={2.2} aria-hidden="true" /> : <Moon size={15} strokeWidth={2.2} aria-hidden="true" />}
        {darkMode ? 'Light mode' : 'Dark mode'}
      </button>
    </nav>
  )
}
