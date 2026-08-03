import bisect

import fitz

LANDSCAPE_RATIO = 1.2
MIN_BOX_WIDTH = 100
MIN_BOX_HEIGHT = 30


def _rect_contains(outer: fitz.Rect, inner: tuple, margin: float = 3.0) -> bool:
    x0, y0, x1, y1 = inner
    return (x0 >= outer.x0 - margin and y0 >= outer.y0 - margin and
            x1 <= outer.x1 + margin and y1 <= outer.y1 + margin)


def _rects_overlap(r: fitz.Rect, other: tuple) -> bool:
    x0, y0, x1, y1 = other
    return not (x1 < r.x0 or x0 > r.x1 or y1 < r.y0 or y0 > r.y1)


def _find_box_rects(page: fitz.Page, exclude: list[fitz.Rect]) -> list[fitz.Rect]:
    boxes = []
    for drawing in page.get_drawings():
        if drawing.get("color") is None:
            continue
        rect = drawing.get("rect")
        if rect is None:
            continue
        r = fitz.Rect(rect)
        if r.width < MIN_BOX_WIDTH or r.height < MIN_BOX_HEIGHT:
            continue
        if any(_rect_contains(ex, (r.x0, r.y0, r.x1, r.y1)) for ex in exclude):
            continue
        boxes.append(r)
    return boxes


def _extract_cell_content(
    page: fitz.Page,
    cell_rect: fitz.Rect,
    img_items: list[tuple[fitz.Rect, int]],
    counter: dict,
    registry: dict,
    page_num: int,
) -> str:
    elements = []
    for block in page.get_text("blocks", clip=cell_rect):
        x0, y0, x1, y1, content, _, block_type = block
        if block_type == 0 and content.strip():
            elements.append((y0, content.strip()))
    for img_rect, xref in img_items:
        counter["n"] += 1
        img_id = f"img_{counter['n']:03d}"
        registry[img_id] = {"page": page_num, "xref": xref, "rect": tuple(img_rect)}
        elements.append((img_rect.y0, f"[이미지:{img_id}]"))
    elements.sort(key=lambda e: e[0])
    return "\n".join(e[1] for e in elements)


def _recover_cell_text(
    page: fitz.Page,
    x_coords: list,
    y_coords: list,
    row_idx: int,
    col_idx: int,
) -> str | None:
    """table.extract()가 셀 값을 놓친 경우, 셀 좌표 구간에 실제 단어가 있을 때만 word-clip으로 복구."""
    if row_idx >= len(y_coords) - 1 or col_idx >= len(x_coords) - 1:
        return None
    clip = fitz.Rect(
        x_coords[col_idx], y_coords[row_idx],
        x_coords[col_idx + 1], y_coords[row_idx + 1],
    )
    words = page.get_text("words", clip=clip)
    if not words:
        return None
    words.sort(key=lambda w: (w[1], w[0]))
    return " ".join(w[4] for w in words).strip()


def _build_grid_cell_index(
    cells_flat: list,
    x_coords: list,
    y_coords: list,
) -> dict[tuple[int, int], tuple[int, fitz.Rect]]:
    """격자 칸 (row_idx, col_idx) → (cells_flat 인덱스, Rect) 색인.

    격자 칸마다 cells_flat 전체를 다시 순회하면 (행×열)×셀수 = O(n²)가 되어, 셀이 수천
    개인 표에서 Lambda 타임아웃(120초)에 걸렸다 — 2.4MB 문서가 120초를 넘겼다(#107).
    대신 셀이 자기가 덮는 칸에만 자신을 등록해, 전체 작업량을 격자 칸 수로 낮춘다.

    x_coords·y_coords가 정렬돼 있으므로 각 셀이 덮는 칸 범위를 bisect로 바로 구한다.
    setdefault라서 cells_flat 인덱스가 작은 셀이 우선한다 — 기존 선형 탐색 + break와
    같은 결과(먼저 만난 셀 우선)이고, 병합 셀 하나가 여러 칸을 덮는 동작도 보존된다.
    """
    centers_x = [(x_coords[i] + x_coords[i + 1]) / 2 for i in range(len(x_coords) - 1)]
    centers_y = [(y_coords[i] + y_coords[i + 1]) / 2 for i in range(len(y_coords) - 1)]

    index: dict[tuple[int, int], tuple[int, fitz.Rect]] = {}
    for idx, cell in enumerate(cells_flat):
        if cell is None:
            continue
        cr = fitz.Rect(cell)
        # bisect 경계가 기존 조건 cr.x0 <= cx <= cr.x1 과 정확히 같은 구간을 준다.
        col_lo = bisect.bisect_left(centers_x, cr.x0)
        col_hi = bisect.bisect_right(centers_x, cr.x1)
        row_lo = bisect.bisect_left(centers_y, cr.y0)
        row_hi = bisect.bisect_right(centers_y, cr.y1)
        for ci in range(col_lo, col_hi):
            for ri in range(row_lo, row_hi):
                index.setdefault((ri, ci), (idx, cr))
    return index


def _find_cell_for_image(img_rect: fitz.Rect, cells_flat: list) -> int | None:
    cx = (img_rect.x0 + img_rect.x1) / 2
    cy = (img_rect.y0 + img_rect.y1) / 2
    for idx, cell in enumerate(cells_flat):
        if cell is None:
            continue
        cr = fitz.Rect(cell)
        if cr.x0 <= cx <= cr.x1 and cr.y0 <= cy <= cr.y1:
            return idx
    return None


