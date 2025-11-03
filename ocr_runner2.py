# pdf_hybrid_extractor.py — 네이티브 PDF 텍스트 + 이미지(차트) OCR 하이브리드 추출기
from __future__ import annotations
import os, json, time, argparse, logging
from typing import Any, Dict, List, Tuple

import fitz  # PyMuPDF
import numpy as np
import cv2
from PIL import Image

# PaddleOCR
try:
    from paddleocr import PaddleOCR
except Exception as e:
    PaddleOCR = None

LOGGER = logging.getLogger("pdf_hybrid")
if not LOGGER.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s"))
    LOGGER.addHandler(h)
LOGGER.setLevel(logging.INFO)


# ---------------------------
# 차트/그림 OCR 전처리
# ---------------------------
def preprocess_chart(img_bgr: np.ndarray, max_side:int=3800) -> np.ndarray:
    # 1) 과대확대 방지 + 해상도 캡
    h, w = img_bgr.shape[:2]
    # 차트는 확대가 도움이 되는 경우가 많지만, 1.5x 정도로 제한
    scale = 1.5
    img = cv2.resize(img_bgr, (int(w*scale), int(h*scale)), interpolation=cv2.INTER_CUBIC)

    # 긴 변 제한 (Paddle 내부 리사이즈 전에 선제 조절)
    ms = max(img.shape[:2])
    if ms > max_side:
        r = max_side / ms
        img = cv2.resize(img, (int(img.shape[1]*r), int(img.shape[0]*r)), interpolation=cv2.INTER_AREA)

    # 2) 그레이스케일 + CLAHE
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8,8))
    gray = clahe.apply(gray)

    # 3) 미세 블러 + 적응 이진화
    blur = cv2.GaussianBlur(gray, (3,3), 0)
    bin_img = cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 9
    )

    # 4) 끊긴 획 연결(미세 닫기)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2,2))
    bin_img = cv2.morphologyEx(bin_img, cv2.MORPH_CLOSE, kernel, iterations=1)

    return cv2.cvtColor(bin_img, cv2.COLOR_GRAY2BGR)


# ---------------------------
# 네이티브 텍스트 추출
# ---------------------------
def extract_native_text(page: fitz.Page) -> Dict[str, Any]:
    """
    PyMuPDF dict 포맷으로 텍스트 블록/라인/스팬을 추출하고,
    각 라인의 bbox, 평균 폰트크기와 함께 리턴
    """
    data = page.get_text("dict")  # blocks/lines/spans 구조
    lines = []
    for b in data.get("blocks", []):
        if b.get("type", 0) != 0:
            # type 0: text, 1: image, 2: drawings
            continue
        for l in b.get("lines", []):
            # 각 라인의 모든 span을 합쳐 한 줄로
            txt_parts = []
            sizes = []
            for s in l.get("spans", []):
                t = s.get("text", "")
                if t:
                    txt_parts.append(t)
                    sz = s.get("size", None)
                    if isinstance(sz, (int, float)):
                        sizes.append(float(sz))
            merged = "".join(txt_parts).strip()
            if not merged:
                continue
            bbox = l.get("bbox", None)
            avg_size = float(sum(sizes)/len(sizes)) if sizes else None
            lines.append({"text": merged, "bbox": bbox, "avg_font_size": avg_size})
    return {"lines": lines}


# ---------------------------
# 페이지의 이미지(차트 후보) 추출
# ---------------------------
def list_image_blocks(page: fitz.Page) -> List[Tuple[fitz.Rect, int]]:
    """
    page.get_text('dict')의 block 중 type==1 이면 image block.
    해당 bbox를 이용해 clip 렌더링.
    리턴: [(bbox_rect, block_index), ...]
    """
    data = page.get_text("dict")
    blocks = data.get("blocks", [])
    image_blocks = []
    for i, b in enumerate(blocks):
        if b.get("type", 0) == 1:
            bbox = b.get("bbox", None)
            if bbox and len(bbox) == 4:
                rect = fitz.Rect(bbox)
                # 너무 작은 이미지는 스킵
                if rect.width < 60 or rect.height < 60:
                    continue
                image_blocks.append((rect, i))
    return image_blocks


