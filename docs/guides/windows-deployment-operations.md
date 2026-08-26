# 报警管理系统 Windows 部署与运维手册

## 1. 适用范围

本手册面向负责安装、启动、停止、备份和故障处理的部署人员。业务页面操作见 [业务使用手册](business-user-manual.md)。

首选交付方式是 Windows 11 x64 自包含 ZIP。它包含 Java、Python 算法程序、PostgreSQL、Vue 前端和合成样例，不要求目标电脑预装 JDK、Python、Node.js、WSL 或 Docker。

本文只陈述当前真实入口：

- Windows 原生包使用本机回环 HTTP，不允许其他电脑访问；
- 网络部署使用 Docker Compose 与 HTTPS，由具备证书和主机管理职责的部署管理员操作；
- 恢复入口只在隔离 PostgreSQL 中验证恢复点，不覆盖当前业务数据库；
- 当前没有 MSI、Windows 服务、自动更新、原生 `status.ps1` 或网页恢复按钮。

## 2. Windows 原生包先决条件

准备以下条件：

- 64 位 Windows 11；
- 当前用户可写的固定安装目录；
- 安装盘至少 2 GB 可用空间；
- 本机端口 `55432`、`8001`、`8080` 未被其他程序占用；
- 完整的 Windows x64 ZIP 及同名 `.sha256` 文件；
- 能访问本机地址的浏览器。

不要把发布包解压到系统根目录、临时下载目录或会自动同步/清理的目录。推荐由组织确定一个长期固定目录。首次启动生成实例身份后，不要移动整个解压目录；路径和实例身份不一致时，启停、备份和清理会拒绝执行。

原生包不会修改系统信任库、防火墙、机器级环境变量，也不会复用电脑上已有的 PostgreSQL。

## 3. 校验和解压 ZIP

### 3.1 核对 SHA-256

把 ZIP 和同名 `.sha256` 放在同一目录。将下面第一行改为实际 ZIP 路径，然后在 PowerShell 中运行：

```powershell
$releaseZip = 'C:\交付\alert-management-system-windows-x64-实际版本.zip'
$actualHash = (Get-FileHash -LiteralPath $releaseZip -Algorithm SHA256).Hash.ToLowerInvariant()
$expectedHash = ((Get-Content -LiteralPath ($releaseZip + '.sha256') -Raw).Trim() -split '\s+')[0].ToLowerInvariant()
if ($actualHash -ne $expectedHash) { throw "发布 ZIP 的 SHA-256 不匹配，禁止解压和启动。" }
"SHA-256 校验通过：$actualHash"
```

若 `.sha256` 缺失、读取失败或结果不一致，停止操作并重新取得完整交付物。本文不指定下载地址；发布包必须来自组织批准的交付渠道。

### 3.2 完整解压

将目标目录改为组织批准的固定路径：

```powershell
$installParent = 'C:\报警管理系统'
New-Item -ItemType Directory -Path $installParent -Force | Out-Null
Expand-Archive -LiteralPath $releaseZip -DestinationPath $installParent
Get-ChildItem -LiteralPath $installParent
```

ZIP 内含一个发布根目录。进入同时包含 `README.txt`、`scripts`、`app`、`runtime` 和 `samples` 的目录。例如：

```powershell
Set-Location 'C:\报警管理系统\alert-management-system-windows-x64'
```

不要只复制脚本或程序文件，也不要把两个不同版本解压到同一目录相互覆盖。

## 4. 预检

在发布根目录运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\preflight.ps1
```

预检会验证：

- Windows x64；
- 发布清单和关键文件 SHA-256；
- 包内 Java、PostgreSQL 和算法程序；
- 目录可写性和磁盘空间；
- 固定端口可用性；
- `samples` 目录存在且至少包含一个演示样例；具体样例在业务验收中实际读取。

只有看到“预检通过”后才启动。预检失败时按输出的中文修复建议处理，不要编辑 `release-manifest.json`、删除校验项或手工替换运行时来绕过。

端口被占用时，本包不会自动换端口。若同一发布实例此前启动过，先运行 `scripts\stop.ps1`。若无法确认占用进程属于本实例，不要直接结束未知进程，应交由电脑管理员核对。

## 5. 启动、访问与健康状态

### 5.1 启动

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1
```

