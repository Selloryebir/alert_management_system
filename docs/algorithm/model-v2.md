# Hybrid-v2 报警分析模型卡与实施契约

## 1. 文档目的与状态

本文定义并记录报警分析算法 `hybrid-v2` 的现行实施基线，覆盖模型边界、数学定义、监督补充分支、参数、解释义务、泛化验收、性能目标和现场校准门槛。任何版本只有在代码、模型、测试和同一候选提交的验收结果一致后才可发布。

建议版本标识如下：

| 项目 | 版本 | 说明 |
| --- | --- | --- |
| HTTP JSON 契约 | `v2` | 新增 7 个必填参数且链窗口语义变化；内部端点为 `POST /api/v2/analyze` |
| 算法服务 | `0.2.0` | 表示算法行为发生兼容性可见的升级 |
| 规则/模型 | `hybrid-v2.0.0` | 专家时序规则、Markov 关联和可弃权原因分类的组合版本 |
| 监督模型 | `cause-hybrid-svm-adaboost-v2` | LinearSVC 文本分支与 AdaBoost 结构分支的只读模型制品 |

本模型只提供离线报警数据的降噪标签、原因类别建议和关联事件链。它不连接业务数据库，不修改报警或处置状态，不向工业控制系统写入，不执行自动抑制，也不把统计关联描述为已确认根因。

## 2. 当前实现判定

`hybrid-v2.0.0` 的现行实现包含四部分：规则强度随时间距离或状态转换证据计算；专家分支按非负权重、分数门槛和类别间距给出可弃权原因建议；关联链由同一关系范围内跨 episode 的一阶 Markov 统计发现；当专家分支弃权时，LinearSVC 文本分支和 AdaBoost 结构分支可在结论一致且通过冻结门槛后补充原因类别。运行时代码不读取合成场景名、固定步骤、固定 UUID、源行区间或预期结果文件，Python 保持纯计算，Java 保持最终写权限。

监督分支使用项目自建工程场景离线训练，并按互不重叠的 `group_id` 固定划分训练、验证和测试。固定样例、独立测试和边界样例只证明实现符合已声明工程场景并具备保守弃权能力；未见数据与变形黑盒证明算法不依赖演示标识。这些结果不能代替获授权现场标注集上的独立准确率评估、因果结论或长期现场性能。

## 3. 输入、输出与共同约束

### 3.1 输入

输入继续使用规范化单批报警记录。每条记录至少提供：

- 标识与追溯字段：`record_id`、`batch_id`、`source_row`；
- 时间字段：`event_time`、可选 `return_time`、可选 `ack_time`；
- 关系字段：`site`、`area`、可选 `unit`、`tag`；
- 语义与状态字段：`description`、`priority`、`state`；
- 可选数值字段：`value`、`threshold`、`engineering_unit`；
- 来源字段：`source_system`、可选 `operator`、`raw_payload`。

一次请求仍只能包含一个批次，记录 ID 不得重复，时间必须带时区。模型不得使用 `record_id`、`source_row` 或输入顺序作为业务特征；这些字段仅用于稳定排序、关联返回和追溯。

### 3.2 确定性排序

所有时序计算先使用以下键稳定排序：

```text
(event_time, source_row, record_id)
```

`source_row` 和 `record_id` 只解决时间完全相同时的确定性顺序，不得影响分类分数。除同一时间戳的真实先后无法判定这一固有限制外，打乱请求数组不得改变规范化结果。

### 3.3 输出语义

- `noise_type`：主要降噪/状态类型，仍为 `NORMAL`、`DUPLICATE`、`CHATTER`、`SHORT_LIVED`、`PERSISTENT`。
- `alarm_class`：由经审查的专家策略从主要类型映射，仍为 `NUISANCE`、`ACTIONABLE`、`STANDARD`。
- `cause_category`：可弃权的原因类别建议；证据不足或冲突时必须返回 `UNKNOWN`。
- `score`：所选主要 `noise_type` 的规则强度，范围 `[0, 1]`。它不是发生概率、分类准确率或安全风险概率。
- `evidence`：包含规则标识、输入特征值、阈值、计算结果和限制说明。
- `event_chains`：统计关联事件链；只能称为关联、传播候选或候选起始报警，不得称为已确认根因。

## 4. Hybrid-v2 模型结构

Hybrid-v2 由四个互补部分构成：

