"""
サッカーゲーム ポイントランキングアプリ
=================================
フェーズ（ラウンド）ごとの勝敗＋得失点差でポイントを計算します。

入力CSV の形式:
    date,round,player_a,score_a,player_b,score_b,pk,pk_winner
    2025-06-01,1回戦,Taro,3,Jiro,1,0,
    2025-06-01,準決勝,Saburo,1,Shiro,1,1,Saburo

    round:      1回戦 / 2回戦 / 準決勝 / 決勝  （設定画面で自由に追加可）
    pk:         PK戦あり=1, なし=0
    pk_winner:  PK勝者の選手名（pk=0なら空欄）

シード選手:
    サイドバーの「シード選手」欄に名前を入力（カンマ区切りで複数可）。
    1回戦終了後に1回戦参加者の平均獲得ポイントが自動付与されます。

選手アイコン:
    icons/ フォルダに「選手名.png」(.jpg/.jpeg/.webp も可) を置くと表示されます。
"""

import io
import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ──────────────────────────────────────────────
# デフォルト設定
# ──────────────────────────────────────────────
INITIAL_SCORE   = 1000   # 全員の初期ポイント
ICON_DIR        = "icons"
DEFAULT_CSV     = "matches.csv"

DEFAULT_ROUNDS = {
    "1回戦":  {"win": 10, "pk_win":  7, "gd_bonus": 1},
    "2回戦":  {"win": 15, "pk_win": 12, "gd_bonus": 2},
    "準決勝": {"win": 20, "pk_win": 17, "gd_bonus": 3},
    "決勝":   {"win": 25, "pk_win": 22, "gd_bonus": 4},
}

st.set_page_config(page_title="⚽ Soccer Ranking", page_icon="⚽", layout="wide")


# ──────────────────────────────────────────────
# ポイント計算ロジック
# ──────────────────────────────────────────────
def opponent_multiplier(my_score: float, opp_score: float) -> float:
    """相手レートに基づく補正係数（±20%以内）
    強い相手に勝つほど係数>1、弱い相手には<1"""
    diff = opp_score - my_score   # 正=相手が格上
    return 1.0 + 0.2 * diff / 1000.0


def compute_points(matches: pd.DataFrame, round_config: dict, seed_players: list):
    scores = {}   # {選手名: 累計ポイント}
    stats  = {}   # {選手名: {games, wins, pk_wins, draws, losses, gf, ga, seed, seed_bonus}}
    history_rows = []
    seed_bonus_applied = False
    SEED_ROUND = "1回戦"

    def ensure(name):
        if name not in scores:
            scores[name] = INITIAL_SCORE
            stats[name]  = dict(games=0, wins=0, pk_wins=0, draws=0, losses=0,
                                gf=0, ga=0, seed=name in seed_players, seed_bonus=0)

    for p in seed_players:
        ensure(p)

    matches = matches.sort_values("date").reset_index(drop=True)

    for idx, row in matches.iterrows():
        a          = str(row["player_a"])
        b          = str(row["player_b"])
        sa         = int(row["score_a"])
        sb         = int(row["score_b"])
        round_name = str(row["round"]).strip()
        is_pk      = bool(int(row["pk"])) if "pk" in matches.columns and str(row.get("pk","")).strip() not in ("","nan") else False
        pk_winner  = str(row.get("pk_winner","")).strip() if "pk_winner" in matches.columns else ""

        ensure(a); ensure(b)

        # 1回戦以外の試合が始まる直前にシードボーナスを付与（1回戦勝ち扱い）
        if not seed_bonus_applied and round_name != SEED_ROUND and seed_players:
            first_round = matches[matches["round"] == SEED_ROUND]
            if len(first_round) > 0:
                fr_players = set(
                    first_round["player_a"].astype(str).tolist() +
                    first_round["player_b"].astype(str).tolist()
                )
                # 1回戦勝者の獲得ポイント平均を計算
                winners = []
                for _, fr_row in first_round.iterrows():
                    fa, fb = str(fr_row["player_a"]), str(fr_row["player_b"])
                    fsa, fsb = int(fr_row["score_a"]), int(fr_row["score_b"])
                    fpk = bool(int(fr_row["pk"])) if "pk" in first_round.columns and str(fr_row.get("pk","")).strip() not in ("","nan") else False
                    fpk_w = str(fr_row.get("pk_winner","")).strip() if "pk_winner" in first_round.columns else ""
                    if fpk:
                        winners.append(fpk_w if fpk_w else fa)
                    elif fsa > fsb:
                        winners.append(fa)
                    elif fsb > fsa:
                        winners.append(fb)
                winner_gains = [scores[p] - INITIAL_SCORE for p in winners if p in scores]
                avg_gain = sum(winner_gains) / len(winner_gains) if winner_gains else 0
                for p in seed_players:
                    scores[p] += avg_gain
                    stats[p]["seed_bonus"] = round(avg_gain, 1)
                    stats[p]["wins"]  += 1   # 1回戦勝ち扱い
                    stats[p]["games"] += 1
            seed_bonus_applied = True

        cfg       = round_config.get(round_name, {"win": 10, "pk_win": 6, "gd_bonus": 2})
        win_pt    = cfg["win"]
        pk_win_pt = cfg["pk_win"]
        gd_bonus  = cfg["gd_bonus"]

        gd_a = sa - sb
        gd_b = sb - sa

        # 相手レート補正（勝利ポイント・PK勝利ポイントのみ適用）
        mult_a = opponent_multiplier(scores[a], scores[b])
        mult_b = opponent_multiplier(scores[b], scores[a])

        if is_pk:
            scores[a] += gd_a * gd_bonus
            scores[b] += gd_b * gd_bonus
            if pk_winner == a:
                scores[a] += pk_win_pt * mult_a
                stats[a]["wins"]    += 1
                stats[a]["pk_wins"] += 1
                stats[b]["losses"]  += 1
            elif pk_winner == b:
                scores[b] += pk_win_pt * mult_b
                stats[b]["wins"]    += 1
                stats[b]["pk_wins"] += 1
                stats[a]["losses"]  += 1
            else:
                stats[a]["draws"] += 1
                stats[b]["draws"] += 1
        elif sa > sb:
            scores[a] += win_pt * mult_a + gd_a * gd_bonus
            scores[b] += gd_b * gd_bonus
            stats[a]["wins"]   += 1
            stats[b]["losses"] += 1
        elif sa < sb:
            scores[b] += win_pt * mult_b + gd_b * gd_bonus
            scores[a] += gd_a * gd_bonus
            stats[b]["wins"]   += 1
            stats[a]["losses"] += 1
        else:
            scores[a] += gd_a * gd_bonus
            scores[b] += gd_b * gd_bonus
            stats[a]["draws"] += 1
            stats[b]["draws"] += 1

        for name, gf, ga in ((a, sa, sb), (b, sb, sa)):
            stats[name]["games"] += 1
            stats[name]["gf"]    += gf
            stats[name]["ga"]    += ga

        snap = {"match_no": idx + 1, "date": str(row["date"]), "round": round_name}
        snap.update({p: round(s, 1) for p, s in scores.items()})
        history_rows.append(snap)

    return scores, pd.DataFrame(history_rows), stats



