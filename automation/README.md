# 自动化阶段控制

本目录把 `docs/planning/README.md` 中的 M0–M15 计划转换为可机器读取、可逐阶段推进的控制面。M0–M7 形成 `v0.1.0` 可运行基线，M8–M14 推进正式产品化，M15 开始正式发布后的产品体验迭代。控制面不执行后台守护进程，也不绕过 Git、测试或人工裁决边界。

## 文件

- `workflow.json`：稳定的阶段顺序、依赖、引用、提示链和验收门槛。
- `state.json`：唯一当前状态；保存各阶段检查点、证据、失败和当前可恢复增量。
- `prompts/start-goal.md`：发起持续目标时的入口提示。
- `prompts/controller.md`：主智能体的循环、状态推进和提交规则。
- `prompts/stage-worker.md`：实现型智能体的最小职责。
- `prompts/reviewer.md`：独立验收和结论格式。

## 使用

先验证契约并查看当前阶段：

```bash
python3 scripts/validate_automation.py
python3 scripts/validate_automation.py --status
```

然后把 `prompts/start-goal.md` 作为目标入口交给主智能体。主智能体按控制器提示读取 `--status`，只推进当前阶段，并在适合并行时把互不覆盖的工作交给阶段执行者。

在支持持久目标的 Codex 会话中，可用 `/goal` 创建长期目标，并把 `prompts/start-goal.md` 全文作为目标内容。目标、验证方法和停止条件都已在提示与工作流中固定；会话中断后先重新运行 `--status`，不要依赖聊天记忆猜测进度。

## 状态机

合法状态为：

```text
blocked -> ready -> in_progress -> review -> passed
                        |            |
                        +-> failed <-+

任一当前活动状态 -> awaiting_human / blocked_external -> 原活动状态
```

这里使用精简的小写持久化状态：`blocked` 对应详细状态机的 `LOCKED`，`in_progress`/`failed` 覆盖实现与针对性修复，`review` 覆盖验证、门槛通过和检查点建立，`passed` 对应已完成且远端可恢复。`awaiting_human` 与 `blocked_external` 保留详细状态机的暂停含义。

- 依赖全部 `passed` 后，控制器才能把阶段从 `blocked` 置为 `ready`。
- `current_stage` 必须指向唯一尚未通过且当前可处理或明确暂停的阶段。
- `ready` 尚未选定增量时 `active_run` 为 `null`；进入实现、审查、修复或暂停后必须填写可恢复的活动记录，暂停时同时记录恢复前状态。
- 只有控制器修改 `state.json`；阶段执行者和审查者只返回结果。
- `passed` 必须有本次实现对应的证据文件、全部验收命令成功记录和已核验的远端检查点。
- 工作流结构调整应同步更新校验器；日常阶段推进只修改 `state.json`。

验收命令是阶段结束门槛。后续阶段的命令或路径在当前可能尚不存在，这是正常的；对应阶段通过前必须由真实实现补齐并成功执行，不能删除命令来规避验收。
