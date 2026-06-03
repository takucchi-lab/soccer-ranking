"""
サッカーゲーム ポイントランキングアプリ
=================================
入力CSV の形式:
    date,tournament,round,player_a,score_a,player_b,score_b,pk,pk_winner
    2025-06-01,第1回,1回戦,Taro,3,Jiro,1,0,
    2025-06-15,第2回,1回戦,Saburo,1,Shiro,1,1,Saburo

選手アイコン:
    icons/ フォルダに「選手名.png」(.jpg/.jpeg/.webp) を置くと表示されます。

パスワード設定 (.streamlit/secrets.toml):
    ADMIN_PASSWORD = "yourpassword"
    GITHUB_CSV_URL = "https://raw.githubusercontent.com/yourname/soccer-ranking/main/matches.csv"
"""

import io
import os

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

# ──────────────────────────────────────────────
# 設定
# ──────────────────────────────────────────────
INITIAL_SCORE = 1000
ICON_DIR      = "icons"

DEFAULT_ROUNDS = {
    "1回戦":  {"win": 10, "pk_win":  7, "gd_bonus": 1},
    "2回戦":  {"win": 15, "pk_win": 12, "gd_bonus": 2},
    "準決勝": {"win": 20, "pk_win": 17, "gd_bonus": 3},
    "決勝":   {"win": 25, "pk_win": 22, "gd_bonus": 4},
}

st.set_page_config(page_title="⚽ Soccer Ranking", page_icon="⚽", layout="wide")


# ──────────────────────────────────────────────
# 管理者チェック
# ──────────────────────────────────────────────
def is_admin() -> bool:
    return st.session_state.get("authenticated", False)


def admin_login_widget():
    """サイドバーにパスワード入力欄を表示。認証済みなら管理者バッジを表示。"""
    if is_admin():
        st.sidebar.success("🔑 管理者モード")
        if st.sidebar.button("ログアウト"):
            st.session_state["authenticated"] = False
            st.rerun()
        return

    st.sidebar.markdown("🔒 **管理者ログイン**")
    pw = st.sidebar.text_input("パスワード", type="password", key="pw_input")
    if st.sidebar.button("ログイン"):
        correct = st.secrets.get("ADMIN_PASSWORD", "admin")
        if pw == correct:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.sidebar.error("パスワードが違います")


# ──────────────────────────────────────────────
# CSV読み込み
# ──────────────────────────────────────────────
def read_csv_bytes(raw: bytes) -> pd.DataFrame | None:
    for enc in ("utf-8-sig", "utf-8", "cp932", "shift-jis"):
        try:
            return pd.read_csv(io.BytesIO(raw), encoding=enc)
        except Exception:
            continue
    return None


def load_matches() -> pd.DataFrame | None:
    st.sidebar.markdown("---")
    st.sidebar.header("📂 データ")

    df = None

    if is_admin():
        # 管理者：アップロードUI表示
        uploaded = st.sidebar.file_uploader(
            "CSVをアップロード", type=["csv"],
            help="date,tournament,round,player_a,score_a,player_b,score_b,pk,pk_winner"
        )
        if uploaded:
            df = read_csv_bytes(uploaded.read())
            if df is None:
                st.error("文字コードを判定できませんでした。UTF-8で保存し直してください。")
                return None
            st.sidebar.success("アップロードしたCSVを表示中")
    
    # 管理者でアップロードなし、または一般ユーザー → GitHubから自動取得
    if df is None:
        github_url = st.secrets.get(
            "GITHUB_CSV_URL",
            "https://raw.githubusercontent.com/yourname/soccer-ranking/main/matches.csv"
        )
        try:
            resp = requests.get(github_url, timeout=10)
            resp.raise_for_status()
            df = read_csv_bytes(resp.content)
            if df is None:
                st.error("GitHubのCSVを読み込めませんでした。")
                return None
            st.sidebar.info("📡 GitHubから最新データを読み込みました")
        except Exception as e:
            st.error(f"GitHubからのCSV取得に失敗しました: {e}")
            return None

    required = {"date", "tournament", "round", "player_a", "score_a", "player_b", "score_b"}
    if not required.issubset(df.columns):
        st.error(f"必要な列が不足しています。\n必要: {required}\n実際: {list(df.columns)}")
        return None
    return df


# ──────────────────────────────────────────────
# ポイント計算ロジック
# ──────────────────────────────────────────────
def opponent_multiplier(my_score: float, opp_score: float) -> float:
    """相手レートによる補正係数（±20%以内）"""
    return 1.0 + 0.2 * (opp_score - my_score) / 1000.0


