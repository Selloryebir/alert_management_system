# 合成报警样例

本目录全部内容均为 **SYNTHETIC 合成数据**，不包含真实厂名、人名、生产位号或生产数据。生成器版本为 `2.0.0`，默认固定种子为 `20260825`，仅使用 Python 标准库。v2 数据保持既有随机数流，只修正时间和关系字段，使重复、抖动及跨 episode 关联场景符合 hybrid-v2 数学定义。

## 已提交数据

- `smoke/synthetic_smoke_utf8.csv`：UTF-8 BOM，300 条。
- `smoke/synthetic_smoke_utf8.txt`：UTF-8、制表符分隔，与 CSV 同逻辑。
- `smoke/synthetic_smoke.xlsx`：与 CSV 同逻辑；首个工作表是隐藏合成元数据，首个可见工作表是 300 条报警。
- `smoke/synthetic_smoke_gb18030.csv`：GB18030 编码的 12 条中文导入小样例。
- `invalid/*.csv`：六类可达输入错误，共 42 条数据行；除缺表头为文件级失败外，其余每行恰有一个明确错误。
- `expected/*.json`：格式、场景计数和确定性摘要；无效集清单精确列出文件级错误及每行的 `source_row`、字段和错误码，不代表真实工业准确率。

无效集清单中的 `valid_rows` 表示通过文件级与逐行校验、可进入 `READY` 的数据行数。`missing_header.csv` 在文件级即阻断，因此不继续制造逐行派生错误，`valid_rows` 为 0。

场景覆盖报警洪泛、重复、抖动、短时恢复、长期未恢复、仪表漂移、设备跳停序列、工艺扰动级联和维护测试。位号、地点、操作员和描述均带有 `SYNTHETIC` 标识。

## 固定重建

从仓库根目录运行：

```bash
python3 samples/generate_samples.py --dataset committed
python3 samples/generate_samples.py --dataset demo --output /path/to/synthetic_demo_20000.csv
```

第二条命令固定生成 20,000 行 Demo CSV；大文件不提交。容量观察数据可显式指定行数：

```bash
python3 samples/generate_samples.py \
  --dataset generated \
  --rows 100000 \
  --output /path/to/synthetic_generated_100000.csv
```

不要把运行时大文件写入仓库。摘要只用于验证同一生成器版本和种子的可重复性，不得表述为千万级能力、现场准确率或真实 DCS 测试。
