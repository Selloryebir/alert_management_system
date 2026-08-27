# 正式交付物生成器

本工具以登记的九份 Markdown 为唯一正文事实源，直接生成 DOCX 和 PDF；不调用 Microsoft Office、WPS、LibreOffice、浏览器、Pandoc 或 LaTeX。

## 准备环境

生成链固定支持 Python 3.12 的 Windows x64 或 Linux/WSL x64 独立虚拟环境：

```bash
python3 -m venv .runtime/deliverables-venv
. .runtime/deliverables-venv/bin/activate
python3 -m pip install --require-hashes --only-binary=:all: -r tools/deliverables/requirements.lock
```

如果 WSL 提示缺少 `venv` 或 `pip`，先安装对应发行版的 `python3-venv` 和 `python3-pip` 软件包。Windows 原生 PowerShell 可用 `py -3.12 -m venv .runtime\deliverables-venv` 创建环境。`.runtime/` 已由仓库忽略，不会污染正式发布构建的干净工作树判断。

## 构建与验收

更新 Markdown 后生成交付物：

```bash
python3 tools/deliverables/build.py
```

验收仓库内产物与当前事实源完全一致：

```bash
python3 tools/deliverables/build.py --check
```

`--check` 只在临时目录生成两次并比较，不修改仓库。它同时验证输出确定性、DOCX/PDF 结构、中文核心正文、来源覆盖编号和当前正式声明边界。

生成的 DOCX/PDF 证明标准文件结构可解析、中文可提取；特定 Microsoft Office 或 WPS 版本的视觉兼容仍需在指定软件和版本上人工确认。
