import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 本地 Demo 通过同源代理验收，避免浏览器跨域环境影响写操作测试。
export default defineConfig({
  plugins: [react()],
  define: {
    'import.meta.env.VITE_API_BASE_URL': JSON.stringify('/__demo_api')
  },
  server: {
    host: '127.0.0.1',
    port: 5180,
    strictPort: true,
    proxy: {
      '/__demo_api': {
        target: 'http://127.0.0.1:18181',
        rewrite: path => path.replace(/^\/__demo_api/, '')
      }
    }
  }
});