1. 数学化专家时序规则识别重复、抖动、短时恢复和持续报警；
2. 一阶 Markov 转移图从本批次的重复 episode 中发现位号间关联；
3. 可弃权专家分类器根据经审查的语义、时序和数值特征给出原因类别建议；
4. 仅在专家弃权时，由 LinearSVC 文本分支和 AdaBoost 结构分支按双分支一致、冻结门槛及语义门禁补充原因类别。

四个部分都必须只读取请求和冻结参数。服务不得在线修改模型文件、保留跨请求隐式状态或访问 PostgreSQL。

## 5. 专家时序规则

### 5.1 分组与记号

对记录 `i` 定义：

```text
g_i = (site_i, area_i, unit_i, tag_i)
t_i = event_time_i
d_i = return_time_i - event_time_i（return_time 存在时）
b_i = 0（state 为 RETURNED），否则为 1
```

`b_i` 表示报警/非报警二值状态。`ACKNOWLEDGED` 仍处于报警侧，不能仅因它与 `ACTIVE` 字符串不同就构成一次抖动转换。

### 5.2 重复报警

若记录 `i` 与同组记录 `j` 的核心字段完全相同，且

```text
0 < |t_i - t_j| <= W_d
```

则二者命中 `DUPLICATE`。核心字段为：

```text
(description, priority, state, value, threshold)
```

对记录 `i` 取时间距离最小的匹配记录 `j*`，规则强度为：

```text
mu_duplicate(i) = exp(-|t_i - t_j*| / W_d)
```

说明中必须记录匹配记录的源行、实际时间差、窗口和完全相同的核心字段集合。该规则不声称两条记录在业务上必然冗余；厂商事件语义未知时仍应保留原始记录。

### 5.3 抖动报警

在同组记录的任一长度为 `W_c` 的滑动窗口中，设记录数为 `N`，相邻二值状态变化次数为：

```text
T = sum(1[b_k != b_(k+1)])
A = T / (N - 1)    （N >= 2）
```

当且仅当以下条件同时成立时，窗口内记录命中 `CHATTER`：

```text
N >= K
A >= rho
```

为兼顾完整窗口判定与大文件可达性，对每个窗口右端点先选择满足门槛的最早起点，形成覆盖范围最大的确定性候选；对记录 `i`，规则强度取覆盖它的候选中最大转换比：

```text
Q_r = 以 r 为右端点、满足 N >= K 且 A >= rho 的窗口中起点最早者
mu_chatter(i) = max(A(Q_r)), i 属于 Q_r
```

实现使用转换前缀和与区间最小值查询寻找最早起点，避免对同一位号的全部窗口做平方级枚举。该强度仍只表示所选规则证据，不是抖动概率。

这是一项对 M8 初始模型卡 `mu=max(全部命中窗口的 A)` 的显式更正。最早合格窗口与全部合格窗口具有相同的记录命中集合，但强度只在确定性候选集合中取最大值；这样避免为逐记录最大密度区间引入未经证明的复杂算法或平方级枚举。反例测试固定了二值状态 `[1,1,1,0,1,0,1]` 在 `K=4、rho=0.8` 时末条强度为 `0.8`，而不是内部更短窗口的 `1.0`。该定义使用报警/非报警转换而不是任意状态字符串变化，避免把确认动作误判为抖动。

### 5.4 短时恢复

若 `return_time` 存在且：

```text
0 <= d_i <= W_s
```

则记录命中 `SHORT_LIVED`，强度为：

```text
mu_short(i) = exp(-d_i / W_s)
```

没有恢复时间时不得推断为短时报警。

### 5.5 持续报警

以下条件同时成立时命中 `PERSISTENT`：

```text
state = ACTIVE
priority = P1
return_time 为空
ack_time 非空，或 persistent_requires_ack = false
```

该规则是审核阶段的显式专家策略，不是从数据训练得到的概率模型。命中强度为 `1`，说明中必须逐项列出条件值。

### 5.6 多规则命中与主要类型

为保持现有业务字段兼容，主要类型优先级保持：

```text
DUPLICATE > CHATTER > SHORT_LIVED > PERSISTENT > NORMAL
```

`score` 使用所选主要类型的 `mu`；未命中任何异常规则时 `noise_type=NORMAL` 且 `score=1`，含义是“完整通过当前规则集”，不是正常状态概率。其他同时命中的规则及其强度必须保留在 `evidence`，不得静默丢弃。

