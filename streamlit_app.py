"""
KoStock 성장주 스크리너 — Streamlit Cloud 대시보드
https://kostock.streamlit.app
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

# ── 페이지 설정 ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="KoStock 성장주 스크리너",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

BASE_DIR     = Path(__file__).parent
REPORTS_DIR  = BASE_DIR / "reports"
WATCHLIST    = BASE_DIR / "watchlist.json"

# ── 공통 스타일 ───────────────────────────────────────────────────────────────
st.markdown("""
<style>
  /* 전체 배경 */
  .stApp { background-color: #0d1117; color: #e6edf3; }
  section[data-testid="stSidebar"] { background: #161b22; }

  /* 헤더 여백 줄이기 */
  .block-container { padding-top: 1.5rem; }

  /* 카드 스타일 */
  .stock-card {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
    padding: 16px 18px;
    margin-bottom: 12px;
    transition: border-color .2s;
  }
  .stock-card:hover { border-color: #58a6ff; }

  /* 점수 배지 */
  .score-badge {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 20px;
    font-weight: 800;
    font-size: 14px;
  }
  .score-high  { background:#1a4731; color:#3fb950; }
  .score-mid   { background:#2d2208; color:#f0c040; }
  .score-low   { background:#2d0f0f; color:#f85149; }

  /* 조건 뱃지 */
  .badge-ok   { background:#1a4731; color:#3fb950; padding:2px 7px; border-radius:4px; font-size:11px; font-weight:600; margin:2px; display:inline-block; }
  .badge-warn { background:#2d2208; color:#e3b341; padding:2px 7px; border-radius:4px; font-size:11px; font-weight:600; margin:2px; display:inline-block; }

  /* 테이블 스타일 오버라이드 */
  .dataframe th { background:#1c2128 !important; color:#8b949e !important; }
  .dataframe td { background:#161b22 !important; color:#e6edf3 !important; }

  div[data-testid="metric-container"] {
    background:#161b22;
    border:1px solid #30363d;
    border-radius:8px;
    padding:12px 16px;
  }
</style>
""", unsafe_allow_html=True)

NAVER_HDR = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://finance.naver.com",
}


# ── 데이터 로더 ───────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_screening() -> tuple[pd.DataFrame, str]:
    csvs = sorted(REPORTS_DIR.glob("*_screening.csv"), reverse=True)
    if not csvs:
        return pd.DataFrame(), ""
    latest = csvs[0]
    date_raw = latest.stem.replace("_screening", "")
    date_fmt = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:]}" if len(date_raw) == 8 else date_raw
    df = pd.read_csv(latest, index_col="ticker", encoding="utf-8-sig")
    return df, date_fmt


@st.cache_data(ttl=60)
def load_indices() -> dict:
    try:
        r = requests.get("https://m.stock.naver.com/api/index/KOSPI/basic",
                         headers=NAVER_HDR, timeout=5)
        kospi = r.json()
        r2 = requests.get("https://m.stock.naver.com/api/index/KOSDAQ/basic",
                          headers=NAVER_HDR, timeout=5)
        kosdaq = r2.json()
        return {"KOSPI": kospi, "KOSDAQ": kosdaq}
    except Exception:
        return {}


@st.cache_data(ttl=60)
def load_watchlist_prices() -> list[dict]:
    import json
    if not WATCHLIST.exists():
        return []
    stocks = json.loads(WATCHLIST.read_text())["stocks"]
    result = []
    for s in stocks[:10]:
        try:
            r = requests.get(
                f"https://m.stock.naver.com/api/stock/{s['ticker']}/basic",
                headers=NAVER_HDR, timeout=5,
            )
            d = r.json()
            r2 = requests.get(
                f"https://m.stock.naver.com/api/stock/{s['ticker']}/price",
                headers=NAVER_HDR, timeout=5,
            )
            d2 = r2.json()
            result.append({
                "ticker":     s["ticker"],
                "name":       s.get("name", d.get("stockName", "")),
                "close":      float(str(d.get("closePrice","0")).replace(",","")),
                "change_pct": float(d.get("fluctuationsRatio", 0)),
                "volume":     int(str(d2.get("accumulatedTradingVolume","0")).replace(",","")),
            })
        except Exception:
            pass
    return result


@st.cache_data(ttl=3600)
def load_backtest_report() -> str:
    path = REPORTS_DIR / "backtest_2022_2024.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


# ── 유틸 ─────────────────────────────────────────────────────────────────────

def arrow(v: float) -> str:
    return "▲" if v > 0 else "▼" if v < 0 else "━"


def score_color(s: float) -> str:
    if s >= 70: return "score-high"
    if s >= 50: return "score-mid"
    return "score-low"


def fmt_price(v) -> str:
    try: return f"{float(v):,.0f}원"
    except: return "-"


# ═══════════════════════════════════════════════════════════════════════════════
# 탭 레이아웃
# ═══════════════════════════════════════════════════════════════════════════════

st.markdown("## 📈 KoStock 성장주 스크리너")

tab1, tab2, tab3 = st.tabs(["🔍 성장주 스크리닝", "📊 시장 현황", "🧪 백테스트 결과"])


# ══════════════════════════
# 탭 1: 스크리닝
# ══════════════════════════
with tab1:
    df, date_str = load_screening()

    if df.empty:
        st.info("스크리닝 결과가 없습니다. `run_screener.py`를 실행하세요.")
    else:
        # ── 헤더 ──────────────────────────────────────────────────────────────
        c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
        with c1:
            st.markdown(f"### 기준일: {date_str}")
        with c2:
            st.metric("통과 종목", f"{len(df)}개")
        with c3:
            st.metric("평균 점수", f"{df['score'].mean():.0f}점")
        with c4:
            st.metric("최고 점수", f"{df['score'].max():.0f}점")

        st.divider()

        # ── 요약 테이블 ───────────────────────────────────────────────────────
        with st.expander("📋 전체 요약 테이블", expanded=True):
            display_cols = {
                "name":              "종목명",
                "close":             "현재가",
                "change_pct":        "등락률",
                "per":               "PER",
                "pbr":               "PBR",
                "rsi":               "RSI",
                "momentum_20d":      "모멘텀20",
                "vol_ratio":         "거래량비",
                "pct_from_52w_high": "52W高比",
                "foreign_consec":    "외국인연속",
                "score":             "종합점수",
            }
            tbl = df[list(display_cols.keys())].copy()
            tbl.columns = list(display_cols.values())
            tbl["현재가"]   = tbl["현재가"].apply(lambda x: f"{x:,.0f}원")
            tbl["등락률"]   = tbl["등락률"].apply(lambda x: f"{arrow(x)}{abs(x):.1f}%")
            tbl["PER"]      = tbl["PER"].apply(lambda x: f"{x:.1f}")
            tbl["PBR"]      = tbl["PBR"].apply(lambda x: f"{x:.2f}")
            tbl["RSI"]      = tbl["RSI"].apply(lambda x: f"{x:.0f}")
            tbl["모멘텀20"] = tbl["모멘텀20"].apply(lambda x: f"{arrow(x)}{abs(x):.1f}%")
            tbl["거래량비"] = tbl["거래량비"].apply(lambda x: f"{x:.1f}x")
            tbl["52W高比"]  = tbl["52W高比"].apply(lambda x: f"{x:.1f}%")
            tbl["외국인연속"] = tbl["외국인연속"].apply(lambda x: f"{int(x)}일")
            tbl["종합점수"] = tbl["종합점수"].apply(lambda x: f"{x:.0f}점")
            st.dataframe(tbl, use_container_width=True, height=420)

        st.divider()

        # ── 종목 카드 ─────────────────────────────────────────────────────────
        st.markdown("#### 종목별 상세")

        for ticker, row in df.iterrows():
            name   = row.get("name", ticker)
            score  = float(row.get("score", 0))
            close  = float(row.get("close", 0))
            chg    = float(row.get("change_pct", 0))
            per    = float(row.get("per", 0))
            pbr    = float(row.get("pbr", 0))
            rsi    = float(row.get("rsi", 50))
            mom    = float(row.get("momentum_20d", 0))
            vol_r  = float(row.get("vol_ratio", 0))
            w52    = float(row.get("pct_from_52w_high", 0))
            consec = int(row.get("foreign_consec", 0))
            marcap = float(row.get("marcap", 0))
            inst   = bool(row.get("inst_turn", False))

            sc = score_color(score)
            with st.expander(
                f"**{name}** ({ticker})　　"
                f"{arrow(chg)}{abs(chg):.1f}%　　"
                f"{close:,.0f}원　　"
                f"{'🟢' if score>=70 else '🟡' if score>=50 else '🔴'} {score:.0f}점",
                expanded=False,
            ):
                # 지표 메트릭 (help= 파라미터로 툴팁)
                m1, m2, m3, m4, m5, m6 = st.columns(6)
                m1.metric(
                    "PER", f"{per:.1f}배",
                    help="**주가수익비율** (Price-to-Earnings)\n\n"
                         "주가 ÷ EPS(주당순이익)\n\n"
                         "📌 낮을수록 저평가\n"
                         "✅ 적정 범위: 5~40배\n"
                         f"현재: **{per:.1f}배**",
                )
                m2.metric(
                    "PBR", f"{pbr:.2f}배",
                    help="**주가순자산비율** (Price-to-Book)\n\n"
                         "주가 ÷ BPS(주당순자산)\n\n"
                         "📌 1 미만 = 청산가치 이하\n"
                         "✅ 적정 범위: 0.3~5배\n"
                         f"현재: **{pbr:.2f}배**",
                )
                m3.metric(
                    "RSI(14)", f"{rsi:.0f}",
                    help="**상대강도지수** (Relative Strength Index)\n\n"
                         "14일 기준 과매수·과매도 측정\n\n"
                         "🔴 70 이상: 과매수 → 조정 가능\n"
                         "🟢 30 이하: 과매도 → 반등 가능\n"
                         "⚪ 30~70: 중립 구간\n"
                         f"현재: **{rsi:.0f}**",
                )
                m4.metric(
                    "모멘텀(20일)", f"{arrow(mom)}{abs(mom):.1f}%",
                    help="**20일 가격 모멘텀**\n\n"
                         "20거래일 전 대비 등락률\n\n"
                         "📌 양수일수록 강한 상승 추세\n"
                         "✅ 스크리닝 기준: 양수\n"
                         f"현재: **{arrow(mom)}{abs(mom):.1f}%**",
                )
                m5.metric(
                    "거래량비", f"{vol_r:.1f}x",
                    help="**거래량 비율**\n\n"
                         "오늘 거래량 ÷ 20일 평균 거래량\n\n"
                         "📌 높을수록 강한 수급 신호\n"
                         "✅ 1.5배 이상: 거래량 급증\n"
                         "🚀 3배 이상: 강한 매집 신호\n"
                         f"현재: **{vol_r:.1f}배**",
                )
                m6.metric(
                    "52W 고가比", f"{w52:.1f}%",
                    help="**52주 신고가 대비 위치**\n\n"
                         "현재가가 52주 최고가에서 얼마나 떨어졌는지\n\n"
                         "📌 0%: 52주 신고가 갱신\n"
                         "✅ -20% 이내: 돌파 임박 구간\n"
                         f"현재: **{w52:.1f}%**",
                )

                # 추가 정보
                i1, i2 = st.columns(2)
                with i1:
                    st.markdown(f"**시가총액:** {marcap/1e12:.2f}조")
                    st.markdown(f"**외국인 연속 순매수:** {consec}일")
                with i2:
                    st.markdown(f"**기관 순매수 전환:** {'✅' if inst else '❌'}")

                # 조건 뱃지
                badges = []
                if row.get("golden_cross"):
                    badges.append('<span class="badge-ok">✅ 골든크로스</span>')
                else:
                    badges.append('<span class="badge-warn">❌ 골든크로스</span>')
                if row.get("near_52w_high"):
                    badges.append('<span class="badge-ok">✅ 52W 고가근접</span>')
                if row.get("volume_surge"):
                    badges.append('<span class="badge-ok">✅ 거래량 급증</span>')
                if consec >= 3:
                    badges.append(f'<span class="badge-ok">✅ 외국인 {consec}일 연속</span>')
                if inst:
                    badges.append('<span class="badge-ok">✅ 기관 전환</span>')

                st.markdown(" ".join(badges), unsafe_allow_html=True)


# ══════════════════════════
# 탭 2: 시장 현황
# ══════════════════════════
with tab2:
    st.markdown("### 주요 지수")

    with st.spinner("시장 데이터 로딩 중..."):
        indices = load_indices()

    if indices:
        ci1, ci2 = st.columns(2)
        for col, key, label in [(ci1, "KOSPI", "KOSPI"), (ci2, "KOSDAQ", "KOSDAQ")]:
            d = indices.get(key, {})
            close = float(str(d.get("closePrice", "0")).replace(",", ""))
            chg   = float(d.get("fluctuationsRatio", 0))
            with col:
                st.metric(
                    label,
                    f"{close:,.2f}",
                    delta=f"{arrow(chg)} {abs(chg):.2f}%",
                )

    st.divider()
    st.markdown("### 관심 종목")

    with st.spinner("관심종목 로딩 중..."):
        wl = load_watchlist_prices()

    if wl:
        wl_df = pd.DataFrame(wl)
        wl_df["등락률"] = wl_df["change_pct"].apply(lambda x: f"{arrow(x)}{abs(x):.2f}%")
        wl_df["현재가"] = wl_df["close"].apply(lambda x: f"{x:,.0f}원")
        wl_df["거래량"] = wl_df["volume"].apply(
            lambda x: f"{x/1e6:.1f}M" if x >= 1e6 else f"{x/1e3:.0f}K"
        )
        st.dataframe(
            wl_df[["name","ticker","현재가","등락률","거래량"]].rename(columns={"name":"종목","ticker":"코드"}),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("관심종목 데이터를 불러올 수 없습니다.")


# ══════════════════════════
# 탭 3: 백테스트
# ══════════════════════════
with tab3:
    st.markdown("### 백테스트 결과 (2022~2024)")

    # 이미지
    img_path = REPORTS_DIR / "backtest_2022_2024.png"
    if img_path.exists():
        st.image(str(img_path), use_container_width=True)
    else:
        st.info("백테스트 차트가 없습니다. `run_backtest.py`를 실행하세요.")

    # 리포트 텍스트
    report_md = load_backtest_report()
    if report_md:
        with st.expander("📄 상세 리포트", expanded=True):
            st.markdown(report_md)
