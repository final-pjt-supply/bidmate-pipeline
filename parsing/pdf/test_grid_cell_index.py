# -*- coding: utf-8 -*-
"""격자 칸 → 셀 색인이 기존 선형 탐색과 같은 결과를 주는지 확인 (이슈 #107).

O(n²) 선형 탐색을 색인 조회로 바꾼 리팩토링이므로 결과값이 달라지면 안 된다.
실제 공고 PDF는 레포에 없어(data/sample 미포함) 표 추출 전체를 돌릴 수 없으므로,
셀 좌표만으로 두 구현을 직접 대조한다.
"""
import fitz

from parsing.pdf.pdf_extractor import _build_grid_cell_index


def _reference_lookup(cells_flat, x_coords, y_coords, row_idx, col_idx):
    """수정 전 구현 그대로 — 격자 칸마다 cells_flat 전체를 순회하고 먼저 만난 셀을 쓴다."""
    if not (row_idx < len(y_coords) - 1 and col_idx < len(x_coords) - 1):
        return None, None
    cy = (y_coords[row_idx] + y_coords[row_idx + 1]) / 2
    cx = (x_coords[col_idx] + x_coords[col_idx + 1]) / 2
    for idx, cell in enumerate(cells_flat):
        if cell is None:
            continue
        cr = fitz.Rect(cell)
        if cr.x0 <= cx <= cr.x1 and cr.y0 <= cy <= cr.y1:
            return idx, cr
    return None, None


def _coords(cells_flat):
    """_format_table이 하는 것과 동일하게 셀 좌표에서 격자 축을 만든다."""
    x = sorted(set(c[i] for c in cells_flat if c is not None for i in (0, 2)))
    y = sorted(set(c[i] for c in cells_flat if c is not None for i in (1, 3)))
    return x, y


def _assert_same(cells_flat, extra_rows=2, extra_cols=2):
    """모든 격자 칸(+범위 밖 여유분)에서 두 구현의 결과가 같은지 확인."""
    x_coords, y_coords = _coords(cells_flat)
    index = _build_grid_cell_index(cells_flat, x_coords, y_coords)

    for row_idx in range(len(y_coords) - 1 + extra_rows):
        for col_idx in range(len(x_coords) - 1 + extra_cols):
            want_idx, want_rect = _reference_lookup(
                cells_flat, x_coords, y_coords, row_idx, col_idx
            )
            got_idx, got_rect = index.get((row_idx, col_idx), (None, None))
            assert got_idx == want_idx, f"({row_idx},{col_idx}) 셀 인덱스 불일치"
            if want_rect is None:
                assert got_rect is None
            else:
                assert tuple(got_rect) == tuple(want_rect), f"({row_idx},{col_idx}) Rect 불일치"


def test_uniform_grid():
    """균일한 3열 × 2행 표."""
    cells = [
        (0, 0, 100, 50), (100, 0, 200, 50), (200, 0, 300, 50),
        (0, 50, 100, 100), (100, 50, 200, 100), (200, 50, 300, 100),
    ]
    _assert_same(cells)


def test_merged_cell_spans_multiple_columns():
    """병합 셀 하나가 여러 칸을 덮는 경우 — 두 칸 모두 같은 셀을 가리켜야 한다."""
    cells = [
        (0, 0, 200, 50),                                    # 1·2열 병합
        (200, 0, 300, 50),
        (0, 50, 100, 100), (100, 50, 200, 100), (200, 50, 300, 100),
    ]
    x_coords, y_coords = _coords(cells)
    index = _build_grid_cell_index(cells, x_coords, y_coords)
    assert index[(0, 0)][0] == 0
    assert index[(0, 1)][0] == 0     # 병합됐으니 같은 셀
    _assert_same(cells)


def test_merged_cell_spans_multiple_rows():
    """행 방향 병합."""
    cells = [
        (0, 0, 100, 100),                                   # 1·2행 병합
        (100, 0, 200, 50), (200, 0, 300, 50),
        (100, 50, 200, 100), (200, 50, 300, 100),
    ]
    x_coords, y_coords = _coords(cells)
    index = _build_grid_cell_index(cells, x_coords, y_coords)
    assert index[(0, 0)][0] == index[(1, 0)][0] == 0
    _assert_same(cells)


def test_none_entries_are_skipped():
    """cells_flat에 None이 섞여 있어도 건너뛴다(PyMuPDF가 실제로 None을 준다)."""
    cells = [
        None,
        (0, 0, 100, 50), (100, 0, 200, 50),
        None,
        (0, 50, 100, 100), (100, 50, 200, 100),
    ]
    _assert_same(cells)


def test_overlapping_cells_first_index_wins():
    """겹치는 셀이 있으면 인덱스가 작은 쪽 — 기존 break와 같은 우선순위."""
    cells = [
        (0, 0, 100, 50),
        (0, 0, 100, 50),     # 동일 영역 중복
        (100, 0, 200, 50),
    ]
    x_coords, y_coords = _coords(cells)
    index = _build_grid_cell_index(cells, x_coords, y_coords)
    assert index[(0, 0)][0] == 0     # 1이 아니라 0
    _assert_same(cells)


def test_ragged_grid():
    """열 폭이 불균일해 격자 축이 촘촘해지는 경우."""
    cells = [
        (0, 0, 30, 20), (30, 0, 155, 20), (155, 0, 300, 20),
        (0, 20, 75, 60), (75, 20, 200, 60), (200, 20, 300, 60),
        (0, 60, 300, 90),                                   # 전체 폭 병합
    ]
    _assert_same(cells)
