def describe_image(image_bytes: bytes, img_id: str, page: int) -> str:
    # TODO: 비전 LLM 연결 예정 (전과정 파이프라인 검증 후 보완)
    return f"[이미지: {img_id} (페이지 {page})]"


# --- NVIDIA Build API 구현 (보관용) ---
# import base64
# import io
# import os
# import time
# from pathlib import Path
#
# import requests
# from PIL import Image
#
# NVIDIA_MODEL = "nvidia/nemotron-nano-12b-v2-vl"
# NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
# MAX_SIDE = 800
# PROMPT = (
#     "이 이미지는 공공 조달 입찰 문서에서 추출된 것입니다. "
#     "이미지 유형(차트, 구성도, 표, 로고 등)을 먼저 밝히고, "
#     "핵심 내용을 한국어로 간결하게 설명하세요."
# )
#
# def _load_api_key() -> str:
#     env_path = Path(__file__).parent.parent / ".env"
#     if env_path.exists():
#         for line in env_path.read_text(encoding="utf-8").splitlines():
#             if line.startswith("NVIDIA_API_KEY="):
#                 return line.split("=", 1)[1].strip()
#     return os.environ.get("NVIDIA_API_KEY", "")
#
# def _resize(image_bytes: bytes) -> tuple[bytes, str]:
#     img = Image.open(io.BytesIO(image_bytes))
#     if max(img.width, img.height) > MAX_SIDE:
#         img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
#     buf = io.BytesIO()
#     fmt = "JPEG" if img.mode == "RGB" else "PNG"
#     if img.mode not in ("RGB", "RGBA"):
#         img = img.convert("RGB")
#         fmt = "JPEG"
#     img.save(buf, format=fmt)
#     mime = "image/jpeg" if fmt == "JPEG" else "image/png"
#     return buf.getvalue(), mime
#
# def describe_image(image_bytes: bytes, img_id: str, page: int) -> str:
#     api_key = _load_api_key()
#     if not api_key:
#         return f"[이미지 설명 불가: API 키 없음 ({img_id})]"
#     image_bytes, mime = _resize(image_bytes)
#     b64 = base64.b64encode(image_bytes).decode("utf-8")
#     payload = {
#         "model": NVIDIA_MODEL,
#         "messages": [
#             {
#                 "role": "user",
#                 "content": [
#                     {"type": "text", "text": PROMPT},
#                     {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
#                 ],
#             }
#         ],
#         "max_tokens": 512,
#     }
#     headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
#     resp = None
#     for attempt in range(3):
#         try:
#             resp = requests.post(NVIDIA_URL, headers=headers, json=payload, timeout=120)
#         except requests.exceptions.Timeout:
#             if attempt < 2:
#                 time.sleep(10 * (attempt + 1))
#                 continue
#             return f"[이미지 설명 실패: 타임아웃 ({img_id})]"
#         if resp.status_code in (429, 500, 502, 503):
#             time.sleep(10 * (attempt + 1))
#             continue
#         break
#     if resp is None or resp.status_code != 200:
#         code = resp.status_code if resp is not None else "N/A"
#         return f"[이미지 설명 실패: HTTP {code} ({img_id})]"
#     try:
#         return resp.json()["choices"][0]["message"]["content"].strip()
#     except (KeyError, IndexError):
#         return f"[이미지 설명 파싱 실패 ({img_id})]"


# --- Gemini API 구현 (보관용) ---
# import base64
# import os
# import time
# from pathlib import Path
# import requests
#
# GEMINI_MODEL = "gemini-2.5-flash"
# GEMINI_URL = (
#     f"https://generativelanguage.googleapis.com/v1beta/models"
#     f"/{GEMINI_MODEL}:generateContent"
# )
# PROMPT = (
#     "이 이미지는 공공 조달 입찰 문서에서 추출된 것입니다. "
#     "이미지 유형(차트, 구성도, 표, 로고 등)을 먼저 밝히고, "
#     "핵심 내용을 한국어로 간결하게 설명하세요."
# )
#
# def _load_api_key() -> str:
#     env_path = Path(__file__).parent.parent / ".env"
#     if env_path.exists():
#         for line in env_path.read_text(encoding="utf-8").splitlines():
#             if line.startswith("GEMINI_API_KEY="):
#                 return line.split("=", 1)[1].strip()
#     return os.environ.get("GEMINI_API_KEY", "")
#
# def _detect_mime(image_bytes: bytes) -> str:
#     return "image/jpeg" if image_bytes[:2] == b"\xff\xd8" else "image/png"
#
# def describe_image(image_bytes: bytes, img_id: str, page: int) -> str:
#     api_key = _load_api_key()
#     if not api_key:
#         return f"[이미지 설명 불가: API 키 없음 ({img_id})]"
#     payload = {
#         "contents": [{"parts": [
#             {"text": PROMPT},
#             {"inline_data": {
#                 "mime_type": _detect_mime(image_bytes),
#                 "data": base64.b64encode(image_bytes).decode("utf-8"),
#             }},
#         ]}]
#     }
#     for attempt in range(3):
#         resp = requests.post(
#             GEMINI_URL, params={"key": api_key}, json=payload, timeout=30
#         )
#         if resp.status_code == 429:
#             time.sleep(15 * (attempt + 1))
#             continue
#         break
#     if resp.status_code != 200:
#         return f"[이미지 설명 실패: HTTP {resp.status_code} ({img_id})]"
#     try:
#         return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
#     except (KeyError, IndexError):
#         return f"[이미지 설명 파싱 실패 ({img_id})]"
