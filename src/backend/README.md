# Java 后端

本模块是报警管理系统灾后重建 Demo 的 Java 主系统骨架，使用 Java 21、Spring Boot 3.5.16、PostgreSQL JDBC 和 Flyway。当前只提供工程启动、数据库迁移和聚合健康接口，不包含业务功能。

## 构建与测试

仓库自带固定 Maven 3.9.16 的 Wrapper，不依赖系统安装 Maven。在仓库根目录执行：

```powershell
.\mvnw.cmd -f src/backend/pom.xml test
.\mvnw.cmd -f src/backend/pom.xml package
```

Linux 或 WSL 使用 `./mvnw`。构建时如果 `src/frontend/dist/` 存在，其内容会复制到 JAR 的 `static/`；该生成目录不提交 Git。目录不存在时仍可独立构建后端。

## 运行

运行前需要可连接的 PostgreSQL 17 实例和目标数据库。默认连接为 `127.0.0.1:5432/alert_management`，首次启动由 Flyway 创建 `app_metadata` 迁移验证表。

```powershell
.\mvnw.cmd -f src/backend/pom.xml spring-boot:run
```

默认监听 `http://127.0.0.1:8080`，聚合健康接口为：

```text
GET http://127.0.0.1:8080/api/v1/health
```

响应固定包含 `system`、`database`、`algorithm` 三项。依赖运行中断时接口仍返回 HTTP 200，总状态为 `DEGRADED`，对应组件为 `DOWN` 并附简短说明。数据库在启动迁移阶段不可用或迁移非法时，应用会直接启动失败，避免把迁移失败隐藏成可用状态。

## 环境变量

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `SERVER_PORT` | `8080` | HTTP 端口 |
| `DB_URL` | `jdbc:postgresql://127.0.0.1:5432/alert_management` | JDBC 地址 |
| `DB_USERNAME` | `alert_management` | 数据库用户 |
| `DB_PASSWORD` | `alert_management` | 数据库口令 |
| `DB_CONNECTION_TIMEOUT_MS` | `1000` | 数据库连接超时毫秒数 |
| `ALGORITHM_HEALTH_URL` | `http://127.0.0.1:8001/health` | Python 健康地址 |
| `ALGORITHM_CONNECT_TIMEOUT` | `500ms` | 算法连接超时 |
| `ALGORITHM_REQUEST_TIMEOUT` | `1s` | 算法请求总超时 |
| `ALGORITHM_SERVICE` | `algorithm-service` | 预期算法服务身份 |
| `ALGORITHM_VERSION` | `0.1.0` | 预期算法服务版本 |
| `ALGORITHM_CONTRACT_VERSION` | `v1` | 预期算法健康契约版本 |
| `APP_SERVICE_NAME` | `alert-management-backend` | 健康响应服务名 |
| `APP_VERSION` | `0.1.0` | 健康响应版本 |
| `APP_IDENTITY` | `2026 年灾后重建 Demo` | 审核身份标识 |

算法服务默认监听 `127.0.0.1:8001`。其 `/health` 必须返回 2xx，且 `status`、`service`、`version`、`contract_version` 与配置完全匹配，否则算法组件状态为 `DOWN`。
