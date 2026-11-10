# Vue 3 + Vite

This template should help get you started developing with Vue 3 in Vite. The template uses Vue 3 `<script setup>` SFCs, check out the [script setup docs](https://v3.vuejs.org/api/sfc-script-setup.html#sfc-script-setup) to learn more.

Learn more about IDE Support for Vue in the [Vue Docs Scaling up Guide](https://vuejs.org/guide/scaling-up/tooling.html#ide-support).

## 前端 (Vue 3 + Element Plus + Vite)

```
cd webapp
npm install
npm run dev      # 开发 (5180, 代理 /api → 8300)
npm run build    # 产物输出到 ../server/web
```

生产访问: `http://localhost:8300/` (FastAPI 静态挂载)。
后端新增端点: `/api/image?path=` (NAS 图片直读), `/api/retouch` 支持 `overrides` JSON (滑杆实时调参)。
