# Leaderboard Visualization

This folder contains a visualization dashboard for the TAAC 2026 leaderboard team `self-supervised`.

Final snapshot:

- Date: `2026-05-23`
- Rank: `#364 / 1861`
- Best AUC: `0.82725`
- Rank band: top `19.56%`, above `80.49%` of teams

Generated artifacts:

- `self_supervised_leaderboard_dashboard.png`
- `self_supervised_leaderboard_dashboard.svg`
- `self_supervised_leaderboard_dashboard.html`
- `self_supervised_leaderboard_summary.json`

Regenerate:

```bash
python leaderboard_visualization/visualize_self_supervised.py --data-dir <TAAC-2026-LeaderBoard>/leaderboard_data
```