# ---------------------------
# PaddleOCR 래퍼
# ---------------------------
class PaddleWrapper:
    def __init__(self, lang: str = "korean"):
        if PaddleOCR is None:
            raise RuntimeError("paddleocr not installed")
        # 큰 해상도 허용(버전별 파라미터 호환)
        try:
            self.ocr = PaddleOCR(lang=lang, det_limit_side_len=5000)
        except TypeError:
            try:
                self.ocr = PaddleOCR(lang=lang, det_max_side_len=5000)
            except TypeError:
                self.ocr = PaddleOCR(lang=lang)

        self.use_predict = hasattr(self.ocr, "predict")

    def run(self, img_bgr: np.ndarray) -> List[str]:
        # Paddle은 RGB/BGR 모두 처리 가능한 케이스가 있으나,
        # 안전하게 RGB로 한 번 시도 후 실패 시 BGR/원본으로 재시도
        try:
            img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
            res = self.ocr.predict(img_rgb) if self.use_predict else self.ocr.ocr(img_rgb)
        except Exception:
            res = self.ocr.predict(img_bgr) if self.use_predict else self.ocr.ocr(img_bgr)
        return self._extract_texts(res)

    @staticmethod
    def _extract_texts(res: Any) -> List[str]:
        texts: List[str] = []
        if isinstance(res, list):
            # 일반적으로 res[0]에 리스트가 들어있음
            if res and isinstance(res[0], list):
                for item in res[0]:
                    # item: [points, (text, score)] 형태가 많음
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        meta = item[1]
                        if isinstance(meta, (list, tuple)) and len(meta) >= 1:
                            txt = meta[0]
                            if isinstance(txt, str) and txt.strip():
                                texts.append(txt.strip())
                    elif isinstance(item, dict):
                        for key in ("text", "transcription", "label"):
                            if key in item and isinstance(item[key], str) and item[key].strip():
                                texts.append(item[key].strip())
            else:
                for obj in res:
                    if isinstance(obj, dict):
                        data = obj.get("data")
                        if isinstance(data, list):
                            for d in data:
                                if isinstance(d, dict):
                                    for key in ("text", "transcription", "label"):
                                        if key in d and isinstance(d[key], str) and d[key].strip():
                                            texts.append(d[key].strip())
        elif isinstance(res, dict):
            data = res.get("data")
            if isinstance(data, list):
                for d in data:
                    if isinstance(d, dict):
                        for key in ("text", "transcription", "label"):
                            if key in d and isinstance(d[key], str) and d[key].strip():
                                texts.append(d[key].strip())
        return texts