## 6. 一阶 Markov 关联事件链

### 6.1 Episode 划分

先按关系键：

```text
r_i = (site_i, area_i, unit_i)
```

分组并排序。相邻记录时间差大于 `episode_gap_seconds` 时开始新的 episode。一个 episode 只是用于统计的连续报警片段，不自动等同于 ISA 定义下的 alarm flood，也不代表一次已确认事故。

重复记录可保留在逐记录结果中，但在建立转移图前，每个 episode 内连续相同 tag 只保留一次，避免重复报警人为放大自转移或邻接支持度。

### 6.2 转移统计

设所有 episode 中出现的 tag 集合为 `V`。只统计每个 episode 内相邻 tag 的一阶有向转移：

```text
C_uv = 所有 episode 中相邻转移 u -> v 的总次数
C_u  = sum_v(C_uv)
C_.v = sum_u(C_uv)
C    = sum_u sum_v(C_uv)
E_uv = 至少包含一次 u -> v 的不同 episode 数
```

当分母非零时，最大似然转移概率、目标基线概率和提升度定义为：

```text
P(v | u) = C_uv / C_u
P(v)     = C_.v / C
lift(u, v) = P(v | u) / P(v)
```

若分母为零，则相应边不存在，不用常数或文本规则兜底。

### 6.3 有效边与链提取

边 `u -> v` 只有同时满足以下条件才有效：

```text
E_uv >= min_episode_support
P(v | u) >= min_transition_probability
lift(u, v) >= min_lift
median_lag(u, v) <= chain_window_seconds
```

其中 `median_lag` 是所有被计数相邻转移的时间差中位数。随后在每个真实 episode 内，仅沿有效相邻边提取最大有序路径；路径成员数达到 `chain_min_steps` 才输出事件链。成员必须属于同一 `site/area/unit`，并保持实际时间顺序。

每条链的 `association_rule` 固定记录模型类别和版本，例如 `MARKOV_TRANSITION_HYBRID_V2`。`explanation` 至少包括每条边的 `C_uv`、`E_uv`、`P(v|u)`、`P(v)`、`lift`、中位延迟和门槛，并明确写出：

> 这是基于重复报警片段的统计关联建议，不代表已确认根因。

链首记录只表示本 episode 中首个满足关联门槛的记录。时间最早不等于因果源头，不得自动覆盖成员的 `cause_category`。

## 7. 可弃权原因类别混合模型

### 7.1 特征边界

原因分类允许使用以下经版本化审查的特征：

- `tag` 和 `description` 的规范化 token、短语或有明确边界的模式；
- 经人工确认的资产类别或位号命名规范；
- 报警频率、状态转换比、持续时间等时序特征；
- 同 tag 具备足够观测时的数值变化特征，例如稳健斜率；
- `value`、`threshold` 和工程单位语义明确时的归一化偏差。

模型特征不得包含 `SYNTHETIC` 场景名、固定 UUID、源行范围、样例序号或“步骤 N”等演示编排信息。没有明确工程单位和高/低限语义时，不得跨不同量纲直接比较数值。

### 7.2 加权专家分数

对原因类别 `c` 定义非负特征向量 `x` 和经专家审查的非负权重向量 `w_c`：

```text
S_c(x) = (w_c dot x) / (||w_c||_2 * ||x||_2)
```

若 `x` 或 `w_c` 的范数为零，则 `S_c=0`。每个类别还可定义少量明确的否决条件，例如文本明确表示维护测试时，不得仅因设备词命中就输出设备故障。否决条件必须版本化并出现在解释中，不能散落为不可追踪的代码分支。

设最高分与次高分为 `S_1`、`S_2`。只有满足：

```text
S_1 >= expert_min_score
S_1 - S_2 >= expert_min_margin
且未命中该类别否决条件
```

才输出最高分类；否则输出 `UNKNOWN`。`UNKNOWN` 是正确的可弃权结果，不得为提高覆盖率强行分类。

当前响应没有独立的原因类别分数字段，因此 `S_1`、`S_2`、margin、主要贡献特征和否决/弃权原因写入 `evidence`；不得把逐记录 `score` 偷换为原因类别概率。

### 7.3 监督补充分支与模型资产