脚本按 PostgreSQL、算法服务、Java 主系统的顺序启动并等待健康。成功输出应包含：

```text
启动成功：http://127.0.0.1:8080
初始管理员：admin
首次登录临时密码文件：<当前安装目录>\data\secrets\bootstrap-admin-password.txt
PostgreSQL、算法服务和主程序均为 UP
```

浏览器打开：

```text
http://127.0.0.1:8080
```

原生模式三个组件均只监听本机地址：

| 组件 | 地址 |
|---|---|
| PostgreSQL | `127.0.0.1:55432` |
| 算法服务 | `127.0.0.1:8001` |
| Java 主系统与网页 | `127.0.0.1:8080` |

不要把这些端口转发、代理或直接开放到局域网。需要其他电脑访问时使用本文的 Docker/HTTPS 管理员附录。

### 5.2 页面健康状态

登录后的单页工作台包含“系统运行状态”，可展开查看主系统、PostgreSQL 和算法服务。点击“重新检查”会重新请求聚合健康接口。

原生包没有 `status.ps1`，不要在说明、桌面快捷方式或运维脚本中虚构该命令。命令行启动成功输出、页面健康状态和 `logs` 目录是当前真实状态入口。

## 6. 首次管理员与凭据边界

首次启动只在数据库没有账号时创建管理员 `admin`。临时密码位于：

```text
data\secrets\bootstrap-admin-password.txt
```

使用要求：

- 只允许当前授权部署人员读取；
- 不发送、不截图、不写入工单正文或 Git；
- 首次登录后立即修改；
- 不把数据库密码、管理员密码、会话或 CSRF 值写入日志；
- 系统管理员应创建组织需要的实名账号并授予最小项目职责；
- 唯一管理员应安全保存当前密码，并按组织职责保留至少一个可用的管理员账号。

系统没有邮件找回、短信找回、默认后门或通用密码重置命令。普通账号忘记密码由系统管理员在网页“账号与项目权限”中重置；唯一管理员失去全部可用凭据时，不得直接修改数据库，应按组织的数据恢复和授权流程处置。

## 7. 日常停止和重新启动

### 7.1 停止并保留数据

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\stop.ps1
```

成功输出：

```text
当前发布包服务已停止。
```

脚本只停止 PID 记录、可执行路径、命令行、工作目录和当前发布根均匹配的进程。身份不一致时会拒绝误杀。

### 7.2 重新启动

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1
```

正常启停不会删除 PostgreSQL 数据、上传文件或恢复点。不要通过任务管理器结束部分组件后再假定系统已完整停止。

## 8. 备份与完整性检查

所有备份命令从发布根目录运行。备份、恢复验证和实例清理共用实例维护锁；同一时刻只执行一个维护动作。

### 8.1 创建手动恢复点

确保当前发布实例正在运行，然后执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\backup.ps1 -RetentionCount 14
```

成功后 `backups` 目录会增加：

```text
<恢复点>.dump
<恢复点>.dump.meta.json
```

`.dump` 是 PostgreSQL 自定义格式备份；元数据记录来源实例、来源提交、大小和 SHA-256。脚本先用包内 `pg_restore --list` 验证，再原子命名，不覆盖同名文件。

`RetentionCount` 有效范围为 1–365，默认 14。自动保留策略只删除当前实例产生且哈希有效的过期恢复点；外来来源或损坏文件不会被自动删除。

### 8.2 运行完整状态检查

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\backup-status.ps1
```

