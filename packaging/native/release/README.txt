报警管理系统（Windows 11 x64）
==============================

本目录是自包含运行包。请完整解压后运行，不要只复制其中某个脚本或程序。
无需预装 JDK、Python、Node.js、WSL 或 Docker。

首次启动
--------
1. 右键打开 PowerShell，切换到本目录。
2. 执行：powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\preflight.ps1
3. 预检通过后执行：powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1
4. 浏览器访问：http://127.0.0.1:8080
5. 使用管理员账号 admin 登录；首次临时密码位于 data\secrets\bootstrap-admin-password.txt。
6. 首次登录后按页面提示立即修改密码。

日常操作
--------
- 停止：powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\stop.ps1
- 备份：powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\backup.ps1
- 演示复位：powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\reset-demo.ps1（会安全提示输入管理员凭据）
- 自动化复位：scripts\reset-demo.ps1 -Force -Username admin -PasswordFile <受限访问的当前密码文件>

数据与日志
----------
- data\：本包独占的 PostgreSQL 数据目录。
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
