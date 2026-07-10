import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

const parseAllowedHosts = (value) => {
  if (!value) return undefined

  const allowedHosts = value
    .split(',')
    .map((host) => host.trim())
    .filter(Boolean)

  return allowedHosts.length > 0 ? allowedHosts : undefined
}

export default defineConfig(({ mode }) => {
  // eslint-disable-next-line no-undef
  const env = loadEnv(mode, process.cwd(), '')
  const allowedHosts = parseAllowedHosts(env.VITE_ALLOWED_HOSTS)

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url))
      }
    },
    server: {
      proxy: {
        '^/api': {
          target: env.VITE_API_URL || 'http://api:5050',
          changeOrigin: true
        }
      },
      watch: {
        usePolling: true,
        ignored: ['**/node_modules/**', '**/dist/**'],
      },
      host: '0.0.0.0',
      allowedHosts,
    }
  }
})
