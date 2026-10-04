import { Monitor, Moon, Sun } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useTheme } from '../lib/theme'

const OPTIONS = [
  { value: 'system', icon: Monitor, labelKey: 'theme.system' },
  { value: 'light', icon: Sun, labelKey: 'theme.light' },
  { value: 'dark', icon: Moon, labelKey: 'theme.dark' },
]

/** Small System / Light / Dark segmented control for the sidebar footer. */
export default function ThemeToggle() {
  const { t } = useTranslation()
  const { choice, setChoice } = useTheme()

  return (
    <div
      role="radiogroup"
      aria-label={t('theme.label')}
      className="inline-flex items-center gap-0.5 rounded-control border border-line bg-sunken p-0.5"
    >
      {OPTIONS.map(({ value, icon: Icon, labelKey }) => {
        const active = choice === value
        return (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={active}
            aria-label={t(labelKey)}
            title={t(labelKey)}
            onClick={() => setChoice(value)}
            className={`flex h-6 w-7 items-center justify-center rounded-chip transition-colors ${
              active ? 'bg-panel text-primary-700 shadow-sm' : 'text-ink-3 hover:text-ink'
            }`}
          >
            <Icon className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        )
      })}
    </div>
  )
}