def compute_points(matches: pd.DataFrame, round_config: dict, seeds_by_tournament: dict):
    """
    seeds_by_tournament: {"第1回": ["Taro", "Jiro"], "第2回": ["Shiro"], ...}
    """
    scores       = {}
    stats        = {}
    history_rows = []

    def ensure(name):
        if name not in scores:
            scores[name] = INITIAL_SCORE
            stats[name]  = dict(games=0, wins=0, pk_wins=0, draws=0, losses=0,
                                gf=0, ga=0, seed_bonus_total=0)

    matches = matches.sort_values(["date", "tournament"]).reset_index(drop=True)

    # 大会ごとにシードボーナスを付与したか追跡
    seed_bonus_applied = {}   # {tournament_name: bool}

    for idx, row in matches.iterrows():
        a          = str(row["player_a"])
        b          = str(row["player_b"])
        sa         = int(row["score_a"])
        sb         = int(row["score_b"])
        round_name = str(row["round"]).strip()
        tournament = str(row["tournament"]).strip()
        is_pk      = bool(int(row["pk"])) if "pk" in matches.columns and str(row.get("pk","")).strip() not in ("","nan") else False
        pk_winner  = str(row.get("pk_winner","")).strip() if "pk_winner" in matches.columns else ""

        ensure(a); ensure(b)

        # この大会のシード選手
        seed_players = seeds_by_tournament.get(tournament, [])

        # シード選手を先に登録
        for p in seed_players:
            ensure(p)

        # 1回戦以外が始まる直前にシードボーナス付与（大会ごと）
        if tournament not in seed_bonus_applied and round_name != "1回戦" and seed_players:
            first_round = matches[(matches["tournament"] == tournament) & (matches["round"] == "1回戦")]
            if len(first_round) > 0:
                winners = []
                for _, fr in first_round.iterrows():
                    fa, fb   = str(fr["player_a"]), str(fr["player_b"])
                    fsa, fsb = int(fr["score_a"]), int(fr["score_b"])
                    fpk      = bool(int(fr["pk"])) if "pk" in first_round.columns and str(fr.get("pk","")).strip() not in ("","nan") else False
                    fpk_w    = str(fr.get("pk_winner","")).strip() if "pk_winner" in first_round.columns else ""
                    if fpk:
                        winners.append(fpk_w if fpk_w else fa)
                    elif fsa > fsb:
                        winners.append(fa)
                    elif fsb > fsa:
                        winners.append(fb)

                gains    = [scores[p] - INITIAL_SCORE for p in winners if p in scores]
                avg_gain = sum(gains) / len(gains) if gains else 0

                for p in seed_players:
                    scores[p]                    += avg_gain
                    stats[p]["seed_bonus_total"] += round(avg_gain, 1)
                    stats[p]["wins"]             += 1
                    stats[p]["games"]            += 1

            seed_bonus_applied[tournament] = True

        cfg       = round_config.get(round_name, {"win": 10, "pk_win": 7, "gd_bonus": 1})
        win_pt    = cfg["win"]
        pk_win_pt = cfg["pk_win"]
        gd_bonus  = cfg["gd_bonus"]
        gd_a      = sa - sb
        gd_b      = sb - sa
        mult_a    = opponent_multiplier(scores[a], scores[b])
        mult_b    = opponent_multiplier(scores[b], scores[a])

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

        snap = {"match_no": idx + 1, "date": str(row["date"]),
                "tournament": tournament, "round": round_name}
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
# サイドバー：シード・配点（管理者のみ）
# ──────────────────────────────────────────────
def build_seeds_by_tournament(matches: pd.DataFrame) -> dict:
    st.sidebar.markdown("---")
    st.sidebar.header("🌟 シード選手設定")

    tournaments = sorted(matches["tournament"].unique()) if matches is not None else []
    seeds = {}

    if not is_admin():
        st.sidebar.caption("（管理者のみ変更可）")
        # 一般ユーザーには空dict返す（シードなし扱い）
        return seeds

    for t in tournaments:
        raw = st.sidebar.text_input(
            f"{t} のシード選手",
            key=f"seed_{t}",
            help="カンマ区切りで複数可。例: Taro, Jiro"
        )
        seeds[t] = [s.strip() for s in raw.split(",") if s.strip()] if raw else []

    return seeds


