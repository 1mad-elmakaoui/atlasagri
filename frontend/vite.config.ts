import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  resolve: {
    // L'alias doit être déclaré ici *et* dans tsconfig.json : TypeScript ne
    // résout que les types, le bundler résout les imports réels.
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: {
      // Le frontend ne connaît pas l'URL du backend en développement :
      // tout passe par /api, ce qui évite une variable d'environnement de plus
      // et supprime les questions de CORS en local.
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
})
