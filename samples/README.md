# 合成报警样例

本目录全部内容均为 **SYNTHETIC 合成数据**，不包含真实厂名、人名、生产位号或生产数据。生成器版本为 `3.0.0`，默认固定种子为 `20260825`，仅使用 Python 标准库。

## 正式演示短集

业务展示优先使用以下三个文件。它们包含相同的 144 条报警，只是文件格式不同；导入后行数、字段和规范化摘要必须一致。

| 文件 | 用途 | 预期 |
| --- | --- | --- |
| `demo/alarm_demo_utf8.csv` | 常用 CSV 导入 | 可导入 |
| `demo/alarm_demo_utf8.txt` | 制表符 TXT 导入 | 可导入 |
| `demo/alarm_demo.xlsx` | 办公人员常用表格导入 | 可导入 |

短集覆盖报警密集上送、重复记录、信号抖动、短时恢复、持续未恢复、设备故障序列、仪表漂移、工艺扰动、维护测试、正常恢复、包含否定表达的假阳性边界，以及优先级和状态混合。描述同时覆盖中文、英文、短文本和较长文本。场景分类只用于生成和测试，不写入运行时字段，算法不得按文件名、行号、场景名或固定结果表查找答案。

## 兼容与回归样例

| 文件 | 用途 | 预期 |
| --- | --- | --- |
| `smoke/synthetic_smoke_utf8.csv` | 既有 300 行回归基线 | 可导入 |
| `smoke/synthetic_smoke_utf8.txt` | 与回归 CSV 同义的制表符文件 | 可导入 |
| `smoke/synthetic_smoke.xlsx` | 与回归 CSV 同义的 XLSX 文件 | 可导入 |
| `smoke/synthetic_smoke_gb18030.csv` | 12 行 GB18030 中文编码检查 | 可导入 |

## 必须拒绝的非法样例

| 文件 | 错误内容 | 预期 |
| --- | --- | --- |
| `invalid/empty.csv` | 空文件 | 必须拒绝 |
| `invalid/unsupported_format.json` | 不支持的 JSON 文件 | 必须拒绝 |
| `invalid/field_too_long.csv` | 单元格超过 4,096 字符 | 必须拒绝 |
| `invalid/missing_header.csv` | 缺少必填表头 | 必须拒绝 |
| `invalid/required_value_missing.csv` | 必填值为空 | 必须拒绝 |
| `invalid/invalid_enum.csv` | 优先级或状态枚举错误 | 必须拒绝 |
| `invalid/invalid_number.csv` | 数值格式错误 | 必须拒绝 |
| `invalid/invalid_time.csv` | 时间格式或时区错误 | 必须拒绝 |
| `invalid/time_order_invalid.csv` | 确认或恢复时间早于发生时间 | 必须拒绝 |

`expected/*.json` 保存格式、场景计数、固定摘要和非法集清单。清单说明样例应触发的输入边界，不代替后端原子导入集成测试，也不代表真实工业准确率。

## 固定重建与长集

从仓库根目录运行：

```bash
python3 samples/generate_samples.py --dataset committed
python3 samples/generate_samples.py --dataset demo --output /path/to/synthetic_demo_20000.csv
```

第二条命令固定生成 20,000 行长集；大文件不提交。容量观察数据可显式指定行数：

```bash
python3 samples/generate_samples.py \
  --dataset generated \
  --rows 100000 \
  --output /path/to/synthetic_generated_100000.csv
```

不要把运行时大文件写入仓库。摘要只用于验证同一生成器版本和种子的可重复性，不得表述为千万级能力、现场准确率或真实 DCS 测试。
