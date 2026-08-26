# M14 Windows 业务用户终验与发布候选实施约定

## 1. 目标与边界

M14 把已经通过 M13 的产品、手册和发布链冻结为可供真实业务用户终验的 `v1.0.0-rc.1` 候选。Windows 11 x64 自包含 ZIP 是首要路径；HTTPS Docker Compose 是次级部署路径。两者复用同一 Java、Python、PostgreSQL、Vue 实现和合成数据，不增加安装器、Windows 服务、网关或第二套业务逻辑。

自动化可以证明构件完整、命令可执行、业务闭环与失败边界成立，但不能证明一名不了解源码的人员能独立理解手册。因此工程自动门槛通过后，阶段必须暂停在绑定候选和 ZIP 的人工业务终验；不能由智能体代签、代填或用浏览器自动化冒充人员理解性。

## 2. 最小实施范围

### 2.1 Windows 发布候选预验收

- 新增 `scripts/release/verify-business-release.ps1` 作为唯一 M14 Windows 自动入口，拒绝脏工作区，固定版本 `1.0.0-rc.1`，在独立输出目录构建并验收 ZIP。
- 原生验收的业务模式必须从登录页完成首次登录和改密，再由页面完成项目、导入、分析、详情、分类修订、处置、报告和备份状态检查；不得用 API 登录替代首次使用体验。
- 在实例 A 创建并完整校验恢复点，把 `.dump` 与 `.meta.json` 成对复制到发布根外，精确清理实例 A；再由全新实例 B 导入该恢复点并执行隔离恢复验证。
- 保留 ZIP 哈希损坏、端口占用、未知实例或越界清理等直接失败边界；任一子命令非零时总入口非零并保存 FAILED 摘要。
- 自动化脚本只用于工程预验收，不写入业务手册的日常安装步骤，也不制造 MSI 或系统级卸载器语义。

### 2.2 HTTPS Docker Compose 正式验收

- 固定命令 `python3 tests/smoke/run.py --target docker --fresh-volume` 先保留本机回环模式闭环，再从另一个空项目卷叠加 `compose.network.yaml`。
- 网络模式缺少 TLS 文件时必须失败；测试用自签证书只用于验收，并由测试客户端显式信任，不得出现在正式部署说明中。
- HTTPS 8443 完成健康、前端、登录、三格式导入、固定分析和处置审计；明文 8080 必须关闭，PostgreSQL 与算法端口不得发布。
- 两种部署的规范化摘要和算法摘要一致，两个隔离 Compose 项目的容器、卷和网络均被精确清理。

### 2.3 发布候选、文档和 CI

- `scripts/validate_release_candidate.py` 检查 M0–M13 状态、57 项来源处置、M13 事实收口、正式交付物摘要、版本入口、Git 工作树和标签边界；阶段尚待人工终验时不得要求或伪造标签。
- README、来源矩阵和正式差距说明更新到 M13 已通过、M14 候选状态；Markdown 变更后重新生成并校验 DOCX/PDF。
- GitHub Actions 只升级已产生 Node 20 弃用警告的官方 action 主版本，不借机重写工作流。
- M13 证据中的命令抄录错误以追加更正方式保留历史并给出重新执行结果。

## 3. 自动验收与证据

固定候选必须在干净工作树执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/release/verify-business-release.ps1
```

```bash
python3 tests/smoke/run.py --target docker --fresh-volume
python3 scripts/validate_release_candidate.py --mode candidate
```

同时运行受影响的仓库、文档生成、PowerShell 解析、Python 单测和前端/Playwright 定向检查。自动证据至少记录完整提交、ZIP 与 SHA-256、发布版本、Windows 版本、双实例恢复、HTTPS 边界、业务摘要、清理结果、失败候选、远端 run 和独立复审结论。

## 4. 人工业务终验

自动门槛与独立复审通过后，由一名不了解源码的业务人员只取得以下材料：

- 固定候选 ZIP；
- 同名 `.sha256`；
- ZIP 内 `manuals` 的业务手册和部署手册。

该人员不读取仓库源码或测试脚本，按随包手册完成哈希核对、全新解压、预检、启动、网页登录与首次改密、项目与 300 行样例闭环、PDF/XLSX 下载、备份与隔离恢复、停止和确认式实例清理，并记录耗时、歧义、阻断/严重缺陷、报告打开软件及版本。验收人和项目责任方必须填写手册末尾记录；智能体不能代填。通过后把原始记录转存为 `docs/verification/evidence/M14.md`，并在 `automation/state.json` 的 `human_acceptance` 中逐项绑定候选提交、ZIP SHA-256、环境、验收人、零阻断/严重缺陷和签署时间；发布校验器同时核对两处，不能仅靠把阶段状态改成 `passed` 绕过 AC-022。

人工结果未返回或存在未关闭阻断/严重缺陷时，M14 使用 `awaiting_human` 或回到定向修复，不标记 `passed`，不创建候选标签。

## 5. Git 与发布顺序

1. 在 `feat/m14-business-release` 形成固定实现候选，完成三条自动门槛和独立复审。
2. 以该候选构建 ZIP，完成人工业务终验。
3. 人工 PASS 后先把 SC-042 在两份来源矩阵中改为“已实现”，同步正式结论、生成交付物和 M14 证据；运行 `python3 scripts/validate_release_candidate.py --mode approved`，标签此时仍必须不存在。
4. 通过 PR 非快进合入 `dev`，在合并提交重跑适用门槛并记录树一致性。
5. 经针对发布的人工确认后，以 `dev -> main` PR 合入；在 `main` 合并提交重跑适用远端检查和 `approved` 校验，随后把该 main 提交同步回 `dev`。
6. 证据完整且标签不存在时在已验证的 main 提交创建 annotated `v1.0.0-rc.1`；标签事件显式运行 `--mode post-main`，并验证标签、远端 main 和 dev 均可达同一发布结果。
7. M14 最终状态和证据同步回 `dev` 后运行 `--mode released`，停止在 `v1.0.0` 正式发布人工门禁；不得自动创建正式版标签。