脚本会计算实际 SHA-256，并列出有效恢复点、总容量、最近成功时间和每个文件状态。退出非零或显示哈希失败时，不得把对应文件用于恢复验证。

需要机器可读结果时可运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\backup-status.ps1 -Json
```

网页“数据与备份”只读元数据和大小，不能代替本命令的完整哈希检查。

### 8.3 配置每日备份

配置当前 Windows 用户每天 02:00 运行、保留 14 个恢复点：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\backup-schedule.ps1 -Action Configure -DailyAt 02:00 -RetentionCount 14
```

查看计划任务：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\backup-schedule.ps1 -Action Status
```

移除计划任务：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\backup-schedule.ps1 -Action Remove
```

计划任务绑定当前 Windows 用户和当前 `instance_id`，只在当前用户登录且该发布实例正在运行时备份。失败记录写入：

```text
logs\scheduled-backup.log
```

重新配置、移除和实例清理都只处理身份匹配的本实例任务。

## 9. 隔离恢复验证

### 9.1 边界

当前支持的入口用于证明恢复点能够恢复，不会覆盖当前业务数据库，也不会把隔离库切换为业务实例。网页没有覆盖恢复按钮。

脚本只接受当前发布根 `backups` 目录中的普通 `.dump` 文件。运行前先执行 `backup-status.ps1`，确认恢复点完整。

### 9.2 执行

把文件名替换为状态检查列出的实际恢复点：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\restore-verify.ps1 -BackupPath backups\实际恢复点.dump
```

脚本会：

1. 核验恢复点和元数据；
2. 使用包内 PostgreSQL 在随机回环端口创建独立临时实例；
3. 用 `pg_restore --exit-on-error` 恢复；
4. 验证 Flyway、关键业务表和关键序列可查询；
5. 把结果写入 `logs\restore-verification-*.json`；
6. 停止并清理带本轮身份标记的临时实例。

成功输出包含：

```text
隔离恢复及关键业务表逐表对账通过
```

该结果证明备份可在隔离目标恢复，不表示当前业务实例已被替换。若数据恢复事件要求切换或覆盖当前数据库，必须停止业务写入并由数据库责任人制定、评审和验证专门恢复方案；当前发布包没有可直接执行的覆盖恢复命令。

## 10. 演示数据复位

网页系统管理员可输入 `RESET_DEMO` 执行复位。部署人员也可在系统运行、管理员已完成首次改密后执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\reset-demo.ps1
```

脚本会要求输入精确确认文本和管理员凭据，只调用受保护的演示复位 API。它不会删除数据库目录、备份或样例。

自动化参数 `-Force` 和 `-PasswordFile` 只供受控验收使用，不作为日常人工操作说明。

## 11. 数据、日志和实例目录

| 路径 | 用途 | 运维要求 |
|---|---|---|
| `data\instance.json` | 当前发布实例身份 | 不编辑、不复制到其他解压目录 |
| `data\postgresql\` | 当前实例数据库 | 不手工替换或删除 |
| `data\secrets\` | 数据库及管理员引导秘密 | 不发送、不截图、不提交 Git |
| `logs\` | PostgreSQL、算法、Java、计划备份和恢复验证日志 | 故障时保留，不公开秘密 |
| `pids\` | 本包进程身份记录 | 不复制到其他实例，不手工伪造 |
| `backups\` | `.dump` 恢复点及元数据 | 成对保留，复制后重新运行状态检查 |
| `samples\` | 合成演示数据 | 不视为真实生产数据 |

## 12. 精确实例清理

### 12.1 默认清理

先保存需要的报告并确认备份状态，再运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\cleanup-instance.ps1
```

脚本显示当前发布目录和完整实例 ID。人工输入相同实例 ID 后，它会：

- 移除当前实例的计划任务；
- 停止身份匹配的服务；
- 清理当前发布根内的 `data`、`logs` 和 `pids`；
- 默认保留 `backups`；
- 保留程序文件，不删除发布根或父目录。

