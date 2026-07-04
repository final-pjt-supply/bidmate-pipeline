# -*- coding: utf-8 -*-
"""이미지 바이트 → 한국어 캡션. NVIDIA Nemotron Nano VL 비전 모델 사용.

추출기(parsing/*_extractor.py)에 '주입'해서 쓰는 재사용 모듈이다. 핵심은
`make_describer()`로 만든 `describe(image_bytes) -> str | None` 하나:
- 성공: 한두 문장 한국어 캡션 문자열
- 실패(타임아웃·네트워크·미지원 포맷·빈 응답): None → 호출부는 그 이미지만 스킵

API 키는 하드코딩하지 않고 환경변수 NVIDIA_API_KEY에서 읽는다(.env).
이미지 포맷은 Pillow로 감지해 PNG/JPEG/WEBP가 아니면 PNG로 변환 후 전송하므로
HWP의 BMP/GIF 등도 처리된다(WMF/EMF 같은 벡터는 Pillow가 못 열어 None).
"""
import base64
import io
import os

import requests
from PIL import Image

INVOKE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
MODEL = "nvidia/nemotron-nano-12b-v2-vl"

# 입찰 공고 문서의 그림/도표를 검색·임베딩용으로 간결히 설명하도록 유도.
DEFAULT_QUERY = (
    "이 이미지는 입찰 공고 문서에 포함된 그림·도표·표·다이어그램이다. "
    "무엇을 나타내는지 한국어로 한두 문장으로 간결히 설명하라. "
    "표나 순서도라면 핵심 항목·흐름을 요약하라. 추측은 하지 말라."
)

# 비전 모델이 그대로 받는 포맷. 그 외(BMP/GIF/TIFF 등)는 PNG로 변환.
_PASSTHROUGH = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}

_TIMEOUT = 60      # 초
_MAX_RETRY = 1     # 실패 시 재시도 횟수
_MAX_TOKENS = 512


def _to_data_uri(image_bytes: bytes) -> str | None:
    """이미지 바이트를 data URI로. 미지원 포맷은 PNG로 변환, 못 열면 None."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        fmt = (img.format or "").upper()
    except Exception:
        return None  # 이미지가 아니거나 Pillow가 못 여는 포맷(WMF/EMF 등)

    if fmt in _PASSTHROUGH:
        mime, payload = _PASSTHROUGH[fmt], image_bytes
    else:
        buf = io.BytesIO()
        try:
            img.convert("RGB").save(buf, format="PNG")
        except Exception:
            return None
        mime, payload = "image/png", buf.getvalue()

    b64 = base64.b64encode(payload).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def make_describer(api_key: str | None = None, query: str = DEFAULT_QUERY):
    """describe(image_bytes) -> str | None 을 반환. 키 없으면 즉시 에러(빠른 실패)."""
    key = api_key or os.getenv("NVIDIA_API_KEY")
    if not key:
        raise RuntimeError(
            "NVIDIA_API_KEY가 설정되지 않았습니다. .env에 NVIDIA_API_KEY를 추가하세요."
        )
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    def describe(image_bytes: bytes) -> str | None:
        uri = _to_data_uri(image_bytes)
        if uri is None:
            return None  # 미지원 포맷 → 캡션 스킵
        payload = {
            "model": MODEL,
            "max_tokens": _MAX_TOKENS,
            "temperature": 0.2,
            "top_p": 1,
            "messages": [
                {"role": "system", "content": "/no_think"},
                {"role": "user", "content": [
                    {"type": "text", "text": query},
                    {"type": "image_url", "image_url": {"url": uri}},
                ]},
            ],
            "stream": False,
        }
        for attempt in range(_MAX_RETRY + 1):
            try:
                r = requests.post(
                    INVOKE_URL, headers=headers, json=payload, timeout=_TIMEOUT
                )
                r.raise_for_status()
                text = r.json()["choices"][0]["message"]["content"].strip()
                return text or None
            except Exception:
                if attempt >= _MAX_RETRY:
                    return None  # 최종 실패 → 그 이미지만 스킵
        return None

    return describe


if __name__ == "__main__":
    # 수동 확인: python -m parsing.hwp_image_describer <이미지경로>
    import sys

    if len(sys.argv) < 2:
        print("usage: python -m parsing.hwp_image_describer <image_path>")
        raise SystemExit(1)
    desc = make_describer()
    with open(sys.argv[1], "rb") as f:
        print(desc(f.read()))