def _format_table(
    page: fitz.Page,
    table,
    all_img_items: list[tuple[fitz.Rect, int]],
    counter: dict,
    registry: dict,
    page_num: int,
) -> str:
    extracted = table.extract()

    try:
        cells_flat = table.cells
    except AttributeError:
        cells_flat = None

    if not cells_flat:
        lines = []
        for row in extracted:
            cells = [str(c or "").strip() for c in row]
            lines.append(" | ".join(cells))
        return "\n".join(lines)

    x_coords = sorted(set(c[i] for c in cells_flat if c is not None for i in (0, 2)))
    y_coords = sorted(set(c[i] for c in cells_flat if c is not None for i in (1, 3)))

    cells_flat_to_imgs: dict[int, list[tuple[fitz.Rect, int]]] = {}
    for img_rect, xref in all_img_items:
        idx = _find_cell_for_image(img_rect, cells_flat)
        if idx is not None:
            cells_flat_to_imgs.setdefault(idx, []).append((img_rect, xref))

    # 칸→셀 조회를 루프 밖에서 한 번만 만든다(#107). 범위를 벗어난 칸은 애초에 색인에
    # 없으므로, 기존의 row_idx/col_idx 상한 검사는 get()의 기본값으로 대체된다.
    grid_cell_index = _build_grid_cell_index(cells_flat, x_coords, y_coords)

    lines = []
    for row_idx, row in enumerate(extracted):
        row_cells = []
        for col_idx, cell_text in enumerate(row):
            cells_flat_idx, cell_rect = grid_cell_index.get((row_idx, col_idx), (None, None))

            cell_imgs = cells_flat_to_imgs.get(cells_flat_idx, []) if cells_flat_idx is not None else []

            if cell_imgs and cell_rect is not None:
                content = _extract_cell_content(page, cell_rect, cell_imgs, counter, registry, page_num)
            else:
                content = str(cell_text or "").strip()
                if not content:
                    recovered = _recover_cell_text(page, x_coords, y_coords, row_idx, col_idx)
                    if recovered:
                        print(
                            f"[표 복구] page={page_num} row={row_idx} col={col_idx} "
                            f"text={recovered!r} source=word_clip_fallback"
                        )
                        content = recovered

            row_cells.append(content)
        lines.append(" | ".join(row_cells))

    return "\n".join(lines)


def _extract_elements(
    page: fitz.Page,
    counter: dict,
    registry: dict,
    page_num: int,
    clip: fitz.Rect = None,
) -> list[tuple[float, str]]:
    elements = []

    all_images: list[tuple[float, fitz.Rect, int]] = []
    seen_xrefs = set()
    for img in page.get_images(full=True):
        xref = img[0]
        if xref in seen_xrefs:
            continue
        seen_xrefs.add(xref)
        for rect in page.get_image_rects(xref):
            if clip and not clip.intersects(rect):
                continue
            all_images.append((rect.y0, rect, xref))

    table_rects: list[fitz.Rect] = []
    for table in page.find_tables(clip=clip):
        rect = fitz.Rect(table.bbox)
        table_rects.append(rect)

        inner_img_items = [
            (img_rect, xref) for _, img_rect, xref in all_images
            if _rects_overlap(rect, (img_rect.x0, img_rect.y0, img_rect.x1, img_rect.y1))
        ]

        formatted = _format_table(page, table, inner_img_items, counter, registry, page_num)
        if formatted.strip():
            elements.append((rect.y0, f"[표]\n{formatted}\n[/표]"))

    for iy, img_rect, xref in all_images:
        if not any(_rects_overlap(tr, (img_rect.x0, img_rect.y0, img_rect.x1, img_rect.y1)) for tr in table_rects):
            counter["n"] += 1
            img_id = f"img_{counter['n']:03d}"
            registry[img_id] = {"page": page_num, "xref": xref, "rect": tuple(img_rect)}
            elements.append((iy, f"[이미지:{img_id}]"))

    box_rects = _find_box_rects(page, table_rects)
    box_contents: dict[tuple, list[tuple[float, str]]] = {}

    for block in page.get_text("blocks", clip=clip):
        x0, y0, x1, y1, content, block_no, block_type = block

        if block_type == 1:
            continue

        if any(_rect_contains(tr, (x0, y0, x1, y1)) for tr in table_rects):
            continue

        if not content.strip():
            continue

        in_box = None
        for box_rect in box_rects:
            if _rect_contains(box_rect, (x0, y0, x1, y1)):
                in_box = (box_rect.x0, box_rect.y0)
                break

        if in_box:
            box_contents.setdefault(in_box, []).append((y0, content.strip()))
        else:
            elements.append((y0, content.strip()))

    for (_, by0), contents in box_contents.items():
        sorted_text = "\n".join(c for _, c in sorted(contents))
        elements.append((by0, f"[박스]\n{sorted_text}\n[/박스]"))

    return sorted(elements, key=lambda e: e[0])


def extract_page_text(
    page: fitz.Page,
    page_num: int,
    counter: dict,
    registry: dict,
) -> str:
    w, h = page.rect.width, page.rect.height

    if w / h > LANDSCAPE_RATIO:
        mid = w / 2
        left = _extract_elements(page, counter, registry, page_num, clip=fitz.Rect(0, 0, mid, h))
        right = _extract_elements(page, counter, registry, page_num, clip=fitz.Rect(mid, 0, w, h))
        elements = left + right
    else:
        elements = _extract_elements(page, counter, registry, page_num)

    return "\n".join(content for _, content in elements)


def extract_text(pdf_path: str) -> dict:
    # FileNotFoundError, fitz.FileDataError 등은 호출부에서 처리할 것
    doc = fitz.open(pdf_path)
    counter = {"n": 0}
    registry = {}
    pages = {}
    for page_num, page in enumerate(doc, start=1):
        pages[page_num] = extract_page_text(page, page_num, counter, registry)
    doc.close()
    return {"pages": pages, "images": registry}