# ──────────────────────────────────────────────
# アイコン取得
# ──────────────────────────────────────────────
def find_icon(name):
    if not os.path.isdir(ICON_DIR):
        return None
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        path = os.path.join(ICON_DIR, f"{name}{ext}")
        if os.path.exists(path):
            return path
    return None


# ──────────────────────────────────────────────
# CSV読み込み
# ──────────────────────────────────────────────
def load_matches():
    st.sidebar.header("📂 データ入力")
    uploaded = st.sidebar.file_uploader(
        "試合結果CSVをアップロード", type=["csv"],
        help="列: date, round, player_a, score_a, player_b, score_b, pk, pk_winner"
    )
    if uploaded is not None:
        raw = uploaded.read()
        for enc in ("utf-8-sig", "utf-8", "cp932", "shift-jis"):
            try:
                df = pd.read_csv(io.BytesIO(raw), encoding=enc)
                break
            except Exception:
                continue
        else:
            st.error("文字コードを判定できませんでした。UTF-8で保存し直してください。")
            return None
    elif os.path.exists(DEFAULT_CSV):
        df = pd.read_csv(DEFAULT_CSV, encoding="utf-8-sig")
        st.sidebar.info(f"`{DEFAULT_CSV}` を読み込みました")
    else:
        st.sidebar.warning("CSVをアップロードしてください")
        return None

    required = {"date", "round", "player_a", "score_a", "player_b", "score_b"}
    if not required.issubset(df.columns):
        st.error(f"CSVに必要な列がありません。必要: {required}\n実際: {list(df.columns)}")
        return None
    return df


# ──────────────────────────────────────────────
# サイドバー：配点設定
# ──────────────────────────────────────────────
def build_seed_players():
    st.sidebar.markdown("---")
    st.sidebar.header("🌟 シード選手")
    seed_input = st.sidebar.text_input(
        "シード選手名（カンマ区切り）",
        help="1回戦をスキップする選手。複数いる場合はカンマで区切ってください。例: Taro, Jiro"
    )
    seeds = [s.strip() for s in seed_input.split(",") if s.strip()] if seed_input else []
    if seeds:
        st.sidebar.info(f"シード: {', '.join(seeds)}")
    return seeds


def build_round_config(matches):
    st.sidebar.markdown("---")
    st.sidebar.header("⚙️ 配点設定")

    # CSVに登場するラウンド名を自動検出 → デフォルト設定とマージ
    csv_rounds = list(matches["round"].unique()) if matches is not None else []
    all_rounds = list(DEFAULT_ROUNDS.keys())
    for r in csv_rounds:
        if r not in all_rounds:
            all_rounds.append(r)

    config = {}
    for rname in all_rounds:
        default = DEFAULT_ROUNDS.get(rname, {"win": 10, "pk_win": 6, "gd_bonus": 2})
        with st.sidebar.expander(f"📋 {rname}"):
            w  = st.number_input("通常勝ち (pt)",   value=default["win"],      key=f"{rname}_win",  step=1)
            p  = st.number_input("PK勝ち (pt)",     value=default["pk_win"],   key=f"{rname}_pk",   step=1)
            g  = st.number_input("得失点差ボーナス (pt/点)", value=default["gd_bonus"], key=f"{rname}_gd",   step=1)
        config[rname] = {"win": w, "pk_win": p, "gd_bonus": g}
    return config