当前监督模型使用项目自建工程场景离线训练：LinearSVC 对经过 Unicode NFKC 规范化的报警描述执行字符 TF-IDF 分类；AdaBoost 对持续时间、优先级、状态、是否返回、是否确认、确认延迟和有明确语义的归一化偏差等结构特征分类。训练、验证和测试按 `group_id` 固定隔离，跨集合完全重复或过度相似的文本，以及完全相同的结构特征签名，都会使训练入口失败。

监督结果只能在以下条件全部满足时补充专家分支的 `UNKNOWN`：

- LinearSVC 与 AdaBoost 给出同一原因类别；
- 两个分支各自的判别分数和第一、第二类别间隔达到冻结门槛；
- 文本未命中“原因不确定”“未发现故障”等保守语义；
- 目标为“维护测试”时，描述中存在维护、检修、试验、标定或同义英文词等可复核语义证据。

仅凭低优先级、已恢复、短持续等结构相似性不得输出维护测试；任何条件不满足时保持 `UNKNOWN`，并在 `evidence` 中记录双分支类别、判别量、间隔和门禁结果。监督判别量不是概率，不得替换逐记录 `score` 的规则强度含义。

模型制品使用受控类型序列化后以 AES-256-GCM 认证加密，模型文件与 32 字节随机密钥分离保存。服务启动时校验格式、版本、类别、Pipeline 组成、AdaBoost 单层树、训练属性和门槛；错误密钥、密文篡改、格式不符或必要字段损坏均失败关闭。模型文件不得在请求处理期间训练、改写或联网更新。

## 8. 冻结参数

以下是 v2 首个实现候选的默认参数。它们是可复核的工程基线，不代表行业通用最优值；任何默认值变更都必须更新规则版本、测试和模型卡。

| 参数 | 默认值 | 约束 | 用途 |
| --- | ---: | --- | --- |
| `duplicate_window_seconds` | 30 | `> 0` | 重复报警时间窗 `W_d` |
| `chatter_window_seconds` | 60 | `> 0` | 抖动滑动窗口 `W_c` |
| `chatter_min_count` | 4 | `>= 2` | 抖动最少记录数 `K` |
| `chatter_min_transition_ratio` | 0.8 | `[0, 1]` | 抖动最小状态转换比 `rho` |
| `short_lived_seconds` | 10 | `> 0` | 短时恢复阈值 `W_s` |
| `persistent_requires_ack` | `true` | 布尔 | 持续报警是否必须已确认 |
| `episode_gap_seconds` | 60 | `> 0` | 关联 episode 分段间隔 |
| `chain_window_seconds` | 60 | `> 0` | 有效边最大中位延迟 |
| `chain_min_steps` | 5 | `2..5` | 输出事件链最少成员数 |
| `min_episode_support` | 3 | `>= 2` | 一条边至少出现的不同 episode 数 |
| `min_transition_probability` | 0.6 | `(0, 1]` | 有效边最小 `P(v|u)` |
| `min_lift` | 2.0 | `>= 1` | 有效边最小提升度 |
| `expert_min_score` | 0.35 | `[0, 1]` | 原因分类最小专家相似度 |
| `expert_min_margin` | 0.10 | `[0, 1]` | 最高与次高类别最小间隔 |

Java 必须显式发送全部参数；Python 必须校验范围并在响应中原样回传。Java 保存前继续校验回传参数与请求完全一致。不得在 Java、Python 和测试中各维护一套含义不同的默认值。

## 9. 解释与审计契约

每个结果必须可以由输入和冻结参数离线复算：

- 重复：匹配记录、核心字段、实际时间差、窗口和指数强度；
- 抖动：窗口起止、记录数、首尾二值状态、转换次数、转换比和阈值；完整状态序列不逐记录复制，避免大组输出平方增长；
- 短时：发生、恢复、持续秒数、阈值和指数强度；
- 持续：优先级、状态、恢复/确认值及每个布尔条件；
- 原因建议：贡献特征、权重、各候选分数、margin、否决或弃权原因；
- Markov 链：关系范围、episode、逐边计数、支持 episode、概率、lift、中位延迟和门槛。

解释文本应使用稳定规则标识和中文说明，不得只输出“模型判断”。相同请求、参数和模型版本必须得到字节语义等价的结果；链 ID 可继续由规则版本和有序成员 ID 确定性生成。

## 10. 泛化、负控与验收

### 10.1 静态隔离门槛

