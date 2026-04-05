import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const workerOrigin = 'http://127.0.0.1:8787';

export default defineConfig({
  plugins: [react()],
  base: '/console/',
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
