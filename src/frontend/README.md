# M2 前端 Demo

Vue 3 + TypeScript + Vite 的最小 Demo。页面醒目标注“2026 年灾后重建 Demo”和“仅使用合成数据”，展示基础健康状态，并提供文件校验、预览、确认导入和最近批次查看入口。

本阶段只实现运行状态和导入向导，不包含算法执行、看板、处置或报告功能。

## 环境

- Node.js 22.12 或更高版本
- npm 9 或更高版本
- 开发时 Vite 将同源 `/api` 请求代理到本机 `127.0.0.1:8080` 的 Java 主系统
- 生产构建产物由 Java 主系统托管

## 开发与验证

```bash
npm ci
npm run dev
npm test -- --run
npm run build
```

启动 Java 主系统后运行 `npm run dev`，访问 `http://127.0.0.1:5173`。浏览器始终请求同源 `/api/v1/**`；代理目标仅用于本地 Vite 开发，不进入生产构建。

`dist/`、测试覆盖率和 `node_modules/` 均为可再生成内容，不提交 Git。