- 运行时代码和模型资产中不得出现合成场景名、固定源行范围、固定 UUID 或固定“步骤 1..5”识别逻辑。
- 算法运行时不得读取 `samples/expected`、测试快照或数据库。
- 样例和预期只能作为外部验收输入，不能成为模型特征或规则数据源。

### 10.2 未见数据泛化

使用随机新 UUID、未见 tag、未见描述和新的时间基准构造输入，并验证：

- 描述不包含场景词时，重复、短时、抖动仍按字段与时间特征命中；
- 至少三个不同 episode 重复出现全新 `A -> B -> C -> D -> E` 序列时，可以形成 Markov 链；
- 新 tag 只出现一次或支持度不足时不形成链；
- 原因证据不足时返回 `UNKNOWN`，而不是依据 tag 名猜测。

### 10.3 蜕变测试

在不改变业务关系的前提下，下列变换不得改变按源记录对齐后的分类和链结构：

- 打乱请求数组；
- 全量重映射 UUID；
- 整体平移所有时间戳；
- 改变不参与模型的原始附加列；
- 在不同 site/area/unit 中复用相同 tag 名。

跨关系范围移动成员、把转移延迟移出窗口、把状态转换比降到门槛以下时，结果必须按定义改变。

### 10.4 负控测试

- 对 tag 顺序做固定种子随机打乱，偶然共现不得越过 episode support、转移概率和 lift 三重门槛。
- 在不同单元拼接相同序列不得成链。
- 只有一次 `A -> B`、超过时间窗的转移、重复自转移均不得产生有效链。
- 原因类别最高分不足、最高与次高分冲突、命中否决条件时均返回 `UNKNOWN`。
- 删除 `return_time` 后不得继续声称短时恢复；把 `ACKNOWLEDGED` 与 `ACTIVE` 互换不得伪造报警/非报警抖动。

### 10.5 回归与一致性

- 相同输入重复运行结果完全一致，且请求对象不被修改。
- Java 必须继续拒绝版本、参数、记录覆盖、类别、分数、事件链顺序或汇总不一致的响应，并保持失败原子性。
- 固定 300 行 smoke 及 20,000 行 demo 均须通过，但它们只证明契约、性能和已植入场景，不构成真实工业准确率证据。
- 若 v2 改变现有固定样例结果，预期文件必须由独立场景规范更新；禁止从算法输出反向生成“真值”。

## 11. 性能复杂度

设单批记录数为 `n`，专家特征总数为固定上限 `F`，有效 Markov 边数为 `m`：

- 分组和稳定排序：时间 `O(n log n)`，空间 `O(n)`；
- 时序窗口扫描：使用双指针、转换前缀和与区间最小值查询，时间 `O(n log n)`，不得对同组记录做无界全对全比较；
- 相邻 Markov 转移计数：时间 `O(n)`，空间 `O(m)`；
- 链提取：时间 `O(n)`；
- 原因专家分类：时间 `O(nF)`，`F` 必须由冻结模型限制，不能随输入文本无界增长。

整体目标为 `O(n log n + nF)` 时间和 `O(n + m)` 空间。20,000 行数据必须在现有审核硬件和算法超时预算内完成；验收记录实际耗时与峰值内存，不以复杂度推导代替实测。

实现不得构造所有 tag 对或所有记录对的 `O(n^2)` 矩阵。若未来采用 transfer entropy、全图因果分析或更高阶模型，应作为独立版本和性能门槛，不得悄悄加入 v2。

## 12. 限制与禁止声明

- Markov 边表示重复 episode 中的统计先后关系，不证明物理因果关系。
- 单批次中罕见 tag 或只出现一次的真实事故会因支持不足而不生成链；这是预期的保守弃权。
- 未提供 P&ID、设备拓扑、过程变量连续轨迹和真实故障标签时，不能确认根因或故障传播路径。
- 规范化记录不是原始连续二值报警轨迹；缺少恢复时间时，抖动持续时间和间隔信息不完整。
- 原因专家模型和监督补充分支只覆盖已审查特征及自建工程场景；新厂商词汇、缩写、否定表达和不同语言可能返回 `UNKNOWN`。
- `score` 没有经过概率校准，不能用于安全完整性等级、风险量化或自动控制决策。
- 模型不执行在线报警抑制。将分析建议用于生产抑制前，必须另行完成报警哲学、风险评审、变更管理和现场验证。
- 无独立真实标注集时，不得声明“98% 准确率”“已适配所有化工装置”“自动定位根因”或同义结论。

