# 平台自动化提交脚本说明

本目录提供比赛期间使用的平台自动化脚本，用于减少手动上传、替换文件、创建训练任务、发布 checkpoint 和提交评估任务时的重复操作。

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

## 2. 脚本列表

```text
automation_submission/
├── README.md
└── scripts/
    ├── taiji_training.py
    ├── taiji_ckpt.py
    └── taiji_eval.py
```

三个脚本的职责：

| 脚本 | 用途 |
| --- | --- |
| `taiji_training.py` | 创建训练任务、上传并替换训练代码、启动训练、轮询实例状态、拉取训练日志 |
| `taiji_ckpt.py` | 查询训练任务产生的 checkpoint，并将指定 checkpoint 发布到模型管理 |
| `taiji_eval.py` | 按模型名称创建评估任务，上传并替换推理代码 |

## 3. 推荐的本地目录约定

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
└── automation_submission/
    └── scripts/
        ├── taiji_training.py
        ├── taiji_ckpt.py
        └── taiji_eval.py
```

平台登录态可以通过环境变量 `TAIJI_COOKIE` 传入，也可以通过 `--headers-file` 指向本地 headers 文件。headers 文件建议放在仓库外或加入 `.gitignore`。

## 4. 训练任务提交模板

训练侧通常需要替换 7 个文件：

- `dataset.py`
- `model.py`
- `ns_groups.json`
- `run.sh`
- `train.py`
- `trainer.py`
- `utils.py`

命令示例：

```powershell
python .\automation_submission\scripts\taiji_training.py `
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

- `<LOCAL_HEADERS_FILE>` 指向浏览器请求头文件，至少包含平台登录态。
- `<TRAIN_UPLOAD_PATH_SUFFIX>` 使用平台训练文件所在的上传目录。
- `<REPO_ROOT>` 换成当前仓库本地路径。

## 5. 训练状态与日志

查看训练任务列表：

```powershell
python .\automation_submission\scripts\taiji_training.py `
  --headers-file <LOCAL_HEADERS_FILE> `
  --list-tasks
```

轮询实例状态：

```powershell
python .\automation_submission\scripts\taiji_training.py `
  --headers-file <LOCAL_HEADERS_FILE> `
  --poll-instance-id <INSTANCE_ID> `
  --poll-count 0 `
  --poll-seconds 60
```

拉取训练日志：

```powershell
python .\automation_submission\scripts\taiji_training.py `
  --headers-file <LOCAL_HEADERS_FILE> `
  --poll-instance-id <INSTANCE_ID> `
  --poll-count 1 `
  --save-log-dir <LOCAL_LOG_DIR>
```

## 6. checkpoint 发布

列出模型或 checkpoint：

```powershell
python .\automation_submission\scripts\taiji_ckpt.py `
  --headers-file <LOCAL_HEADERS_FILE> `
  list-models --search taac2026_v080
```

```powershell
python .\automation_submission\scripts\taiji_ckpt.py `
  --headers-file <LOCAL_HEADERS_FILE> `
  list-ckpt --instance-id <INSTANCE_ID>
```

发布 checkpoint：

```powershell
python .\automation_submission\scripts\taiji_ckpt.py `
  --headers-file <LOCAL_HEADERS_FILE> `
  release-ckpt `
  --instance-id <INSTANCE_ID> `
  --ckpt <CHECKPOINT_NAME> `
  --name taac2026_v080 `
  --desc "final v080 checkpoint"
```

## 7. 评估任务提交模板

评估侧通常替换 3 个文件：

- `dataset.py`
- `model.py`
- `infer.py`

命令示例：

```powershell
python .\automation_submission\scripts\taiji_eval.py `
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

## 8. 常见问题

### 8.1 训练任务 ID 和平台内部 ID 混用

平台页面上展示的字符串任务名、内部数字 ID、instance id 不是同一种东西。自动化脚本中应明确区分：

- 创建任务：一般不需要用户手动填 job id。
- 查询任务列表：可以展示任务名、内部 ID、实例 ID。
- 轮询实例状态：使用平台返回的 instance id。

### 8.2 upload path suffix 无法推断

如果脚本报错类似：

```text
could not infer upload path suffix from existing files
```

说明当前模板文件分布在多个目录，脚本不能自动判断上传到哪个目录。解决方式是显式传入训练或推理对应的 path suffix。

### 8.3 推理侧忘记替换 infer.py

训练任务通常不需要 `infer.py`，但评估任务必须替换：

- `dataset.py`
- `model.py`
- `infer.py`

如果只替换训练侧文件，评估阶段可能仍然使用模板 infer 逻辑，导致结构参数不一致或 fallback 配置错误。
