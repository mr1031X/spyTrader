import path from 'path'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { VitePWA } from 'vite-plugin-pwa'
import { inspectAttr } from 'kimi-plugin-inspect-react'

export default defineConfig({
  base: '/',
  plugins: [
    inspectAttr(),
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      injectRegister: 'auto',
      includeAssets: ['icons/icon-192.png', 'icons/icon-512.png'],
      manifest: {
        name: 'Apex Options Pilot',
        short_name: 'Apex',
        description: 'Autonomous SPY and QQQ options trading operations dashboard',
        theme_color: '#050810',
        background_color: '#050810',
        display: 'standalone',
        start_url: '/',
        icons: [
          { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any maskable' },
          { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any maskable' },
        ],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,png,woff2}'],
        navigateFallback: null,
        runtimeCaching: [{ urlPattern: /\/api\//, handler: 'NetworkOnly' }],
      },
    }),
  ],
  server: {
    port: 3000,
    allowedHosts: ['3000.proxy.internal'],
    proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } },
  },
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
})
