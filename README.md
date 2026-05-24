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

- v085 same-fid pair gate 的 valid 指标很好，但测试 AUC 只有 `0.813999`。
- v088 user/item low-rank pair token 的 valid 接近 v080，但测试 AUC 只有 `0.815706`。
- v082 hour-only token 的 valid 好于主线，但测试 AUC 下降到 `0.823677`。

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
| missing group gate | 测试明显下降 | 粗粒度缺失组无法替代原有细粒度 missing 表达 |
| same-fid / user-item pair token | valid 虚高，test 掉点 | 重复放大 user/item 共现噪声，泛化弱 |
| DIN / target attention 变体 | 没有稳定超过主线 | 与已有 Q token cross-attention 功能重叠 |
| TokenFormer / MLCC 强 context 注入 | 表达力增强但 test 不稳 | 多次注入 context token，放大验证集局部模式 |
| 单纯扩模型容量或换优化器 | 收益不稳定 | 当前瓶颈不是容量不足，而是信号注入和验证集错配 |
| 简单 long sequence summary / recent top-k | 加速有效，精度不足 | recent-only 可能丢掉长期偏好，简单 summary 表达力不够 |

## 7. 为什么很多看似合理的方向掉点

### 7.1 信息冗余比信息缺失更严重

推荐模型里很多特征不是没有进入模型，而是已经通过其他路径表达过：

- user dense 已经进入 dense token，再做 user-item pair token 可能重复放大；
- missing 状态已经影响原始特征取值和辅助路径，再做粗 group gate 可能丢掉字段级差异；
- hour/day 已经通过周期时间 token 进入主干，再加离散 hour token 容易记忆局部时间先验；
- same-fid int/dense 融合如果不替换原路径，而是额外新增 token，容易造成冗余。

所以后期一个重要判断是：**新增特征前先问它是否提供了新的信息，而不是只问它是否看起来有业务意义。**

### 7.2 强特征需要低自由度表达

时间特征是强信号，但强信号不等于高维 embedding 更好。完整 hour/date embedding 的自由度更高，在验证集上更容易拟合局部转化率差异；周期 sin/cos 的自由度较低，更像泛化约束。

这也是 v033/v080 比多个 hour/day 扩展版本更稳的原因。

### 7.3 主干改动和后层拼接不是一回事

后层拼接本质上更接近 calibration，能让分类器最后调整 logit，但不能改变模型如何理解序列。时间 token 进入主干后，会影响每一层序列交互和 Q token 生成，因此它比简单拼接更有效。

这也是我后期对“特征放在哪里”更敏感的原因。

### 7.4 valid/test 错配会放大错误方向

如果验证集是随机切分，而测试集集中在某个日期/小时窗口，模型很容易在 valid 上学习到局部共现或时间先验。v085、v088 这类 pair 版本就是典型：valid 指标很好，但 public test 掉点很大。

因此后期判断一个版本是否值得测试，不能只看 best valid AUC，还要看：

- 是否改善 LogLoss；
- 是否过早过拟合；
- 是否引入了高自由度强特征；
- 是否重复注入同源信息；
- 是否能解释 public test 分布下的泛化。

## 8. 面试可追问问题与回答思路

### Q1：为什么时间特征进入主干会比后层拼接更有效？

后层拼接只能在最终分类头做 logit 校准，而时间会影响历史行为的语义解释。比如同样的用户历史，在不同曝光时段可能对应不同意图强度。把时间做成 prefix token 后，它可以参与序列 self-attention、query 生成和 cross-attention，让模型在构造用户表示时就融合当前请求上下文。

### Q2：为什么不用更强的 hour/date embedding？

hour/date 的区分度确实强，但训练集和测试集存在时间窗口差异。高自由度离散 embedding 容易记忆训练窗口里某个小时或日期的 CVR 先验，导致验证集提升但测试不稳。sin/cos 周期特征是更低自由度的表达，能保留周期性，又减少记忆绝对时间的风险。

### Q3：为什么 pair 特征看起来有业务意义，最后反而掉点？

pair 特征容易捕捉 user/item 共现，但在当前模型里 user embedding、item embedding、dense token、NS token 已经表达了大量同源信息。额外 pair token 如果不是替换式融合，而是直接新增，会重复放大共现噪声。它在随机验证集上可能有效，但 public test 分布变化时泛化差。

### Q4：为什么没有继续扩大模型容量？

扩容量的前提是模型欠拟合。但实验中很多复杂结构 valid 高、test 低，说明主要瓶颈不是表达能力不足，而是过拟合、信息冗余和验证集错配。盲目加大容量会让模型更快拟合局部模式，未必提升线上排序能力。

### Q5：长序列这么明显，为什么长序列 summary 没有效？

长序列问题是真实存在的，但简单 summary token 或 recent top-k 不一定能保留有效偏好。recent-only 会丢掉旧历史中的稳定兴趣，简单 mean/summary 又可能太粗。更合理的方向应是目标感知的历史选择，或者 recent window + long-term summary 的组合，而不是只压缩成一个弱 token。

### Q6：为什么轻正则有效，但大正则或复杂 gate 不一定有效？

v080 的轻正则只约束辅助投影和输出投影，不破坏核心时间主干路径。复杂 gate 或大正则如果作用在强信号主路径上，可能会削弱有效信息；如果作用在冗余特征上，又可能放大局部噪声。正则要和特征路径匹配，而不是统一加大。

### Q7：如果继续优化，会优先做什么？

我会优先做三件事：

1. 构建更贴近 public test 的时间切片验证集，减少 random valid 误导。
2. 对高基数 embedding 做系统化降参，例如 hash/shared embedding、频次分桶和更稳的 OOV 处理。
3. 对长序列做目标感知选择，把 recent 行为和长期偏好摘要结合，而不是简单截断。

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

平台自动化脚本显著减少了重复上传和填错参数的时间，也让实验记录更一致。对算法工程师来说，模型设计、数据分析、训练稳定性、日志分析和平台工程是连在一起的，不是割裂的。

## 12. 总结

这个方案的关键不是堆更多模块，而是围绕数据和主干结构做克制迭代：

- 先保证 baseline 对齐；
- 用 EDA 找到真实强信号；
- 把时间信号以低自由度 token 形式放进主干；
- 对辅助路径做轻正则；
- 对 valid/test 错配保持警惕；
- 避免重复注入同源信息。

最终经验可以总结为一句话：

**推荐系统比赛中的有效提升，往往来自对数据分布、特征路径和泛化风险的判断，而不只是模型结构复杂度。**
