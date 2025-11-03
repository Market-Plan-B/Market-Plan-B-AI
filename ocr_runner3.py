from __future__ import annotations
"""
✅ PaddleOCR + Donut ChartOCR 하이브리드 구조
✅ PDF 내 텍스트 + 그래프 수치 모두 추출
✅ 결과를 ocr_results_sum1 폴더에 JSON/TXT로 저장
"""
from huggingface_hub import login

class DonutChartExtractor:
    def __init__(self, model_name="naver-clova-ix/donut-base-finetuned-chartqa"):
        # ✅ 환경변수로부터 토큰 읽기
        hf_token = os.environ.get("HF_TOKEN")
        if not hf_token:
            print("[WARN] HF_TOKEN 환경변수가 설정되지 않았습니다. 로그인 없이 시도합니다.")
        else:
            print("[INFO] Hugging Face 토큰 감지 → 인증 중...")
            login(token=hf_token)
        
        self.processor = DonutProcessor.from_pretrained(model_name, use_auth_token=hf_token)
        self.model = VisionEncoderDecoderModel.from_pretrained(model_name, use_auth_token=hf_token)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model.to(self.device)


import os, time, json, argparse, logging
import numpy as np
import cv2
from PIL import Image
import fitz  # PyMuPDF

from paddleocr import PaddleOCR
from transformers import DonutProcessor, VisionEncoderDecoderModel
import torch

# --------------------------------------
# 기본 설정
# --------------------------------------
LOGGER = logging.getLogger("hybrid_ocr")
if not LOGGER.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s"))
    LOGGER.addHandler(h)
LOGGER.setLevel(logging.INFO)

# --------------------------------------
# PaddleOCR 텍스트 추출
# --------------------------------------
class PaddleTextExtractor:
    def __init__(self, lang="korean"):
        self.ocr = PaddleOCR(use_textline_orientation=True, lang=lang)

    def extract(self, img_bgr: np.ndarray):
        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        res = self.ocr.ocr(img_rgb)
        texts = []
        for line in (res[0] or []):
            _pts, (txt, score) = line
            if txt:
                texts.append({"text": txt.strip(), "score": float(score)})
        return texts

# --------------------------------------
# Donut Chart OCR (그래프 인식)
# --------------------------------------
class DonutChartExtractor:
    def __init__(self, model_name="naver-clova-ix/donut-base-finetuned-chartqa"):
        self.processor = DonutProcessor.from_pretrained(model_name)
        self.model = VisionEncoderDecoderModel.from_pretrained(model_name)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        if "5060" in torch.cuda.get_device_name(0):
            self.device = "cpu"
            print("[INFO] RTX 5060Ti는 CUDA 미지원 → CPU로 전환합니다.")
        self.model.to(self.device)

    def extract(self, img_bgr: np.ndarray):
        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)
        pixel_values = self.processor(pil_img, return_tensors="pt").pixel_values.to(self.device)
        task_prompt = "<s_chart>"
        decoder_input_ids = self.processor.tokenizer(task_prompt, add_special_tokens=False, return_tensors="pt").input_ids.to(self.device)

        with torch.no_grad():
            outputs = self.model.generate(pixel_values, decoder_input_ids=decoder_input_ids, max_length=512)
        result = self.processor.batch_decode(outputs, skip_special_tokens=True)[0]
        try:
            parsed = json.loads(result[result.index("{"):])
        except Exception:
            parsed = {"raw": result}
        return parsed

# --------------------------------------
# PDF 처리 파이프라인
# --------------------------------------
def run_hybrid(pdf_path: str, out_dir: str, dpi: int, lang: str, debug: bool):
    os.makedirs(out_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    paddle = PaddleTextExtractor(lang)
    donut = DonutChartExtractor()

    for page_idx, page in enumerate(doc):
        page_id = f"{os.path.splitext(os.path.basename(pdf_path))[0]}_p{page_idx}"
        LOGGER.info(f"Processing Page {page_idx+1}/{len(doc)}")

        pix = page.get_pixmap(dpi=dpi)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
        if pix.n == 4:
            img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)

        # PaddleOCR 텍스트
        text_blocks = paddle.extract(img)

        # 차트 후보 감지 (간단히 이미지 내 특정 밀도 감지)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 80, 180)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        chart_results = []
        for i, cnt in enumerate(contours):
            x, y, w, h = cv2.boundingRect(cnt)
            if w > 400 and h > 250:
                crop = img[y:y+h, x:x+w]
                try:
                    parsed = donut.extract(crop)
                    chart_results.append({"bbox": [x, y, w, h], "chart_data": parsed})
                    if debug:
                        os.makedirs(os.path.join(out_dir, "_debug"), exist_ok=True)
                        cv2.imwrite(os.path.join(out_dir, "_debug", f"{page_id}_chart{i}.png"), crop)
                except Exception as e:
                    LOGGER.warning(f"Donut fail on chart {i}: {e}")

        # 결과 저장
        out_json = {
            "page": page_idx,
            "text_blocks": text_blocks,
            "chart_blocks": chart_results
        }
        json_path = os.path.join(out_dir, f"{page_id}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(out_json, f, ensure_ascii=False, indent=2)
        LOGGER.info(f"Saved: {json_path}")

    LOGGER.info(f"✅ 모든 페이지 처리 완료 → {out_dir}")

# --------------------------------------
# CLI
# --------------------------------------
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True, help="PDF 파일 경로")
    ap.add_argument("--out_dir", default="./ocr_results_sum1", help="결과 저장 폴더")
    ap.add_argument("--dpi", type=int, default=340, help="렌더링 DPI")
    ap.add_argument("--lang", default="korean", help="OCR 언어")
    ap.add_argument("--debug", action="store_true", help="디버그 모드")
    args = ap.parse_args()

    run_hybrid(args.pdf, args.out_dir, args.dpi, args.lang, args.debug)
