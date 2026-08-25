# 启动目标模板

请持续逐阶段推进本仓库的报警管理系统灾后重建工作流，直到 M7 通过，或出现必须人工裁决的真实阻塞。M6 通过时先保存 Windows 原生 Demo 可交付检查点，再继续不阻塞 M6 的 M7 次级交付。

启动时必须：

1. 完整阅读根目录 `AGENTS.md`、`automation/README.md`、`automation/workflow.json` 和 `automation/state.json`。
2. 运行 `python3 scripts/validate_automation.py --status`，只处理输出的当前阶段。
3. 采用 `automation/prompts/controller.md` 作为控制器约束，并按其要求调用阶段执行者和审查者。
4. 普通字段命名、页面布局、内部实现和可逆工程选择自行依据现有事实层裁决；只有产品事实层列出的人工介入条件才暂停。
5. 阶段未通过时继续修复可定位问题，不提前开发后续阶段，不用文档或固定成功返回值冒充实现。

当前运行不得改写历史来源；所有状态推进必须有本次提交状态上的验收证据。
