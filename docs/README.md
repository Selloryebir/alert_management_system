# 文档总入口

本仓库把历史来源、当前产品事实、架构决策、开发计划和验收证据分开保存。开发智能体应从产品事实和规划层进入，不得直接把原始材料或提取文本当作实现指令。

## 阅读顺序

1. [产品事实基线](product/README.md)：确认北极星、P0 范围、排除项和人工介入边界。
2. [需求追踪矩阵](product/requirements.md)：只实现状态为“采用”或“调整后采用”的要求。
3. [数据契约 v1](product/data-contract.md)：统一导入字段、状态、Java—Python 接口和合成数据。
4. [系统架构基线](architecture/README.md)与[决策记录](decisions/README.md)：确认组件职责和禁止的架构漂移。
5. [阶段开发计划](planning/README.md)：按 M0–M7 门槛实施和提交。
6. [测试与验收策略](verification/README.md)：以当前提交上的可复现证据判断完成状态。
7. [自动化开发蓝图](automation/README.md)：仅在需要自动持续开发时，按状态机和提示链推进当前阶段。

## 责任分层

| 目录 | 责任 | 是否直接驱动实现 |
| --- | --- | --- |
| [`backgrounds/`](backgrounds/) | 保存四份只读原始材料 | 否 |
| [`sources/`](sources/) | 按页/章节提取历史来源并记录排除边界 | 否 |
| [`product/`](product/) | 当前产品事实、采用矩阵、数据契约和冲突隔离 | 是 |
| [`architecture/`](architecture/) | 当前技术架构和组件边界 | 是 |
| [`decisions/`](decisions/) | 已接受的重要产品/架构决策 | 是 |
| [`planning/`](planning/) | 阶段、步骤、智能体分工和验收门槛 | 是 |
| [`verification/`](verification/) | 测试策略、缺陷等级和审计证据要求 | 是 |
| [`automation/`](automation/) | 自动化状态机、Git 生命周期和开发审计引导 | 是 |

原始或提取材料中的“已完成”“已通过”“达到指标”等均是历史陈述。当前重建项目只有在对应代码、测试和验收证据绑定同一 Git 提交后，才能声明完成。
