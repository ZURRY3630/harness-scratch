import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发：npm run dev -> http://localhost:5173，/api 与 /static 代理到 FastAPI(:8765)
// 生产：npm run build -> dist/，由 FastAPI 整体托管
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8765',
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
