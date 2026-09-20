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
    build: {
      // 不要让 esbuild 把 @media (max-width: 768px) 压成 @media (width<=768px)：
      // 媒体查询范围语法要 Safari 16.4 / Chrome 104 以上才认，老手机上会被整条忽略，
      // 表现是「全站响应式失效、侧边栏收起等移动端规则全部不生效」。
      cssTarget: ['chrome87', 'edge88', 'firefox78', 'safari14']
    },
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