# ──────────────────────────────────────────────
# タブ1：ランキング
# ──────────────────────────────────────────────
def render_standings(scores, stats):
    st.subheader("🏆 ランキング")
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    for rank, (name, score) in enumerate(ranked, 1):
        s = stats[name]
        col_rank, col_icon, col_name, col_score, col_record = st.columns([0.6, 0.8, 2, 1.5, 2.5])
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"{rank}")
        col_rank.markdown(f"### {medal}")

        icon = find_icon(name)
        if icon:
            col_icon.image(icon, width=50)
        else:
            col_icon.markdown("### 👤")

        col_name.markdown(f"### {name}")
        delta = score - INITIAL_SCORE
        col_score.metric("ポイント", f"{score:.0f}", delta=f"{delta:+.0f}")

        pk_str   = f"（うちPK {s['pk_wins']}）" if s["pk_wins"] > 0 else ""
        seed_str = f"　🌟シード（ボーナス {s['seed_bonus']:+.1f}pt）" if s.get("seed") else ""
        col_record.markdown(
            f"**{s['wins']}**勝{pk_str} **{s['draws']}**分 **{s['losses']}**敗  \n"
            f"得点 {s['gf']} / 失点 {s['ga']}　得失差 {s['gf']-s['ga']:+d}{seed_str}"
        )
        st.divider()

    table = pd.DataFrame([
        {"順位": i, "選手": n, "ポイント": round(s, 1),
         "初期値との差": round(s - INITIAL_SCORE, 1),
         "試合": stats[n]["games"], "勝": stats[n]["wins"],
         "PKでの勝ち": stats[n]["pk_wins"],
         "分": stats[n]["draws"], "敗": stats[n]["losses"],
         "得点": stats[n]["gf"], "失点": stats[n]["ga"],
         "得失差": stats[n]["gf"] - stats[n]["ga"]}
        for i, (n, s) in enumerate(ranked, 1)
    ])
    st.download_button(
        "📥 ランキングをCSVでダウンロード",
        table.to_csv(index=False).encode("utf-8-sig"),
        file_name="ranking.csv", mime="text/csv",
    )


# ──────────────────────────────────────────────
# タブ2：ポイント変動グラフ
# ──────────────────────────────────────────────
def render_chart(history, scores):
    st.subheader("📈 ポイント変動")
    players = [c for c in history.columns if c not in ("match_no", "date", "round")]
    selected = st.multiselect("表示する選手（未選択なら全員）", sorted(players), default=[])
    show = selected if selected else players
    ordered = sorted(show, key=lambda p: scores.get(p, 0), reverse=True)

    fig = go.Figure()
    for name in ordered:
        fig.add_trace(go.Scatter(
            x=history["match_no"], y=history[name],
            mode="lines+markers",
            name=f"{name} ({scores[name]:.0f}pt)",
            connectgaps=False, marker=dict(size=5),
        ))
    fig.add_hline(y=INITIAL_SCORE, line_dash="dot", line_color="gray",
                  annotation_text=f"初期値 {INITIAL_SCORE}pt")
    fig.update_layout(
        xaxis_title="試合番号", yaxis_title="ポイント",
        hovermode="x unified", height=600,
    )
    st.plotly_chart(fig, use_container_width=True)


# ──────────────────────────────────────────────
# メイン
# ──────────────────────────────────────────────
def main():
    st.title("⚽ Soccer Game Ranking")
    st.caption("ラウンド×勝敗×得失点差でポイントを計算します")

    matches = load_matches()
    seed_players = build_seed_players()
    round_config = build_round_config(matches)

    if matches is None:
        st.info(
            "👈 サイドバーからCSVをアップロードしてください。\n\n"
            "**CSV形式の例:**\n```\n"
            "date,round,player_a,score_a,player_b,score_b,pk,pk_winner\n"
            "2025-06-01,1回戦,Taro,3,Jiro,1,0,\n"
            "2025-06-01,準決勝,Saburo,1,Shiro,1,1,Saburo\n```"
        )
        return

    scores, history, stats = compute_points(matches, round_config, seed_players)

    tab1, tab2, tab3 = st.tabs(["🏆 ランキング", "📈 ポイント変動", "📋 試合履歴"])
    with tab1:
        render_standings(scores, stats)
    with tab2:
        render_chart(history, scores)
    with tab3:
        st.subheader("📋 試合履歴")
        st.dataframe(matches.sort_values("date").reset_index(drop=True), use_container_width=True)


if __name__ == "__main__":
    main()
