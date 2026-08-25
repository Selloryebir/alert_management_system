# M4 真浏览器验收

本目录只覆盖审核员可达的业务闭环，不复制前端单元测试或后端规则。Playwright 使用真实 Chromium 访问由 Java 托管的 Vue 页面，业务请求进入实际 Java、Python 和 PostgreSQL。

默认主流程使用仓库固定的 300 行 Smoke CSV：

```bash
npm ci --prefix tests/e2e
npm --prefix tests/e2e run install:chromium
npm --prefix tests/e2e run test:smoke
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

# M5 报告、审计和演示复位验收

M5 在真实 Chromium 中执行两轮 300 行完整闭环，比较移除 UUID 和时间后的固定摘要；随后执行一次 20,000 行 PDF/XLSX 报告门槛。浏览器只验证下载、类型和文件签名，报告内容深度解析由后端集成测试负责。

```bash
scripts/dev/m5-report-audit-reset-smoke.sh
```

脚本会先将本项目 PostgreSQL 写入 `.runtime/backups/` 的可读自定义格式备份，再建立不属于业务表的 SQL 哨兵，验证每次演示复位都不会越界删除。报告耗时、大小和规范化摘要保存在 `.runtime/m5/results/`。
