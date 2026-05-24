#!/usr/bin/env python3
"""Generate leaderboard visualizations for team self-supervised."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch
from matplotlib.ticker import FuncFormatter
import numpy as np
import pandas as pd
import seaborn as sns


TEAM_NAME = "self-supervised"


def load_leaderboard(data_dir: Path) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for path in sorted(data_dir.glob("*.json")):
        if path.name == "leaderboard_tracker.py":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        date = pd.to_datetime(data.get("date") or path.stem)
        for team in data.get("teams", []):
            try:
                score = float(team["bestScore"])
            except (KeyError, TypeError, ValueError):
                continue
            rows.append(
                {
                    "date": date,
                    "rank": int(team["rank"]),
                    "team": str(team["teamName"]),
                    "score": score,
                    "delta": int(team.get("delta") or 0),
                    "best_time": team.get("bestScoreTime"),
                    "total": int(data.get("totalCount") or len(data.get("teams", []))),
                }
            )
    if not rows:
        raise RuntimeError(f"No leaderboard rows found in {data_dir}")
    return pd.DataFrame(rows).sort_values(["date", "rank"]).reset_index(drop=True)


def annotate_card(fig: plt.Figure, text: str, xy: tuple[float, float], width: float) -> None:
    x, y = xy
    box = FancyBboxPatch(
        (x, y),
        width,
        0.072,
        boxstyle="round,pad=0.012,rounding_size=0.015",
        transform=fig.transFigure,
        facecolor="#101828",
        edgecolor="#7dd3fc",
        linewidth=1.1,
        alpha=0.93,
    )
    fig.patches.append(box)
    fig.text(x + 0.012, y + 0.043, text.split("\n")[0], color="#e0f2fe", fontsize=11, weight="bold")
    if "\n" in text:
        fig.text(x + 0.012, y + 0.018, text.split("\n", 1)[1], color="#94a3b8", fontsize=8.5)


def configure_fonts() -> None:
    candidates = [
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\simsun.ttc"),
    ]
    for font_path in candidates:
        if font_path.exists():
            font_manager.fontManager.addfont(str(font_path))
            family = font_manager.FontProperties(fname=str(font_path)).get_name()
            plt.rcParams["font.family"] = family
            plt.rcParams["font.sans-serif"] = [family, "DejaVu Sans", "Arial"]
            break
    else:
        plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "Arial"]
    plt.rcParams["axes.unicode_minus"] = False


def make_visualization(df: pd.DataFrame, output_png: Path, output_svg: Path, team_name: str) -> dict[str, Any]:
    latest_date = df["date"].max()
    latest = df[df["date"] == latest_date].copy()
    team_hist = df[df["team"].str.lower() == team_name.lower()].copy()
    if team_hist.empty:
        raise RuntimeError(f"Team not found: {team_name}")
    team_latest = team_hist[team_hist["date"] == team_hist["date"].max()].iloc[-1]

    total = int(team_latest["total"])
    rank = int(team_latest["rank"])
    score = float(team_latest["score"])
    beat_percentile = 100.0 * (1.0 - (rank - 1) / total)
    top_percentile = 100.0 * rank / total
    leader_score = float(latest.iloc[0]["score"])
    gap_to_top = leader_score - score

    score_quantiles = latest["score"].quantile([0.5, 0.75, 0.9, 0.95, 0.99]).to_dict()
    better_count = int((latest["score"] > score).sum())
    top_teams = latest.nsmallest(14, "rank").copy()
    around = latest[(latest["rank"] >= max(1, rank - 3)) & (latest["rank"] <= min(total, rank + 3))].copy()

    sns.set_theme(style="whitegrid")
    configure_fonts()

    fig = plt.figure(figsize=(18, 12), dpi=180)
    fig.patch.set_facecolor("#07111f")
    gs = fig.add_gridspec(2, 2, left=0.08, right=0.965, top=0.735, bottom=0.10, hspace=0.30, wspace=0.18)

    title_color = "#f8fafc"
    sub_color = "#94a3b8"
    accent = "#22d3ee"
    highlight = "#f97316"
    grid_color = "#233044"

    fig.text(0.04, 0.955, "TAAC 2026 Leaderboard Snapshot", color=title_color, fontsize=25, weight="bold")
    fig.text(
        0.04,
        0.925,
        f"Team: {team_name} | final date: {latest_date.strftime('%Y-%m-%d')} | data source: axdyer/TAAC-2026-LeaderBoard",
        color=sub_color,
        fontsize=11,
    )
    annotate_card(fig, f"Rank #{rank}\nTop {top_percentile:.1f}% | beats {beat_percentile:.1f}%", (0.04, 0.825), 0.20)
    annotate_card(fig, f"AUC {score:.5f}\nGap to #1: {gap_to_top:.5f}", (0.27, 0.825), 0.20)
    annotate_card(fig, f"Better teams {better_count}\nScore >= P90: {score >= score_quantiles[0.9]}", (0.50, 0.825), 0.20)
    annotate_card(fig, f"Best time\n{team_latest['best_time']}", (0.73, 0.825), 0.22)

    ax_rank = fig.add_subplot(gs[0, 0])
    ax_rank.set_facecolor("#0b1628")
    ax_rank.plot(team_hist["date"], team_hist["rank"], color=accent, linewidth=3.0, marker="o", markersize=5)
    ax_rank.scatter([team_latest["date"]], [rank], s=210, color=highlight, edgecolor="white", linewidth=1.5, zorder=5)
    ax_rank.invert_yaxis()
    ax_rank.set_title("Rank trajectory", loc="left", color=title_color, fontsize=15, weight="bold")
    ax_rank.set_ylabel("Rank, lower is better", color=sub_color)
    ax_rank.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax_rank.tick_params(colors=sub_color)
    ax_rank.grid(color=grid_color, linewidth=0.8, alpha=0.8)
    ax_rank.annotate(
        f"#{rank}",
        xy=(team_latest["date"], rank),
        xytext=(10, -18),
        textcoords="offset points",
        color=highlight,
        fontsize=12,
        weight="bold",
        arrowprops=dict(arrowstyle="->", color=highlight),
    )

    ax_score = fig.add_subplot(gs[0, 1])
    ax_score.set_facecolor("#0b1628")
    ax_score.plot(team_hist["date"], team_hist["score"], color="#a78bfa", linewidth=3.0, marker="o", markersize=5)
    ax_score.fill_between(team_hist["date"], team_hist["score"], team_hist["score"].min(), color="#7c3aed", alpha=0.20)
    ax_score.scatter([team_latest["date"]], [score], s=210, color=highlight, edgecolor="white", linewidth=1.5, zorder=5)
    ax_score.set_title("Best score trajectory", loc="left", color=title_color, fontsize=15, weight="bold")
    ax_score.set_ylabel("AUC", color=sub_color)
    ax_score.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    ax_score.tick_params(colors=sub_color)
    ax_score.grid(color=grid_color, linewidth=0.8, alpha=0.8)
    ax_score.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.4f}"))

    ax_dist = fig.add_subplot(gs[1, 0])
    ax_dist.set_facecolor("#0b1628")
    scores = latest["score"].to_numpy()
    sns.histplot(scores, bins=55, kde=True, ax=ax_dist, color="#38bdf8", edgecolor="#0f172a", alpha=0.75)
    ax_dist.axvline(score, color=highlight, linewidth=3.0, label=f"{team_name}: {score:.5f}")
    for q, c in [(0.9, "#facc15"), (0.95, "#fb7185")]:
        ax_dist.axvline(score_quantiles[q], color=c, linestyle="--", linewidth=1.8, alpha=0.9, label=f"P{int(q*100)} {score_quantiles[q]:.5f}")
    ax_dist.set_title("Final score distribution", loc="left", color=title_color, fontsize=15, weight="bold")
    ax_dist.set_xlabel("AUC", color=sub_color)
    ax_dist.set_ylabel("Teams", color=sub_color)
    ax_dist.set_xlim(max(0.79, score_quantiles[0.5] - 0.02), min(0.84, leader_score + 0.002))
    ax_dist.tick_params(colors=sub_color)
    ax_dist.grid(color=grid_color, linewidth=0.8, alpha=0.8)
    leg = ax_dist.legend(facecolor="#111827", edgecolor="#334155", fontsize=9)
    for text in leg.get_texts():
        text.set_color("#e5e7eb")

    ax_bar = fig.add_subplot(gs[1, 1])
    ax_bar.set_facecolor("#0b1628")
    display = pd.concat([top_teams, around]).drop_duplicates("team").sort_values("rank").copy()
    display["label"] = display.apply(lambda r: f"#{int(r['rank'])} {r['team']}", axis=1)
    colors = [highlight if t.lower() == team_name.lower() else "#64748b" for t in display["team"]]
    ax_bar.barh(display["label"], display["score"], color=colors, edgecolor="#0f172a")
    ax_bar.axvline(score, color=highlight, linestyle="--", linewidth=1.8, alpha=0.85)
    ax_bar.set_title("Top teams + self-supervised neighborhood", loc="left", color=title_color, fontsize=15, weight="bold")
    ax_bar.set_xlabel("AUC", color=sub_color)
    ax_bar.tick_params(axis="x", colors=sub_color)
    ax_bar.tick_params(axis="y", colors="#dbeafe", labelsize=9)
    ax_bar.grid(axis="x", color=grid_color, linewidth=0.8, alpha=0.8)
    ax_bar.invert_yaxis()
    xmin = max(scores.min(), score - 0.004)
    xmax = max(leader_score + 0.0005, score + 0.004)
    ax_bar.set_xlim(xmin, xmax)

    for ax in [ax_rank, ax_score, ax_dist, ax_bar]:
        for spine in ax.spines.values():
            spine.set_color("#334155")

    fig.text(
        0.04,
        0.025,
        "Interpretation: score improvements were real but the leaderboard was dense; a ~0.011 AUC gap to #1 still placed the team in the top 20%.",
        color="#cbd5e1",
        fontsize=10,
    )
    fig.savefig(output_png, bbox_inches="tight", facecolor=fig.get_facecolor())
    fig.savefig(output_svg, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)

    return {
        "team": team_name,
        "date": latest_date.strftime("%Y-%m-%d"),
        "rank": rank,
        "total": total,
        "score": round(score, 5),
        "leader_score": round(leader_score, 5),
        "gap_to_top": round(gap_to_top, 5),
        "top_percentile": round(top_percentile, 2),
        "beat_percentile": round(beat_percentile, 2),
        "better_count": better_count,
        "best_time": team_latest["best_time"],
        "p50": round(float(score_quantiles[0.5]), 5),
        "p90": round(float(score_quantiles[0.9]), 5),
        "p95": round(float(score_quantiles[0.95]), 5),
        "p99": round(float(score_quantiles[0.99]), 5),
    }


def write_html(summary: dict[str, Any], output_html: Path, svg_name: str) -> None:
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>TAAC 2026 Leaderboard - {summary['team']}</title>
  <style>
    body {{ margin: 0; background: #06101f; color: #e5e7eb; font-family: Inter, "Microsoft YaHei", Arial, sans-serif; }}
    .wrap {{ max-width: 1280px; margin: 0 auto; padding: 32px 24px 42px; }}
    h1 {{ margin: 0 0 8px; font-size: 34px; letter-spacing: .2px; }}
    .sub {{ color: #94a3b8; margin-bottom: 22px; }}
    .cards {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin-bottom: 22px; }}
    .card {{ background: linear-gradient(135deg, #0f172a, #10243f); border: 1px solid #1e3a5f; border-radius: 16px; padding: 16px 18px; box-shadow: 0 18px 50px rgba(0,0,0,.25); }}
    .k {{ color: #7dd3fc; font-size: 13px; }}
    .v {{ font-size: 25px; font-weight: 800; margin-top: 5px; }}
    .viz {{ background: #07111f; border: 1px solid #1e293b; border-radius: 18px; padding: 10px; }}
    img {{ width: 100%; display: block; border-radius: 12px; }}
    .note {{ margin-top: 18px; color: #cbd5e1; line-height: 1.7; }}
  </style>
</head>
<body>
  <main class="wrap">
    <h1>TAAC 2026 Leaderboard Snapshot</h1>
    <div class="sub">Team <b>{summary['team']}</b>, final leaderboard date {summary['date']}</div>
    <section class="cards">
      <div class="card"><div class="k">Rank</div><div class="v">#{summary['rank']} / {summary['total']}</div></div>
      <div class="card"><div class="k">Best AUC</div><div class="v">{summary['score']:.5f}</div></div>
      <div class="card"><div class="k">Rank Band</div><div class="v">Top {summary['top_percentile']:.1f}%</div></div>
      <div class="card"><div class="k">Gap to #1</div><div class="v">{summary['gap_to_top']:.5f}</div></div>
    </section>
    <section class="viz"><img src="{svg_name}" alt="leaderboard visualization" /></section>
    <section class="note">
      self-supervised 最终分数为 {summary['score']:.5f}，排名 #{summary['rank']}，位于全部 {summary['total']} 支队伍的前 {summary['top_percentile']:.1f}%，超过 {summary['beat_percentile']:.1f}% 的队伍。
      最终榜首分数为 {summary['leader_score']:.5f}，差距 {summary['gap_to_top']:.5f}。图中同时展示了分数走势、排名走势、最终分布以及 Top 队伍附近的相对位置。
    </section>
  </main>
</body>
</html>
"""
    output_html.write_text(html, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("leaderboard_data"))
    parser.add_argument("--team", default=TEAM_NAME)
    parser.add_argument("--out-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    df = load_leaderboard(args.data_dir)
    png = args.out_dir / "self_supervised_leaderboard_dashboard.png"
    svg = args.out_dir / "self_supervised_leaderboard_dashboard.svg"
    html = args.out_dir / "self_supervised_leaderboard_dashboard.html"
    summary = make_visualization(df, png, svg, args.team)
    write_html(summary, html, svg.name)
    (args.out_dir / "self_supervised_leaderboard_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"png={png}")
    print(f"svg={svg}")
    print(f"html={html}")


if __name__ == "__main__":
    main()
