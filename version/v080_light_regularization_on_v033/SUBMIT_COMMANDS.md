# v080 提交命令

```powershell
D:\anaconda\anaconda\envs\pytorch\python.exe .\scripts\taiji_training.py `
  --headers-file .\scripts\tmp\taiji_headers.txt `
  --create `
  --new-job-name taac2026_v080_light_regularization_on_v033 `
  --new-job-desc "v067/v033 + light projector dropout regularization" `
  --host-gpu-num 1 `
  --path-suffix ams_2026_1029731869646209448/train `
  --replace "dataset.py=D:\contest\TAAC2026\version\v080_light_regularization_on_v033\dataset.py" `
  --replace "model.py=D:\contest\TAAC2026\version\v080_light_regularization_on_v033\model.py" `
  --replace "ns_groups.json=D:\contest\TAAC2026\version\v080_light_regularization_on_v033\ns_groups.json" `
  --replace "run.sh=D:\contest\TAAC2026\version\v080_light_regularization_on_v033\run.sh" `
  --replace "train.py=D:\contest\TAAC2026\version\v080_light_regularization_on_v033\train.py" `
  --replace "trainer.py=D:\contest\TAAC2026\version\v080_light_regularization_on_v033\trainer.py" `
  --replace "utils.py=D:\contest\TAAC2026\version\v080_light_regularization_on_v033\utils.py" `
  --start
```
