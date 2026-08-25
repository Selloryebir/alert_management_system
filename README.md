# 报警管理系统（灾后重建）

本仓库用于重建报警管理系统的可演示版本。当前处于资料提取、需求收敛和工程基础初始化阶段，尚未形成可运行的业务系统。

阶段目标是交付一个可重复演示的最小闭环：导入报警样例文件，完成校验、分析和展示，并能导出处置结果。实现与验收以稳定、可解释、可复现为优先，不把历史材料中未经验证的指标直接作为承诺。

## 仓库入口

- [`docs/`](docs/)：文档总入口及推荐阅读顺序。
- `docs/backgrounds/`：只读原始 PDF、DOCX 资料及来源索引。
- [`docs/sources/`](docs/sources/)：从原始资料生成的可追溯 Markdown、图片和提取清单。
- [`docs/product/`](docs/product/)：经筛选的产品事实、范围和数据契约。
- [`docs/architecture/`](docs/architecture/)：系统边界与已接受的技术决策。
- [`docs/planning/`](docs/planning/)：阶段规划、范围和验收入口。
- [`src/`](src/)：产品代码的职责边界说明；业务代码尚未初始化。
- [`tools/document-extraction/`](tools/document-extraction/)：可重复的历史材料提取工具。
- [`scripts/validate_repository.py`](scripts/validate_repository.py)：无第三方依赖的仓库结构与文档链接检查。
- [`CONTRIBUTING.md`](CONTRIBUTING.md)：开发、验证和提交约定。

后续文档应把原始事实、筛选后的产品要求、架构决策和验收记录分开保存。原始材料存在冲突时，以已落盘的产品决策和可执行验收标准为准。

团队职责按交付物划分：前端、Java 后端和 Python 算法代码进入各自源码组件；测试组负责跨组件契约、集成、端到端与演示验收；工程组负责 `scripts/`、持续集成和原生/Docker 交付。实际任务出现时再建立对应目录，不用空目录或占位代码模拟进度。

## 本地验证

Windows 11 x64（首要环境）：

```powershell
py -3 scripts/validate_repository.py
```

Linux、WSL 或 macOS：

```bash
python3 scripts/validate_repository.py
```

该命令只验证仓库基础结构、Markdown 相对链接和已跟踪文件中是否混入生成目录；它不代表业务功能已经实现或通过验收。

## 交付约束

- Windows 11 x64 原生启动包为首要交付物，应提供环境预检、启动、停止、演示数据复位和日志定位入口。
- Docker Compose 为次级交付方式，必须复用同一套应用、数据库迁移和配置语义。
- 不为了模拟团队分工而拆出无必要的运行组件。Java 负责业务主流程，Python 仅在算法需求成立时承担算法计算，PostgreSQL 作为业务事实源，前端独立开发后随原生包交付。
- 在实现出现前，不提供空启动脚本、空服务或虚假的“可运行”说明。

## 当前状态

M0 的材料提取、信息隔离、产品事实、架构和验收基线已经建立；业务代码尚未初始化。下一步按 [`M1 可运行工程骨架`](docs/planning/#m1可运行工程骨架) 开始实现。
