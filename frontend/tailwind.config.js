/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          50: '#FFF8ED',
          100: '#FFF2DB',
          200: '#FAD185',
          300: '#F2B359',
          400: '#E8A040',
          500: '#D4902E',
          600: '#C78033',
          700: '#A66A28',
          800: '#7A4E1E',
          900: '#4D3113',
        },
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
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
  ],
}
