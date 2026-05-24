# TAAC 2026 CVR Prediction Solution

本仓库整理了我在 TAAC2026 广告转化率预测赛道中最终保留的单模型方案，以及比赛过程中用于提升迭代效率的平台自动化脚本。

- `baseline/`：官方 baseline 代码，作为对照和复现实验入口。
- `version/v080/`：最终保留版本代码。
- `automation_submission/`：训练任务创建、checkpoint 发布、评估任务提交的自动化脚本和使用说明。

## 1. 比赛背景

TAAC2026 的任务是广告/推荐场景下的 CVR 预测。模型需要根据用户侧特征、物品侧特征、四路历史行为序列、曝光时间、缺失状态等信息，预测用户发生转化的概率。线上主要以 AUC 评价，同时需要兼顾训练效率、推理时延和平台提交稳定性。

输入特征可以概括为四类：

- **非序列特征**：用户侧离散/连续特征、物品侧离散特征。
- **多路行为序列**：`seq_a`、`seq_b`、`seq_c`、`seq_d` 四个 domain 的历史行为。
- **时间特征**：曝光 `timestamp`，以及序列事件与曝光时刻之间的 time-diff bucket。
- **状态特征**：序列长度、字段缺失状态、高基数 ID 的长尾分布。

这个任务的核心难点不只是建模能力，而是如何处理推荐系统中常见的三类问题：

1. **时间漂移**：训练集和测试集的曝光日期、小时窗口并不完全一致，强时间特征容易在验证集上有效、测试集上失效。
2. **长序列压缩**：部分 domain 的历史序列很长，简单截断会损失长期偏好，但盲目扩大序列建模成本也很高。
3. **信息冗余**：很多 user/item/pair/missing 特征在不同路径中表达的是同一类业务状态，重复注入容易过拟合。

## 2. 最终结果

已确认的线上测试结果：

| 版本 | 测试 AUC | 说明 |
| --- | ---: | --- |
| 官方 baseline | `0.813207` | 官方初始模型 |
| v080 | `0.827252` | 本仓库最终保留单模型 |

最终单模型相对官方 baseline 提升约 `+0.014045` AUC。

这个提升不是来自单个复杂 trick，而是来自一条逐步收敛的工程和建模路线：

```text
baseline 对齐
  -> attention / 主干稳定化
  -> 时间信号主干化
  -> 轻正则约束
  -> 多 checkpoint 选择
```

## 3. 整体建模思路

比赛前期我尝试了很多方向，但后期逐渐把判断标准收敛为四个问题。

### 3.1 baseline 是否可信

在推荐系统比赛中，baseline 复现质量会直接影响所有后续判断。早期我首先检查了：

- 训练入口、推理入口和平台模板文件是否一致；
- 官方 baseline 每个 epoch 后对 embedding 和优化器状态的 reset 行为是否保留；
- 训练和评估文件替换是否完整；
- 线上 checkpoint 发布和评估是否实际加载了目标模型结构。

这个阶段的结论是：**先保证 baseline 行为完全对齐，再做模型创新**。如果 baseline 本身与官方实现不一致，后续看到的 AUC 波动很可能只是实现差异，不是算法改进。

### 3.2 真实强信号是什么

通过线上 EDA，我重点分析了标签分布、时间分布、序列长度、缺失率、高基数字段和测试集无标签分布。比较明确的信号包括：

- 曝光时间与 CVR 强相关，但绝对日期和小时有明显分布漂移风险。
- `domain_d` 序列很长，长历史截断是真实问题。
- missing pattern 能表达用户/物品状态，例如冷启动、画像完整度或物品侧字段缺失。
- 高基数字段具有强长尾属性，大 embedding 容易过拟合和拖慢训练。

但这些信号不能简单理解成“强信号就应该强注入”。后续很多掉点版本说明，强信号如果位置不对或自由度太高，反而会学到局部窗口先验。

### 3.3 强信号应该放在哪里

一个重要判断是：**强业务信号如果只在输出层拼接，通常只能做浅层校准；如果进入主干，才能影响序列理解和 query 生成。**

最终有效的 v033/v080 选择把曝光时间做成序列 prefix token，而不是只拼到 classifier 前。这样时间信号可以参与：

- 序列 self-attention；
- MultiSeqQueryGenerator 的 Q token 生成；
- Q token 与序列的 cross-attention；
- 多 domain RankMixer 融合。

相比后层拼接，这种方式让模型在理解历史行为时就知道当前请求发生在什么时间上下文下。

