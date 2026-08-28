报警管理系统（Windows 11 x64）
==============================

本目录是自包含运行包。请完整解压后运行，不要只复制其中某个脚本或程序。
无需预装 JDK、Python、Node.js、WSL 或 Docker。
面向业务人员和部署人员的图文说明位于 manuals\：业务使用手册、Windows 部署与运维手册
和组合模型技术手册均提供 DOCX 与 PDF。首次操作前请先阅读对应手册。

首次启动
--------
1. 用普通用户权限打开 PowerShell（不要选择“以管理员身份运行”），切换到本目录。
2. 执行：powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\preflight.ps1
3. 预检通过后执行：powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1
4. 浏览器访问：http://127.0.0.1:8080
5. 使用管理员账号 admin 登录；首次临时密码位于 data\secrets\bootstrap-admin-password.txt。
6. 首次登录后按页面提示立即修改密码。

日常操作
--------
- 停止：powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\stop.ps1
- 备份：powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\backup.ps1 -RetentionCount 14
- 备份状态：powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\backup-status.ps1
- 隔离恢复验证：powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\restore-verify.ps1 -BackupPath backups\<备份文件>.dump
- 演示复位：powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\reset-demo.ps1（会安全提示输入管理员凭据）

备份恢复验证
------------
restore-verify.ps1 使用包内 PostgreSQL，在随机回环端口启动独立临时实例，恢复指定备份，
并验证迁移版本、关键业务表和关键序列均可查询。脚本不会覆盖当前数据库，结束后只删除带有
本实例身份标记的临时恢复目录，恢复事实写入 logs\。

日常验证旧备份时不要求当前数据库仍与备份时相同。刚执行备份并要求证明当前库完全一致时，
追加 -RequireCurrentMatch，并在验证期间暂停业务写入；此时才会把迁移、表行数与内容摘要、
序列状态逐项同当前源库对账。

每个备份由 .dump 与同名 .dump.meta.json 组成，元数据记录来源实例 ID、来源提交、文件大小和
SHA-256。来源字段用于追溯，不限制把保留的恢复点复制到新安装进行状态检查和恢复。
backup.ps1 只保留由当前实例产生且哈希有效的最新 RetentionCount 个恢复点，默认 14 个；
外来来源、身份异常或元数据损坏的文件不会被自动删除。backup-status.ps1 会计算哈希并列出恢复点、
总容量和最近成功时间；状态失败时不要把该文件用于恢复。

每日自动备份（当前 Windows 用户）
---------------------------------
配置每天 02:00 运行并保留 14 个恢复点：
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\backup-schedule.ps1 -Action Configure -DailyAt 02:00 -RetentionCount 14

查看：scripts\backup-schedule.ps1 -Action Status
移除：scripts\backup-schedule.ps1 -Action Remove

任务名包含当前 instance_id，只在当前用户登录且本发布实例正在运行时备份；失败记录写入
logs\scheduled-backup.log。重新配置只覆盖身份匹配的本实例任务，同名但身份不符时拒绝操作。
执行 cleanup-instance.ps1 时会先安全移除本实例的计划任务。

备份、隔离恢复和实例清理使用同一实例维护锁，不会并发修改运行数据。清理会先核验、停止并
等待本实例计划任务，再取得维护锁；锁被其他手工备份或恢复占用时会拒绝继续。

卸载与实例清理
--------------
先保留好需要的备份，再执行：
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\cleanup-instance.ps1

脚本要求输入当前实例 ID，停止并核对当前发布实例后，只清理当前解压目录内的 data、logs
和 pids，默认保留 backups。确需同时删除备份时追加 -RemoveBackups。脚本不会自删程序文件。
“默认保留”只表示脚本不删除包内 backups；如需保留恢复点，必须先把成对文件复制到发布根之外
的受控目录并保留状态检查成功记录，确认副本有效后才可手工删除当前解压目录。实例首次启动后不要移动
整个解压目录；路径与实例身份不一致时，启停、备份和清理都会拒绝执行。
默认保留的恢复点可在清理后重新启动生成新实例身份，再用 backup-status.ps1 和
restore-verify.ps1 检查或恢复；自动保留策略不会删除这些旧来源恢复点。

数据与日志
----------
- data\：本包独占的 PostgreSQL 数据目录。
- data\instance.json：当前发布实例身份；不要编辑或复制到其他解压目录。
- data\secrets\：当前 Windows 用户专用的实例密钥；不得发送、截图或提交到 Git。
- logs\：PostgreSQL、算法服务和主程序日志；启动失败时不会删除。
- pids\：本包进程身份记录；不要手工复制到另一个解压目录。
- backups\：经 pg_restore 验证后的自定义格式备份。
- samples\：内置合成示例数据，不代表真实工业数据或准确率。

固定端口为 PostgreSQL 55432、算法服务 8001、主程序 8080。端口被占用时脚本会失败，
不会自动换端口。脚本只管理当前解压目录中的进程和数据；若提示进程身份不一致，请先
人工核对，切勿直接结束未知进程。

本机模式只监听 127.0.0.1，不接受其他电脑访问。需要局域网部署时请使用正式网络部署
方案和 HTTPS 证书，不能把本机端口直接暴露到网络。
