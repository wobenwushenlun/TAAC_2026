# 自动化提交说明（脱敏版）

本目录记录比赛期间使用的自动化提交思路，方便复盘工程流程。这里不包含真实 headers、cookie、task id、instance id、项目 id、本地个人路径或任何可直接复用的账号凭据。

## 1. 自动化目标

官方平台的训练/评估通常需要重复执行以下操作：

1. 选择一个已有模板或历史任务。
2. 上传本地版本目录中的代码文件。
3. 替换平台模板中的同名文件。
4. 创建新的训练任务。
5. 启动训练并轮询任务状态。
6. 训练完成后发布 checkpoint。
7. 提交评估任务，并替换推理侧 `dataset.py/model.py/infer.py`。
8. 拉取训练/评估日志，记录 AUC、LogLoss、推理耗时和错误信息。

自动化脚本的作用是把这些机械步骤标准化，避免手动上传时漏文件、传错路径或填错任务 ID。

## 2. 推荐的本地目录约定

```text
project_root/
├── baseline/
├── version/
│   └── v080/
│       ├── dataset.py
│       ├── infer.py
│       ├── model.py
│       ├── ns_groups.json
│       ├── run.sh
│       ├── train.py
│       ├── trainer.py
│       └── utils.py
└── scripts/
    ├── taiji_training.py        # 本地训练任务自动化脚本，不建议公开真实配置
    ├── taiji_ckpt.py            # 本地 checkpoint/model 发布辅助脚本
    ├── taiji_eval.py            # 本地评估任务辅助脚本
    └── tmp/
        └── <local_headers_file> # 本地私密文件，禁止提交
```

公开仓库中只保留版本代码和脱敏说明，不保留 `scripts/tmp/`、cookie、headers 或平台返回的任务详情。

## 3. 训练任务提交模板

训练侧通常需要替换 7 个文件：

- `dataset.py`
- `model.py`
- `ns_groups.json`
- `run.sh`
- `train.py`
- `trainer.py`
- `utils.py`

脱敏命令示例：

```powershell
python .\scripts\taiji_training.py `
  --headers-file <LOCAL_HEADERS_FILE> `
  --create `
  --new-job-name taac2026_v080 `
  --new-job-desc "final v080: time prefix token + light regularization" `
  --host-gpu-num 1 `
  --path-suffix <TRAIN_UPLOAD_PATH_SUFFIX> `
  --replace "dataset.py=<REPO_ROOT>\version\v080\dataset.py" `
  --replace "model.py=<REPO_ROOT>\version\v080\model.py" `
  --replace "ns_groups.json=<REPO_ROOT>\version\v080\ns_groups.json" `
  --replace "run.sh=<REPO_ROOT>\version\v080\run.sh" `
  --replace "train.py=<REPO_ROOT>\version\v080\train.py" `
  --replace "trainer.py=<REPO_ROOT>\version\v080\trainer.py" `
  --replace "utils.py=<REPO_ROOT>\version\v080\utils.py" `
  --start
```

注意：

- `<LOCAL_HEADERS_FILE>` 是只存在本地的私密 headers/cookie 文件。
- `<TRAIN_UPLOAD_PATH_SUFFIX>` 应来自平台当前比赛项目的训练上传路径，不要写入公开仓库。
- `<REPO_ROOT>` 换成当前仓库本地路径。

## 4. 评估任务提交模板

评估侧通常只替换 3 个文件：

- `dataset.py`
- `model.py`
- `infer.py`

脱敏命令示例：

```powershell
python .\scripts\taiji_eval.py `
  --headers-file <LOCAL_HEADERS_FILE> `
  --create `
  --model-name taac2026_v080 `
  --path-suffix <INFER_UPLOAD_PATH_SUFFIX> `
  --replace "dataset.py=<REPO_ROOT>\version\v080\dataset.py" `
  --replace "model.py=<REPO_ROOT>\version\v080\model.py" `
  --replace "infer.py=<REPO_ROOT>\version\v080\infer.py"
```

注意：

- 评估创建时只需要模型名称，不应把训练任务 ID 当作 long 类型 ID 填入。
- 如果平台无法自动推断上传路径，应显式传入 `<INFER_UPLOAD_PATH_SUFFIX>`。
- 不要提交真实模型 ID、评估 ID 或平台内部 instance id。

## 5. 常见问题

### 5.1 训练任务 ID 和平台内部 ID 混用

平台页面上展示的字符串任务名、内部数字 ID、instance id 不是同一种东西。自动化脚本中应明确区分：

- 创建任务：一般不需要用户手动填 job id。
- 查询任务列表：可以展示任务名、内部 ID、实例 ID。
- 轮询实例状态：使用平台返回的 instance id。

### 5.2 upload path suffix 无法推断

如果脚本报错类似：

```text
could not infer upload path suffix from existing files
```

说明当前模板文件分布在多个目录，脚本不能自动判断上传到哪个目录。解决方式是显式传入训练或推理对应的 path suffix。公开说明中只保留占位符，不写真实项目路径。

### 5.3 推理侧忘记替换 infer.py

训练任务通常不需要 `infer.py`，但评估任务必须替换：

- `dataset.py`
- `model.py`
- `infer.py`

如果只替换训练侧文件，评估阶段可能仍然使用模板 infer 逻辑，导致结构参数不一致或 fallback 配置错误。

## 6. 隐私与安全约束

不要提交以下内容：

- headers/cookie/token；
- 真实 task id、instance id、model id、eval id；
- 平台内部项目路径或个人项目编号；
- 本地用户目录、微信截图、临时日志；
- checkpoint、训练数据、评估输出文件。

推荐做法：

- 公开仓库只保留代码、脱敏 README、版本说明。
- 私密凭据放在本地 `scripts/tmp/`，并通过 `.gitignore` 排除。
- 提交前用关键词扫描：`cookie`、`headers`、`taskId`、`instanceId`、本地用户名、平台项目编号。
