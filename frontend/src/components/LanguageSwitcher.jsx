import { useTranslation } from 'react-i18next'
import { Languages } from 'lucide-react'

export default function LanguageSwitcher() {
  const { i18n, t } = useTranslation()

  const current = (i18n.resolvedLanguage || i18n.language || 'en').startsWith('zh') ? 'zh' : 'en'

  const handleChange = (e) => {
    const lang = e.target.value
    i18n.changeLanguage(lang)
  }

  return (
    <label className="flex items-center gap-1.5 text-xs text-gray-500" title={t('common.language')}>
      <Languages className="w-3.5 h-3.5 text-gray-400" aria-hidden="true" />
      <select
        value={current}
        onChange={handleChange}
        className="bg-transparent border border-gray-200 rounded-md px-1.5 py-0.5 text-xs font-medium text-gray-700 focus:outline-none focus:ring-1 focus:ring-primary-300 cursor-pointer"
        aria-label={t('common.language')}
      >
        <option value="en">{t('language_switcher.english')}</option>
        <option value="zh">{t('language_switcher.chinese')}</option>
      </select>
    </label>
  )
}
