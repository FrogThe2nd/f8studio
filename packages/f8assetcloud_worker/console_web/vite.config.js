import { defineConfig } from 'vite';
import path from 'node:path';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

const workerOrigin = 'http://127.0.0.1:8787';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: '/',
  build: {
    assetsDir: '_portal',
  },
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, 'src'),
    },
  },
  test: {
    environment: 'jsdom',
  },
  server: {
    proxy: {
      '/api/auth': {
        target: workerOrigin,
        changeOrigin: true,
      },
      '/v1': {
        target: workerOrigin,
        changeOrigin: true,
      },
    },
  },
});
