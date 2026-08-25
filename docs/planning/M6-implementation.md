# M6 Windows 11 x64 原生发布实施契约

## 1. 阶段目标

从已通过 G5 的 `origin/dev` 检查点生成一个可复制、可解压、可直接演示的 Windows 11 x64 ZIP。目标电脑运行 Demo 时不依赖预装 JDK、Python、Node、WSL 或 Docker；浏览器仍访问 `http://127.0.0.1:8080` 完成 M2 至 M5 的同一业务闭环。

M6 只改变构建、打包、运行和验收载体，不改变业务规则、数据库结构、API、页面含义或合成数据预期。

需求范围：PRD-002、PRD-004、PRD-005、PRD-006、PRD-015、PRD-016、PRD-019。

## 2. 发布物边界

构建产物写入忽略的 `.runtime/native/`，二进制运行时和 ZIP 不提交 Git：

```text
alert-management-demo-windows-x64/
  app/core-api.jar
  app/algorithm/algorithm-service.exe
  runtime/jre/
  runtime/postgresql/
  config/runtime.json
  samples/
  scripts/preflight.ps1
  scripts/start.ps1
  scripts/stop.ps1
  scripts/backup.ps1
  scripts/reset-demo.ps1
  README.txt
  THIRD-PARTY-NOTICES.txt
  release-manifest.json
```

`data/`、`logs/`、`pids/` 和 `backups/` 由发布包首次运行创建。Vue 静态资源只随 Java JAR 交付；Python 使用 PyInstaller `onedir`；Java 使用 jlink 运行时；PostgreSQL 使用项目独占的数据目录、端口和进程。

锁定的构建输入为 Microsoft OpenJDK 21.0.12.1、Python 3.12.14+20260814 Windows x64 standalone、PyInstaller 6.22.2、Node.js 22.22.1、PostgreSQL 17.11 Windows x64。所有下载必须使用版本化 URL 和 SHA-256；Node 仅用于构建/验收，不进入 ZIP。

## 3. PowerShell 运维契约

- `preflight.ps1`：检查 Windows x64、包内文件、目录可写性、磁盘和固定端口；执行包内 Java/PostgreSQL/算法版本检查；任何失败返回非零并给出中文修复建议，不自动换端口。
- `start.ps1`：按 PostgreSQL、Python、Java 顺序启动；首次执行 `initdb`；等待数据库、算法和聚合健康；中途失败只回收本包已确认的进程并保留日志；健康全部为 `UP` 后才返回 0。
- `stop.ps1`：只停止 PID、命令行/可执行路径和本包目录相符的 Java/算法进程；PostgreSQL 只通过本包 `pg_ctl -D` 停止；PID 复用时拒绝误杀。
- `backup.ps1`：只连接本包数据库；先生成临时自定义格式备份，经包内 `pg_restore --list` 验证后原子改名，不覆盖既有备份。
- `reset-demo.ps1`：要求交互确认或显式 `-Force`；调用现有 `/api/v1/demo/reset`，核对业务状态为空，不复制清表 SQL，也不删除数据库目录、备份或样例。

所有脚本只由自身所在目录解析发布根目录，不读取仓库绝对路径，不调用 `wsl.exe`、Docker、开发脚本或系统 Java/Python/Node。

## 4. 构建与验收步骤

1. `scripts/native/build-release.ps1` 下载并校验锁定输入，在唯一暂存目录构建 Vue、Java JAR、Python EXE、jlink JRE，复制 PostgreSQL、样例、配置和发布脚本。
2. 构建脚本生成含源提交、文件清单、版本和 SHA-256 的 `release-manifest.json`，再生成 ZIP 与 `.sha256`；已存在目标拒绝覆盖。
3. `scripts/native/verify-release.ps1` 把同一 ZIP 解压到两个全新目录：一个短 ASCII 路径，一个带空格和中文的路径。
4. 两轮均在受限 PATH 下执行预检、启动、聚合健康、真实 Chromium 主链、M5 报告/审计/复位、20,000 行、备份和停止；核对实际进程均来自当前解压目录。
5. 验收捕获浏览器 `console.error`、`pageerror`、服务错误日志、端口/PID 残留及两轮规范化摘要；任一不一致均失败。
6. 第一轮正式启动前占用一个固定端口，确认预检能以非零退出且没有启动任何服务。

唯一 G6 门槛命令：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/native/verify-release.ps1
```

## 5. G6 通过条件

- 同一固定 ZIP 在两个新解压目录完成两轮彩排，第二个路径含中文和空格。
- 发布运行路径不需要预装 JDK、Python、Node、WSL 或 Docker；三个服务进程均来自发布根目录。
- 预检、启停、备份和复位成功/失败语义正确，无越界进程和数据操作。
- M2 至 M5 的 P0 浏览器闭环、20,000 行流程、PDF/XLSX、审计和固定摘要通过。
- 两轮无浏览器控制台错误、未处理异常、错误服务日志、PID/端口残留；阻断和严重缺陷为零。
- 独立审查和候选/`dev` 远端 Windows 检查均通过，证据记录源提交、ZIP SHA、环境、耗时、版本和缺陷结论。

## 6. 明确不做

M6 不建设 MSI、Windows 服务、管理员安装、自动更新、端口自动漂移、生产凭据、通用恢复管理、Docker 交付或第二套业务/迁移/测试路径。只有发布包真实暴露的 P0 缺陷允许在本阶段最小修复。
