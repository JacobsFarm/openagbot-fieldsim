import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// Build naar openagbot_fieldsim/static (daar serveert de Python-server de UI). Tijdens `npm run dev` gaan /api en /ws naar
// de draaiende sim op 8765.
export default defineConfig({
  plugins: [svelte()],
  base: './',
  build: { outDir: '../openagbot_fieldsim/static', emptyOutDir: true, chunkSizeWarningLimit: 1200 },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8765',
      '/ws': { target: 'ws://127.0.0.1:8765', ws: true },
    },
  },
});
