import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In dev: Vite at :5173 proxies /api, /admin, /static to Django at :8000 so
// the browser sees same-origin. In prod: Django serves the build via
// WhiteNoise and the SPA's relative /api/* paths hit it directly.
// Dev mode: served at http://localhost:5173/, proxies /api etc to Django.
// Build mode: assets reference /static/* so WhiteNoise serves them via
// Django after `collectstatic`.
export default defineConfig(({ command }) => ({
  plugins: [react()],
  base: command === 'build' ? '/static/' : '/',
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
      '/admin': 'http://localhost:8000',
      '/static': 'http://localhost:8000',
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
}))
