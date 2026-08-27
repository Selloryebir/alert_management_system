# 报警管理系统

本仓库提供报警文件导入、规范化、可解释分析、统计看板、人工处置、审计、报告和备份恢复验证的一体化应用。`v0.8.0` 已通过 Windows 11 x64 自包含运行包、Docker Compose、身份权限、HTTPS 边界和有限可靠性验收；当前正在形成 `1.0.0-rc.1` 业务发布候选，最终发布仍须通过非技术业务用户终验和人工批准。

产品北极星是让业务人员仅按中文说明即可部署并完成“导入校验 → 分析 → 查看 → 处置 → 报告 → 备份恢复”。实现与验收以稳定、可解释、可复现为优先，不把立项阶段未经验证的指标直接作为承诺。仓库内置数据均为合成示例数据。

## Windows 11 快速开始

首选交付物是由部署负责人提供的 Windows x64 自包含 ZIP 及同名 `.sha256`。目标电脑无需预装 JDK、Python、Node.js、WSL 或 Docker。首次使用按以下顺序执行：

1. 在 PowerShell 中用 `Get-FileHash -Algorithm SHA256` 核对 ZIP 与 `.sha256` 的首个字段一致。
2. 把 ZIP 完整解压到当前用户可写的固定目录；不要覆盖另一版本，也不要在首次启动后移动目录。
3. 在发布根目录运行预检和启动：

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\preflight.ps1
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1
   ```

4. 浏览器打开 `http://127.0.0.1:8080`，使用账号 `admin` 和 `data\secrets\bootstrap-admin-password.txt` 中的临时密码登录并立即改密。
5. 选择默认演示项目，上传 `samples\smoke\synthetic_smoke_utf8.csv`，依次完成字段预览、确认导入、分析、详情、处置和 PDF/XLSX 报告。
6. 完成后运行 `scripts\stop.ps1`。备份、隔离恢复验证和精确清理请严格按部署运维手册执行。

详细操作见[业务使用手册](docs/guides/business-user-manual.md)和[Windows 部署与运维手册](docs/guides/windows-deployment-operations.md)。系统不提供覆盖当前业务库的一键恢复、原生 `status.ps1`、MSI、Windows 服务或自动更新；不要从说明文字推断不存在的入口。

不便阅读 Markdown 时，可直接使用仓库内已生成的[业务手册 PDF](deliverables/business-user-manual.pdf)、[业务手册 DOCX](deliverables/business-user-manual.docx)、[部署手册 PDF](deliverables/windows-deployment-operations.pdf)或[部署手册 DOCX](deliverables/windows-deployment-operations.docx)。Windows 自包含 ZIP 也会在 `manuals\` 内携带这四份文件。

## 仓库入口

- [`docs/`](docs/)：文档总入口及推荐阅读顺序。
- [`docs/product/`](docs/product/)：经筛选的产品事实、范围和数据契约。
- [`docs/architecture/`](docs/architecture/)：系统边界与已接受的技术决策。
- [`docs/guides/`](docs/guides/)：正式业务使用和 Windows 部署运维说明。
- [`docs/deliverables/`](docs/deliverables/)：正式项目过程文档的 Markdown 单一事实源。
- [`deliverables/`](deliverables/)：由 Markdown 确定生成的正式 DOCX/PDF 说明书与项目过程文件。
- [`tools/deliverables/`](tools/deliverables/)：正式交付物生成和一致性验证工具。
- [`src/`](src/)：Java 后端、Python 算法服务和 Vue 前端源码。
- [`scripts/validate_repository.py`](scripts/validate_repository.py)：无第三方依赖的仓库结构与文档链接检查。
- [`CONTRIBUTING.md`](CONTRIBUTING.md)：开发、验证和提交约定。

产品要求、架构决策和验收记录分区保存；冲突以已批准的需求基线、产品决策和可执行验收标准为准。

正式源码包由仓库维护流程从已验证的干净提交生成，包内 `SOURCE-MANIFEST.json` 记录源提交、精确文件、权限、大小和 SHA-256。源码包不包含 Git 元数据、本地运行数据、内部阶段控制、内部验收证据或项目档案。

团队职责按交付物划分：前端、Java 后端和 Python 算法代码进入各自源码组件；测试组负责跨组件契约、集成、端到端与演示验收；工程组负责 `scripts/`、持续集成和原生/Docker 交付。实际任务出现时再建立对应目录，不用空目录或不完整实现模拟进度。

## 本地验证

Windows 11 x64 的当前 WSL2 开发环境（在仓库根目录的 PowerShell 中）：

```powershell
wsl.exe python3 scripts/validate_repository.py
wsl.exe scripts/dev/quality.sh
wsl.exe scripts/dev/test.sh
```

Linux、WSL 或 macOS：

```bash
python3 scripts/validate_repository.py
scripts/dev/quality.sh
scripts/dev/test.sh
```

这些命令验证仓库结构、文档链接、静态质量和开发测试；完整业务能力还应按下文运行对应的真实环境验收。

正式 DOCX/PDF 的生成环境、更新方法和只读一致性检查见 [`tools/deliverables/README.md`](tools/deliverables/README.md)。验收已提交交付物时执行：

```bash
python3 tools/deliverables/build.py --check
```

## 开发启动与阶段验证

当前 Windows 11 + WSL2 开发路径要求 Docker Desktop 已启动，并在 WSL 中提供 Node.js 22.12+、`curl`、`tar`、`sha256sum`；Windows 侧提供 JDK 21。Docker 在 M1 只承载 PostgreSQL 17.6，不是最终原生交付方案。脚本会在忽略的 `.runtime/` 中准备固定 Python 3.12 环境：

```bash
scripts/dev/test.sh
scripts/dev/start.sh
scripts/dev/status.sh
scripts/dev/stop.sh
```

启动成功后访问 `http://127.0.0.1:8080`。`status.sh` 在任一进程、数据库或健康状态异常时返回非零。`scripts/dev/restart-smoke.sh` 会连续执行两轮启停，验证三项聚合健康、Vue 静态资源和 Flyway 重复迁移。

