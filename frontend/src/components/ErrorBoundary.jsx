import { Component } from 'react'
import i18n from '../i18n'

export default class ErrorBoundary extends Component {
  state = { hasError: false, error: null }

  static getDerivedStateFromError(error) {
    return { hasError: true, error }
  }

  componentDidCatch(error, info) {
    console.error('ErrorBoundary caught:', error, info)
  }

  render() {
    if (this.state.hasError) {
      const t = i18n.t.bind(i18n)
      return (
        <div className="flex items-center justify-center h-screen bg-gray-50">
          <div className="text-center p-8">
            <h2 className="text-xl font-bold text-gray-900 mb-2">{t('common.something_went_wrong')}</h2>
            <p className="text-gray-500 mb-4">{this.state.error?.message || t('common.unexpected_error')}</p>
            <button
              onClick={() => window.location.reload()}
              className="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700"
            >
              {t('common.reload_page')}
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