def build_round_config(matches: pd.DataFrame) -> dict:
    st.sidebar.markdown("---")
    st.sidebar.header("⚙️ 配点設定")

    csv_rounds = list(matches["round"].unique()) if matches is not None else []
    all_rounds = list(DEFAULT_ROUNDS.keys())
    for r in csv_rounds:
        if r not in all_rounds:
            all_rounds.append(r)

    config = {}
    for rname in all_rounds:
        default = DEFAULT_ROUNDS.get(rname, {"win": 10, "pk_win": 7, "gd_bonus": 1})
        if is_admin():
            st.sidebar.markdown(f"**📋 {rname}**")
            w = st.sidebar.number_input("通常勝ち (pt)",          value=default["win"],      key=f"{rname}_win", step=1)
            p = st.sidebar.number_input("PK勝ち (pt)",            value=default["pk_win"],   key=f"{rname}_pk",  step=1)
            g = st.sidebar.number_input("得失点差ボーナス (pt/点)", value=default["gd_bonus"], key=f"{rname}_gd",  step=1)
            config[rname] = {"win": w, "pk_win": p, "gd_bonus": g}
        else:
            config[rname] = default

    if not is_admin():
        st.sidebar.caption("（管理者のみ変更可）")

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
        col_icon.image(icon, width=50) if icon else col_icon.markdown("### 👤")

        col_name.markdown(f"### {name}")
        delta = score - INITIAL_SCORE
        col_score.metric("ポイント", f"{score:.0f}", delta=f"{delta:+.0f}")

        pk_str   = f"（うちPK {s['pk_wins']}）" if s["pk_wins"] > 0 else ""
        seed_str = f"　🌟累計ボーナス {s['seed_bonus_total']:+.1f}pt" if s["seed_bonus_total"] != 0 else ""
        col_record.markdown(
            f"**{s['wins']}**勝{pk_str} **{s['draws']}**分 **{s['losses']}**敗  \n"
            f"得点 {s['gf']} / 失点 {s['ga']}　得失差 {s['gf']-s['ga']:+d}{seed_str}"
        )
        st.divider()

    table = pd.DataFrame([
        {"順位": i, "選手": n, "ポイント": round(s, 1),
         "初期値との差": round(s - INITIAL_SCORE, 1),
         "試合": stats[n]["games"], "勝": stats[n]["wins"],
         "PKでの勝ち": stats[n]["pk_wins"], "分": stats[n]["draws"], "敗": stats[n]["losses"],
         "得点": stats[n]["gf"], "失点": stats[n]["ga"],
         "得失差": stats[n]["gf"] - stats[n]["ga"],
         "シードボーナス計": stats[n]["seed_bonus_total"]}
        for i, (n, s) in enumerate(ranked, 1)
    ])
    st.download_button(
        "📥 ランキングCSVダウンロード",
        table.to_csv(index=False).encode("utf-8-sig"),
        file_name="ranking.csv", mime="text/csv",
    )


# ──────────────────────────────────────────────
# タブ2：ポイント変動グラフ
# ──────────────────────────────────────────────
def render_chart(history, scores):
    st.subheader("📈 ポイント変動")
    players  = [c for c in history.columns if c not in ("match_no", "date", "tournament", "round")]
    selected = st.multiselect("表示する選手（未選択なら全員）", sorted(players), default=[])
    show     = selected if selected else players
    ordered  = sorted(show, key=lambda p: scores.get(p, 0), reverse=True)

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

    admin_login_widget()
    matches      = load_matches()
    seeds        = build_seeds_by_tournament(matches if matches is not None else pd.DataFrame(columns=["tournament"]))
    round_config = build_round_config(matches if matches is not None else pd.DataFrame(columns=["round"]))

    if matches is None:
        st.info(
            "データを読み込めませんでした。\n\n"
            "**CSV形式の例:**\n```\n"
            "date,tournament,round,player_a,score_a,player_b,score_b,pk,pk_winner\n"
            "2025-06-01,第1回,1回戦,Taro,3,Jiro,1,0,\n"
            "2025-06-15,第2回,準決勝,Saburo,1,Shiro,1,1,Saburo\n```"
        )
        return

    scores, history, stats = compute_points(matches, round_config, seeds)

    tab1, tab2, tab3 = st.tabs(["🏆 ランキング", "📈 ポイント変動", "📋 試合履歴"])
    with tab1:
        render_standings(scores, stats)
    with tab2:
        render_chart(history, scores)
    with tab3:
        st.subheader("📋 試合履歴")
        st.dataframe(matches.sort_values(["date","tournament"]).reset_index(drop=True),
                     use_container_width=True)


if __name__ == "__main__":
    main()
