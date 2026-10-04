/** @type {import('tailwindcss').Config} */

// Colours backed by CSS custom properties (RGB channels, see src/index.css) so
// Tailwind's alpha syntax (`bg-accent/20`) works and light/dark flips without
// any `dark:` classes in components.
const v = (name) => `rgb(var(--${name}) / <alpha-value>)`

export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  // Tri-state theme: explicit [data-theme="dark"], or the OS preference unless
  // the user pinned [data-theme="light"]. Only needed for the few places that
  // cannot be expressed with tokens (e.g. prose-invert).
  darkMode: ['variant', [
    '@media (prefers-color-scheme: dark) { &:where(:not([data-theme="light"], [data-theme="light"] *)) }',
    '&:where([data-theme="dark"], [data-theme="dark"] *)',
  ]],
  theme: {
    extend: {
      colors: {
        // ---- semantic tokens (flip with theme) ----
        bg: v('bg'),
        panel: v('panel'),
        sunken: v('sunken'),
        line: { DEFAULT: v('line'), 2: v('line-2') },
        ink: { DEFAULT: v('ink'), 2: v('ink-2'), 3: v('ink-3') },
        accent: { DEFAULT: v('accent'), strong: v('accent-strong'), ink: v('accent-ink') },
        ok: { DEFAULT: v('ok'), ink: v('ok-ink') },
        warn: { DEFAULT: v('warn'), ink: v('warn-ink') },
        bad: { DEFAULT: v('bad'), ink: v('bad-ink') },
        info: { DEFAULT: v('info'), ink: v('info-ink') },
        // ---- brand scale (50-200 / 700-900 flip in dark; 300-600 stay amber) ----
        primary: {
          50: v('primary-50'),
          100: v('primary-100'),
          200: v('primary-200'),
          300: v('primary-300'),
          400: v('primary-400'),
          500: v('primary-500'),
          600: v('primary-600'),
          700: v('primary-700'),
          800: v('primary-800'),
          900: v('primary-900'),
        },
        // ---- chart categories (fixed meaning, see lib/chartColors.js) ----
        chart: {
          heart: v('c-heart'),
          sleep: v('c-sleep'),
          activity: v('c-activity'),
          body: v('c-body'),
          vitals: v('c-vitals'),
          mind: v('c-mind'),
          env: v('c-env'),
          other: v('c-other'),
        },
        // legacy static brand colours
        hime: {
          cream: '#FFF2DB',
          rose: '#E0808A',
          'rose-light': '#FFC7D4',
          warm: '#F9F5F0',
          brown: '#241F1A',
          leaf: '#5AAD50',
          sky: '#7AC4E8',
        },
      },
      borderRadius: {
        chip: 'var(--r-chip)',
        control: 'var(--r-control)',
        card: 'var(--r-card)',
      },
      boxShadow: {
        // Two elevation levels, never coloured.
        sm: 'var(--shadow-1)',
        DEFAULT: 'var(--shadow-1)',
        md: 'var(--shadow-1)',
        lg: 'var(--shadow-2)',
        xl: 'var(--shadow-2)',
        '2xl': 'var(--shadow-2)',
        inner: 'inset 0 1px 2px rgb(0 0 0 / 0.06)',
      },
      ringColor: { DEFAULT: v('accent') },
      borderColor: { DEFAULT: v('line') },
      divideColor: { DEFAULT: v('line') },
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
  ],
}
