# v080 提交说明

训练侧需要替换 7 个文件：`dataset.py`、`model.py`、`ns_groups.json`、`run.sh`、`train.py`、`trainer.py`、`utils.py`。

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

更多自动化提交流程说明见仓库根目录的 `automation_submission/README.md`。
