import { defineConfig, mergeConfig } from 'vite'
import base from './vite.config.ts'

// Phone preview for manual mobile testing: `npm run phone`, then open
// http://localhost:5230/phone-preview.html. It shows the app in several
// phone-sized frames side by side (iframes get real phone widths, so media
// queries and measured layouts behave as on a device). /api goes to the live
// site, so answers are real (rate limits and the daily budget apply).
// Set GLASSBOX_API_PROXY to use a local API instead.
export default mergeConfig(base, defineConfig({
  server: {
    port: 5230,
    strictPort: true,
    proxy: {
      '/api': {
        target: process.env.GLASSBOX_API_PROXY ?? 'https://basel.engineering',
        changeOrigin: true,
        secure: true,
      },
    },
  },
}))
