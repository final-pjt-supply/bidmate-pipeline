"""PR diff를 Vertex AI Gemini로 리뷰하고 PR에 요약 코멘트를 답니다.
GitHub Actions(gemini-pr-review.yml)에서 호출됩니다.
"""
import os
import json
import sys
import requests
import google.auth
import google.auth.transport.requests
from google.oauth2 import service_account

GH_TOKEN = os.environ["GH_TOKEN"]
PR = os.environ["PR_NUMBER"]
REPO = os.environ["REPO"]
TITLE = os.environ.get("PR_TITLE", "")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
PROJECT_ID = os.environ["GCP_PROJECT_ID"]
MARKER = "<!-- gemini-pr-review -->"

# 1) 서비스 계정 인증
creds_json = os.environ["GOOGLE_CREDENTIALS"]
creds_info = json.loads(creds_json)
credentials = service_account.Credentials.from_service_account_info(
    creds_info,
    scopes=["https://www.googleapis.com/auth/cloud-platform"],
)
credentials.refresh(google.auth.transport.requests.Request())

# 2) diff 읽기
with open("diff.txt", encoding="utf-8", errors="ignore") as f:
    diff = f.read()

MAX = 40000
truncated = len(diff) > MAX
if truncated:
    diff = diff[:MAX]

if not diff.strip():
    print("변경 내용이 없어 리뷰를 건너뜁니다.")
    sys.exit(0)

# 3) 프롬프트
prompt = f"""당신은 숙련된 파이썬 코드 리뷰어입니다. 아래 GitHub Pull Request의 변경사항(diff)을 보고 한국어로 리뷰를 작성하세요.

PR 제목: {TITLE}

다음 형식을 지켜 마크다운으로 작성하세요:

## 요약
- 이 PR이 무엇을 바꾸는지 2~4줄로 설명

## 주요 리뷰
- 버그 가능성, 로직 오류, 예외 처리 누락
- 보안 이슈 (예: 하드코딩된 비밀키, SQL 인젝션 등)
- 파이썬 관례(PEP8), 가독성, 네이밍
- 각 항목은 가능하면 파일명을 함께 언급

## 칭찬할 점
- 잘 작성된 부분이 있으면 짧게

문제가 없으면 솔직하게 "큰 문제 없음"이라고 적으세요. 과한 칭찬이나 불필요한 장황함은 피하세요.

변경사항(diff):
```diff
{diff}
```"""

if truncated:
    prompt += "\n\n(참고: diff가 너무 커서 일부만 전달되었습니다.)"

# 4) Vertex AI Gemini API 호출
url = (
    f"https://us-central1-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}"
    f"/locations/us-central1/publishers/google/models/{MODEL}:generateContent"
)
resp = requests.post(
    url,
    headers={
        "Authorization": f"Bearer {credentials.token}",
        "Content-Type": "application/json",
    },
    json={"contents": [{"role": "user", "parts": [{"text": prompt}]}]},
    timeout=120,
)

if resp.status_code != 200:
    print("Vertex AI API 오류:", resp.status_code, resp.text[:500])
    sys.exit(1)

data = resp.json()
try:
    review = data["candidates"][0]["content"]["parts"][0]["text"]
except (KeyError, IndexError):
    print("응답 파싱 실패:", json.dumps(data)[:500])
    sys.exit(1)

body = (
    f"{MARKER}\n## 🤖 Gemini 코드 리뷰\n\n{review}\n\n"
    "---\n*자동 생성된 리뷰입니다. 참고용으로만 활용하세요.*"
)

# 5) 기존 봇 코멘트 갱신 또는 새로 작성
headers = {
    "Authorization": f"Bearer {GH_TOKEN}",
    "Accept": "application/vnd.github+json",
}
list_url = f"https://api.github.com/repos/{REPO}/issues/{PR}/comments"
existing = requests.get(list_url, headers=headers, timeout=60).json()

comment_id = None
if isinstance(existing, list):
    for c in existing:
        if MARKER in (c.get("body") or ""):
            comment_id = c["id"]
            break

if comment_id:
    r = requests.patch(
        f"https://api.github.com/repos/{REPO}/issues/comments/{comment_id}",
        headers=headers,
        json={"body": body},
        timeout=60,
    )
else:
    r = requests.post(list_url, headers=headers, json={"body": body}, timeout=60)

print("코멘트 게시 결과:", r.status_code)
if r.status_code >= 300:
    print(r.text[:500])
    sys.exit(1)