## 13. 现场数据校准与模型替换门槛

当前 LinearSVC 与 AdaBoost 只作为保守的原因建议补充分支。要把监督模型用于具体装置、替换当前模型或对外声明现场效果，必须同时满足以下条件：

1. 数据已获授权并完成脱敏、来源、时间范围、装置和标签口径登记；
2. 每条标签有工艺/设备/仪表领域专家审查，允许保留“不确定”和多专家分歧；
3. 训练、验证、测试按 `site/unit/time` 分组隔离，禁止随机逐行切分造成同装置或相邻事件泄漏；
4. 模型与当前专家基线在同一独立测试集比较；
5. 报告各类别 precision、recall、F1、macro-F1、混淆矩阵、`UNKNOWN` 覆盖率和置信区间；
6. 预先冻结目标指标和失败处置，不能在看到测试集后反复调门槛；
7. 训练代码、输入 manifest/hash、参数、依赖锁、随机种子、模型 artifact、代码提交和评估报告可重放；
8. 发布模型只读加载，不在线学习，不因一次新文件静默改变全局行为；
9. 新模型仍须输出逐记录解释或可审计的主要特征贡献，并保留人工复核和 `UNKNOWN`；
10. 未通过独立现场测试和人工门槛前，不得替换当前监督制品、放宽弃权门槛或形成现场准确率承诺。

项目自建工程场景可用于训练和验证当前监督分支的可达性、确定性、分组隔离、保守弃权和负控，但不适合证明现场准确率。现场数据不足或标签责任未明确时，系统继续保留当前门槛和 `UNKNOWN`，不得以扩大覆盖率代替独立验证。

## 14. 研究与标准依据

以下链接均指向标准组织官方页面、论文 DOI 或论文的一手发布页：

1. [ISA-18 Series of Standards](https://www.isa.org/standards-and-publications/isa-standards/isa-18-series-of-standards)：报警系统生命周期、合理化、监测以及 nuisance alarm/alarm flood 管理的官方标准系列入口。
2. [IEC 62682:2022, Management of alarm systems for the process industries](https://webstore.iec.ch/en/publication/65543)：适用于连续、批次和离散过程的报警管理官方标准条目，明确报警与事件日志、历史和性能监测边界。
3. Jiandong Wang, Tongwen Chen, [An online method to remove chattering and repeating alarms based on alarm durations and intervals](https://doi.org/10.1016/j.compchemeng.2014.03.018), *Computers & Chemical Engineering*, 2014：以报警持续时间和间隔识别抖动与重复报警。
4. Naseeb Ahmed Adnan, Iman Izadi, Tongwen Chen, [On expected detection delays for alarm systems with deadbands and delay-timers](https://doi.org/10.1016/j.jprocont.2011.06.019), *Journal of Process Control*, 2011：说明降噪与误报、漏报、检测延迟之间必须权衡。
5. Md Parvez, Wenkai Hu, Tongwen Chen, [An association rule mining approach to predict alarm events in industrial alarm floods](https://doi.org/10.1016/j.conengprac.2023.105617), *Control Engineering Practice*, 2023：从历史报警序列、共现和置信信息学习关联并预测后续报警。
6. Timothy D. Butters, Stefan Güttel, Jonathan L. Shapiro, [Detecting and reducing redundancy in alarm networks](https://doi.org/10.1109/CoASE.2015.7294265), IEEE CASE, 2015：以报警网络/Markov 表示分析连通性和冗余关系。
7. [Cause-effect analysis of industrial alarm variables using transfer entropies](https://doi.org/10.1016/j.conengprac.2017.04.012), *Control Engineering Practice*, 2017：对二值报警变量使用归一化 transfer entropy 和显著性检验；论文同时说明可靠估计需要足够报警出现次数。
8. Vicent Rodrigo, Moncef Chioua, Tore Hägglund, Martin Hollender, [Causal analysis for alarm flood reduction](https://doi.org/10.1016/j.ifacol.2016.07.269), *IFAC-PapersOnLine*, 2016：结合报警日志、过程数据与连接关系提供因果报警建议，说明仅凭时序日志的边界。

这些资料支持把 v2 定位为可解释、可复算的报警管理决策支持模型，而不是未经现场验证的自动根因裁决系统。