### 3.4 valid 提升是否可信

比赛后期最重要的反思是：**随机验证集 AUC 并不总能代表 public test。**

典型现象：

- 将同一 fid 下的用户离散 ID embedding 与对应 dense 数值做门控融合后，valid 指标很好，但测试 AUC 只有 `0.813999`。
- 将用户表示和物品表示降维后做内积，并把这个 user-item pair 表示作为额外 token 加入主干后，valid 接近最终主线，但测试 AUC 只有 `0.815706`。
- 将曝光小时做成额外 hour token 加入主干后，valid 好于主线，但测试 AUC 下降到 `0.823677`。

这说明很多特征交互版本学到的是训练分布内的共现模式或时间先验，而不是可泛化排序能力。因此后期我不再只看 valid AUC，而是同时看：

- valid LogLoss 是否同步改善；
- 训练后期是否快速过拟合；
- 预测均值和分布是否异常；
- public test 无标签 EDA 是否与训练/验证分布一致；
- 改动是否有明确业务解释，而不是纯粹依赖指标小幅波动。

## 4. 模型架构

最终模型仍然基于官方多序列 HyFormer/Transformer 风格 baseline。整体流程如下：

```text
user/item non-seq features
        -> embedding / RankMixer-style tokenizer
        -> NS tokens

user dense features
        -> dense projector
        -> dense token

seq_a / seq_b / seq_c / seq_d
        -> side-info embedding
        -> event time bucket embedding
        -> prepend exposure time token
        -> sequence encoder

NS tokens + sequence summaries
        -> MultiSeqQueryGenerator
        -> per-domain Q tokens

Q tokens + NS tokens + encoded sequences
        -> MultiSeq HyFormer blocks
        -> RankMixer interaction
        -> final Q representation
        -> classifier
        -> CVR logit
```

关键模块：

- **RankMixer-style NS tokenizer**：将非序列用户/物品离散特征压缩为固定数量的 NS tokens。
- **MultiSeqQueryGenerator**：根据非序列 tokens 和四路序列摘要生成每个 domain 的 Q tokens。
- **HyFormer block**：对序列、Q tokens 和 NS tokens 做多轮交互。
- **Q/K Norm**：在 attention 内部对每个 head 的 Q/K 做归一化，提高训练稳定性。
- **时间 prefix token**：把曝光时间周期特征投影为 token，并插入四路序列主干。
- **轻正则 projector**：只在部分辅助投影路径和输出投影后加入小 dropout。

## 5. v080 的核心改动

v080 的核心原则是：保留真正有效的主干改动，只对容易过拟合的路径做轻量约束。

### 5.1 曝光时间进入主干

从曝光 `timestamp` 构造 4 维周期特征：

- `sin(hour_of_day)`
- `cos(hour_of_day)`
- `sin(day_of_week)`
- `cos(day_of_week)`

这些特征经过 projector 形成 64 维时间 token，并 prepend 到 `seq_a/b/c/d` 四路序列中。

这样做的原因：

1. **周期化降低自由度**  
   用 sin/cos 表示小时和星期，可以表达周期关系，但不会像离散 hour/date embedding 那样轻易记忆某个绝对小时或日期。

2. **进入主干而不是后层补丁**  
   时间上下文会影响用户对历史行为的解释。例如同样的历史行为，在不同曝光时段可能对应不同转化倾向。让时间参与序列编码，比只在分类头前加一个 dense residual 更合理。

3. **避免重复注入**  
   当时间 token 已经进入主序列后，不再把同一时间投影强行拼到输出层，避免同源信息被重复放大。

### 5.2 轻正则

v080 在几个容易放大噪声的投影路径上加入非常小的 dropout：

```bash
--ns_dense_dropout 0.01
--aux_projector_dropout 0.02
--output_projector_dropout 0.015
```

这里没有对时间 prefix token 主路径加 dropout。原因是前面实验显示时间主干化是主要收益来源，直接扰动它可能损伤有效信号。轻正则主要作用在：

- user dense token 投影；
- seq length / missing 辅助投影；
- 最终 Q tokens 输出投影。

我的理解是：主干中的时间 token 是核心信号，辅助路径更像噪声放大器，需要轻微抑制。

### 5.3 多 epoch checkpoint 保存

训练从第 3 个 epoch 开始每个 epoch 保存 checkpoint，并以验证集 LogLoss 追踪最优点；最低 LogLoss 后继续保存 3 个 epoch 再停止。

这么做的原因：

