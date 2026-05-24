# TAAC 2026 CVR Prediction Solution

本仓库整理了我在 TAAC2026 广告转化率预测赛道中最终保留的单模型方案，以及比赛过程中用于提升迭代效率的平台自动化脚本。

- `baseline/`：官方 baseline 代码，作为对照和复现实验入口。
- `version/v080/`：最终保留版本代码。
- `automation_submission/`：训练任务创建、checkpoint 发布、评估任务提交的自动化脚本和使用说明。

## 1. 比赛背景

TAAC2026 的任务是广告/推荐场景下的 CVR 预测。模型输入包含：

- 用户侧离散特征和连续特征；
- 物品侧离散特征；
- `seq_a`、`seq_b`、`seq_c`、`seq_d` 四路历史行为序列；
- 曝光时间戳；
- 缺失状态和序列长度等上下文信息。

目标是在官方平台上输出每条样本的转化概率，线上主要以 AUC 评价，同时需要兼顾训练效率、推理时延和线上提交稳定性。

## 2. 最终结果

已确认的线上测试结果：

| 版本 | 测试 AUC | 说明 |
| --- | ---: | --- |
| 官方 baseline | `0.813207` | 官方初始模型 |
| v080 | `0.827252` | 本仓库最终保留单模型 |

最终单模型相对官方 baseline 提升约 `+0.014045` AUC。

## 3. 我的整体思路

这次比赛的核心难点不是“缺少可以尝试的 trick”，而是如何判断哪些 trick 真的泛化。比赛过程中我逐步把迭代逻辑收敛成四个问题：

1. **baseline 是否可信。**  
   先确认官方 baseline 的训练、推理、embedding reset、文件替换和模型发布链路是否一致。早期如果不对齐官方行为，任何模型实验结论都会失真。

2. **数据里真正强的信号是什么。**  
   通过线上 EDA 发现，时间分布、长序列截断、缺失模式、高基数字段长尾都是强信号。但这些信号不能简单粗暴地全部作为强 token 或强 gate 注入。

3. **强信号应该放在模型的什么位置。**  
   仅在输出层拼接上下文特征，通常只能做浅层校准；把时间信号放进序列主干，让它参与序列编码和 query 生成，收益更稳定。

4. **验证集提升是否能转化到线上测试。**  
   很多版本 valid AUC 看起来更高，但测试掉点。后期我更重视测试集无标签 EDA、LogLoss、预测均值、时间窗口和业务解释，而不是只看 valid AUC。

最终保留下来的 v080 是一个相对克制的版本：它没有继续堆复杂结构，而是在已确认有效的 v033 时间主干化基础上加入轻正则。

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

这些特征经过 projector 形成 64 维时间 token，并 prepend 到 `seq_a/b/c/d` 四路序列中。这样时间信息不再只是分类头前的辅助特征，而是参与：

- 序列 self-attention；
- query 生成；
- Q tokens 与序列的 cross attention；
- 最终 RankMixer 融合。

这个改动来自一个核心判断：时间是强信号，但应该以低维周期形式参与主干交互，而不是用高自由度的 hour/date embedding 去记忆训练窗口。

### 5.2 轻正则

v080 在几个容易放大噪声的投影路径上加入非常小的 dropout：

```bash
--ns_dense_dropout 0.01
--aux_projector_dropout 0.02
--output_projector_dropout 0.015
```

注意，这里没有对时间 prefix token 主路径加 dropout，因为前面的实验显示时间主干化是主要收益来源，直接扰动它可能损伤效果。

### 5.3 多 epoch checkpoint 保存

训练从第 3 个 epoch 开始每个 epoch 保存 checkpoint，并以验证集 LogLoss 追踪最优点；最低 LogLoss 后继续保存 3 个 epoch 再停止。这是为了解决后期模型常见的“继续训练后 valid/test 不稳定”问题。

## 6. 主要实验结论

比赛期间我尝试过很多方向，最后的判断是：不是所有 valid 提升都值得合入主线。

### 6.1 有效方向

| 方向 | 结论 |
| --- | --- |
| baseline 行为对齐 | 必须先做，否则实验不可比 |
| Q/K Norm 与主干稳定化 | 有稳定收益，是后续版本底座 |
| 曝光时间 prefix token | 最重要的结构性提升 |
| 轻正则 | 小幅但稳定，最终保留 |
| 多 checkpoint 保存 | 方便选择更稳的 epoch |

### 6.2 没有稳定转化的方向

| 方向 | 问题 |
| --- | --- |
| hour/day/date 强离散 token | 容易记忆训练时间窗口，测试不稳 |
| missing group gate | 粗粒度 gate 无法替代细粒度 missing 信息 |
| same-fid / user-item pair token | valid 可能虚高，但测试泛化差 |
| DIN / target attention 变体 | 容易和已有 Q/NS/Seq 交互路径冗余 |
| TokenFormer / MLCC 强 context 注入 | 表达力增强但过拟合风险高 |
| 单纯扩模型容量或换优化器 | 不是当前主要瓶颈 |
| 简单 long sequence summary / recent top-k | 加速有价值，但精度收益不稳定 |

## 7. 为什么很多看似合理的方向掉点

后期最重要的反思是：推荐场景里的很多特征并不是“缺失”的，而是已经通过其他路径进入模型了。

例如：

- user dense 信息已经进入 dense token，再做 user-item pair token 可能重复放大；
- missing 状态已经影响原始特征取值和辅助路径，再做粗 group gate 可能丢失细节；
- hour/day 信息已经通过周期时间 token 进入主干，再加离散 hour token 容易记忆局部时间先验；
- same-fid int/dense 融合如果不替换原路径，而是额外新增 token，容易造成冗余。

这些方向在随机验证集上经常看起来有效，但 public test 时间窗口更窄、分布更特殊，最终会暴露泛化问题。

## 8. 目录结构

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

## 9. 使用方式

本代码主要面向 TAAC 官方训练/评估平台。平台训练时使用对应版本目录下的 `run.sh` 作为入口。

本地做语法检查：

```bash
python -m py_compile baseline/*.py
python -m py_compile version/v080/*.py
```

在官方平台提交时，将 `version/v080/` 下的代码文件替换到平台训练模板即可。自动化提交流程见：

```text
automation_submission/README.md
```

## 10. 经验总结

这次比赛对我最有价值的不是某个单独 trick，而是形成了一套更稳的迭代判断：

- 先让 baseline 可复现、可比较；
- 用 EDA 判断真实业务强信号；
- 把强信号放到主干中，而不是只在输出层拼接；
- 新增特征时先判断是否和已有路径冗余；
- 对 valid 高但 test 不稳的版本保持警惕；
- 后期宁可保留简单稳定的主线，也不要为了验证集小涨幅引入复杂高风险结构。

一句话总结：  
**这个方案的关键不是堆更多模块，而是把时间这个强信号放到正确的位置，并用轻正则控制主干外辅助信息的过拟合。**
