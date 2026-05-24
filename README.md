# TAAC 2026 解决方案总结

本仓库整理了我在 TAAC2026 广告转化率预测赛道中的最终保留方案。仓库只保留两部分代码：

- `baseline/`：官方 baseline 代码，用于复现原始模型和对照实验。
- `version/v080/`：最终保留的最优单模型版本。

数据集、训练日志、模型 checkpoint、平台 cookie 和临时实验文件均未上传。

## 比赛背景

TAAC2026 任务是典型的广告/推荐场景 CVR 预测。模型需要根据用户侧特征、物品侧特征、四路历史行为序列、曝光时间、缺失状态等信息，预测样本发生转化的概率。线上评估主要关注 AUC，同时也需要兼顾训练效率和推理时延。

本方案从官方 baseline 出发，围绕三个问题迭代：

1. 如何让多路长序列、用户/物品特征和上下文特征稳定交互。
2. 如何利用曝光时间、序列时间差、缺失模式等强业务信号。
3. 如何避免随机验证集提升无法泛化到线上测试集。

## 最终成绩

已确认的线上测试结果：

| 版本 | 测试 AUC | 说明 |
| --- | ---: | --- |
| 官方 baseline | `0.813207` | 官方初始模型 |
| 最终保留版本 v080 | `0.827252` | 本仓库提交版本 |

相对官方 baseline，最终单模型提升约 `+0.014045` AUC。

## 模型概览

最终版本基于官方 HyFormer/Transformer 风格 baseline，主体仍然是多序列推荐模型：

- 用户/物品离散特征通过 embedding 和 RankMixer 风格 tokenizer 压缩为 NS tokens。
- 用户 dense 特征投影为 dense token。
- `seq_a/seq_b/seq_c/seq_d` 四路行为序列分别做 side-info embedding 和事件级 time bucket embedding。
- MultiSeqQueryGenerator 根据 NS tokens 和序列摘要生成各 domain 的 Q tokens。
- 多层 HyFormer block 对序列、Q tokens 和 NS tokens 做交互。
- 最终使用 Q tokens 表示，拼接轻量辅助信息进入分类头输出 CVR logit。

最终有效的关键改动是：

1. **Q/K Norm 与主干稳定化**  
   在 attention 内部使用 head 级 Q/K RMSNorm，提升多序列 attention 的训练稳定性。

2. **曝光时间进入主干**  
   从曝光 `timestamp` 构造 `hour_of_day`、`day_of_week` 的 sin/cos 周期特征，投影为时间 token，并 prepend 到四路序列中。这样时间信号参与序列编码、query 生成和主干交互，而不是只在输出层后拼接。

3. **轻正则而非堆复杂结构**  
   在最终版本中保留已验证有效的时间主干路径，不新增额外复杂 token，只在容易放大噪声的投影路径加入很小的 dropout：
   - `ns_dense_dropout=0.01`
   - `aux_projector_dropout=0.02`
   - `output_projector_dropout=0.015`

## 为什么选择 v080

比赛过程中尝试过大量方向，包括：

- hour/day 离散 token；
- missing group gate；
- same-fid user int/dense 融合；
- user/item 低维 pair token；
- DIN / target item attention；
- TokenFormer / MLCC calibration；
- SENet gating / NS self-attention；
- 长序列 summary / recent top-k / HSTU semi-local mask；
- Muon、OrthAdamW、LR warmup、focal loss；
- hash embedding 与高基数字段降参。

很多版本在验证集上看起来有提升，但在线上测试集掉点。主要原因是：

- public test 的时间窗口与随机验证集分布不一致；
- 强 hour/date/pair 特征容易学习训练窗口先验；
- user/item/pair/same-fid/missing 等路径存在明显信息冗余；
- 新增 token 或 gate 容易干扰原有 Q/NS/Seq 主干交互。

v080 的优势是克制：它保留了最确定有效的时间主干化和 attention 稳定化，只做轻量正则，因此线上泛化最稳。

## 目录结构

```text
.
├── README.md
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
    └── v080_light_regularization_on_v033/
        ├── dataset.py
        ├── infer.py
        ├── model.py
        ├── ns_groups.json
        ├── run.sh
        ├── train.py
        ├── trainer.py
        ├── utils.py
        ├── VERSION.md
        └── SUBMIT_COMMANDS.md
```

## 运行方式

本代码主要面向 TAAC 官方训练/评估平台，平台会调用对应版本目录中的 `run.sh` 作为训练入口。

本地只做语法检查时可以运行：

```bash
python -m py_compile baseline/*.py
python -m py_compile version/v080/*.py
```

在官方平台提交时，使用 `version/v080/` 下的全部文件替换训练模板文件即可。

## 主要经验

这次比赛最重要的经验是：推荐系统比赛中的有效迭代，不是简单堆更多特征和模块，而是找到真实强信号，把它放到正确的位置，并控制它不要在错误的验证集上过拟合。

最终保留的判断：

- 时间信号有效，但应低维、周期化、进入主干，避免强记忆绝对小时/日期。
- 轻正则有效，但不能补救错误的信息路径。
- 验证集 AUC 不能单独作为最终判断，需要结合测试集分布、LogLoss、预测均值和业务解释。
- 新增 pair、gate、context token 时必须警惕信息冗余。
- 在工程上，稳定的实验目录、日志、checkpoint 保存和平台自动化，比单个 trick 更重要。