# ---------------------------
# 메인 파이프라인 (한 페이지)
# ---------------------------
def process_page(doc: fitz.Document, page_index: int, dpi: int, ocr: PaddleWrapper, out_dir: str, debug: bool=False) -> Dict[str, Any]:
    page = doc[page_index]
    page_id = f"p{page_index}"
    page_out = {"page_index": page_index, "dpi": dpi, "native": {}, "images": [], "merged_text": ""}

    # 1) 네이티브 텍스트(폰트사이즈 포함)
    native = extract_native_text(page)
    page_out["native"] = native

    # 2) 이미지 블록만 잘라서 차트 전처리 + OCR
    img_blocks = list_image_blocks(page)
    img_texts_all = []
    for rect, bi in img_blocks:
        # clip 렌더링
        mat = fitz.Matrix(dpi/72.0, dpi/72.0)
        pix = page.get_pixmap(matrix=mat, clip=rect)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        if pix.n == 4:
            img_bgr = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        elif pix.n == 3:
            img_bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        else:
            img_bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

        img_pre = preprocess_chart(img_bgr, max_side=3800)
        texts = ocr.run(img_pre)
        img_texts_all.extend(texts)

        # 디버그 저장
        if debug:
            os.makedirs(os.path.join(out_dir, "_debug"), exist_ok=True)
            cv2.imwrite(os.path.join(out_dir, "_debug", f"{page_id}_imgblock{bi}_clip.png"), img_bgr)
            cv2.imwrite(os.path.join(out_dir, "_debug", f"{page_id}_imgblock{bi}_proc.png"), img_pre)
            with open(os.path.join(out_dir, "_debug", f"{page_id}_imgblock{bi}_ocr.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(texts))

        page_out["images"].append({
            "block_index": bi,
            "bbox": [rect.x0, rect.y0, rect.x1, rect.y1],
            "ocr_lines": texts,
            "count": len(texts),
        })

    # 3) 병합 텍스트(간단 룰)
    #    - 네이티브 텍스트 라인 → 위에서 아래 순으로
    #    - 이미지 OCR 텍스트는 페이지 끝에 섹션으로 합침
    native_lines = [ln["text"] for ln in native.get("lines", []) if ln.get("text")]
    merged_lines = native_lines[:]
    if img_texts_all:
        merged_lines.append("")  # 구분 빈줄
        merged_lines.append("[[OCR (Images/Charts)]]")
        merged_lines.extend(img_texts_all)
    page_out["merged_text"] = "\n".join(merged_lines)

    return page_out


# ---------------------------
# 전체 문서 처리
# ---------------------------
def run(pdf_path: str, out_dir: str, dpi: int, lang: str, debug: bool):
    t0 = time.time()
    os.makedirs(out_dir, exist_ok=True)
    if debug:
        os.makedirs(os.path.join(out_dir, "_debug"), exist_ok=True)

    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        LOGGER.error(f"Failed to open PDF: {pdf_path} ({e})")
        return

    if PaddleOCR is None:
        raise RuntimeError("paddleocr is not installed. Please `pip install paddleocr`.")

    # Paddle OCR 준비
    try:
        ocr = PaddleWrapper(lang=lang)
    except Exception as e:
        LOGGER.error(f"Failed to init PaddleOCR: {e}")
        return

    all_pages = []
    base = os.path.splitext(os.path.basename(pdf_path))[0]

    for i in range(len(doc)):
        LOGGER.info(f"Processing page {i+1}/{len(doc)} ...")
        page_out = process_page(doc, i, dpi, ocr, out_dir, debug=debug)
        all_pages.append(page_out)

        # 페이지별 산출물 저장
        # 1) JSON
        with open(os.path.join(out_dir, f"{base}_p{i:02d}.json"), "w", encoding="utf-8") as f:
            json.dump(page_out, f, ensure_ascii=False, indent=2)

        # 2) TXT(병합 텍스트)
        with open(os.path.join(out_dir, f"{base}_p{i:02d}.txt"), "w", encoding="utf-8") as f:
            f.write(page_out.get("merged_text", "") or "")

    doc.close()

    # 문서 전체 합본 TXT
    merged_all = []
    for p in all_pages:
        merged_all.append(f"### [Page {p['page_index']+1}]")
        merged_all.append(p.get("merged_text", "") or "")
        merged_all.append("")  # 빈줄
    with open(os.path.join(out_dir, f"{base}__merged.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(merged_all))

    # 문서 전체 JSON
    with open(os.path.join(out_dir, f"{base}__all.json"), "w", encoding="utf-8") as f:
        json.dump({"pdf": os.path.abspath(pdf_path), "dpi": dpi, "pages": all_pages}, f, ensure_ascii=False, indent=2)

    LOGGER.info(f"Done. {len(all_pages)} pages processed in {time.time()-t0:.1f}s. Results -> {out_dir}")


# ---------------------------
# CLI
# ---------------------------
def _cli():
    ap = argparse.ArgumentParser(description="PDF Hybrid Extractor: native text + chart OCR")
    ap.add_argument("--pdf", required=True, help="Path to PDF")
    ap.add_argument("--out_dir", default="./ocr_results", help="Output directory")
    ap.add_argument("--dpi", type=int, default=320, help="DPI for image rendering (for image OCR)")
    ap.add_argument("--lang", default="korean", help="PaddleOCR language (korean/en/chinese etc.)")
    ap.add_argument("--debug", action="store_true", help="Save debug crops")
    args = ap.parse_args()

    run(args.pdf, args.out_dir, args.dpi, args.lang, args.debug)


if __name__ == "__main__":
    _cli()
