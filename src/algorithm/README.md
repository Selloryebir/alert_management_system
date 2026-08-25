# Python 算法服务

该组件是报警管理系统灾后重建 Demo 的纯计算服务骨架。M1 只提供进程健康检查，不连接 PostgreSQL，不读取业务数据，也不提供伪造的分析结果。`POST /api/v1/analyze` 将在 M3 按公共数据契约实现，在此之前请求会明确返回 404。

## 环境

- Python 3.12
- 默认监听 `127.0.0.1:8001`
- 健康端点：`GET /health`

依赖版本以 `requirements.lock` 为准。该锁文件同时包含最小运行依赖和健康检查测试依赖，减少阶段骨架中的依赖入口数量。

## 安装与测试

在本目录执行：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock
.venv\Scripts\python.exe -m pytest
```

WSL/Linux 使用后续统一提供的 Python 3.12 环境：

```bash
python3.12 -m pip install -r requirements.lock
python3.12 -m pytest
```

## 启动

```powershell
.venv\Scripts\python.exe -m algorithm_service
```

```bash
python3.12 -m algorithm_service
```

启动成功后，`GET http://127.0.0.1:8001/health` 返回：

```json
{
  "status": "UP",
  "service": "algorithm-service",
  "version": "0.1.0",
  "contract_version": "v1"
}
```

## 配置

| 环境变量 | 默认值 | 约束 |
|---|---|---|
| `ALGORITHM_HOST` | `127.0.0.1` | 非空；Docker 阶段可明确设置为容器监听地址 |
| `ALGORITHM_PORT` | `8001` | 1–65535 的整数 |

配置在启动 Uvicorn 前校验。错误配置会向标准错误输出中文原因并以退出码 2 结束，不会启动一个假健康服务。

## 当前限制

- 只有 `/health`，没有算法分析、数据库访问、任务状态或报告能力。
- 未获得真实工业数据，本组件不声明准确率、性能或行业合规性。
- Windows 自包含可执行文件由 M6 工程交付阶段基于该入口构建，本阶段不提交二进制运行时。