合成导入样例位于 `samples/`。以下命令会在 PostgreSQL 17.6 上验证 CSV/TXT/XLSX 等价导入、GB18030、非法批次零落库、重复确认冲突，并生成和导入固定种子的 20,000 行示例数据：

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

Docker Desktop 或 Docker Engine 可用且 Docker Compose 版本不低于 2.24.4 时，从仓库根目录启动同一套 Java、Python 和 PostgreSQL 17 实现；该最低版本用于支持网络覆盖文件中的 `!override` 合并语义：

```bash
scripts/security/prepare-local-secrets.sh
docker compose -p alert-management-m7 up --build --detach --wait
```

浏览器访问 `http://127.0.0.1:8080`，使用账号 `admin` 和脚本输出路径中的首次登录密码，登录后立即改密。密钥仅保存在忽略的 `.runtime/compose-secrets/`，不得提交或发送。停止并保留项目数据使用：

```bash
docker compose -p alert-management-m7 down --remove-orphans
```

删除并仅重建本项目数据卷使用：

```bash
docker compose -p alert-management-m7 down --volumes --remove-orphans
docker compose -p alert-management-m7 up --build --detach --wait
```

网络部署必须另行提供受信的 PKCS#12 证书及密码文件，并叠加 `compose.network.yaml`；该模式只发布 HTTPS 8443，不发布明文 8080、PostgreSQL 或算法端口。缺少 TLS 或实例密钥时会拒绝启动，不能把本机模式端口直接暴露到局域网。

正式容器验收会使用隔离的临时 project，先从空卷完成本机业务闭环，再用全新卷、临时自签证书和显式测试信任完成 HTTPS 网络业务闭环：

```bash
python3 tests/smoke/run.py --target docker --fresh-volume
```

本机阶段只向回环发布 Java 的 8080 端口；网络阶段只发布 HTTPS 8443，明文 8080、PostgreSQL 与算法服务均不对宿主开放。不要使用全局容器或卷清理命令。

## 版本与当前状态

`v0.8.0` 精确指向 `main` 的安全部署与可靠性发布提交。当前 M14 负责固定业务发布候选；`v1.0.0-rc.1` 只能在候选源码、Windows ZIP、SHA-256、说明书和部署验收相互一致，并由非技术业务人员完成终验后创建；在此之前不得把候选描述为正式发布。版本标签和发布门槛见 [`docs/releases/versioning.md`](docs/releases/versioning.md)。