- 后期很多版本 AUC 峰值和 LogLoss 峰值不完全一致；
- 继续训练后常出现 valid AUC 短暂上升但 LogLoss 或测试稳定性变差；
- 多保留几个 epoch checkpoint，可以在评估次数有限时选择更稳的模型。

## 6. 主要实验结论

### 6.1 有效方向

| 方向 | 为什么有效 |
| --- | --- |
| baseline 行为对齐 | 排除实现差异，保证实验可比 |
| Q/K Norm 与主干稳定化 | 缓解 attention 分数尺度波动，提升多序列交互稳定性 |
| 曝光时间 prefix token | 让强时间信号参与序列理解和 query 生成 |
| 轻正则 | 抑制辅助投影路径噪声，避免过度拟合验证集 |
| 多 checkpoint 保存 | 避免只保留过拟合后的单一 checkpoint |

### 6.2 没有稳定转化的方向

| 方向 | 现象 | 深层原因 |
| --- | --- | --- |
| hour/day/date 强离散 token | valid 可能提升，test 下降 | 容易记忆训练窗口的小时/日期先验 |
| 将多个缺失字段聚合成 group gate，并替代原 missing 辅助路径 | 测试明显下降 | 粗粒度缺失组无法替代原有细粒度 missing 表达 |
| 同一 fid 下用户离散 ID 与 dense 数值做门控融合 | valid 虚高，test 掉点 | 这类特征和原 user embedding / user dense token 同源，容易重复放大用户侧局部模式 |
| 用户表示和物品表示降维后做内积，再作为 pair token 加入模型 | valid 接近主线，test 明显下降 | pair token 捕捉共现很强，但对随机验证集更友好，对时间漂移后的测试集泛化弱 |
| 用目标物品 embedding 初始化 query 或增加 DIN 式 target attention | 没有稳定超过主线 | 与已有 Q token cross-attention 功能重叠，新增路径带来的信息增量有限 |
| 多个上下文 token 反复注入 Q/NS/Seq 主干 | 表达力增强但 test 不稳 | 多次注入 context token，容易放大验证集局部模式 |
| 单纯扩模型容量或换优化器 | 收益不稳定 | 当前瓶颈不是容量不足，而是信号注入和验证集错配 |
| 简单 long sequence summary / recent top-k | 加速有效，精度不足 | recent-only 可能丢掉长期偏好，简单 summary 表达力不够 |

## 7. 为什么很多看似合理的方向掉点

### 7.1 信息冗余比信息缺失更严重

推荐模型里很多特征不是没有进入模型，而是已经通过其他路径表达过：

- user dense 已经进入 dense token，再把用户向量和物品向量做低维内积并新增 pair token，可能重复放大 user-item 共现；
- missing 状态已经影响原始特征取值和辅助路径，再把多个缺失字段压成一个粗粒度 gate，可能丢掉字段级差异；
- hour/day 已经通过周期时间 token 进入主干，再加离散 hour token 容易记忆局部时间先验；
- 同一 fid 下的用户离散 ID 和 dense 数值如果只是额外融合，而不替换原有 user embedding / user dense 路径，容易造成冗余。

所以后期一个重要判断是：**新增特征前先问它是否提供了新的信息，而不是只问它是否看起来有业务意义。**

### 7.2 强特征需要低自由度表达

时间特征是强信号，但强信号不等于高维 embedding 更好。完整 hour/date embedding 的自由度更高，在验证集上更容易拟合局部转化率差异；周期 sin/cos 的自由度较低，更像泛化约束。

这也是 v033/v080 比多个 hour/day 扩展版本更稳的原因。

### 7.3 主干改动和后层拼接不是一回事

后层拼接本质上更接近 calibration，能让分类器最后调整 logit，但不能改变模型如何理解序列。时间 token 进入主干后，会影响每一层序列交互和 Q token 生成，因此它比简单拼接更有效。

这也是我后期对“特征放在哪里”更敏感的原因。

### 7.4 valid/test 错配会放大错误方向

如果验证集是随机切分，而测试集集中在某个日期/小时窗口，模型很容易在 valid 上学习到局部共现或时间先验。用户-物品 pair token、同 fid 用户特征门控融合这类版本就是典型：valid 指标很好，但 public test 掉点很大。

因此后期判断一个版本是否值得测试，不能只看 best valid AUC，还要看：

- 是否改善 LogLoss；
- 是否过早过拟合；
- 是否引入了高自由度强特征；
- 是否重复注入同源信息；
- 是否能解释 public test 分布下的泛化。

