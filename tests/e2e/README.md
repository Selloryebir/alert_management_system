# M4 真浏览器验收

本目录只覆盖审核员可达的业务闭环，不复制前端单元测试或后端规则。Playwright 使用真实 Chromium 访问由 Java 托管的 Vue 页面，业务请求进入实际 Java、Python 和 PostgreSQL。

默认主流程使用仓库固定的 300 行 Smoke CSV：

```bash
npm ci --prefix tests/e2e
npm --prefix tests/e2e run install:chromium
npm --prefix tests/e2e test
```

统一验收入口为：

```bash
scripts/dev/m4-browser-smoke.sh
```

脚本先启动开发组件，运行 300 行完整浏览器闭环，再生成并运行 20,000 行首屏验收。测试每次通过页面创建新批次，不依赖已有数据库顺序，也不停止共享算法服务。失败产物仅保存在 `tests/e2e/test-results/`，不提交截图或浏览器缓存。

可配置环境变量：

- `E2E_BASE_URL`：默认 `http://127.0.0.1:8080`。
- `E2E_MODE`：`smoke` 执行完整闭环；`demo` 只验证 20,000 行上传、分析和看板首屏。
- `E2E_DATASET`：相对仓库根目录或绝对路径；默认 Smoke CSV。
- `E2E_EXPECTED_TOTAL`：默认 `300`。

算法不可用状态通过当前浏览器页内的单次请求故障注入验证，不停止机器上由其他测试使用的服务。
