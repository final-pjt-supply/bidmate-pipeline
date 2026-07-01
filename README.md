# 나라장터 curated JSON 첨부문서 저장 도구

이 프로젝트는 파이프라인의 2번 단계만 수행합니다. `raw_json.py`가 만든 **curated JSON**을 입력으로 받습니다.

```text
curated JSON(attachments) -> 문서 URL/메타데이터 추출 -> 실제 첨부문서 저장
```

API 호출, S3 저장, HWP/PDF 변환, 텍스트 추출, 임베딩 단계는 포함하지 않습니다.

## 설치

```bash
pip install -r requirements.txt
```

## 실행

기본값은 `BASE_DIR/curated` 폴더 전체를 처리합니다(`raw_json.py`와 동일한 데이터 루트 `/Users/oloqlq/Desktop/bidding`).

```bash
python bid_pipeline.py
```

curated JSON 파일 하나만 처리할 때:

```bash
python bid_pipeline.py --raw-path /Users/oloqlq/Desktop/bidding/curated/bid_servc_20260630.json
```

기본값은 전체 공고를 처리합니다. 일부만 테스트하려면:

```bash
python bid_pipeline.py --notice-limit 10
```

## 출력

첨부문서 메타데이터 (기본):

```text
/Users/oloqlq/Desktop/bidding/metadata/bid_files.json
```

다운로드 파일 (기본):

```text
/Users/oloqlq/Desktop/bidding/downloads/업무구분/공고번호/파일명
```

메타데이터에는 공고번호, 공고명, 수요기관, 공고기관, 파일명, 문서 URL, 다운로드 결과, 저장 경로가 들어갑니다.

## 주요 옵션

> `BASE_DIR` = `/Users/oloqlq/Desktop/bidding` (`raw_json.py`와 공유하는 데이터 루트)

- `--raw-path`: curated JSON 파일 또는 폴더, 기본값 `BASE_DIR/curated`
- `--notice-limit`: 처리할 공고 개수, 기본값 `0`(전체)
- `--metadata-path`: 메타데이터 JSON 저장 경로, 기본값 `BASE_DIR/metadata/bid_files.json`
- `--download-dir`: 첨부문서 저장 폴더, 기본값 `BASE_DIR/downloads`
- `--timeout`: 파일 다운로드 제한 시간 초, 기본값 60