# M11 API 黑盒验收客户端

`security_smoke.py` 只通过公开 HTTP API 检查身份、授权和输入边界，不读取数据库、
不扫描源码，也不负责启动服务或配置 TLS。调用方必须提供已启动的完整系统、首次管理
员密码文件和独立证据目录：

```bash
python3 tests/m11/security_smoke.py \
  --base-url http://127.0.0.1:8080 \
  --bootstrap-username admin \
  --bootstrap-password-file /path/to/bootstrap-admin-password.txt \
  --output-dir /path/to/evidence
```

验收会创建带随机后缀的项目和账号，并真实覆盖：

- 匿名 401、CSRF 403、首次改密、五次失败锁定；
- `SYSTEM_ADMIN`、`MANAGER`、`ANALYST` 三种职责和跨项目 UUID；
- 改密、退出、重置密码、停用账号导致的会话失效；
- 伪造动作操作者被忽略、源操作员仍作为报警事实保留；
- SQL、XSS、路径型文本，以及 JSON、查询、上传、行列、工作表、单元格、映射和修正上限；
- 1001 行修正和 1001 个校验错误分别命中独立上限，错误上限先于在线修正门禁；
- 每项拒绝后的中文 JSON 错误、零越权写入和零半批次。

脚本不会把密码、Cookie 或 CSRF token 写入证据。网络模式 HTTPS、端口暴露和进程
启动拒绝由部署验收脚本负责，避免在 API 客户端内复制进程控制。

解析 30 秒超时和有界解析队列繁忙无法在共享黑盒环境中稳定、快速地构造；这两项由
后端定向测试注入可控时钟/执行器来验证。黑盒脚本不使用睡眠或依赖机器负载的伪压力
断言，避免偶发通过掩盖资源边界缺陷。
