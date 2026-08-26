# 身份、授权与安全部署契约（M11）

本文件冻结 PRD-033 的共享产品和接口含义。M11 在现有 Java 单体、Vue 单页应用、Python 纯计算服务和 PostgreSQL 事实源内补齐可达安全边界，不建设通用身份平台。

## 身份与角色

系统使用同源有状态会话。所有业务接口均须登录；匿名只允许静态登录页、`GET /api/v1/health`、CSRF 初始化和登录接口。

- `SYSTEM_ADMIN` 是账号的全局角色，可管理账号并访问全部项目。
- 非系统管理员账号的全局角色为 `NONE`。
- `MANAGER`、`ANALYST` 是项目成员关系中的角色，同一账号可在不同项目承担不同职责。
- `MANAGER` 可管理所属项目的资料、生命周期和成员，并执行该项目全部业务操作。
- `ANALYST` 可读取所属项目并执行导入、分析、分类复核、处置和报告，不可管理项目资料、成员、账号、全局审计或演示复位。

创建项目仅限系统管理员，创建者自动成为项目负责人。不得移除项目最后一个有效负责人，也不得停用最后一个有效系统管理员。列表查询必须在 SQL 中按成员关系过滤；通过已知批次、运行或记录 UUID 跨项目访问时返回 404，已确认项目归属但角色不足时返回 403。

## 账号、密码与会话

账号名使用规范化小写 ASCII `[a-z0-9._-]{3,50}`，展示名为 1–100 个字符。密码使用 BCrypt cost 12，长度 12–64 个字符且 UTF-8 不超过 72 字节，不得等于账号名或当前密码。

- 启动时仅在数据库没有账号的情况下，从部署秘密文件建立首个系统管理员。
- 临时密码首次登录后必须修改；改密前只允许读取当前身份、取得 CSRF、修改密码和退出。
- 连续 5 次失败锁定 15 分钟；未知账号执行等价摘要校验并返回相同中文错误。
- 会话空闲 30 分钟；Cookie 为 `HttpOnly`、`SameSite=Lax`，网络 HTTPS 模式同时为 `Secure`。
- 停用账号、管理员重置密码或用户改密后增加凭据版本，已有会话立即失效。
- 所有修改请求启用 CSRF。401/403 返回统一 JSON 错误，不重定向到 HTML。

最小身份接口：

- `GET /api/v1/auth/csrf`
- `POST /api/v1/auth/login`、`POST /api/v1/auth/logout`
- `GET /api/v1/auth/me`、`POST /api/v1/auth/password`
- `GET/POST /api/v1/admin/users`
- `PATCH /api/v1/admin/users/{userId}`
- `POST /api/v1/admin/users/{userId}/reset-password`
- `GET /api/v1/projects/{projectId}/members`
- `PUT/DELETE /api/v1/projects/{projectId}/members/{userId}`

共享 JSON 使用 snake_case：CSRF 响应为 `token`、`header_name`、`parameter_name`；登录请求为 `username`、`password`；当前身份响应为 `user_id`、`username`、`display_name`、`global_role`、`must_change_password`。改密请求为 `current_password`、`new_password`。账号视图另含 `status`、`locked_until`、`created_at`；项目成员视图另含 `project_role`。项目列表和详情为当前用户增加 `project_role`，系统管理员固定返回 `SYSTEM_ADMIN`。密码和摘要永不出现在响应中。

不提供自助注册、邮件找回、OAuth/OIDC、JWT、API key、MFA、LDAP、Redis 会话或自定义角色。

## 项目授权与真实操作者

`projectId` 直接校验项目成员；`batchId` 通过 `import_batch.project_id` 反查；`runId` 通过 `analysis_run → import_batch` 反查；`recordId` 必须同时证明属于已授权的运行或项目。项目列表、批次列表和审计列表不得先读取全量后在 Java 内过滤。

业务审计、项目管理、导入、分析、分类修订、处置、报告、人工报警修订/作废和演示复位的操作者一律来自当前登录会话。为兼容既有 v1 请求，`operator`、`edited_by` 等动作身份字段可以暂时被反序列化，但服务端必须忽略；前端不再显示这些输入框。

