"""
KoStock 성장주 스크리너 — Streamlit Cloud 대시보드
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

# ── 페이지 설정 ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="KoStock 성장주 스크리너",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

BASE_DIR    = Path(__file__).parent
REPORTS_DIR = BASE_DIR / "reports"
WATCHLIST   = BASE_DIR / "watchlist.json"

NAVER_HDR = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer":    "https://finance.naver.com",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

# ── 글로벌 CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  .stApp { background:#0d1117; color:#e6edf3; }
  .block-container { padding-top:1.2rem; padding-bottom:2rem; }
  div[data-testid="metric-container"] {
    background:#161b22; border:1px solid #30363d;
    border-radius:10px; padding:14px 18px;
  }
  div[data-testid="stExpander"] > div:first-child {
    background:#161b22; border:1px solid #30363d; border-radius:10px;
  }
  .stTabs [data-baseweb="tab-list"] { background:#161b22; border-radius:8px; padding:4px; }
  .stTabs [data-baseweb="tab"] { color:#8b949e; border-radius:6px; padding:8px 20px; font-size:14px; }
  .stTabs [aria-selected="true"] { background:#1c2128 !important; color:#e6edf3 !important; }
  .stDataFrame { border:1px solid #30363d; border-radius:10px; overflow:hidden; }
  .badge-ok   { background:#1a4731; color:#3fb950; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600; margin:2px; display:inline-block; }
  .badge-warn { background:#2d2208; color:#e3b341; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600; margin:2px; display:inline-block; }
  .badge-bad  { background:#2d0f0f; color:#f85149; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600; margin:2px; display:inline-block; }
  h3 { color:#e6edf3 !important; }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# 데이터 로더
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=300)
def load_screening() -> tuple[pd.DataFrame, str]:
    csvs = sorted(REPORTS_DIR.glob("*_screening.csv"), reverse=True)
    if not csvs:
        return pd.DataFrame(), ""
    latest = csvs[0]
    raw = latest.stem.replace("_screening", "")
    date_fmt = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}" if len(raw) == 8 else raw
    df = pd.read_csv(latest, index_col="ticker", encoding="utf-8-sig")
    return df, date_fmt


@st.cache_data(ttl=60)
def load_index(code: str) -> dict:
    try:
        r = requests.get(f"https://m.stock.naver.com/api/index/{code}/basic",
                         headers=NAVER_HDR, timeout=8)
        return r.json()
    except Exception:
        return {}


@st.cache_data(ttl=300)
def load_investor_flow(code: str = "KOSPI") -> dict:
    """개인/외국인/기관 순매수 (억원) — Naver Finance"""
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
            def _parse(key):
                m = re.search(rf"{key}([+\-][\d,]+)억", text)
                if not m:
                    return 0
                return int(m.group(1).replace(",", ""))
            return {
                "개인":   _parse("개인"),
                "외국인": _parse("외국인"),
                "기관":   _parse("기관"),
            }
    except Exception:
        pass
    return {"개인": 0, "외국인": 0, "기관": 0}


@st.cache_data(ttl=300)
def load_macro() -> dict:
    try:
        import yfinance as yf
        symbols = {
            "^GSPC":    "S&P 500",
            "^IXIC":    "나스닥",
            "^DJI":     "다우존스",
            "^VIX":     "VIX",
            "^N225":    "닛케이",
            "KRW=X":    "USD/KRW",
            "JPYKRW=X": "JPY/KRW",
            "CL=F":     "WTI 원유",
            "GC=F":     "금",
        }
        tickers = yf.Tickers(" ".join(symbols.keys()))
        result = {}
        for sym, name in symbols.items():
            try:
                fi = tickers.tickers[sym].fast_info
                result[sym] = {
                    "name":       name,
                    "close":      fi.last_price,
                    "change_pct": (fi.last_price / fi.previous_close - 1) * 100,
                }
            except Exception:
                pass
        return result
    except Exception:
        return {}


@st.cache_data(ttl=60)
def load_watchlist_prices() -> list[dict]:
    if not WATCHLIST.exists():
        return []
    stocks = json.loads(WATCHLIST.read_text())["stocks"]
    result = []
    for s in stocks[:12]:
        try:
            r  = requests.get(f"https://m.stock.naver.com/api/stock/{s['ticker']}/basic",
                               headers=NAVER_HDR, timeout=5)
            r2 = requests.get(f"https://m.stock.naver.com/api/stock/{s['ticker']}/price",
                               headers=NAVER_HDR, timeout=5)
            d, d2 = r.json(), r2.json()
            result.append({
                "ticker":     s["ticker"],
                "name":       s.get("name", d.get("stockName", "")),
                "note":       s.get("note", ""),
                "close":      float(str(d.get("closePrice","0")).replace(",","")),
                "change_pct": float(d.get("fluctuationsRatio", 0)),
                "volume":     int(str(d2.get("accumulatedTradingVolume","0")).replace(",","")),
            })
        except Exception:
            pass
    return result


@st.cache_data(ttl=86400)
def load_reports(rtype: str) -> list[dict]:
    out = []
    for p in sorted(REPORTS_DIR.glob(f"*_{rtype}.md"), reverse=True)[:10]:
        raw = p.stem.split("_")[0]
        date_fmt = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}" if len(raw) == 8 else raw
        out.append({"date": date_fmt, "name": p.name,
                    "content": p.read_text(encoding="utf-8")})
    return out


# ══════════════════════════════════════════════════════════════════════════════
# HTML 컴포넌트
# ══════════════════════════════════════════════════════════════════════════════

def _arrow(v): return "▲" if v > 0 else "▼" if v < 0 else "━"
def _color(v): return "#3fb950" if v > 0 else "#f85149" if v < 0 else "#8b949e"


def index_card_html(label: str, close: float, chg: float, vol: str = "") -> str:
    color = _color(chg)
    return f"""
    <div style="background:#161b22;border:1px solid #30363d;border-radius:12px;
                padding:20px 24px;text-align:center">
      <div style="font-size:12px;color:#8b949e;margin-bottom:6px;letter-spacing:.5px">{label}</div>
      <div style="font-size:32px;font-weight:800;color:{color};line-height:1">{close:,.2f}</div>
      <div style="font-size:14px;font-weight:700;color:{color};margin-top:6px">
        {_arrow(chg)} {abs(chg):.2f}%
      </div>
      {f'<div style="font-size:11px;color:#8b949e;margin-top:4px">거래대금 {vol}</div>' if vol else ''}
    </div>"""


def vix_gauge_html(vix: float) -> str:
    if vix >= 35:   level, color, label = 90, "#f85149", "극단적 공포"
    elif vix >= 25: level, color, label = 70, "#f0883e", "공포"
    elif vix >= 18: level, color, label = 50, "#e3b341", "중립"
    elif vix >= 12: level, color, label = 30, "#7fb950", "안정"
    else:           level, color, label = 15, "#3fb950", "탐욕"
    return f"""
    <div style="background:#161b22;border:1px solid #30363d;border-radius:12px;
                padding:20px;text-align:center">
      <div style="font-size:11px;color:#8b949e;margin-bottom:8px;letter-spacing:.5px">
        VIX 공포지수
      </div>
      <div style="font-size:44px;font-weight:900;color:{color};line-height:1">{vix:.1f}</div>
      <div style="font-size:14px;font-weight:700;color:{color};margin-top:6px">{label}</div>
      <div style="margin:14px auto 0;width:180px;height:8px;border-radius:4px;
                  background:linear-gradient(to right,#3fb950,#e3b341,#f85149);position:relative">
        <div style="position:absolute;top:-5px;left:calc({min(level,95)}% - 8px);
                    width:16px;height:16px;border-radius:50%;
                    background:{color};border:2px solid #0d1117"></div>
      </div>
      <div style="display:flex;justify-content:space-between;width:180px;
                  margin:6px auto 0;font-size:10px;color:#8b949e">
        <span>안정</span><span>공포</span>
      </div>
    </div>"""


def investor_flow_html(data: dict, title: str) -> str:
    max_abs = max(abs(v) for v in data.values()) or 1
    rows = ""
    for name, val in data.items():
        color  = _color(val)
        bar_w  = abs(val) / max_abs * 100
        sign   = "+" if val >= 0 else ""
        rows += f"""
        <div style="margin-bottom:10px">
          <div style="display:flex;justify-content:space-between;margin-bottom:4px">
            <span style="font-size:12px;color:#8b949e;font-weight:500">{name}</span>
            <span style="font-size:13px;font-weight:700;color:{color}">{sign}{val:,}억</span>
          </div>
          <div style="height:7px;background:#1c2128;border-radius:4px">
            <div style="height:7px;width:{bar_w:.1f}%;background:{color};border-radius:4px;
                        transition:width .3s"></div>
          </div>
        </div>"""
    return f"""
    <div style="background:#161b22;border:1px solid #30363d;border-radius:12px;
                padding:18px 20px">
      <div style="font-size:11px;color:#8b949e;margin-bottom:14px;letter-spacing:.5px">
        {title} 수급 (순매수)
      </div>
      {rows}
    </div>"""


def macro_card_html(name: str, close: float, chg: float, decimals: int = 2) -> str:
    color = _color(chg)
    return f"""
    <div style="background:#161b22;border:1px solid #30363d;border-radius:10px;
                padding:14px 16px;display:flex;justify-content:space-between;align-items:center">
      <span style="font-size:12px;color:#8b949e">{name}</span>
      <div style="text-align:right">
        <div style="font-size:14px;font-weight:700;color:#e6edf3">
          {close:,.{decimals}f}
        </div>
        <div style="font-size:11px;color:{color};font-weight:600">
          {_arrow(chg)} {abs(chg):.2f}%
        </div>
      </div>
    </div>"""


# ══════════════════════════════════════════════════════════════════════════════
# 탭 레이아웃
# ══════════════════════════════════════════════════════════════════════════════

tab1, tab2, tab3, tab4 = st.tabs([
    "📊 시장 현황",
    "🔍 성장주 스크리닝",
    "☀️ 시장 브리핑",
    "🌙 마감 분석",
])


# ════════════════════════════════════════
# 탭 1: 시장 현황
# ════════════════════════════════════════
with tab1:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M 기준")
    is_open = datetime.now().weekday() < 5 and 900 <= datetime.now().hour * 100 + datetime.now().minute <= 1530
    market_badge = "🟢 장 중" if is_open else "⚫ 장 마감"
    st.markdown(f"<span style='color:#8b949e;font-size:13px'>{now_str} &nbsp; {market_badge}</span>",
                unsafe_allow_html=True)

    # ── 지수 + VIX ───────────────────────────────────────────────────────────
    with st.spinner("시장 데이터 로딩 중..."):
        kospi  = load_index("KOSPI")
        kosdaq = load_index("KOSDAQ")
        macro  = load_macro()
        flow_k = load_investor_flow("KOSPI")
        flow_d = load_investor_flow("KOSDAQ")

    def _idx_val(d, key, default=0.0):
        try: return float(str(d.get(key, default)).replace(",", ""))
        except: return default

    c1, c2, c3 = st.columns(3)
    with c1:
        close_k = _idx_val(kospi, "closePrice")
        chg_k   = _idx_val(kospi, "fluctuationsRatio")
        st.markdown(index_card_html("KOSPI", close_k, chg_k), unsafe_allow_html=True)
    with c2:
        close_d = _idx_val(kosdaq, "closePrice")
        chg_d   = _idx_val(kosdaq, "fluctuationsRatio")
        st.markdown(index_card_html("KOSDAQ", close_d, chg_d), unsafe_allow_html=True)
    with c3:
        vix_data = macro.get("^VIX", {})
        vix_val  = vix_data.get("close", 20.0)
        st.markdown(vix_gauge_html(vix_val), unsafe_allow_html=True)

    st.markdown("<div style='margin-top:16px'></div>", unsafe_allow_html=True)

    # ── 수급 현황 ─────────────────────────────────────────────────────────────
    st.markdown("#### 📦 수급 현황 (오늘 순매수)")
    fc1, fc2 = st.columns(2)
    with fc1:
        st.markdown(investor_flow_html(flow_k, "KOSPI"), unsafe_allow_html=True)
    with fc2:
        st.markdown(investor_flow_html(flow_d, "KOSDAQ"), unsafe_allow_html=True)

    st.markdown("<div style='margin-top:16px'></div>", unsafe_allow_html=True)

    # ── 글로벌 매크로 ─────────────────────────────────────────────────────────
    st.markdown("#### 🌐 글로벌 매크로")
    MACRO_LIST = [
        ("^GSPC",    "S&P 500",    2),
        ("^IXIC",    "나스닥",      2),
        ("^DJI",     "다우존스",    0),
        ("^N225",    "닛케이225",   0),
        ("KRW=X",    "USD/KRW",    1),
        ("JPYKRW=X", "JPY/KRW",    2),
        ("CL=F",     "WTI 원유",   2),
        ("GC=F",     "금",          0),
    ]
    cols = st.columns(4)
    for i, (sym, name, dec) in enumerate(MACRO_LIST):
        d = macro.get(sym, {})
        if d:
            cols[i % 4].markdown(
                macro_card_html(name, d["close"], d["change_pct"], dec),
                unsafe_allow_html=True,
            )

    st.markdown("<div style='margin-top:20px'></div>", unsafe_allow_html=True)

    # ── 관심종목 ──────────────────────────────────────────────────────────────
    st.markdown("#### 👀 관심종목")
    with st.spinner("로딩 중..."):
        wl = load_watchlist_prices()

    if wl:
        rows_html = ""
        for s in wl:
            c = s["change_pct"]
            color = _color(c)
            vol_str = (f"{s['volume']/1e6:.1f}M" if s["volume"] >= 1e6
                       else f"{s['volume']/1e3:.0f}K")
            rows_html += f"""
            <tr>
              <td style="padding:10px 12px;font-weight:600">{s['name']}</td>
              <td style="padding:10px 12px;color:#8b949e;font-size:11px">{s['ticker']}</td>
              <td style="padding:10px 12px;font-weight:600">{s['close']:,.0f}원</td>
              <td style="padding:10px 12px;color:{color};font-weight:700">
                {_arrow(c)} {abs(c):.2f}%</td>
              <td style="padding:10px 12px;color:#8b949e">{vol_str}</td>
              <td style="padding:10px 12px;color:#8b949e;font-size:11px">{s.get('note','')}</td>
            </tr>"""
        st.markdown(f"""
        <div style="background:#161b22;border:1px solid #30363d;border-radius:12px;
                    overflow:hidden;margin-top:4px">
          <table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead>
              <tr style="border-bottom:1px solid #30363d">
                <th style="padding:10px 12px;text-align:left;color:#8b949e;font-size:11px;
                           text-transform:uppercase;letter-spacing:.4px">종목</th>
                <th style="padding:10px 12px;text-align:left;color:#8b949e;font-size:11px">코드</th>
                <th style="padding:10px 12px;text-align:left;color:#8b949e;font-size:11px">현재가</th>
                <th style="padding:10px 12px;text-align:left;color:#8b949e;font-size:11px">등락률</th>
                <th style="padding:10px 12px;text-align:left;color:#8b949e;font-size:11px">거래량</th>
                <th style="padding:10px 12px;text-align:left;color:#8b949e;font-size:11px">메모</th>
              </tr>
            </thead>
            <tbody>{rows_html}</tbody>
          </table>
        </div>""", unsafe_allow_html=True)
    else:
        st.info("관심종목 데이터를 불러올 수 없습니다.")

    if st.button("🔄 시장 데이터 새로고침", key="refresh_market"):
        st.cache_data.clear()
        st.rerun()


# ════════════════════════════════════════
# 탭 2: 성장주 스크리닝
# ════════════════════════════════════════
with tab2:
    df, date_str = load_screening()

    if df.empty:
        st.info("스크리닝 결과가 없습니다. `run_screener.py`를 실행하세요.")
    else:
        # 헤더
        h1, h2, h3, h4 = st.columns([3, 1, 1, 1])
        h1.markdown(f"### 📅 기준일: {date_str}")
        h2.metric("통과 종목", f"{len(df)}개")
        h3.metric("평균 점수", f"{df['score'].mean():.0f}점")
        h4.metric("최고 점수", f"{df['score'].max():.0f}점")

        st.divider()

        # 요약 테이블
        with st.expander("📋 전체 요약 테이블", expanded=True):
            cols_map = {
                "name": "종목명", "close": "현재가", "change_pct": "등락률",
                "per": "PER", "pbr": "PBR", "rsi": "RSI",
                "momentum_20d": "모멘텀20", "vol_ratio": "거래량비",
                "pct_from_52w_high": "52W高比", "foreign_consec": "외국인연속",
                "score": "종합점수",
            }
            tbl = df[[c for c in cols_map if c in df.columns]].copy()
            tbl.columns = [cols_map[c] for c in tbl.columns]
            tbl["현재가"]    = tbl["현재가"].apply(lambda x: f"{x:,.0f}원")
            tbl["등락률"]    = tbl["등락률"].apply(lambda x: f"{_arrow(x)}{abs(x):.1f}%")
            tbl["PER"]       = tbl["PER"].apply(lambda x: f"{x:.1f}")
            tbl["PBR"]       = tbl["PBR"].apply(lambda x: f"{x:.2f}")
            tbl["RSI"]       = tbl["RSI"].apply(lambda x: f"{x:.0f}")
            tbl["모멘텀20"]  = tbl["모멘텀20"].apply(lambda x: f"{_arrow(x)}{abs(x):.1f}%")
            tbl["거래량비"]  = tbl["거래량비"].apply(lambda x: f"{x:.1f}x")
            tbl["52W高比"]   = tbl["52W高比"].apply(lambda x: f"{x:.1f}%")
            tbl["외국인연속"] = tbl["외국인연속"].apply(lambda x: f"{int(x)}일")
            tbl["종합점수"]  = tbl["종합점수"].apply(lambda x: f"{x:.0f}점")
            st.dataframe(tbl, use_container_width=True, height=400)

        st.divider()

        # 종목 카드
        st.markdown("#### 종목별 상세 분석")
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

            emoji = "🟢" if score >= 70 else "🟡" if score >= 50 else "🔴"
            with st.expander(
                f"**{name}** ({ticker})　"
                f"{_arrow(chg)}{abs(chg):.1f}%　"
                f"{close:,.0f}원　"
                f"{emoji} **{score:.0f}점**",
            ):
                m1, m2, m3, m4, m5, m6 = st.columns(6)
                m1.metric("PER", f"{per:.1f}배",
                    help="**주가수익비율** (Price-to-Earnings Ratio)\n\n"
                         "주가 ÷ EPS(주당순이익)\n\n"
                         "- 낮을수록 저평가\n"
                         "- 적정 범위: **5~40배**\n"
                         "- 10배 이하: 매우 저평가")
                m2.metric("PBR", f"{pbr:.2f}배",
                    help="**주가순자산비율** (Price-to-Book Ratio)\n\n"
                         "주가 ÷ BPS(주당순자산)\n\n"
                         "- 1배 미만: 청산가치 이하\n"
                         "- 적정 범위: **0.3~5배**\n"
                         "- 성장주는 PBR이 높은 경향")
                m3.metric("RSI(14)", f"{rsi:.0f}",
                    delta="과매수" if rsi > 70 else ("과매도" if rsi < 30 else "중립"),
                    delta_color="inverse" if rsi > 70 else "normal",
                    help="**상대강도지수** (Relative Strength Index)\n\n"
                         "14일 기준 가격 모멘텀 강도\n\n"
                         "- **70 이상**: 과매수 → 단기 조정 가능\n"
                         "- **30 이하**: 과매도 → 반등 가능\n"
                         "- **30~70**: 중립 구간")
                m4.metric("모멘텀 20일", f"{_arrow(mom)}{abs(mom):.1f}%",
                    help="**20일 가격 모멘텀**\n\n"
                         "20거래일 전 종가 대비 현재 등락률\n\n"
                         "- 양수: 상승 추세 유지 중\n"
                         "- 높을수록 강한 추세\n"
                         "- 스크리닝 조건: 양수")
                m5.metric("거래량비", f"{vol_r:.1f}x",
                    help="**거래량 비율**\n\n"
                         "오늘 거래량 ÷ 20일 평균 거래량\n\n"
                         "- **1.5배 이상**: 거래량 급증 신호\n"
                         "- **3배 이상**: 강한 수급 매집 신호\n"
                         "- 거래량은 추세의 확신도를 의미")
                m6.metric("52W 고가比", f"{w52:.1f}%",
                    help="**52주 신고가 대비 위치**\n\n"
                         "현재가가 52주 최고가에서 얼마나 떨어졌는지\n\n"
                         "- **0%**: 52주 신고가 갱신 중\n"
                         "- **-20% 이내**: 돌파 임박 구간\n"
                         "- 신고가 근처에서 매수세 강함")

                a1, a2 = st.columns(2)
                a1.markdown(
                    f"**시가총액:** {marcap/1e12:.2f}조　"
                    f"**외국인 연속 순매수:** {consec}일"
                )
                a2.markdown(f"**기관 순매수 전환:** {'✅ 전환' if inst else '❌ 미전환'}")

                badges = []
                if row.get("golden_cross"):  badges.append('<span class="badge-ok">✅ 골든크로스</span>')
                else:                         badges.append('<span class="badge-bad">❌ 골든크로스</span>')
                if row.get("near_52w_high"): badges.append('<span class="badge-ok">✅ 52W 근접</span>')
                if row.get("volume_surge"):  badges.append('<span class="badge-ok">✅ 거래량 급증</span>')
                else:                         badges.append('<span class="badge-warn">⚠️ 거래량 보통</span>')
                if consec >= 3:              badges.append(f'<span class="badge-ok">✅ 외국인 {consec}일 연속</span>')
                if inst:                     badges.append('<span class="badge-ok">✅ 기관 전환</span>')
                st.markdown(" ".join(badges), unsafe_allow_html=True)


# ════════════════════════════════════════
# 탭 3 & 4: 리포트 뷰어 (공통)
# ════════════════════════════════════════
def render_report_tab(rtype: str):
    reports = load_reports(rtype)
    if not reports:
        st.info(f"리포트가 없습니다. `python main.py {rtype}` 를 실행하세요.")
        return

    sel_label = st.selectbox(
        "날짜 선택",
        options=[r["date"] for r in reports],
        key=f"sel_{rtype}",
    )
    selected = next((r for r in reports if r["date"] == sel_label), None)
    if selected:
        st.markdown("---")
        st.markdown(selected["content"])


with tab3:
    st.markdown("### ☀️ 시장 브리핑")
    render_report_tab("morning")

with tab4:
    st.markdown("### 🌙 마감 분석")
    render_report_tab("closing")
