import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  base: './',
  server: {
    port: 5180,
    proxy: { '/api': 'http://127.0.0.1:8300' },
  },
  build: {
    outDir: '../server/web',
    emptyOutDir: true,
    chunkSizeWarningLimit: 1200,
  },
})
