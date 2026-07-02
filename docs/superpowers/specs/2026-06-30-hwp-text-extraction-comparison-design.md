# HWP 텍스트 추출 방식 비교 실험 — 설계 문서

작성일: 2026-06-30

## 목적

같은 HWP 입찰 문서를 텍스트로 추출하는 두 파이프라인을 동일 조건에서 비교한다.

- **Method A**: HWP → PDF(한컴 COM) → txt(PyMuPDF)
- **Method B**: HWP → XML(pyhwp `hwp5proc xml --embedbin` 동등) → txt(lxml)

두 방식 모두 문서 중 이미지를 만나면 LLM 설명 텍스트로 그 자리를 대체한다(이번 실험에서는 Bedrock 미사용, 사람이 대신 설명 작성). 비교 축은 (1) 시간, (2) 정확도/품질, (3) CPU 사용량.

## 대상 입력

`C:\Users\Administrator\Downloads\hwp` 폴더의 HWP 3개와 같은 이름의 정답 txt 3개.

| HWP 파일 | 정답 txt |
|---|---|
| `01_입찰공고문(공간광변조기_PA202601001).hwp.hwp` | `01_입찰공고문(공간광변조기_PA202601001).hwp.txt` |
| `과업 지시서.hwp` | `과업 지시서.txt` |
| `입찰공고문_ 2026-3905-2.hwp` | `입찰공고문_2026-3905-2.txt` |

정답 txt는 원본 본문의 "중간 발췌"이며, 정확도 채점의 ground truth로 사용한다.

## 이미지 처리 정책

- `과업 지시서.hwp` 끝부분의 사진 1장만 실제 LLM 설명으로 대체한다(정답 txt가 이 이미지 하나만 설명하고 있음).
- 그 외 모든 임베디드 이미지(장식선, 로고, 도장 등)는 두 방식 공통으로 `[이미지]` placeholder를 그 자리에 삽입해 공정성을 맞춘다.
- 이미지 설명은 `image_captions.py`의 고정 사전에 보관해 두 방식이 동일 텍스트를 삽입하도록 한다. 키는 이미지 바이트의 해시(또는 파일 내 출현 순번), 값은 설명 문자열.

## 아키텍처 (공통 하니스 + 변환기 모듈 분리)

```
transforming/
  compare/
    harness.py        # 오케스트레이션 + 측정 + 채점 + 리포트 생성
    method_pdf.py     # Method A
    method_xml.py     # Method B
    image_captions.py # 사람이 작성한 이미지 설명 사전
  output/
    methodA/<name>.txt
    methodB/<name>.txt
    intermediate/     # 생성된 pdf, xml 보관(디버깅/검증용)
    report.md         # 최종 비교 리포트
```

각 변환기는 동일 인터페이스를 노출한다:

```python
def convert(hwp_path: str) -> ConvertResult
# ConvertResult: text:str, stage_timings:dict[str,float],
#                cpu_seconds:float, image_count:int, notes:list[str]
```

하니스만 측정·채점·리포트를 알고, 변환기는 변환만 안다(관심사 분리).

## Method A — PDF 경로 (`method_pdf.py`)

1. 한컴 COM(`HWPFrame.HwpObject`)으로 HWP→PDF 저장. 기존 `transforming/testing.py`의 검증된 호출 패턴 재사용(`Open(src, "HWP", "forceopen:true")` → `SaveAs(dst, "PDF")`). COM 초기화는 1회만, 파일 3개에 재사용.
2. PyMuPDF(`fitz`)로 PDF 열어 페이지 순서대로 `page.get_text("dict")`로 텍스트 블록과 이미지 블록을 위치(y, x) 순으로 정렬해 읽기 순서 재구성.
3. 이미지 블록을 만나면 해당 이미지 바이트 해시로 캡션 사전 조회 → 설명 또는 `[이미지]` 삽입.
4. 결과를 `output/methodA/<name>.txt`로 저장.

## Method B — XML 경로 (`method_xml.py`)

1. `hwp5` 파이썬 API로 in-process 변환(서브프로세스 `hwp5proc` 대신). `--embedbin` 동등: 그림은 BinData로 임베드. *in-process여야 psutil이 CPU를 정확히 포착함.*
2. lxml로 XML body를 순회: 문단/run 텍스트를 순서대로 수집, 표(table/cell)는 행·열 구조를 텍스트로 복원.
3. 그림 참조(`Picture`/`BinData` 연결)를 만나면 임베드된 이미지 바이트 해시로 캡션 사전 조회 → 설명 또는 `[이미지]` 삽입.
4. 결과를 `output/methodB/<name>.txt`로 저장. 생성한 XML은 `output/intermediate/`에 보관.

## 측정 방법

- **시간**: 단계별 `time.perf_counter` 델타(변환 단계, 텍스트 추출 단계, 합계).
- **CPU**: `psutil` user+system CPU초 델타.
  - Method B: in-process이므로 `psutil.Process()`(self) 측정으로 충분.
  - Method A의 **PDF 변환은 한컴이 별도 프로세스(Hwp.exe)에서 수행**하므로 해당 프로세스를 이름으로 찾아 `cpu_times()` 델타로 측정한다. PDF→txt 단계는 in-process라 self로 측정. 두 값을 합산.
  - **한계 명시**: 한컴 프로세스 CPU 귀속은 self 측정만큼 정밀하지 않을 수 있음 — 리포트에 캐비엇으로 기록.
- 파일 3개를 순차 처리(병렬 아님)해 자원 경합 없이 측정.

## 채점 방법 (정확도/품질)

- 정답 txt와 각 출력 txt를 공백/개행 정규화 후 비교.
- 정답이 "중간 발췌"이므로, 출력 전체에서 정답과 가장 잘 맞는 구간을 찾아 `difflib.SequenceMatcher` ratio(0~1) 계산.
- 추가 정성 지표(자동/반자동): 표 구조 보존 여부, 이미지 설명이 올바른 위치에 삽입됐는지, 문단 순서 보존 여부 → 리포트에 메모.

## 리포트 (`report.md`)

- 파일×방식 표: 변환 시간(s), 텍스트추출 시간(s), 합계 시간(s), CPU(s), 정확도(ratio).
- 방식별 종합 코멘트(장단점, 실패/주의사항), 한컴 CPU 측정 캐비엇.

## 비범위 (YAGNI)

- 병렬 변환, .hwpx 지원, Bedrock 연동, 캐시/증분 처리, GUI는 이번 실험 범위 밖.
- 모든 이미지에 대한 일반 캡션 자동화는 하지 않는다(지정된 1장만 설명).

## 위험/주의

- pyhwp(0.1b15)는 구버전 패키지지만 Python 3.14에서 wheel 빌드·설치 확인됨. `hwp5proc` CLI는 `xmllint` 부재 경고를 내지만 XML 출력 자체는 정상.
- 한컴 COM은 GUI 자동화라 환경에 따라 보안 모듈 등록(`FilePathCheckDLL`)이 필요.
