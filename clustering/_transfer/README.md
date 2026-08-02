# _transfer (임시)

노트북으로 데이터를 옮기기 위한 **일회성 폴더**다. 받은 뒤 삭제한다.

- `vectors_servc.npy` — 용역 7,513건 제목 벡터 (float16, 원본 float32 대비 코사인 오차 평균 0.000085)
- `meta_servc.csv` — 같은 순서의 bid_id / title / biz_div

재생성 방법: `01_eda_preprocessing.ipynb` 실행 후 servc 부분만 잘라내면 된다.
