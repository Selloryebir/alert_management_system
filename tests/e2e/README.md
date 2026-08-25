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
- `E2E_CYCLES`：M5 Smoke 闭环轮数，开发验收默认 `2`；原生发布验收在每个全新解压目录设为 `1`，两目录合计两轮。

所有 E2E 用例统一把浏览器 `console.error` 和未处理的 `pageerror` 视为失败，并将具体错误附加到 Playwright 失败产物。

算法不可用状态通过当前浏览器页内的单次请求故障注入验证，不停止机器上由其他测试使用的服务。

# M5 报告、审计和演示复位验收

M5 在真实 Chromium 中执行两轮 300 行完整闭环，比较移除 UUID 和时间后的固定摘要；随后执行一次 20,000 行 PDF/XLSX 报告门槛。浏览器只验证下载、类型和文件签名，报告内容深度解析由后端集成测试负责。

```bash
scripts/dev/m5-report-audit-reset-smoke.sh
```

脚本会先将本项目 PostgreSQL 写入 `.runtime/backups/` 的可读自定义格式备份，再建立不属于业务表的 SQL 哨兵，验证每次演示复位都不会越界删除。报告耗时、大小和规范化摘要保存在 `.runtime/m5/results/`。

# M6 Windows 原生发布验收

正式入口在 Windows PowerShell 5.1 下构建或复用当前提交的固定 ZIP，再把同一 ZIP 解压到 ASCII 路径及带中文和空格的两个全新目录。每个目录把 `E2E_CYCLES` 设为 `1`，合计完成两轮 M4/M5 及 20,000 行流程；验收同时核对受限 PATH、失败预检、包内进程、固定端口、PID 记录、健康状态、备份、复位、日志和规范化摘要。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/native/verify-release.ps1
```

开发阶段脏工作区试构建可显式增加 `-AllowDirty`；正式验收不使用该参数。也可通过 `-ArchivePath <zip>` 验证已有发布包，但 ZIP 的 SHA-256 和源提交仍必须与当前仓库一致。
