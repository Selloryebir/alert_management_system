# 报警管理系统（灾后重建）

本仓库用于重建报警管理系统的可演示版本。M1 至 M6 已完成可运行骨架、合成数据导入、可解释分析、浏览器业务闭环、报告审计和 Windows 11 x64 原生发布；当前实现 Docker Compose 次级交付。

阶段目标是交付一个可重复演示的最小闭环：导入报警样例文件，完成校验、分析和展示，并能导出处置结果。实现与验收以稳定、可解释、可复现为优先，不把历史材料中未经验证的指标直接作为承诺。

## 仓库入口

- [`docs/`](docs/)：文档总入口及推荐阅读顺序。
- `docs/backgrounds/`：只读原始 PDF、DOCX 资料及来源索引。
- [`docs/sources/`](docs/sources/)：从原始资料生成的可追溯 Markdown、图片和提取清单。
- [`docs/product/`](docs/product/)：经筛选的产品事实、范围和数据契约。
- [`docs/architecture/`](docs/architecture/)：系统边界与已接受的技术决策。
- [`docs/planning/`](docs/planning/)：阶段规划、范围和验收入口。
- [`automation/`](automation/)：可恢复的阶段状态、机器可读工作流和 Codex 提示链。
- [`src/`](src/)：Java 后端、Python 算法服务和 Vue 前端源码。
- [`tools/document-extraction/`](tools/document-extraction/)：可重复的历史材料提取工具。
- [`scripts/validate_repository.py`](scripts/validate_repository.py)：无第三方依赖的仓库结构与文档链接检查。
- [`CONTRIBUTING.md`](CONTRIBUTING.md)：开发、验证和提交约定。

后续文档应把原始事实、筛选后的产品要求、架构决策和验收记录分开保存。原始材料存在冲突时，以已落盘的产品决策和可执行验收标准为准。

团队职责按交付物划分：前端、Java 后端和 Python 算法代码进入各自源码组件；测试组负责跨组件契约、集成、端到端与演示验收；工程组负责 `scripts/`、持续集成和原生/Docker 交付。实际任务出现时再建立对应目录，不用空目录或占位代码模拟进度。

## 本地验证

Windows 11 x64 的当前 WSL2 开发环境（在仓库根目录的 PowerShell 中）：

```powershell
wsl.exe python3 scripts/validate_repository.py
wsl.exe python3 scripts/validate_automation.py
```

Linux、WSL 或 macOS：

```bash
python3 scripts/validate_repository.py
python3 scripts/validate_automation.py
```

这些命令只验证仓库基础结构、文档链接和自动化状态定义；它们不代表业务功能已经实现或通过验收。

## 开发启动与阶段验证

当前 Windows 11 + WSL2 开发路径要求 Docker Desktop 已启动，并在 WSL 中提供 Node.js 22.12+、`curl`、`tar`、`sha256sum`；Windows 侧提供 JDK 21。Docker 在 M1 只承载 PostgreSQL 17.6，不是最终原生交付方案。脚本会在忽略的 `.runtime/` 中准备固定 Python 3.12 环境：

```bash
scripts/dev/test.sh
scripts/dev/start.sh
scripts/dev/status.sh
scripts/dev/stop.sh
```

启动成功后访问 `http://127.0.0.1:8080`。`status.sh` 在任一进程、数据库或健康状态异常时返回非零。`scripts/dev/restart-smoke.sh` 会连续执行两轮启停，验证三项聚合健康、Vue 静态资源和 Flyway 重复迁移。

M2 的合成导入样例位于 `samples/`。以下命令会在 PostgreSQL 17.6 上验证 CSV/TXT/XLSX 等价导入、GB18030、非法批次零落库、重复确认冲突，并生成和导入固定种子的 20,000 行 Demo：

```bash
scripts/dev/m2-import-smoke.sh
```

M3 的独立黄金预期位于 `samples/expected/analysis-smoke-expected.json`。以下命令会使用实际 Java、Python 和 PostgreSQL 17.6，逐行核对 300 行规则结果与 12 条关联事件链，验证算法中断后的失败零结果和恢复重试，并完成 20,000 行分析：

```bash
scripts/dev/m3-analysis-smoke.sh
```

M4 的真实浏览器验收复用同一批固定样例。以下命令会构建并启动完整应用，由 Chromium 完成导入、分析、看板核对、详情追溯、事件链查看、人工处置及失败提示检查；随后再完成固定种子的 20,000 行页面演示流程：

```bash
scripts/dev/m4-browser-smoke.sh
```

脚本会在 WSL 缺少 Chromium 运行库时把所需 Debian 包解压到忽略的 `.runtime/`，不会修改系统软件；首次运行需联网下载浏览器。运行结束后自动停止应用，失败时保留 `.runtime/logs/` 供定位。

M5 在同一真实环境生成 PDF/XLSX、查询统一业务审计、验证人工分类修订，并连续执行两轮 300 行复位闭环和一次 20,000 行报告流程。脚本先为本项目 PostgreSQL 生成可读备份，再用非业务表哨兵确认复位不越界：

```bash
scripts/dev/m5-report-audit-reset-smoke.sh
```

单独备份当前项目数据库可执行 `scripts/dev/backup.sh`；备份写入忽略的 `.runtime/backups/`，不会覆盖同名文件。浏览器报告和指标保存在 `.runtime/m5/results/`，仅作为当前机器验收产物，不提交 Git。

## 交付约束

- Windows 11 x64 原生启动包为首要交付物，应提供环境预检、启动、停止、演示数据复位和日志定位入口。
- Docker Compose 为次级交付方式，必须复用同一套应用、数据库迁移和配置语义。
- 不为了模拟团队分工而拆出无必要的运行组件。Java 负责业务主流程，Python 仅承担算法计算，PostgreSQL 作为业务事实源，前端构建后由 Java 托管。
- 不提供空启动脚本、空服务或虚假的“可运行”说明。

## Docker Compose 次级交付

Docker Desktop 或 Docker Engine 可用时，从仓库根目录启动同一套 Java、Python 和 PostgreSQL 17 实现：

```bash
docker compose -p alert-management-m7 up --build --detach --wait
```

浏览器访问 `http://127.0.0.1:8080`。停止并保留项目数据使用：

```bash
docker compose -p alert-management-m7 down --remove-orphans
```

删除并仅重建本项目数据卷使用：

```bash
docker compose -p alert-management-m7 down --volumes --remove-orphans
docker compose -p alert-management-m7 up --build --detach --wait
```

正式 G7 验收会使用隔离的临时 project，从空卷完成健康、CSV/TXT/XLSX 导入、固定分析、处置审计和清理闭环：

```bash
python3 tests/smoke/run.py --target docker --fresh-volume
```

Compose 只向本机发布 Java 的 8080 端口；PostgreSQL 与算法服务仅在项目网络内可达。不要使用全局容器或卷清理命令。

## 当前状态

M0 至 M6 已通过；M7 Docker Compose 次级交付正在实现。状态以 `automation/state.json` 及对应阶段的当前提交证据为准。
