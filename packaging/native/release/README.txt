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

日常操作
--------
- 停止：powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\stop.ps1
- 备份：powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\backup.ps1
- 演示复位：powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\reset-demo.ps1
- 无交互复位：powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\reset-demo.ps1 -Force

数据与日志
----------
- data\：本包独占的 PostgreSQL 数据目录。
- logs\：PostgreSQL、算法服务和主程序日志；启动失败时不会删除。
- pids\：本包进程身份记录；不要手工复制到另一个解压目录。
- backups\：经 pg_restore 验证后的自定义格式备份。
- samples\：内置合成示例数据，不代表真实工业数据或准确率。

固定端口为 PostgreSQL 55432、算法服务 8001、主程序 8080。端口被占用时脚本会失败，
不会自动换端口。脚本只管理当前解压目录中的进程和数据；若提示进程身份不一致，请先
人工核对，切勿直接结束未知进程。
