import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.js'],
    css: false,
    // Avoid timeouts on slower CI machines
    testTimeout: 10000,
    // Clear mocks between tests
    clearMocks: true,
    restoreMocks: true,
  },
})
