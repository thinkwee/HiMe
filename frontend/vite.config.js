import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// vite.config.js runs in Node, so we have to call loadEnv() explicitly to
// pick up values from frontend/.env / frontend/.env.local. Without this,
// only variables exported in the shell are visible via process.env.
//
// To allow access from a custom hostname (e.g. when fronting Vite with a
// reverse proxy or tunnel), set VITE_ALLOWED_HOSTS=host1,host2 in:
//   - frontend/.env.local      (gitignored, recommended)
//   - frontend/.env            (committed)
//   - the shell environment
// Localhost / IP addresses are always allowed by Vite regardless.
//
// To point Vite's /api proxy at a backend on a different machine, set
// VITE_API_TARGET=http://<host>:8000 the same way.

export default defineConfig(({ mode }) => {
  const env = { ...loadEnv(mode, process.cwd(), ''), ...process.env }

  const allowedHosts = (env.VITE_ALLOWED_HOSTS || '')
    .split(',')
    .map((h) => h.trim())
    .filter(Boolean)

  return {
    base: env.VITE_BASE_PATH || '/',
    plugins: [react()],
    server: {
      port: 5173,
      host: true,
      allowedHosts,
      proxy: {
        '/api': {
          target: env.VITE_API_TARGET || 'http://localhost:8000',
          changeOrigin: true,
          ws: true, // Enable WebSocket proxy for /api path
        },
      },
    },
  }
})
