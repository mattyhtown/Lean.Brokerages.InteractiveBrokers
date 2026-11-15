import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Proxy desk_api so the browser stays same-origin (no CORS).
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api/desk/state': {
        target: 'http://127.0.0.1:8787',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
      '/api/desk/signal_state.json': {
        target: 'http://127.0.0.1:8787',
        changeOrigin: true,
        rewrite: () => '/desk/state',
      },
    },
  },
})
