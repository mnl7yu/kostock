"""
텔레그램 봇 알림 전송
- 4096자 초과 시 자동 분할
- 마크다운 리포트 파일 첨부 지원
"""
from __future__ import annotations

import math
import time
from pathlib import Path

import requests

import config

_API_BASE = "https://api.telegram.org/bot{token}/{method}"

_MAX_TEXT = 4096        # 텔레그램 메시지 한도
_SPLIT_HEADER = "📄 *리포트 {part}/{total}*\n\n"


def _url(method: str) -> str:
    return _API_BASE.format(token=config.TELEGRAM_BOT_TOKEN, method=method)


def _post(method: str, **kwargs) -> dict:
    resp = requests.post(_url(method), timeout=30, **kwargs)
    resp.raise_for_status()
    return resp.json()


# ── 마크다운 이스케이프 (MarkdownV2 불필요, 기본 Markdown 사용) ───────────────

def _split_text(text: str, header_len: int = 0) -> list[str]:
    """최대 _MAX_TEXT 글자씩 분할 (단락 경계 우선)."""
    limit = _MAX_TEXT - header_len
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            parts.append(remaining)
            break
        # 단락(\n\n) 기준으로 자르기
        cut = remaining.rfind("\n\n", 0, limit)
        if cut == -1:
            # 줄바꿈 기준
            cut = remaining.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        parts.append(remaining[:cut])
        remaining = remaining[cut:].lstrip()
    return parts


def send_text(text: str) -> None:
    """긴 텍스트를 자동 분할해서 전송."""
    dummy_header = _SPLIT_HEADER.format(part=99, total=99)
    parts = _split_text(text, len(dummy_header))
    total = len(parts)
    for i, part in enumerate(parts, 1):
        body = (_SPLIT_HEADER.format(part=i, total=total) + part) if total > 1 else part
        _post(
            "sendMessage",
            json={
                "chat_id": config.TELEGRAM_CHAT_ID,
                "text": body,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
        )
        if total > 1 and i < total:
            time.sleep(1)   # 과부하 방지


def send_document(file_path: Path, caption: str = "") -> None:
    """마크다운 파일을 문서로 전송."""
    with open(file_path, "rb") as f:
        _post(
            "sendDocument",
            data={
                "chat_id": config.TELEGRAM_CHAT_ID,
                "caption": caption[:1024],
                # parse_mode 제거 — 파일명에 특수문자 있으면 400 에러
            },
            files={"document": (file_path.name, f, "text/plain")},
        )


def send_report(report_text: str, report_file: Path) -> None:
    """
    1) 리포트 전문 텍스트 메시지 전송
    2) .md 파일 문서 첨부 전송
    """
    send_text(report_text)
    time.sleep(1)
    send_document(report_file, caption=f"📎 {report_file.name}")
