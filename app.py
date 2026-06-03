"""
サッカーゲーム Eloランキングアプリ
=================================
14人でサッカーゲームの試合結果からEloレートを計算し、ランキングとレート変動を表示します。

使い方:
    streamlit run app.py

入力CSV (例: matches.csv) の形式:
    date,player_a,score_a,player_b,score_b
    2025-06-01,Taro,3,Jiro,1
    2025-06-01,Saburo,2,Shiro,2
    ...

選手アイコン:
    icons/ フォルダに「選手名.png」(または .jpg) を置くと自動で表示されます。
    例: icons/Taro.png
"""

import io
import os
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ─────────────────────────────────────────────
# 設定
# ─────────────────────────────────────────────
INITIAL_RATING = 1500      # 初期レート
K_FACTOR = 20              # K係数（変動の大きさ。大きいほど1試合の変動が大きい）
ICON_DIR = "icons"         # アイコン画像フォルダ
DEFAULT_CSV = "matches.csv"  # デフォルト読み込みCSV

st.set_page_config(page_title="⚽ Soccer Elo Ranking", page_icon="⚽", layout="wide")


# ─────────────────────────────────────────────
# Elo計算ロジック
# ─────────────────────────────────────────────
def expected_score(rating_a: float, rating_b: float) -> float:
    """AがBに勝つ期待値（0〜1）"""
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400))


def match_result(score_a: int, score_b: int) -> float:
    """試合結果をAの得点(1.0=勝ち, 0.5=引き分け, 0.0=負け)に変換"""
    if score_a > score_b:
        return 1.0
    if score_a < score_b:
        return 0.0
    return 0.5


def goal_diff_multiplier(score_a: int, score_b: int) -> float:
    """得点差によるK係数の補正。大差の試合ほどレート変動を大きくする"""
    diff = abs(score_a - score_b)
    if diff <= 1:
        return 1.0
    elif diff == 2:
        return 1.5
    else:
        return (11 + diff) / 8.0  # 公式戦でよく使われる補正式


def compute_elo(matches: pd.DataFrame):
    """
    試合履歴からEloレートを計算する。
    返り値:
      ratings: {選手名: 最終レート}
      history: レート変動履歴のDataFrame (試合ごとの各選手レート)
      stats:   {選手名: {games, wins, draws, losses, gf, ga}}
    """
    ratings = {}
    stats = {}
    history_rows = []

    def ensure_player(name):
        if name not in ratings:
            ratings[name] = INITIAL_RATING
            stats[name] = dict(games=0, wins=0, draws=0, losses=0, gf=0, ga=0)

    # 日付順に処理
    matches = matches.sort_values("date").reset_index(drop=True)

    for idx, row in matches.iterrows():
        a, b = str(row["player_a"]), str(row["player_b"])
        sa, sb = int(row["score_a"]), int(row["score_b"])
        is_pk = bool(int(row["pk"])) if "pk" in matches.columns and str(row.get("pk","")).strip() not in ("","nan") else False
        ensure_player(a)
        ensure_player(b)

        ra, rb = ratings[a], ratings[b]
        ea = expected_score(ra, rb)
        eb = 1.0 - ea

        # PK戦は「引き分け」としてElo計算（変動を小さく抑える）
        if is_pk:
            result_a = 0.5
            k = K_FACTOR
        else:
            result_a = match_result(sa, sb)
            k = K_FACTOR * goal_diff_multiplier(sa, sb)
        result_b = 1.0 - result_a

        ratings[a] = ra + k * (result_a - ea)
        ratings[b] = rb + k * (result_b - eb)

        # 戦績更新
        for name, gf, ga in ((a, sa, sb), (b, sb, sa)):
            stats[name]["games"] += 1
            stats[name]["gf"] += gf
            stats[name]["ga"] += ga
        if is_pk:
            # PK戦：pk_winner列で勝者を判定
            pk_winner = str(row.get("pk_winner", "")).strip() if "pk_winner" in matches.columns else ""
            if pk_winner == a:
                stats[a]["wins"] += 1
                stats[b]["losses"] += 1
            elif pk_winner == b:
                stats[b]["wins"] += 1
                stats[a]["losses"] += 1
            else:
                # pk_winnerが空欄や不明の場合は引き分け扱い
                stats[a]["draws"] += 1
                stats[b]["draws"] += 1
        elif sa > sb:
            stats[a]["wins"] += 1
            stats[b]["losses"] += 1
        elif sa < sb:
            stats[b]["wins"] += 1
            stats[a]["losses"] += 1
        else:
            stats[a]["draws"] += 1
            stats[b]["draws"] += 1

        # 履歴に全選手の現レートを記録
        snapshot = {"match_no": idx + 1, "date": row["date"]}
        snapshot.update({p: round(r, 1) for p, r in ratings.items()})
        history_rows.append(snapshot)

    history = pd.DataFrame(history_rows)
    return ratings, history, stats


# ─────────────────────────────────────────────
# アイコン取得
# ─────────────────────────────────────────────
def find_icon(player_name: str):
    """icons/フォルダから選手のアイコンパスを探す。なければNone"""
    if not os.path.isdir(ICON_DIR):
        return None
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        path = os.path.join(ICON_DIR, f"{player_name}{ext}")
        if os.path.exists(path):
            return path
    return None


