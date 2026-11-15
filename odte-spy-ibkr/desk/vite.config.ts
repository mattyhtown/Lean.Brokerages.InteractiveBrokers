import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

const deskRoot = path.dirname(fileURLToPath(import.meta.url))
const signalStateFile = path.resolve(deskRoot, '../data/desk/signal_state.json')

/** Serve the on-disk last-tick mirror — works even when desk_api is down. */
function deskSignalFilePlugin(): Plugin {
  return {
    name: 'desk-signal-state-file',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = req.url?.split('?')[0]
        if (url !== '/api/desk/signal_state.json') {
          next()
          return
        }
        if (!fs.existsSync(signalStateFile)) {
          res.statusCode = 404
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ error: 'signal_state_missing' }))
          return
        }
        res.statusCode = 200
        res.setHeader('Content-Type', 'application/json')
        res.setHeader('Cache-Control', 'no-store')
        fs.createReadStream(signalStateFile).pipe(res)
      })
    },
  }
}

// Proxy desk_api so the browser stays same-origin (no CORS).
export default defineConfig({
  plugins: [react(), deskSignalFilePlugin()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    fs: {
      allow: [deskRoot, path.resolve(deskRoot, '..')],
    },
    proxy: {
      '/api/desk/state': {
        target: 'http://127.0.0.1:8787',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
      // signal_state.json is handled by deskSignalFilePlugin (real file, not /desk/state)
    },
  },
})
