# 历史材料提取工具

本工具将 `docs/backgrounds` 中的四份只读历史材料提取为 `docs/sources` 下的可审阅内容。它不会移动或改写原始文件，也不会把历史材料中的测试结论视为当前实现已经通过的事实。

## 使用方法

在仓库根目录执行：

```bash
npm --prefix tools/document-extraction ci
npm --prefix tools/document-extraction run extract
npm --prefix tools/document-extraction run check
```

`extract` 会重新生成受管输出；`check` 在临时目录中重新提取并与当前输出逐文件比较，用于发现遗漏或不可重复结果。

## 固定提取边界

- PDF 第 1–30 页、52–64 页：提取文本，按页保留来源位置。
- PDF 第 31–51 页：生成用户手册 Markdown，并保存页面图片。
- PDF 第 65 页：只登记为软著参考材料，不作为实现要求或验收证据。
- PDF 第 66–131 页：占位代码，明确排除且不生成页面图片。
- 混合 DOCX：仅提取 RD1 报警管理系统部分；RD2、RD4、RD5 明确排除。
- 两份 RD3 DOCX：只生成元数据、摘要和不相关标记，不提取正文作为产品输入。

## 维护约束

- 源文件名、页码或章节范围及排除理由必须写入 manifest。
- 任何对提取边界的修改都应先人工审阅源材料并更新本 README。
- `docs/sources` 是历史来源层，不是当前产品事实层；开发需求应以经筛选的产品与架构文档为准。
