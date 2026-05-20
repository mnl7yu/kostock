#!/usr/bin/env python3
"""
한국 주식 자동 분석 대시보드
Run: python3 dashboard.py
Open: http://localhost:8765
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import threading
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent))

import config
from collectors.macro import get_macro_snapshot
from collectors.market_data import (
    get_index_summary,
    get_last_trading_date,
    get_market_breadth,
    get_top_movers,
)

PORT = 8765
BASE_DIR = Path(__file__).parent

# ── 데이터 캐시 (60초) ────────────────────────────────────────────────────────
_cache: dict = {}
_cache_time: datetime | None = None
_cache_lock = threading.Lock()
_CACHE_TTL = 60


def _load_watchlist() -> list[dict]:
    with open(config.WATCHLIST_PATH, encoding="utf-8") as f:
        return json.load(f)["stocks"]


def _is_market_open() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 100 + now.minute
    return 900 <= t <= 1530


def _get_market_data() -> dict:
    global _cache, _cache_time
    with _cache_lock:
        if _cache_time and (datetime.now() - _cache_time).seconds < _CACHE_TTL:
            return _cache
        try:
            date = get_last_trading_date()
            watchlist = _load_watchlist()

            from collectors.market_data import get_watchlist_data
            indices  = get_index_summary(date)
            breadth_k = get_market_breadth(date, "KOSPI")
            breadth_d = get_market_breadth(date, "KOSDAQ")
            movers_k = get_top_movers(date, "KOSPI", 10)
            movers_d = get_top_movers(date, "KOSDAQ", 10)
            wl_data  = get_watchlist_data(watchlist, date)
            macro    = get_macro_snapshot()

            _cache = {
                "date": date,
                "market_open": _is_market_open(),
                "updated_at": datetime.now().isoformat(),
                "indices": indices,
                "breadth": {"KOSPI": breadth_k, "KOSDAQ": breadth_d},
                "movers": {"KOSPI": movers_k, "KOSDAQ": movers_d},
                "watchlist": wl_data,
                "macro": macro,
            }
            _cache_time = datetime.now()
        except Exception as e:
            _cache = {"error": str(e), "updated_at": datetime.now().isoformat()}
        return _cache


def _list_reports() -> list[dict]:
    reports = []
    for p in sorted(config.REPORTS_DIR.glob("*.md"), reverse=True)[:20]:
        # 스크리닝 리포트는 별도 탭에서 처리
        if "_screening" in p.name:
            continue
        stat = p.stat()
        reports.append({
            "name": p.name,
            "size": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    return reports


def _read_report(name: str) -> str:
    path = config.REPORTS_DIR / name
    if not path.exists() or not name.endswith(".md"):
        return ""
    return path.read_text(encoding="utf-8")


def _load_screening() -> dict:
    """최신 스크리닝 CSV 로드."""
    csvs = sorted(config.REPORTS_DIR.glob("*_screening.csv"), reverse=True)
    if not csvs:
        return {"date": None, "stocks": []}
    latest = csvs[0]
    date_raw = latest.stem.replace("_screening", "")
    date_fmt = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:]}" if len(date_raw) == 8 else date_raw

    stocks = []
    try:
        with open(latest, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                def fv(k, default=0.0):
                    try: return float(row.get(k, default) or default)
                    except: return default
                def bv(k):
                    v = row.get(k, "False")
                    return str(v).strip().lower() in ("true", "1", "yes")
                stocks.append({
                    "ticker":           row.get("ticker", ""),
                    "name":             row.get("name", ""),
                    "market":           row.get("market", ""),
                    "close":            fv("close"),
                    "change_pct":       fv("change_pct"),
                    "marcap":           fv("marcap"),
                    "per":              fv("per"),
                    "pbr":              fv("pbr"),
                    "score":            fv("score"),
                    "rsi":              fv("rsi"),
                    "momentum_20d":     fv("momentum_20d"),
                    "vol_ratio":        fv("vol_ratio"),
                    "pct_from_52w_high":fv("pct_from_52w_high"),
                    "foreign_consec":   fv("foreign_consec"),
                    "inst_turn":        bv("inst_turn"),
                    "golden_cross":     bv("golden_cross"),
                    "near_52w_high":    bv("near_52w_high"),
                    "volume_surge":     bv("volume_surge"),
                })
    except Exception as e:
        pass
    return {"date": date_fmt, "stocks": stocks}


# ── HTML ─────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>📊 한국 주식 분석 대시보드</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#0d1117;color:#e6edf3;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;min-height:100vh}
  a{color:inherit;text-decoration:none}

  header{background:#161b22;border-bottom:1px solid #30363d;padding:14px 24px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100}
  header h1{font-size:17px;font-weight:700;display:flex;align-items:center;gap:8px}
  .status-dot{width:8px;height:8px;border-radius:50%;background:#3fb950;animation:pulse 2s infinite}
  .status-dot.closed{background:#6e7681;animation:none}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
  .header-right{display:flex;align-items:center;gap:12px}
  .updated{font-size:12px;color:#8b949e}
  .badge{padding:3px 10px;border-radius:12px;font-size:11px;font-weight:700}
  .badge-open{background:#1a4731;color:#3fb950}
  .badge-closed{background:#21262d;color:#8b949e}
  .btn{background:#21262d;border:1px solid #30363d;color:#e6edf3;padding:5px 12px;border-radius:6px;cursor:pointer;font-size:12px}
  .btn:hover{background:#30363d}

  /* 탭 */
  .tabs{background:#161b22;border-bottom:1px solid #30363d;padding:0 24px;display:flex;gap:0}
  .tab{padding:12px 20px;font-size:13px;font-weight:600;cursor:pointer;color:#8b949e;border-bottom:2px solid transparent;transition:color .15s,border-color .15s}
  .tab:hover{color:#e6edf3}
  .tab.active{color:#58a6ff;border-bottom-color:#58a6ff}
  .tab-content{display:none}
  .tab-content.active{display:block}

  main{padding:20px 24px;max-width:1280px;margin:0 auto}
  .section-title{font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:.6px;margin-bottom:12px;margin-top:24px}

  /* Index Cards */
  .index-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:4px}
  .index-card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:18px 20px}
  .index-name{font-size:12px;color:#8b949e;margin-bottom:6px}
  .index-value{font-size:26px;font-weight:700;margin-bottom:4px}
  .index-change{font-size:13px;font-weight:600}
  .up{color:#3fb950}.down{color:#f85149}.flat{color:#8b949e}

  /* Breadth */
  .breadth-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:4px}
  .breadth-card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px 20px}
  .breadth-label{font-size:12px;color:#8b949e;margin-bottom:10px}
  .breadth-bar{display:flex;height:8px;border-radius:4px;overflow:hidden;margin-bottom:8px}
  .bar-up{background:#3fb950}
  .bar-flat{background:#6e7681}
  .bar-down{background:#f85149}
  .breadth-nums{display:flex;gap:16px;font-size:12px}
  .b-up{color:#3fb950}.b-flat{color:#8b949e}.b-down{color:#f85149}

  .two-col{display:grid;grid-template-columns:1.4fr 1fr;gap:16px;margin-bottom:4px}
  .card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:18px 20px}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th{text-align:left;padding:7px 10px;color:#8b949e;border-bottom:1px solid #30363d;font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.4px}
  td{padding:9px 10px;border-bottom:1px solid #21262d}
  tr:last-child td{border-bottom:none}
  tr:hover td{background:#1c2128}
  .ticker-tag{font-size:10px;color:#8b949e;margin-left:4px}

  .macro-list{display:flex;flex-direction:column;gap:2px}
  .macro-item{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #21262d;font-size:13px}
  .macro-item:last-child{border-bottom:none}
  .macro-name{color:#8b949e;font-size:12px}
  .macro-val{font-weight:600}

  .movers-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:4px}
  .movers-card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px 20px}
  .mover-row{display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid #21262d;font-size:12px}
  .mover-row:last-child{border-bottom:none}
  .mover-name{font-weight:500;max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .mover-pct{font-weight:700;font-size:13px;min-width:60px;text-align:right}

  /* AI 리포트 */
  .reports-layout{display:grid;grid-template-columns:260px 1fr;gap:16px;margin-bottom:4px}
  .report-list-item{padding:10px 12px;border-radius:6px;cursor:pointer;border-bottom:1px solid #21262d;transition:background .15s}
  .report-list-item:hover,.report-list-item.active{background:#1c2128}
  .report-list-item:last-child{border-bottom:none}
  .report-filename{font-size:12px;font-weight:600;margin-bottom:2px}
  .report-meta{font-size:11px;color:#8b949e}
  .report-viewer{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:20px 24px;height:480px;overflow-y:auto}
  .report-empty{color:#8b949e;font-size:13px;padding:20px 0}

  /* 스크리닝 탭 */
  .screening-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}
  .screening-date{font-size:13px;color:#8b949e}
  .screening-count{background:#1a4731;color:#3fb950;padding:4px 12px;border-radius:12px;font-size:12px;font-weight:700}
  .stock-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px}
  .stock-card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px 18px;transition:border-color .15s}
  .stock-card:hover{border-color:#58a6ff}
  .sc-top{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px}
  .sc-name{font-size:14px;font-weight:700}
  .sc-ticker{font-size:11px;color:#8b949e;margin-top:2px}
  .sc-score{background:#1c2128;border:1px solid #30363d;border-radius:8px;padding:6px 10px;text-align:center}
  .sc-score-num{font-size:20px;font-weight:800;color:#f0c040}
  .sc-score-label{font-size:10px;color:#8b949e;margin-top:1px}
  .sc-price-row{display:flex;gap:16px;margin-bottom:10px;font-size:13px}
  .sc-price{font-weight:700;font-size:15px}
  .sc-metrics{display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;margin-bottom:10px}
  .sc-metric{background:#0d1117;border-radius:6px;padding:6px 8px;text-align:center;position:relative;cursor:default}
  .sc-metric-val{font-size:12px;font-weight:700}
  .sc-metric-label{font-size:10px;color:#8b949e;margin-top:1px}

  /* 툴팁 */
  [data-tip]{position:relative}
  [data-tip]::after{
    content:attr(data-tip);
    position:absolute;
    bottom:calc(100% + 8px);
    left:50%;
    transform:translateX(-50%);
    background:#1c2128;
    border:1px solid #444c56;
    color:#e6edf3;
    font-size:11px;
    line-height:1.55;
    padding:8px 11px;
    border-radius:7px;
    white-space:pre-line;
    width:220px;
    text-align:left;
    pointer-events:none;
    opacity:0;
    transition:opacity .15s;
    z-index:999;
    box-shadow:0 4px 16px rgba(0,0,0,.5);
  }
  [data-tip]:hover::after{opacity:1}
  /* 화살표 */
  [data-tip]::before{
    content:'';
    position:absolute;
    bottom:calc(100% + 2px);
    left:50%;
    transform:translateX(-50%);
    border:5px solid transparent;
    border-top-color:#444c56;
    opacity:0;
    transition:opacity .15s;
    z-index:1000;
  }
  [data-tip]:hover::before{opacity:1}
  /* 테이블 헤더 툴팁 */
  th[data-tip]::after{width:200px;bottom:calc(100% + 8px)}
  .sc-badges{display:flex;flex-wrap:wrap;gap:4px}
  .badge-ok{background:#1a4731;color:#3fb950;padding:2px 7px;border-radius:4px;font-size:10px;font-weight:600}
  .badge-warn{background:#2d2208;color:#e3b341;padding:2px 7px;border-radius:4px;font-size:10px;font-weight:600}
  .badge-bad{background:#2d0f0f;color:#f85149;padding:2px 7px;border-radius:4px;font-size:10px;font-weight:600}

  .md-content h1{font-size:18px;font-weight:700;margin-bottom:12px;color:#e6edf3;border-bottom:1px solid #30363d;padding-bottom:8px}
  .md-content h2{font-size:15px;font-weight:700;margin:14px 0 6px;color:#58a6ff}
  .md-content h3{font-size:13px;font-weight:700;margin:10px 0 4px;color:#e6edf3}
  .md-content p{font-size:13px;line-height:1.7;margin-bottom:8px;color:#c9d1d9}
  .md-content ul{margin:6px 0 8px 18px}
  .md-content li{font-size:13px;line-height:1.7;color:#c9d1d9;margin-bottom:2px}
  .md-content strong{color:#e6edf3;font-weight:700}
  .md-content em{color:#8b949e}
  .md-content code{background:#1c2128;padding:1px 5px;border-radius:3px;font-size:12px}
  .md-content hr{border:none;border-top:1px solid #30363d;margin:12px 0}
  .md-content table{margin:8px 0}
  .md-content th,.md-content td{padding:6px 10px;border:1px solid #30363d;font-size:12px}
  .md-content th{background:#1c2128;color:#8b949e}

  .empty-state{text-align:center;padding:60px 20px;color:#8b949e}
  .empty-state .empty-icon{font-size:40px;margin-bottom:12px}
  .empty-state p{font-size:13px}

  @media(max-width:900px){
    .index-grid,.two-col,.movers-grid,.reports-layout,.stock-grid{grid-template-columns:1fr}
    .breadth-grid{grid-template-columns:1fr}
  }
</style>
</head>
<body>
<header>
  <h1>
    <span class="status-dot" id="status-dot"></span>
    📊 한국 주식 분석 대시보드
  </h1>
  <div class="header-right">
    <span class="updated" id="updated">로딩 중...</span>
    <span class="badge" id="market-badge">-</span>
    <button class="btn" onclick="loadAll()">새로고침</button>
  </div>
</header>

<!-- 탭 -->
<div class="tabs">
  <div class="tab active" onclick="switchTab('market',this)">📈 시장 현황</div>
  <div class="tab" onclick="switchTab('screening',this)">🔍 성장주 스크리닝</div>
  <div class="tab" onclick="switchTab('reports',this)">📄 AI 리포트</div>
</div>

<main>

  <!-- ① 시장 현황 탭 -->
  <div class="tab-content active" id="tab-market">
    <div class="section-title">주요 지수</div>
    <div class="index-grid" id="index-grid"></div>

    <div class="section-title">시장 폭 (상승 / 보합 / 하락)</div>
    <div class="breadth-grid" id="breadth-grid"></div>

    <div class="section-title">관심종목 & 해외 지표</div>
    <div class="two-col">
      <div class="card">
        <table>
          <thead><tr><th>종목</th><th>현재가</th><th>등락률</th><th>거래량</th><th>52주</th></tr></thead>
          <tbody id="watchlist-body"></tbody>
        </table>
      </div>
      <div class="card">
        <div class="macro-list" id="macro-list"></div>
      </div>
    </div>

    <div class="section-title">오늘의 상위 등락 종목</div>
    <div class="movers-grid" id="movers-grid"></div>
  </div>

  <!-- ② 스크리닝 탭 -->
  <div class="tab-content" id="tab-screening">
    <div class="screening-header" style="margin-top:20px">
      <div>
        <div style="font-size:16px;font-weight:700">🔍 성장주 스크리닝 결과</div>
        <div class="screening-date" id="screening-date">로딩 중...</div>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <span class="screening-count" id="screening-count">-</span>
        <button class="btn" onclick="loadScreening()">새로고침</button>
      </div>
    </div>

    <!-- 요약 테이블 -->
    <div class="card" style="margin-bottom:16px;overflow-x:auto">
      <table id="screening-table">
        <thead>
          <tr>
            <th>순위</th><th>종목</th><th>현재가</th><th>등락률</th>
            <th data-tip="PER (주가수익비율)\n주가 ÷ EPS\n적정범위: 5~40배">PER</th>
            <th data-tip="PBR (주가순자산비율)\n주가 ÷ BPS\n적정범위: 0.3~5배">PBR</th>
            <th data-tip="RSI (14일)\n70↑ 과매수  30↓ 과매도">RSI</th>
            <th data-tip="20일 모멘텀\n20거래일 전 대비 등락률">모멘텀20</th>
            <th data-tip="거래량 비율\n오늘 ÷ 20일 평균\n1.5배↑ = 급증 신호">거래량비</th>
            <th data-tip="52주 신고가 대비\n0%=신고가  -20% 이내=돌파 임박">52W高比</th>
            <th data-tip="외국인 연속 순매수 일수\n3일↑ = 수급 유입 신호">외국인</th>
            <th data-tip="종합점수 (100점 만점)\n수급 45 + 기술 30\n+ 밸류 15 + 성장 10">종합점수</th>
          </tr>
        </thead>
        <tbody id="screening-tbody"></tbody>
      </table>
    </div>

    <!-- 카드 그리드 -->
    <div class="stock-grid" id="stock-grid"></div>
  </div>

  <!-- ③ AI 리포트 탭 -->
  <div class="tab-content" id="tab-reports">
    <div class="section-title" style="margin-top:20px">AI 분석 리포트</div>
    <div class="reports-layout">
      <div class="card" style="padding:8px;overflow-y:auto;max-height:540px">
        <div id="report-list"></div>
      </div>
      <div class="report-viewer" style="height:540px" id="report-viewer">
        <div class="report-empty">왼쪽에서 리포트를 선택하세요</div>
      </div>
    </div>
  </div>

</main>

<script>
// ── 탭 전환 ──────────────────────────────────────────────────────────────────
function switchTab(name, el){
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('tab-'+name).classList.add('active');
  if(name==='screening') loadScreening();
  if(name==='reports') loadReports();
}

// ── 헬퍼 ─────────────────────────────────────────────────────────────────────
function arrow(pct){ return pct > 0 ? '▲' : pct < 0 ? '▼' : '━' }
function cls(pct){ return pct > 0 ? 'up' : pct < 0 ? 'down' : 'flat' }
function fmt(n, d=2){ return parseFloat(n).toLocaleString('ko-KR', {minimumFractionDigits:d, maximumFractionDigits:d}) }
function fmtVol(n){ n = parseInt(n); return n >= 1e6 ? (n/1e6).toFixed(0)+'M' : n >= 1e3 ? (n/1e3).toFixed(0)+'K' : n }
function fmtMarcap(v){ v = parseFloat(v); return v >= 1e12 ? (v/1e12).toFixed(1)+'조' : v >= 1e8 ? (v/1e8).toFixed(0)+'억' : v }

function inlineFmt(t){
  return t
    .replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>')
    .replace(/\*(.+?)\*/g,'<em>$1</em>')
    .replace(/`(.+?)`/g,'<code>$1</code>');
}

function md2html(text){
  const lines = text.split('\n');
  const out = [];
  let i = 0;

  while(i < lines.length){
    const line = lines[i];

    // 헤더
    if(line.startsWith('### ')){
      out.push(`<h3>${inlineFmt(line.slice(4))}</h3>`);
      i++; continue;
    }
    if(line.startsWith('## ')){
      out.push(`<h2>${inlineFmt(line.slice(3))}</h2>`);
      i++; continue;
    }
    if(line.startsWith('# ')){
      out.push(`<h1>${inlineFmt(line.slice(2))}</h1>`);
      i++; continue;
    }

    // 구분선
    if(line.trim() === '---'){
      out.push('<hr>'); i++; continue;
    }

    // 테이블 블록
    if(line.startsWith('|')){
      const trows = [];
      let isFirst = true;
      while(i < lines.length && lines[i].startsWith('|')){
        const cells = lines[i].split('|').slice(1,-1).map(c=>c.trim());
        const isSep = cells.every(c => /^[\-: ]+$/.test(c));
        if(isSep){ i++; isFirst = false; continue; }
        const tag = isFirst ? 'th' : 'td';
        trows.push('<tr>'+cells.map(c=>`<${tag}>${inlineFmt(c)}</${tag}>`).join('')+'</tr>');
        i++; isFirst = false;
      }
      if(trows.length) out.push('<table>'+trows.join('')+'</table>');
      continue;
    }

    // 리스트 블록
    if(line.startsWith('- ') || line.startsWith('  - ')){
      const items = [];
      while(i < lines.length && (lines[i].startsWith('- ') || lines[i].startsWith('  - '))){
        items.push(`<li>${inlineFmt(lines[i].replace(/^\s*-\s/,''))}</li>`);
        i++;
      }
      out.push('<ul>'+items.join('')+'</ul>');
      continue;
    }

    // 빈 줄
    if(line.trim() === ''){
      out.push(''); i++; continue;
    }

    // 일반 단락
    out.push(`<p>${inlineFmt(line)}</p>`);
    i++;
  }

  return out.join('\n');
}

// ── 지수 ─────────────────────────────────────────────────────────────────────
function renderIndices(data){
  const map = [['KOSPI','KOSPI'],['KOSDAQ','KOSDAQ'],['KOSPI200','KOSPI 200']];
  document.getElementById('index-grid').innerHTML = map.map(([key,label]) => {
    const d = data[key];
    if(!d||!d.close) return `<div class="index-card"><div class="index-name">${label}</div><div class="index-value flat">-</div></div>`;
    const c = d.change_pct;
    return `<div class="index-card">
      <div class="index-name">${label}</div>
      <div class="index-value ${cls(c)}">${fmt(d.close,2)}</div>
      <div class="index-change ${cls(c)}">${arrow(c)} ${Math.abs(c).toFixed(2)}%</div>
    </div>`;
  }).join('');
}

// ── 시장 폭 ──────────────────────────────────────────────────────────────────
function renderBreadth(data){
  document.getElementById('breadth-grid').innerHTML = ['KOSPI','KOSDAQ'].map(mkt => {
    const b = data[mkt]||{};
    const adv=b.advance||0, unc=b.unchanged||0, dec=b.decline||0;
    const total=adv+unc+dec||1;
    return `<div class="breadth-card">
      <div class="breadth-label">${mkt} 시장 폭</div>
      <div class="breadth-bar">
        <div class="bar-up" style="width:${(adv/total*100).toFixed(1)}%"></div>
        <div class="bar-flat" style="width:${(unc/total*100).toFixed(1)}%"></div>
        <div class="bar-down" style="width:${(dec/total*100).toFixed(1)}%"></div>
      </div>
      <div class="breadth-nums">
        <span class="b-up">▲ ${adv}종목</span>
        <span class="b-flat">━ ${unc}</span>
        <span class="b-down">▼ ${dec}</span>
      </div>
    </div>`;
  }).join('');
}

// ── 관심종목 ──────────────────────────────────────────────────────────────────
function renderWatchlist(stocks){
  const tbody = document.getElementById('watchlist-body');
  if(!stocks||!stocks.length){
    tbody.innerHTML='<tr><td colspan="5" style="color:#8b949e;text-align:center;padding:20px">데이터 없음</td></tr>';
    return;
  }
  tbody.innerHTML = stocks.map(s => {
    const c=s.change_pct||0, close=s.close||0, h52=s.high52||0, l52=s.low52||0;
    let pos52='-';
    if(h52&&l52&&h52!==l52) pos52=`<span style="color:#8b949e">${((close-l52)/(h52-l52)*100).toFixed(0)}%</span>`;
    return `<tr>
      <td><span style="font-weight:600">${s.name||'-'}</span><span class="ticker-tag">${s.ticker}</span></td>
      <td style="font-weight:600">${close?fmt(close,0)+'원':'-'}</td>
      <td class="${cls(c)}" style="font-weight:700">${arrow(c)} ${Math.abs(c).toFixed(2)}%</td>
      <td style="color:#8b949e">${fmtVol(s.volume||0)}</td>
      <td>${pos52}</td>
    </tr>`;
  }).join('');
}

// ── 해외 지표 ─────────────────────────────────────────────────────────────────
const MACRO_ORDER=[
  {key:'^GSPC',label:'S&P 500'},{key:'^IXIC',label:'나스닥'},{key:'^DJI',label:'다우존스'},
  {key:'^VIX',label:'VIX'},{key:'^N225',label:'닛케이225'},{key:'KRW=X',label:'USD/KRW'},
  {key:'JPYKRW=X',label:'JPY/KRW'},{key:'CL=F',label:'WTI 원유'},{key:'GC=F',label:'금'},
];
function renderMacro(macro){
  document.getElementById('macro-list').innerHTML = MACRO_ORDER.map(({key,label}) => {
    const d=macro[key]; if(!d) return '';
    const c=d.change_pct||0;
    return `<div class="macro-item">
      <span class="macro-name">${d.name||label}</span>
      <div style="text-align:right">
        <div class="macro-val ${cls(c)}">${fmt(d.close,key==='KRW=X'||key==='JPYKRW=X'?1:2)}</div>
        <div style="font-size:11px;${c>0?'color:#3fb950':c<0?'color:#f85149':'color:#8b949e'}">${arrow(c)} ${Math.abs(c).toFixed(2)}%</div>
      </div>
    </div>`;
  }).join('');
}

// ── 상위 등락 ─────────────────────────────────────────────────────────────────
function renderMovers(movers){
  document.getElementById('movers-grid').innerHTML = [
    {label:'KOSPI 상승',  list:((movers.KOSPI||{}).gainers||[]).slice(0,10)},
    {label:'KOSPI 하락',  list:((movers.KOSPI||{}).losers||[]).slice(0,10)},
    {label:'KOSDAQ 상승', list:((movers.KOSDAQ||{}).gainers||[]).slice(0,10)},
    {label:'KOSDAQ 하락', list:((movers.KOSDAQ||{}).losers||[]).slice(0,10)},
  ].map(({label,list}) => {
    const rows = list.map(s => {
      const c=s.change_pct||0;
      return `<div class="mover-row">
        <span class="mover-name">${s.name||s.ticker}</span>
        <span class="mover-pct ${cls(c)}">${arrow(c)} ${Math.abs(c).toFixed(2)}%</span>
      </div>`;
    }).join('')||'<div style="color:#8b949e;font-size:12px;padding:8px 0">데이터 없음</div>';
    return `<div class="movers-card"><div style="font-size:12px;color:#8b949e;margin-bottom:10px">${label}</div>${rows}</div>`;
  }).join('');
}

// ── 스크리닝 ──────────────────────────────────────────────────────────────────
async function loadScreening(){
  const resp = await fetch('/api/screening');
  const data = await resp.json();
  const stocks = data.stocks || [];

  document.getElementById('screening-date').textContent =
    data.date ? `기준일: ${data.date}` : '스크리닝 결과 없음';
  document.getElementById('screening-count').textContent =
    `${stocks.length}개 통과`;

  // 요약 테이블
  const tbody = document.getElementById('screening-tbody');
  if(!stocks.length){
    tbody.innerHTML = '<tr><td colspan="12" style="text-align:center;color:#8b949e;padding:30px">스크리닝 결과가 없습니다. run_screener.py를 실행하세요.</td></tr>';
    document.getElementById('stock-grid').innerHTML = '';
    return;
  }

  tbody.innerHTML = stocks.map((s,i) => {
    const c=s.change_pct||0;
    const scoreColor = s.score>=70?'#3fb950':s.score>=50?'#f0c040':'#f85149';
    return `<tr>
      <td style="color:#8b949e;font-size:11px">${i+1}</td>
      <td><strong>${s.name}</strong><span class="ticker-tag">${s.ticker}</span></td>
      <td style="font-weight:600">${fmt(s.close,0)}원</td>
      <td class="${cls(c)}" style="font-weight:700">${arrow(c)}${Math.abs(c).toFixed(1)}%</td>
      <td>${s.per.toFixed(1)}</td>
      <td>${s.pbr.toFixed(2)}</td>
      <td style="${s.rsi>70?'color:#f85149':s.rsi<30?'color:#3fb950':''}">${s.rsi.toFixed(0)}</td>
      <td class="${cls(s.momentum_20d)}">${arrow(s.momentum_20d)}${Math.abs(s.momentum_20d).toFixed(1)}%</td>
      <td>${s.vol_ratio.toFixed(1)}x</td>
      <td>${s.pct_from_52w_high.toFixed(1)}%</td>
      <td>${parseInt(s.foreign_consec)}일</td>
      <td style="font-weight:800;color:${scoreColor}">${s.score.toFixed(0)}점</td>
    </tr>`;
  }).join('');

  // 카드 그리드
  document.getElementById('stock-grid').innerHTML = stocks.map(s => {
    const c = s.change_pct||0;
    const scoreColor = s.score>=70?'#3fb950':s.score>=50?'#f0c040':'#f85149';

    const badges = [];
    if(s.golden_cross)   badges.push('<span class="badge-ok">골든크로스</span>');
    else                 badges.push('<span class="badge-bad">데드크로스</span>');
    if(s.near_52w_high)  badges.push('<span class="badge-ok">52W고가근접</span>');
    if(s.volume_surge)   badges.push('<span class="badge-ok">거래량급증</span>');
    else                 badges.push('<span class="badge-warn">거래량보통</span>');
    if(s.foreign_consec>=3) badges.push(`<span class="badge-ok">외국인${parseInt(s.foreign_consec)}일연속</span>`);
    else                    badges.push('<span class="badge-warn">외국인미확인</span>');
    if(s.inst_turn)      badges.push('<span class="badge-ok">기관전환</span>');

    return `<div class="stock-card">
      <div class="sc-top">
        <div>
          <div class="sc-name">${s.name}</div>
          <div class="sc-ticker">${s.ticker} · ${s.market} · ${fmtMarcap(s.marcap)}</div>
        </div>
        <div class="sc-score">
          <div class="sc-score-num" style="color:${scoreColor}">${s.score.toFixed(0)}</div>
          <div class="sc-score-label">점수</div>
        </div>
      </div>
      <div class="sc-price-row">
        <div>
          <div class="sc-price">${fmt(s.close,0)}원</div>
          <div style="font-size:11px" class="${cls(c)}">${arrow(c)} ${Math.abs(c).toFixed(2)}%</div>
        </div>
      </div>
      <div class="sc-metrics">
        <div class="sc-metric" data-tip="PER (주가수익비율)\n주가 ÷ EPS\n\n낮을수록 저평가.\n• 적정: 5~40배\n• 이 종목: ${s.per.toFixed(1)}배">
          <div class="sc-metric-val">${s.per.toFixed(1)}</div>
          <div class="sc-metric-label">PER</div>
        </div>
        <div class="sc-metric" data-tip="PBR (주가순자산비율)\n주가 ÷ BPS\n\n1 미만이면 청산가치 이하.\n• 적정: 0.3~5배\n• 이 종목: ${s.pbr.toFixed(2)}배">
          <div class="sc-metric-val">${s.pbr.toFixed(2)}</div>
          <div class="sc-metric-label">PBR</div>
        </div>
        <div class="sc-metric" data-tip="RSI (상대강도지수, 14일)\n과매수·과매도 측정\n\n• 70 이상: 과매수 (조정 가능)\n• 30 이하: 과매도 (반등 가능)\n• 현재: ${s.rsi.toFixed(0)}">
          <div class="sc-metric-val" style="${s.rsi>70?'color:#f85149':s.rsi<30?'color:#3fb950':''}">${s.rsi.toFixed(0)}</div>
          <div class="sc-metric-label">RSI(14)</div>
        </div>
        <div class="sc-metric" data-tip="20일 모멘텀\n20거래일 전 대비 등락률\n\n추세 강도 측정.\n• 양수: 상승 추세\n• 현재: ${arrow(s.momentum_20d)}${Math.abs(s.momentum_20d).toFixed(1)}%">
          <div class="sc-metric-val ${cls(s.momentum_20d)}">${arrow(s.momentum_20d)}${Math.abs(s.momentum_20d).toFixed(1)}%</div>
          <div class="sc-metric-label">모멘텀20</div>
        </div>
        <div class="sc-metric" data-tip="거래량 비율\n오늘 거래량 ÷ 20일 평균\n\n• 1.5배 이상: 거래량 급증\n• 3배 이상: 강한 수급 신호\n• 현재: ${s.vol_ratio.toFixed(1)}배">
          <div class="sc-metric-val">${s.vol_ratio.toFixed(1)}x</div>
          <div class="sc-metric-label">거래량비</div>
        </div>
        <div class="sc-metric" data-tip="52주 신고가 대비\n현재가가 고가에서 얼마나 떨어졌는지\n\n• 0%: 52주 신고가\n• -20% 이내: 돌파 임박 구간\n• 현재: ${s.pct_from_52w_high.toFixed(1)}%">
          <div class="sc-metric-val">${s.pct_from_52w_high.toFixed(1)}%</div>
          <div class="sc-metric-label">52W高比</div>
        </div>
      </div>
      <div class="sc-badges">${badges.join('')}</div>
    </div>`;
  }).join('');
}

// ── 리포트 ────────────────────────────────────────────────────────────────────
let activeReport = null;
async function loadReports(){
  const resp = await fetch('/api/reports');
  const reports = await resp.json();
  const el = document.getElementById('report-list');
  if(!reports.length){
    el.innerHTML='<div style="color:#8b949e;font-size:12px;padding:12px">리포트 없음</div>';
    return;
  }
  el.innerHTML = reports.map(r => {
    const label = r.name.includes('morning')?'☀️ 오전 브리핑':'🌙 마감 분석';
    const date  = r.name.replace(/[^0-9]/g,'').slice(0,8);
    const dFmt  = date?`${date.slice(0,4)}-${date.slice(4,6)}-${date.slice(6,8)}`:r.name;
    return `<div class="report-list-item ${r.name===activeReport?'active':''}" onclick="openReport('${r.name}')">
      <div class="report-filename">${label}</div>
      <div class="report-meta">${dFmt} · ${(r.size/1024).toFixed(1)} KB</div>
    </div>`;
  }).join('');
}

async function openReport(name){
  activeReport = name;
  loadReports();
  const resp = await fetch('/api/report/'+encodeURIComponent(name));
  const text = await resp.text();
  document.getElementById('report-viewer').innerHTML =
    `<div class="md-content">${md2html(text)}</div>`;
}

// ── 시장 현황 ─────────────────────────────────────────────────────────────────
async function loadAll(){
  try{
    const resp = await fetch('/api/market');
    const data = await resp.json();
    if(data.error){
      document.getElementById('updated').textContent='오류: '+data.error;
      return;
    }
    document.getElementById('updated').textContent =
      '업데이트: '+new Date(data.updated_at).toLocaleTimeString('ko-KR');
    const dot=document.getElementById('status-dot');
    const badge=document.getElementById('market-badge');
    if(data.market_open){
      dot.className='status-dot'; badge.className='badge badge-open'; badge.textContent='장 중';
    } else {
      dot.className='status-dot closed'; badge.className='badge badge-closed'; badge.textContent='장 마감';
    }
    if(data.indices)  renderIndices(data.indices);
    if(data.breadth)  renderBreadth(data.breadth);
    if(data.watchlist) renderWatchlist(data.watchlist);
    if(data.macro)    renderMacro(data.macro);
    if(data.movers)   renderMovers(data.movers);
  } catch(e){
    document.getElementById('updated').textContent='로딩 실패: '+e.message;
  }
}

loadAll();
setInterval(loadAll, 60000);
</script>
</body>
</html>
"""


# ── HTTP 핸들러 ───────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _json(self, data):
        body = json.dumps(data, ensure_ascii=False, default=str).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, text: str, content_type: str = "text/plain") -> None:
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            self._text(HTML, "text/html")

        elif path == "/api/market":
            try:
                self._json(_get_market_data())
            except Exception as e:
                self._json({"error": str(e)})

        elif path == "/api/screening":
            try:
                self._json(_load_screening())
            except Exception as e:
                self._json({"error": str(e), "stocks": []})

        elif path == "/api/reports":
            self._json(_list_reports())

        elif path.startswith("/api/report/"):
            name = path[len("/api/report/"):]
            content = _read_report(name)
            if content:
                self._text(content)
            else:
                self.send_response(404)
                self.end_headers()

        else:
            self.send_response(404)
            self.end_headers()


# ── 실행 ─────────────────────────────────────────────────────────────────────

def main():
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"\n🚀 한국 주식 대시보드 실행 중 → {url}\n")
    print("   Ctrl+C 로 종료\n")
    try:
        subprocess.Popen(["open", url])
    except Exception:
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n서버 종료")
        server.server_close()


if __name__ == "__main__":
    main()