# ─────────────────────────────────────────────
# データ読み込み
# ─────────────────────────────────────────────
def load_matches():
    """CSVアップロード or デフォルトファイルから試合データを読み込む"""
    st.sidebar.header("📂 データ入力")
    uploaded = st.sidebar.file_uploader(
        "試合結果CSVをアップロード", type=["csv"],
        help="列: date, player_a, score_a, player_b, score_b"
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
            st.error("CSVの文字コードを判定できませんでした。UTF-8で保存し直してください。")
            return None
    elif os.path.exists(DEFAULT_CSV):
        df = pd.read_csv(DEFAULT_CSV, encoding="utf-8-sig")
        st.sidebar.info(f"`{DEFAULT_CSV}` を読み込みました")
    else:
        st.sidebar.warning("CSVをアップロードしてください")
        return None

    # 列名チェック
    required = {"date", "player_a", "score_a", "player_b", "score_b"}
    if not required.issubset(df.columns):
        st.error(f"CSVに必要な列がありません。必要な列: {required}")
        st.write("読み込んだ列:", list(df.columns))
        return None

    return df


# ─────────────────────────────────────────────
# 表示: スコア一覧タブ
# ─────────────────────────────────────────────
def render_standings(ratings, stats):
    st.subheader("🏆 ランキング")

    # ランキング順にソート
    ranked = sorted(ratings.items(), key=lambda x: x[1], reverse=True)

    for rank, (name, rating) in enumerate(ranked, start=1):
        s = stats[name]
        col_rank, col_icon, col_name, col_rating, col_record = st.columns(
            [0.6, 0.8, 2, 1.2, 2]
        )

        # メダル表示
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"{rank}")
        col_rank.markdown(f"### {medal}")

        # アイコン
        icon = find_icon(name)
        if icon:
            col_icon.image(icon, width=50)
        else:
            col_icon.markdown("### 👤")

        col_name.markdown(f"### {name}")
        col_rating.metric("レート", f"{rating:.0f}")
        col_record.markdown(
            f"**{s['wins']}**勝 **{s['draws']}**分 **{s['losses']}**敗  \n"
            f"得点 {s['gf']} / 失点 {s['ga']} "
            f"(差 {s['gf'] - s['ga']:+d})"
        )
        st.divider()

    # 表形式でもダウンロード可能に
    table = pd.DataFrame([
        {
            "順位": i,
            "選手": name,
            "レート": round(rating, 1),
            "試合": stats[name]["games"],
            "勝": stats[name]["wins"],
            "分": stats[name]["draws"],
            "敗": stats[name]["losses"],
            "得点": stats[name]["gf"],
            "失点": stats[name]["ga"],
            "得失差": stats[name]["gf"] - stats[name]["ga"],
        }
        for i, (name, rating) in enumerate(ranked, start=1)
    ])
    st.download_button(
        "📥 ランキングをCSVでダウンロード",
        table.to_csv(index=False).encode("utf-8-sig"),
        file_name="ranking.csv",
        mime="text/csv",
    )


# ─────────────────────────────────────────────
# 表示: レート変動グラフタブ
# ─────────────────────────────────────────────
def render_chart(history, ratings):
    st.subheader("📈 レート変動")

    players = [c for c in history.columns if c not in ("match_no", "date")]

    # 表示する選手を選べるように
    selected = st.multiselect(
        "表示する選手を選択（未選択なら全員）",
        options=sorted(players),
        default=[],
    )
    show = selected if selected else players

    fig = go.Figure()
    # 最終レート順で凡例を並べる
    ordered = sorted(show, key=lambda p: ratings.get(p, 0), reverse=True)
    for name in ordered:
        # その選手が登場するまではNaN（線を引かない）
        y = history[name]
        fig.add_trace(go.Scatter(
            x=history["match_no"],
            y=y,
            mode="lines+markers",
            name=f"{name} ({ratings[name]:.0f})",
            connectgaps=False,
            marker=dict(size=4),
        ))

    fig.update_layout(
        xaxis_title="試合番号",
        yaxis_title="レート",
        hovermode="x unified",
        height=600,
        legend=dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.02),
    )
    fig.add_hline(y=INITIAL_RATING, line_dash="dot", line_color="gray",
                  annotation_text=f"初期値 {INITIAL_RATING}")
    st.plotly_chart(fig, use_container_width=True)


# ─────────────────────────────────────────────
# メイン
# ─────────────────────────────────────────────
def main():
    st.title("eFootball Ranking")
    st.caption("試合結果からレートを計算してランキング化します")

    matches = load_matches()
    if matches is None:
        st.info(
            "👈 サイドバーから試合結果のCSVをアップロードしてください。\n\n"
            "**CSV形式の例:**\n```\n"
            "date,player_a,score_a,player_b,score_b\n"
            "2025-06-01,Taro,3,Jiro,1\n"
            "2025-06-01,Saburo,2,Shiro,2\n```"
        )
        return

    ratings, history, stats = compute_elo(matches)

    tab1, tab2, tab3 = st.tabs(["🏆 スコア一覧", "📈 レート変動", "📋 試合履歴"])
    with tab1:
        render_standings(ratings, stats)
    with tab2:
        render_chart(history, ratings)
    with tab3:
        st.subheader("📋 試合履歴")
        st.dataframe(
            matches.sort_values("date").reset_index(drop=True),
            use_container_width=True,
        )


if __name__ == "__main__":
    main()
