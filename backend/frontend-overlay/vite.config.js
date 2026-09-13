import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig(({ command }) => ({
  plugins: [react()],
  base: command === 'build' ? '/overlay/' : '/',
  server: {
    port: 5174,
    host: '127.0.0.1',
  },
}))