## 8. 后续可继续优化的方向

如果继续迭代，我会优先做三件事：

1. **构建更贴近线上分布的验证集。**  
   当前最明显的问题是随机验证集会高估时间先验和 user-item 共现特征。更合理的方式是增加时间切片验证，例如单独观察最后一天、重点小时段、长序列样本和高缺失样本上的 AUC/LogLoss。

2. **系统化处理高基数 embedding。**  
   高基数字段既带来表达能力，也带来长尾过拟合和训练成本。后续更值得系统探索 hash/shared embedding、频次分桶、低频 ID 合并、OOV 表达和 embedding 正则，而不是继续扩大 dense MLP 容量。

3. **做目标感知的长序列选择。**  
   长序列问题是真实存在的，但简单 recent top-k 或单个 summary token 不够。更合理的做法是结合目标 item/query，对历史行为做相关性选择，同时保留长期偏好摘要，形成 recent window + long-term memory 的结构。

## 9. 目录结构

```text
.
├── README.md
├── automation_submission/
│   ├── README.md
│   └── scripts/
│       ├── taiji_training.py
│       ├── taiji_ckpt.py
│       └── taiji_eval.py
├── baseline/
│   ├── dataset.py
│   ├── infer.py
│   ├── model.py
│   ├── ns_groups.json
│   ├── run.sh
│   ├── train.py
│   ├── trainer.py
│   └── utils.py
└── version/
    └── v080/
        ├── dataset.py
        ├── infer.py
        ├── model.py
        ├── ns_groups.json
        ├── run.sh
        ├── train.py
        ├── trainer.py
        ├── utils.py
        └── SUBMIT_COMMANDS.md
```

## 10. 使用方式

本代码主要面向 TAAC 官方训练/评估平台。平台训练时使用对应版本目录下的 `run.sh` 作为入口。

本地做语法检查：

```bash
python -m py_compile baseline/*.py
python -m py_compile version/v080/*.py
python -m py_compile automation_submission/scripts/*.py
```

在官方平台提交时，将 `version/v080/` 下的代码文件替换到平台训练模板即可。自动化提交流程见：

```text
automation_submission/README.md
```

## 11. 个人复盘

这次比赛对我最大的训练不是实现某个模型模块，而是建立了一套更接近工业推荐场景的实验方法。

### 11.1 从追 trick 到验证假设

前期很容易看到别人说某个 trick 有效，就想快速复现。但后面发现，trick 是否有效高度依赖数据分布、特征组织、验证集构造和模型主干。后期我更倾向于先写清楚假设：这个改动解决什么问题，增加了什么信息，是否和已有路径冗余，是否可能只提升 random valid。

### 11.2 更重视数据分布，而不是只看模型结构

时间 EDA、测试集无标签 EDA、序列长度分析、missing 分布分析，对判断方向的价值很大。很多掉点版本不是代码写错，而是没有处理好数据分布差异。这个经验对真实业务也很重要：线上模型失败经常不是架构不够先进，而是训练/验证/线上分布不一致。

### 11.3 更重视简洁和可解释

最终保留的 v080 并不复杂，但它的每个改动都有比较清楚的解释：时间进入主干、辅助路径轻正则、多 checkpoint。相比之下，一些复杂结构虽然 valid 更高，但很难解释为什么能泛化。比赛后期我更愿意保留简单、稳定、可解释的版本。

### 11.4 工程自动化同样是算法能力的一部分

平台自动化脚本显著减少了重复上传和填错参数的时间，也让实验记录更一致。对这类项目而言，模型设计、数据分析、训练稳定性、日志分析和平台工程是连在一起的，不是割裂的；很多时候，能否稳定完成一轮实验、正确保留日志和 checkpoint，本身就决定了后续判断是否可靠。

## 12. 总结

这个方案最终沉淀下来的不是某个单点技巧，而是一套比较稳定的迭代方式：

- 先保证 baseline 对齐；
- 用 EDA 找到真实强信号；
- 把时间信号以低自由度 token 形式放进主干；
- 对辅助路径做轻正则；
- 对 valid/test 错配保持警惕；
- 避免重复注入同源信息。

回头看，真正稳定的提升主要来自对数据分布、特征路径和泛化风险的持续判断，而不是单纯追求模型结构更复杂。后期最有价值的经验，是逐渐把注意力从“哪个模块名字更先进”转到“这个改动是否真的引入了新信息、是否放在了正确的位置、是否会在测试分布下失效”。
