"""GitHub Actions에서 호출: 변경된 리포트를 텔레그램으로 전송"""
import os
import subprocess
import requests

BOT  = os.environ.get("BOT_TOKEN", "")
CHAT = os.environ.get("CHAT_ID", "")
URL  = f"https://api.telegram.org/bot{BOT}/sendMessage"

print(f"[notify] BOT 길이={len(BOT)} CHAT={CHAT}")

if not BOT or not CHAT:
    print("[notify] ERROR: 시크릿이 비어있음")
    exit(1)

# 이번 커밋에서 변경된 reports/*.md 파일 찾기
result = subprocess.run(
    ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
    capture_output=True, text=True
)
all_changed = result.stdout.strip().split("\n")
print(f"[notify] 전체 변경 파일: {all_changed}")

targets = [
    f for f in all_changed
    if f.startswith("reports/") and f.endswith(".md") and os.path.exists(f)
]
print(f"[notify] 전송 대상: {targets}")

if not targets:
    print("[notify] 전송할 파일 없음")
    exit(0)

def send(text):
    for i in range(0, len(text), 4000):
        r = requests.post(URL, json={
            "chat_id": CHAT,
            "text": text[i:i+4000],
            "disable_web_page_preview": True
        }, timeout=15)
        data = r.json()
        print(f"[notify] 전송 {r.status_code} ok={data.get('ok')} err={data.get('description','')}")

for path in targets:
    text = open(path, encoding="utf-8").read().strip()
    print(f"[notify] {path} ({len(text)}자) 전송 시작")
    send(text)

print("[notify] 완료")