`cleanup-instance.ps1` 的“默认保留”只表示脚本不删除发布根内的 `backups`，不表示随后删除整个解压目录时备份仍会存在。如需保留恢复点，必须先把成对的 `.dump` 与 `.dump.meta.json` 复制到发布根之外的组织受控目录，并保留一份状态检查成功记录；将其放入新安装的 `backups` 后还要重新运行状态检查和隔离恢复验证。确认外部副本有效后，才可手工删除当前精确解压目录。

### 12.2 删除备份

`-RemoveBackups` 会删除当前实例 `backups` 目录中的全部恢复点，属于不可恢复操作。只有在已经取得独立保留副本并获得组织授权时才能使用；业务人员和普通卸载流程不得使用。

不要使用 `-Force` 绕过人工实例身份确认。脚本发现 Junction、符号链接、未知进程、身份不一致或维护锁冲突时会拒绝清理；应调查原因，不应删除校验代码。

## 13. 故障处理

### 13.1 预检失败

| 提示 | 处理 |
|---|---|
| 发布文件缺失或哈希不一致 | 若该目录从未成功启动且没有 `data\instance.json`、业务数据或恢复点，可删除本次不完整解压目录后重新取得 ZIP；否则先由部署人员保全数据和备份，再处理程序文件。 |
| 剩余空间不足 2 GB | 清理目标磁盘或选择新的固定安装目录后重新解压。 |
| 固定端口被占用 | 先尝试停止本实例；无法确认占用者时交由电脑管理员核对，切勿结束未知进程。 |
| 目录不可写 | 改用当前用户明确有写权限的固定目录，不通过关闭校验或修改脚本绕过。 |
| 运行时版本或 PE 目标错误 | 重新取得 Windows x64 正式交付物，不混用其他平台文件。 |

### 13.2 启动失败

启动脚本会停止本次已经成功启动的受管组件，并把日志留在 `logs`。按发生时间检查：

```text
logs\postgresql-*.err.log
logs\algorithm-*.err.log
logs\backend-*.err.log
```

记录完整中文错误、发生时间、发布根路径和 `release-manifest.json` 中的 `source_commit`，再交由维护人员处理。不得把密码文件或完整业务上传文件附在故障报告中。

### 13.3 网页或业务失败

- 页面打不开：确认启动成功和地址为 `http://127.0.0.1:8080`。
- 页面健康为 DOWN：保留页面提示和对应组件日志，正常停止后重新启动。
- 登录锁定：等待 15 分钟或由其他系统管理员重置临时密码。
- 导入失败：使用页面源行、字段和原因修正文件，不直接修改数据库。
- 分析失败：确认算法服务健康后，在页面对失败批次执行“重试分析”。
- 报告失败：保留当前分析，确认主系统后重试，不接受空文件。
- 备份状态失败：隔离损坏恢复点并调查，不用于恢复验证，也不通过删除元数据伪装正常。
- 停止提示身份不一致：不要使用任务管理器误杀，核对发布路径、PID 记录和实例身份。

## 14. Docker Compose 本机模式管理员附录

Docker 是次级交付方式，不属于 Windows 自包含原生包。以下命令要求在仓库源码根目录的 Bash 环境运行，并已安装 Docker Desktop 或 Docker Engine、Docker Compose 2.24.4 或更高版本、Bash 和 OpenSSL；该 Compose 最低版本用于支持 HTTPS 覆盖文件中的 `!override` 合并语义。

### 14.1 准备本机秘密并启动

```bash
scripts/security/prepare-local-secrets.sh
docker compose --file compose.yaml --project-name alert-management-m7 up --build --detach --wait
```

脚本输出管理员 `admin` 的首次登录密码文件路径。浏览器访问：

```text
http://127.0.0.1:8080
```

本机 Compose 只向宿主回环地址发布 Java 8080；PostgreSQL 和算法服务不发布宿主端口。

