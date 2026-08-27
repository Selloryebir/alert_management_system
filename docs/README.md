# 文档总入口

本仓库按产品需求、架构决策、操作说明、正式过程文件和内部验收记录分区维护。产品实现以当前需求基线、代码和可复现验收结果为准。

## 阅读顺序

1. [产品事实基线](product/README.md)：确认北极星、P0 范围、排除项和人工介入边界。
2. [需求追踪矩阵](product/requirements.md)：只实现状态为“采用”或“调整后采用”的要求。
3. [数据契约 v1](product/data-contract.md)：统一导入字段、状态、Java—Python 接口和合成数据。
4. [系统架构基线](architecture/README.md)与[决策记录](decisions/README.md)：确认组件职责和禁止的架构漂移。
5. `guides/`：面向业务人员和部署管理员的正式操作说明。
6. `deliverables/`：立项、中期、测试、结项、过程与方案差距的 Markdown 单一事实源。
7. 仓库根目录 [`../deliverables/`](../deliverables/)：从上述事实源确定生成的 DOCX/PDF 正式交付文件。

`planning/`、`verification/` 和 `automation/` 服务内部研发与验收，不属于正式源码导出内容。

## 责任分层

| 目录 | 责任 | 是否直接驱动实现 |
| --- | --- | --- |
| [`product/`](product/) | 当前产品事实、采用矩阵、数据契约和冲突隔离 | 是 |
| [`architecture/`](architecture/) | 当前技术架构和组件边界 | 是 |
| [`decisions/`](decisions/) | 已接受的重要产品/架构决策 | 是 |
| [`planning/`](planning/) | 内部阶段、步骤和验收门槛 | 是 |
| [`verification/`](verification/) | 测试策略、缺陷等级和审计证据要求 | 是 |
| [`automation/`](automation/) | 自动化状态机、Git 生命周期和开发审计引导 | 是 |
| `guides/` | 正式业务使用与部署运维说明 | 是 |
| `deliverables/` | 可重复生成 DOCX/PDF 的正式项目过程文档源 | 是 |

生成物不在 `docs/` 内维护；更新事实源后按 [`../tools/deliverables/README.md`](../tools/deliverables/README.md)重新生成并执行只读一致性检查。

任何“已完成”“已通过”或性能指标都必须由对应代码、测试和同一 Git 提交上的验收证据支持。
