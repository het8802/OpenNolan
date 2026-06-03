import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In dev, the React app calls /api/* and Vite forwards to the FastAPI server.
// Start the backend with:  uvicorn server.app:app --reload   (port 8000)
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
