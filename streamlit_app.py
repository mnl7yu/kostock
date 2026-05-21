"""
KoStock 성장주 스크리너 — 모바일 최적화 Streamlit 대시보드
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="KoStock 📈",
    page_icon="📈",
    layout="centered",          # 모바일에서 centered가 더 좋음
    initial_sidebar_state="collapsed",
)

BASE_DIR    = Path(__file__).parent
REPORTS_DIR = BASE_DIR / "reports"
WATCHLIST   = BASE_DIR / "watchlist.json"

NAVER_HDR = {
    "User-Agent":      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "Referer":         "https://finance.naver.com",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

# ── CSS (모바일 우선) ─────────────────────────────────────────────────────────
st.markdown("""
<style>
  /* Streamlit 기본 헤더/여백 제거 */
  #MainMenu, header, footer { visibility: hidden; height: 0; }
  .block-container { padding: 0.8rem 1rem 2rem !important; max-width: 640px; }

  /* 전체 배경 */
  .stApp { background: #0d1117; color: #e6edf3; }

  /* 탭 */
  .stTabs [data-baseweb="tab-list"] {
    background: #161b22; border-radius: 10px; padding: 4px; gap: 2px;
    position: sticky; top: 0; z-index: 100;
  }
  .stTabs [data-baseweb="tab"] {
    color: #8b949e; border-radius: 8px;
    padding: 8px 12px; font-size: 13px; font-weight: 600;
  }
  .stTabs [aria-selected="true"] {
    background: #21262d !important; color: #e6edf3 !important;
  }

  /* metric 카드 */
  div[data-testid="metric-container"] {
    background: #161b22; border: 1px solid #30363d;
    border-radius: 12px; padding: 14px 16px;
  }
  div[data-testid="stMetricValue"] { font-size: 22px !important; }
  div[data-testid="stMetricLabel"] { font-size: 11px !important; }

  /* expander */
  div[data-testid="stExpander"] summary {
    font-size: 14px; padding: 12px 16px;
    background: #161b22; border-radius: 10px;
  }

  /* divider */
  hr { border-color: #30363d; margin: 12px 0; }

  /* dataframe */
  .stDataFrame { border-radius: 10px; overflow: hidden; }
  .stDataFrame th { background: #1c2128 !important; }

  /* 섹션 타이틀 */
  .sec-title {
    font-size: 11px; color: #8b949e; text-transform: uppercase;
    letter-spacing: .6px; margin: 16px 0 8px;
  }

  /* 뱃지 */
  .badge-ok   { background:#1a4731; color:#3fb950; padding:3px 8px; border-radius:5px; font-size:11px; font-weight:700; margin:2px; display:inline-block; }
  .badge-warn { background:#2d2208; color:#e3b341; padding:3px 8px; border-radius:5px; font-size:11px; font-weight:700; margin:2px; display:inline-block; }
  .badge-bad  { background:#2d0f0f; color:#f85149; padding:3px 8px; border-radius:5px; font-size:11px; font-weight:700; margin:2px; display:inline-block; }

  /* 큰 지수 카드 */
  .big-index {
    background: #161b22; border: 1px solid #30363d;
    border-radius: 14px; padding: 18px 20px; margin-bottom: 8px;
  }
  .big-index .label { font-size: 12px; color: #8b949e; margin-bottom: 4px; }
  .big-index .value { font-size: 30px; font-weight: 800; line-height: 1; }
  .big-index .chg   { font-size: 14px; font-weight: 700; margin-top: 4px; }
  .green { color: #3fb950; } .red { color: #f85149; } .gray { color: #8b949e; }

  /* VIX 카드 */
  .vix-card {
    background: #161b22; border: 1px solid #30363d;
    border-radius: 14px; padding: 18px 20px; text-align: center; margin-bottom: 8px;
  }

  /* 매크로 아이템 */
  .macro-item {
    display: flex; justify-content: space-between; align-items: center;
    padding: 11px 14px; border-bottom: 1px solid #21262d; font-size: 14px;
  }
  .macro-item:last-child { border-bottom: none; }
  .macro-wrap {
    background: #161b22; border: 1px solid #30363d; border-radius: 12px;
    overflow: hidden; margin-bottom: 12px;
  }

  /* 관심종목 */
  .wl-item {
    display: flex; justify-content: space-between; align-items: center;
    padding: 12px 14px; border-bottom: 1px solid #21262d;
  }
  .wl-item:last-child { border-bottom: none; }
  .wl-wrap {
    background: #161b22; border: 1px solid #30363d;
    border-radius: 12px; overflow: hidden; margin-bottom: 12px;
  }

  /* 수급 바 */
  .flow-card {
    background: #161b22; border: 1px solid #30363d;
    border-radius: 12px; padding: 16px 18px; margin-bottom: 8px;
  }
  .flow-row { margin-bottom: 10px; }
  .flow-label { display: flex; justify-content: space-between; margin-bottom: 4px; }
  .flow-bar-bg { height: 8px; background: #1c2128; border-radius: 4px; }
  .flow-bar    { height: 8px; border-radius: 4px; }
</style>
""", unsafe_allow_html=True)


# ── 유틸 ──────────────────────────────────────────────────────────────────────
def _arrow(v): return "▲" if v > 0 else "▼" if v < 0 else "━"
def _cls(v):   return "green" if v > 0 else ("red" if v < 0 else "gray")


# ── 데이터 로더 ───────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_screening():
    csvs = sorted(REPORTS_DIR.glob("*_screening.csv"), reverse=True)
    if not csvs:
        return pd.DataFrame(), ""
    p = csvs[0]
    raw = p.stem.replace("_screening", "")
    date = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}" if len(raw) == 8 else raw
    return pd.read_csv(p, index_col="ticker", encoding="utf-8-sig"), date


@st.cache_data(ttl=60)
def load_index(code):
    try:
        r = requests.get(f"https://m.stock.naver.com/api/index/{code}/basic",
                         headers=NAVER_HDR, timeout=8)
        return r.json()
    except Exception:
        return {}


@st.cache_data(ttl=300)
def load_investor_flow(code="KOSPI"):
    try:
        r = requests.get(
            f"https://finance.naver.com/sise/sise_index.naver?code={code}",
            headers=NAVER_HDR, timeout=10,
        )
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "html.parser")
        for dl in soup.select("dl"):
            text = dl.get_text(strip=True)
            if "투자자별" not in text:
                continue
            def _p(key):
                m = re.search(rf"{key}([+\-][\d,]+)억", text)
                return int(m.group(1).replace(",", "")) if m else 0
            return {"개인": _p("개인"), "외국인": _p("외국인"), "기관": _p("기관")}
    except Exception:
        pass
    return {"개인": 0, "외국인": 0, "기관": 0}


@st.cache_data(ttl=300)
def load_macro():
    try:
        import yfinance as yf
        syms = {
            "^GSPC": "S&P 500", "^IXIC": "나스닥", "^DJI": "다우존스",
            "^VIX": "VIX", "^N225": "닛케이",
            "KRW=X": "USD/KRW", "JPYKRW=X": "JPY/KRW",
            "CL=F": "WTI", "GC=F": "금",
        }
        tks = yf.Tickers(" ".join(syms))
        out = {}
        for sym, name in syms.items():
            try:
                fi = tks.tickers[sym].fast_info
                out[sym] = {
                    "name": name, "close": fi.last_price,
                    "chg": (fi.last_price / fi.previous_close - 1) * 100,
                }
            except Exception:
                pass
        return out
    except Exception:
        return {}


@st.cache_data(ttl=60)
def load_watchlist():
    if not WATCHLIST.exists():
        return []
    stocks = json.loads(WATCHLIST.read_text())["stocks"]
    out = []
    for s in stocks[:12]:
        try:
            d  = requests.get(f"https://m.stock.naver.com/api/stock/{s['ticker']}/basic",
                               headers=NAVER_HDR, timeout=5).json()
            out.append({
                "ticker": s["ticker"], "name": s.get("name", d.get("stockName", "")),
                "note": s.get("note", ""),
                "close":  float(str(d.get("closePrice","0")).replace(",","")),
                "chg":    float(d.get("fluctuationsRatio", 0)),
            })
        except Exception:
            pass
    return out


@st.cache_data(ttl=300)
def load_reports(rtype):
    out = []
    for p in sorted(REPORTS_DIR.glob(f"*_{rtype}.md"), reverse=True)[:10]:
        raw = p.stem.split("_")[0]
        date = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}" if len(raw) == 8 else raw
        out.append({"date": date, "content": p.read_text(encoding="utf-8")})
    return out


# ══════════════════════════════════════════════════════════════════════════════
# 앱 레이아웃
# ══════════════════════════════════════════════════════════════════════════════

# 헤더 (KST 기준)
KST = timezone(timedelta(hours=9))
now = datetime.now(KST)
is_open = now.weekday() < 5 and 900 <= now.hour * 100 + now.minute <= 1530
badge_color = "#3fb950" if is_open else "#6e7681"
badge_txt   = "장 중" if is_open else "장 마감"
st.markdown(
    f"<div style='display:flex;justify-content:space-between;align-items:center;"
    f"margin-bottom:12px'>"
    f"<span style='font-size:17px;font-weight:800'>📈 KoStock</span>"
    f"<span style='background:{badge_color}22;color:{badge_color};"
    f"padding:4px 12px;border-radius:20px;font-size:12px;font-weight:700'>"
    f"● {badge_txt}</span></div>",
    unsafe_allow_html=True,
)

tab1, tab2, tab3, tab4 = st.tabs(["시장현황", "스크리닝", "브리핑", "마감분석"])


# ════════════════════════════
# TAB 1 : 시장 현황
# ════════════════════════════
with tab1:
    with st.spinner("데이터 로딩 중..."):
        kospi  = load_index("KOSPI")
        kosdaq = load_index("KOSDAQ")
        flow_k = load_investor_flow("KOSPI")
        flow_d = load_investor_flow("KOSDAQ")
        macro  = load_macro()
        wl     = load_watchlist()

    def _fv(d, k, default=0.0):
        try: return float(str(d.get(k, default)).replace(",", ""))
        except: return default

    # ── 지수 ──────────────────────────────────────────────────────────────────
    st.markdown("<div class='sec-title'>주요 지수</div>", unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    for col, d, label in [(c1, kospi, "KOSPI"), (c2, kosdaq, "KOSDAQ")]:
        close = _fv(d, "closePrice")
        chg   = _fv(d, "fluctuationsRatio")
        cls   = _cls(chg)
        with col:
            st.markdown(f"""
            <div class="big-index">
              <div class="label">{label}</div>
              <div class="value {cls}">{close:,.2f}</div>
              <div class="chg {cls}">{_arrow(chg)} {abs(chg):.2f}%</div>
            </div>""", unsafe_allow_html=True)

    # ── VIX ───────────────────────────────────────────────────────────────────
    vix_val = macro.get("^VIX", {}).get("close", 0)
    if vix_val:
        if vix_val >= 35:   vix_cls, vix_lbl = "red",   "극단적 공포"
        elif vix_val >= 25: vix_cls, vix_lbl = "red",   "공포"
        elif vix_val >= 18: vix_cls, vix_lbl = "gray",  "중립"
        elif vix_val >= 12: vix_cls, vix_lbl = "green", "안정"
        else:               vix_cls, vix_lbl = "green", "탐욕"
        needle = min(int(vix_val / 40 * 100), 95)
        st.markdown(f"""
        <div class="vix-card">
          <div style="font-size:11px;color:#8b949e;margin-bottom:6px">VIX 공포지수</div>
          <div style="font-size:36px;font-weight:900" class="{vix_cls}">{vix_val:.1f}</div>
          <div style="font-size:13px;font-weight:700;margin:4px 0 12px" class="{vix_cls}">{vix_lbl}</div>
          <div style="height:8px;border-radius:4px;
                      background:linear-gradient(to right,#3fb950,#e3b341,#f85149);
                      position:relative;margin:0 4px">
            <div style="position:absolute;top:-4px;left:{needle}%;
                        transform:translateX(-50%);width:14px;height:14px;
                        border-radius:50%;background:white;border:2px solid #0d1117"></div>
          </div>
          <div style="display:flex;justify-content:space-between;
                      font-size:10px;color:#8b949e;margin-top:6px">
            <span>안정</span><span>공포</span>
          </div>
        </div>""", unsafe_allow_html=True)

    # ── 수급 현황 ─────────────────────────────────────────────────────────────
    st.markdown("<div class='sec-title'>수급 현황 (오늘 순매수)</div>", unsafe_allow_html=True)

    for market, flow in [("KOSPI", flow_k), ("KOSDAQ", flow_d)]:
        max_abs = max(abs(v) for v in flow.values()) or 1

        def _bar(name, val):
            color = "#3fb950" if val >= 0 else "#f85149"
            width = abs(val) / max_abs * 100
            sign  = "+" if val >= 0 else ""
            return (
                f"<div class='flow-row'>"
                f"<div class='flow-label'>"
                f"<span style='font-size:13px;color:#c9d1d9;font-weight:600'>{name}</span>"
                f"<span style='font-size:13px;font-weight:700;color:{color}'>{sign}{val:,}억</span>"
                f"</div>"
                f"<div class='flow-bar-bg'>"
                f"<div class='flow-bar' style='width:{width:.1f}%;background:{color}'></div>"
                f"</div></div>"
            )

        bars = "".join(_bar(k, v) for k, v in flow.items())
        st.markdown(
            f"<div class='flow-card'>"
            f"<div style='font-size:12px;color:#8b949e;margin-bottom:10px;font-weight:600'>{market}</div>"
            f"{bars}</div>",
            unsafe_allow_html=True,
        )

    # ── 글로벌 매크로 ─────────────────────────────────────────────────────────
    st.markdown("<div class='sec-title'>글로벌 매크로</div>", unsafe_allow_html=True)
    MACRO_ORDER = [
        ("^GSPC", 2), ("^IXIC", 2), ("^DJI", 0), ("^N225", 0),
        ("KRW=X", 1), ("JPYKRW=X", 2), ("CL=F", 2), ("GC=F", 0),
    ]
    rows_html = ""
    for sym, dec in MACRO_ORDER:
        d = macro.get(sym)
        if not d:
            continue
        color = "#3fb950" if d["chg"] > 0 else ("#f85149" if d["chg"] < 0 else "#8b949e")
        rows_html += (
            f"<div class='macro-item'>"
            f"<span style='color:#c9d1d9;font-weight:500'>{d['name']}</span>"
            f"<div style='text-align:right'>"
            f"<div style='font-weight:700'>{d['close']:,.{dec}f}</div>"
            f"<div style='font-size:12px;color:{color};font-weight:600'>"
            f"{_arrow(d['chg'])} {abs(d['chg']):.2f}%</div>"
            f"</div></div>"
        )
    if rows_html:
        st.markdown(f"<div class='macro-wrap'>{rows_html}</div>", unsafe_allow_html=True)

    # ── 관심종목 ──────────────────────────────────────────────────────────────
    st.markdown("<div class='sec-title'>관심종목</div>", unsafe_allow_html=True)
    if wl:
        rows_html = ""
        for s in wl:
            color = "#3fb950" if s["chg"] > 0 else ("#f85149" if s["chg"] < 0 else "#8b949e")
            rows_html += (
                f"<div class='wl-item'>"
                f"<div>"
                f"<div style='font-weight:700;font-size:14px'>{s['name']}</div>"
                f"<div style='font-size:11px;color:#8b949e'>{s['ticker']} · {s['note']}</div>"
                f"</div>"
                f"<div style='text-align:right'>"
                f"<div style='font-weight:700;font-size:15px'>{s['close']:,.0f}원</div>"
                f"<div style='font-size:13px;color:{color};font-weight:600'>"
                f"{_arrow(s['chg'])} {abs(s['chg']):.2f}%</div>"
                f"</div></div>"
            )
        st.markdown(f"<div class='wl-wrap'>{rows_html}</div>", unsafe_allow_html=True)

    if st.button("🔄 새로고침", use_container_width=True):
        st.cache_data.clear()
        st.rerun()


# ════════════════════════════
# TAB 2 : 스크리닝
# ════════════════════════════
with tab2:
    df, date_str = load_screening()

    if df.empty:
        st.warning("스크리닝 결과가 없습니다.")
    else:
        st.markdown(f"<div style='font-size:12px;color:#8b949e;margin-bottom:8px'>기준일 {date_str}</div>",
                    unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        c1.metric("통과", f"{len(df)}개")
        c2.metric("평균점수", f"{df['score'].mean():.0f}점")
        c3.metric("최고점수", f"{df['score'].max():.0f}점")

        st.divider()

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
            chg_cls = _cls(chg)

            emoji = "🟢" if score >= 70 else "🟡" if score >= 50 else "🔴"
            with st.expander(
                f"{emoji} **{name}** — {score:.0f}점 · "
                f"{'▲' if chg>0 else '▼'}{abs(chg):.1f}%"
            ):
                # 가격 / 점수
                p1, p2 = st.columns(2)
                p1.metric("현재가", f"{close:,.0f}원",
                          delta=f"{_arrow(chg)}{abs(chg):.1f}%")
                p2.metric("시가총액", f"{marcap/1e12:.1f}조")

                st.divider()

                # 지표 — 2열 배치 (모바일 친화)
                m1, m2 = st.columns(2)
                m1.metric("PER", f"{per:.1f}배",
                    help="주가수익비율\n주가 ÷ EPS\n적정: 5~40배\n낮을수록 저평가")
                m2.metric("PBR", f"{pbr:.2f}배",
                    help="주가순자산비율\n주가 ÷ BPS\n적정: 0.3~5배\n1배 미만 = 청산가치 이하")

                m3, m4 = st.columns(2)
                m3.metric("RSI(14)", f"{rsi:.0f}",
                    delta="과매수" if rsi > 70 else ("과매도" if rsi < 30 else "중립"),
                    delta_color="inverse" if rsi > 70 else "normal",
                    help="상대강도지수\n70↑ 과매수 · 30↓ 과매도\n30~70 중립")
                m4.metric("모멘텀 20일", f"{_arrow(mom)}{abs(mom):.1f}%",
                    help="20거래일 수익률\n양수 = 상승추세 유지\n높을수록 강한 모멘텀")

                m5, m6 = st.columns(2)
                m5.metric("거래량비", f"{vol_r:.1f}x",
                    help="오늘 거래량 ÷ 20일 평균\n1.5배↑ 급증 · 3배↑ 강한 수급")
                m6.metric("52W 고가比", f"{w52:.1f}%",
                    help="52주 최고가 대비 위치\n0% = 신고가 · -20% 이내 = 돌파 임박")

                st.divider()

                # 외국인/기관
                f1, f2 = st.columns(2)
                f1.metric("외국인 연속", f"{consec}일",
                    help="외국인 연속 순매수 일수\n3일↑ = 수급 유입 신호")
                f2.metric("기관전환", "✅" if inst else "❌",
                    help="기관 순매수 전환 여부\n전주 대비 기관 매수 전환 시 유리")

                # 조건 뱃지
                badges = []
                if row.get("golden_cross"):  badges.append('<span class="badge-ok">골든크로스</span>')
                else:                         badges.append('<span class="badge-bad">골든크로스❌</span>')
                if row.get("near_52w_high"): badges.append('<span class="badge-ok">52W근접</span>')
                if row.get("volume_surge"):  badges.append('<span class="badge-ok">거래량급증</span>')
                else:                         badges.append('<span class="badge-warn">거래량보통</span>')
                if consec >= 3:              badges.append(f'<span class="badge-ok">외국인{consec}일</span>')
                if inst:                     badges.append('<span class="badge-ok">기관전환</span>')
                st.markdown(" ".join(badges), unsafe_allow_html=True)


# ════════════════════════════
# TAB 3 & 4 : 리포트
# ════════════════════════════
def render_report_tab(rtype: str):
    reports = load_reports(rtype)
    if not reports:
        st.info("아직 리포트가 없습니다.")
        return
    sel = st.selectbox("날짜", [r["date"] for r in reports], key=f"sel_{rtype}")
    report = next((r for r in reports if r["date"] == sel), None)
    if report:
        st.divider()
        st.markdown(report["content"])


with tab3:
    render_report_tab("morning")

with tab4:
    render_report_tab("closing")