### 14.2 停止并保留数据卷

```bash
docker compose --file compose.yaml --project-name alert-management-m7 down --remove-orphans
```

不要使用全局容器、镜像或卷清理命令。`down --volumes` 会删除该 Compose 项目的 PostgreSQL 数据卷，不属于日常停止命令。

## 15. Docker Compose HTTPS 网络模式管理员附录

### 15.1 前提和秘密文件

网络模式只供负责证书、DNS、主机端口和数据备份的部署管理员使用。启动前必须取得组织批准且与访问主机名匹配的 PKCS#12 证书、密码文件，并准备以下受限目录：

```text
<APP_SECRETS_DIR>/database-password.txt
<APP_SECRETS_DIR>/bootstrap-admin-password.txt
<APP_SECRETS_DIR>/tls-keystore.p12
<APP_SECRETS_DIR>/tls-keystore-password.txt
```

基础数据库和管理员秘密可由仓库脚本生成；TLS 文件必须由组织的证书责任人提供，不能用验收自签名证书冒充正式证书：

```bash
export APP_SECRETS_DIR=/受限绝对路径/alert-management-secrets
scripts/security/prepare-local-secrets.sh
```

把批准的 `tls-keystore.p12` 和只含密码的 `tls-keystore-password.txt` 放入同一受限目录后，再启动。

### 15.2 启动 HTTPS 网络模式

将绑定地址设置为经批准的服务器地址；先把下面第二行改为实际地址，未设置时配置会绑定所有接口：

```bash
export APP_SECRETS_DIR=/受限绝对路径/alert-management-secrets
export NETWORK_BIND_ADDRESS=经批准的服务器IP
docker compose \
  --file compose.yaml \
  --file compose.network.yaml \
  --project-name alert-management-network \
  up --build --detach --wait
```

网络模式只发布 HTTPS 8443。浏览器入口使用证书中批准的真实主机名：

```text
https://经批准的主机名:8443
```

不要改用 `http://`，不要同时开放 8080，也不要发布 PostgreSQL 5432 或算法 8001。缺少 TLS、数据库或管理员秘密时，启动必须失败，不能回退到明文模式。

可在系统信任组织证书的管理终端检查健康接口：

```bash
export APP_HOST=经批准的主机名
curl --fail --silent --show-error "https://${APP_HOST}:8443/api/v1/health"
```

### 15.3 停止并保留数据卷

```bash
export APP_SECRETS_DIR=/受限绝对路径/alert-management-secrets
docker compose \
  --file compose.yaml \
  --file compose.network.yaml \
  --project-name alert-management-network \
  down --remove-orphans
```

### 15.4 网络部署备份边界

Windows 原生包的 `backup.ps1`、`backup-status.ps1` 和 `restore-verify.ps1` 不适用于 Compose 数据卷。当前 Compose 配置把备份管理标记为“由部署环境管理”，仓库没有可供业务人员运行的网络部署覆盖恢复入口。

在把网络模式用于持续业务前，部署责任人必须另行冻结并验收与该环境匹配的 PostgreSQL 备份、保留、完整性校验和恢复切换方案。不得把开发脚本、删除数据卷或原生包脚本描述为网络生产恢复方案。

## 16. 运维边界

- 不把本机原生 HTTP 端口暴露到局域网。
- 不记录或传播数据库密码、管理员密码、会话、CSRF、证书私钥或完整上传数据。
- 不直接编辑 PostgreSQL 表替代页面业务操作。
- 不宣称隔离恢复验证已经覆盖当前正式数据库。
- 不把短时演示结果外推为 7×24 小时、任意硬件性能或异地容灾承诺。
- 不承诺任意浏览器、Office/WPS 或 Windows Server 兼容性；以绑定发布候选的实际验收记录为准。
- 网络正式启用、证书信任、数据恢复切换和不可恢复清理均必须由对应责任人批准。