报警文件中的 `operator` 和人工报警中的源操作员是报警事实字段，继续保存和展示为“源操作员”，不得误当成登录身份。责任人必须是该项目的有效成员；历史自由文本责任人继续只读展示。

审计事件增加可空 `actor_user_id` 和 `project_id`。系统管理员可查询全局或指定项目审计，项目负责人只能查询所属项目审计，分析人员不能读取审计。登录失败没有认证主体，详情只记录规范化尝试账号，不记录密码、会话、CSRF 或完整上传数据。

## 部署模式与传输

同一 Java 应用支持三种明确模式，不能静默降级：

| 模式 | 对外边界 | 允许传输 |
|---|---|---|
| `LOCAL_NATIVE` | Java、Python、PostgreSQL 仅绑定 `127.0.0.1` | 本机回环 HTTP |
| `LOCAL_CONTAINER` | 容器内 Java 可监听容器接口，宿主只发布 `127.0.0.1:8080` | 本机回环 HTTP |
| `NETWORK` | Java 只发布 HTTPS，默认 `8443`；数据库和算法端口不发布 | TLS |

网络模式缺少证书、私钥、数据库秘密或首个管理员秘密时拒绝启动；不同时开放明文 8080。TLS 由 Spring Boot 直接承担，不新增网关。验收证书在临时目录生成，不提交证书、私钥、密码或 `.env`。

Windows 原生包只支持 `LOCAL_NATIVE`。启动脚本强制 `SERVER_ADDRESS=127.0.0.1`，首次生成实例专用数据库密码与首个管理员临时密码文件，只显示账号和文件路径，不把密码写入日志、ZIP 或 manifest；脚本不修改系统信任库、防火墙或机器级环境变量。

## 输入和资源边界

以下上限是 M11 支持范围，不是性能承诺：

| 对象 | 上限 |
|---|---:|
| multipart 请求 | 52 MiB |
| 单个上传文件 | 50 MiB |
| 数据行 | 100,000 |
| 列 | 256 |
| XLSX 工作表 | 8 |
| 单元格文本 | 4,096 字符 |
| 表头文本 | 120 字符 |
| 字段映射 JSON | 32 KiB |
| 行修正 JSON | 1 MiB、最多 1,000 行 |
| 普通 JSON 请求体 | 1 MiB |
| 查询字符串 | 2 KiB |
| 解析时间 | 30 秒 |

CSV 必须迭代读取并在第 100,001 行立即失败，不使用 `getRecords()`。XLSX 在打开前设置 POI 压缩比和单条目展开限制，并在遍历中提前检查工作表、稀疏行列和单元格。解析使用有界执行器；超时、中断、队列繁忙、畸形文件和超限均返回稳定中文 4xx/429，且不留下批次、暂存行或报警记录。

最多保存 1,000 个校验错误；超过 200 个可修正错误行时要求离线修正，不返回看似完整的截断源数据。文件名不参与磁盘路径，XLSX 公式只作为文本读取，不执行。筛选值、账号和业务文本继续使用参数化 SQL 与枚举白名单。

## 浏览器行为与响应头

前端统一通过一个同源请求层携带会话和 CSRF。401 清空内存会话并提示重新登录；403 保留当前页面并显示中文无权限原因。角色只控制页面可见性和可用性，后端仍逐请求授权。

页面继续使用 Vue 文本绑定，不引入 HTML 注入能力；Java 返回 CSP、`X-Content-Type-Options: nosniff`、frame 限制和收窄的 Referrer-Policy。报告下载固定内容类型和服务端文件名。

## 明确非目标

M11 不引入通用 RBAC/ABAC、组织树、SSO、互联网多租户、PostgreSQL RLS、网关、WAF、服务网格、Kubernetes、主机防火墙修改、区块链审计或合规认证声明。M12 再验证备份恢复、长时间资源、依赖质量和安全卸载，不在 M11 提前扩展。
