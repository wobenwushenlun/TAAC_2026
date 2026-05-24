# v080_light_regularization_on_v033

## 版本目标

基于 `v067_logloss_epoch_ckpts_on_v033` 做一版轻正则实验。核心思想是保留 `v033` 已经验证有效的主干、时间 prefix token、RankMixer、bf16 和按 `LogLoss` 保存多 epoch checkpoint 的逻辑，只在容易放大噪声的投影路径上加很小的 dropout。

## 核心改动

- 保留 `v033/v067` 的主体结构，不新增特征、不改变 token 数、不改变 query/DIN/attention。
- 新增三类轻正则参数：
  - `ns_dense_dropout=0.01`：只作用在 user/item dense token 投影后。
  - `aux_projector_dropout=0.02`：只作用在 `seq_len`、`missing` 辅助投影后。
  - `output_projector_dropout=0.015`：只作用在最终 Q tokens 拼接后的 `output_proj` 后。
- 不对已验证有效的曝光时间 prefix token 加 dropout，避免破坏 `v033` 的主要收益来源。
- 继续使用 `v067` 的 checkpoint 保存策略：
  - 第 3 个 epoch 开始每个 epoch 单独保存 checkpoint；
  - 以验证集 `LogLoss` 追踪最优点；
  - 最低 `LogLoss` 后再保存 3 个 epoch checkpoint 后停止。

## 实验假设

近期多个结构增强版本出现验证/测试不一致，说明主干外的辅助信号容易被过拟合或放大噪声。本版用很小的 dropout 做低扰动正则，验证“轻微去噪”是否能提升线上泛化，而不是继续增加模型复杂度。

## 观察重点

- 如果 valid AUC 略降但 `LogLoss` 更稳，优先保留多个 epoch checkpoint 做评估选择。
- 如果线上优于 v033/v067，说明后续组合版本应默认带一层轻正则。
- 如果明显掉点，说明当前瓶颈不在正则不足，而更可能是特征位置或训练/评估分布差异。
