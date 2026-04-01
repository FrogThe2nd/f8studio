import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: '/',
  server: {
    proxy: {
      '/v1': {
        target: 'http://127.0.0.1:8787',
        changeOrigin: true,
      },
    },
  },
});
