# M7 Docker Compose 次级交付实施契约

## 1. 阶段目标

在不修改业务 API、算法规则、数据库迁移和页面语义的前提下，为现有 Java 主系统、Python 算法服务和 PostgreSQL 17 增加一套 Docker Compose 次级启动方式。Compose 必须从空项目卷健康启动，并用仓库已有固定样例复用 AC-003、AC-005、AC-008；它不替代已通过 G6 的 Windows 原生包。

## 2. 固定边界

- 运行服务只有 `postgres`、`algorithm`、`backend`；Vue 仍编译进同一个 Java JAR，不新增前端运行容器。
- Java 仍是唯一业务入口和事实写入者；Python 仍为不连接数据库的纯计算服务；Flyway 仍由 Java 启动时执行。
- PostgreSQL 使用项目专用 Compose 卷；只将 Java 的 HTTP 端口绑定到 `127.0.0.1`，数据库和算法服务不发布到宿主机。
- 容器通过现有 `DB_*`、`ALGORITHM_*`、`ALGORITHM_HOST`、`ALGORITHM_PORT` 配置，不增加第二套配置语义。
- 镜像只打包现有源码和锁文件产生的构件，不提交 JAR、前端 `dist`、Python 环境或数据库数据。
- 不建设 Kubernetes、镜像仓库发布、生产密钥、反向代理、动态扩缩容、跨架构矩阵或第二套业务测试。

## 3. 最小实现

1. 在 `packaging/docker/` 提供 Java/Vue 多阶段构建和 Python 服务镜像定义及精确 `.dockerignore`。
2. 在仓库根提供 `compose.yaml`，声明依赖健康顺序、项目卷、容器健康检查和本机 HTTP 绑定。
3. 实现 `python3 tests/smoke/run.py --target docker --fresh-volume`：
   - 使用独立 Compose project，启动前后均删除它自己的容器、网络和卷；
   - 构建镜像并从空卷 `up --wait`，核对三个容器均健康、聚合健康为 `UP`、页面带重建 Demo 标识；
   - 导入等价 CSV/TXT/XLSX，各 300 行规范化结果一致（AC-003）；
   - 对固定 Smoke 执行实际 Python 分析，并与 `samples/expected/analysis-smoke-expected.json` 对账（AC-005）；
   - 将一个报警执行 `OPEN → IN_PROGRESS → CLOSED`，核对详情历史和审计字段（AC-008）；
   - 输出提交、镜像、固定摘要、耗时和清理结果；失败时保存 `compose ps` 与服务日志并返回非零。
4. 增加独立 Docker CI，在相关文件变化时执行同一 G7 命令；不让 Docker 检查阻断或重跑已封板的 G6 原生构建。
5. 根说明记录直接启动、停止、空卷复位和正式验收命令。

## 4. 允许修改路径

- `packaging/docker/`
- `.dockerignore`
- `compose.yaml`
- `tests/smoke/`
- `.github/workflows/docker-compose-check.yml`
- `README.md`
- `automation/workflow.json`
- `automation/state.json`
- `docs/planning/M7-implementation.md`
- `docs/verification/evidence/M7.md`

任何业务源码、Flyway、样例或既有测试的修改都需要先证明 Compose 无法正确复用当前契约；否则视为架构漂移。

## 5. 验收门槛 G7

- 本机 Docker Desktop 从空项目卷执行正式验收成功，结束后无该 project 的容器和卷残留。
- 远端 Ubuntu Docker 执行同一命令成功。
- CSV/TXT/XLSX 规范化结果一致；分析摘要与共享固定预期一致；处置和审计闭环完整。
- 健康等待失败、构建失败、断言失败或清理失败均不得返回假成功。
- 仓库校验、自动化状态校验和 `git diff --check` 通过；独立审查确认没有第二套业务实现和未关闭阻断/严重缺陷。

达到以上全部条件后才可将 M7 标记为 `passed`。Docker 守护进程不可用属于外部阻断，必须保留可恢复状态，不得用静态配置检查冒充运行验收。
