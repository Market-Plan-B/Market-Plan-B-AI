# ocr_runner.py — 최종 안정화 통합본
from __future__ import annotations
import os, time, json, logging, argparse
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np
import cv2
from PIL import Image
import fitz  # PyMuPDF

# --- (선택) GPU 메모리 프로브: 없으면 무시 ---
try:
    import pynvml
    pynvml.nvmlInit()
    _NV_OK = True
except Exception:
    _NV_OK = False

LOGGER = logging.getLogger("ocr_runner")
if not LOGGER.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s"))
    LOGGER.addHandler(h)
LOGGER.setLevel(logging.INFO)

# =========================
# 1) 전처리
# =========================
@dataclass
class PreprocConfig:
    target_dpi: int = 240
    deskew: bool = True
    clahe: bool = False
    adaptive_threshold: bool = False

def _estimate_skew_angle(gray: np.ndarray) -> float:
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(blur, 50, 150, apertureSize=3)
    lines = cv2.HoughLines(edges, 1, np.pi/180, threshold=120)
    if lines is None:
        return 0.0
    angles = []
    for rho_theta in lines[:200]:
        for _rho, theta in rho_theta:
            angle = (theta - np.pi/2) * 180/np.pi
            if -30 <= angle <= 30:
                angles.append(angle)
    return float(np.median(angles)) if angles else 0.0

def _rotate(img: np.ndarray, deg: float) -> np.ndarray:
    if abs(deg) < 0.5:
        return img
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w//2, h//2), deg, 1.0)
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

def preprocess(img_bgr: np.ndarray, cfg: PreprocConfig) -> np.ndarray:
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    if cfg.deskew:
        angle = _estimate_skew_angle(gray)
        if abs(angle) > 0.1:
            img_bgr = _rotate(img_bgr, angle)
            gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    if cfg.clahe:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
        gray = clahe.apply(gray)
    if cfg.adaptive_threshold:
        bin_img = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 35, 10
        )
        img_bgr = cv2.cvtColor(bin_img, cv2.COLOR_GRAY2BGR)
    return img_bgr

# 차트 전용 전처리(라벨/축/범례 강화)
def preprocess_chart(img_bgr: np.ndarray) -> np.ndarray:
    h, w = img_bgr.shape[:2]
    scale = 1.7
    img = cv2.resize(img_bgr, (int(w*scale), int(h*scale)), interpolation=cv2.INTER_CUBIC)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8,8))
    gray = clahe.apply(gray)

    blur = cv2.GaussianBlur(gray, (3,3), 0)
    bin_img = cv2.adaptiveThreshold(
        blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 9
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2,2))
    bin_img = cv2.morphologyEx(bin_img, cv2.MORPH_CLOSE, kernel, iterations=1)
    return cv2.cvtColor(bin_img, cv2.COLOR_GRAY2BGR)

# 공통: JSON 저장 헬퍼
def _save_json(path: str, data: Dict[str, Any]):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)

# =========================
# 2) 엔진 베이스
# =========================
class BaseEngine:
    name: str = "base"
    def ocr_text(self, img_bgr: np.ndarray) -> str:
        raise NotImplementedError
    def ocr_json(self, img_bgr: np.ndarray) -> Dict[str, Any]:
        """엔진별 JSON 형태 출력을 원하면 오버라이드. 기본은 텍스트만."""
        return {"engine": self.name, "text": self.ocr_text(img_bgr)}

# =========================
# 3) PaddleOCR (예: 문서/한글)
# =========================
try:
    from paddleocr import PaddleOCR
except Exception:
    PaddleOCR = None

def _extract_texts_from_paddle(res: Any) -> List[str]:
    texts: List[str] = []
    if isinstance(res, list):
        if res and isinstance(res[0], list):
            for item in res[0]:
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

class PaddleTextEngine(BaseEngine):
    name = "paddleocr"
    def __init__(self, lang: str = "korean", debug: bool = False):
        if PaddleOCR is None:
            raise RuntimeError("paddleocr not installed")
        self.client = PaddleOCR(lang=lang)
        self._use_predict = hasattr(self.client, "predict")
        self.debug = debug

    def _run(self, img_bgr: np.ndarray):
        try:
            return self.client.predict(img_bgr) if self._use_predict else self.client.ocr(img_bgr)
        except Exception:
            img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
            return self.client.predict(img_rgb) if self._use_predict else self.client.ocr(img_rgb)

    def ocr_text(self, img_bgr: np.ndarray) -> str:
        res = self._run(img_bgr)
        if self.debug:
            _save_json("./ocr_results/_debug/paddle_raw_%d.json" % int(time.time()*1000), {"raw": res})
        texts = _extract_texts_from_paddle(res)
        return "\n".join(texts)

    def ocr_json(self, img_bgr: np.ndarray) -> Dict[str, Any]:
        res = self._run(img_bgr)
        lines = _extract_texts_from_paddle(res)
        return {"engine": self.name, "lines": lines, "count": len(lines)}

# =========================
# 4) TrOCR (Transformers, GPU→CPU 자동 폴백, JSON 저장)
# =========================
class TrOCREngine(BaseEngine):
    name = "trocr"
    def __init__(self, model_name="microsoft/trocr-base-printed", device: Optional[str] = None, fp16: bool = False, debug: bool = False):
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel
        import torch
        # 'use_fast' 경고 사전 억제용(일부 버전에서 인자 미지원 가능성 대비)
        try:
            self.processor = TrOCRProcessor.from_pretrained(model_name, use_fast=True)
        except TypeError:
            self.processor = TrOCRProcessor.from_pretrained(model_name)

        self.model = VisionEncoderDecoderModel.from_pretrained(model_name)
        self.debug = debug

        if device:
            self.device = device
        else:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"

        try:
            self.model.to(self.device)
            if fp16 and self.device == "cuda":
                try:
                    self.model.half()
                except Exception as e:
                    LOGGER.warning(f"⚠️ FP16 변환 실패: {e} → CPU로 전환합니다.")
                    self.device = "cpu"
                    self.model.to(self.device)
        except Exception as e:
            LOGGER.warning(f"⚠️ GPU 초기화 실패 ({e}) → CPU로 전환합니다.")
            self.device = "cpu"
            self.model.to(self.device)

        self.torch = torch

    def _gen(self, img_bgr: np.ndarray) -> str:
        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(img_rgb)
        pixel_values = self.processor(images=pil, return_tensors="pt").pixel_values.to(self.device)
        # 품질 안정화를 위한 기본 빔서치
        with self.torch.no_grad():
            generated_ids = self.model.generate(
                pixel_values,
                max_length=512,
                num_beams=4,
                do_sample=False,
                early_stopping=True
            )
        text = self.processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
        return text

    def ocr_text(self, img_bgr: np.ndarray) -> str:
        text = self._gen(img_bgr)
        return text

    def ocr_json(self, img_bgr: np.ndarray) -> Dict[str, Any]:
        text = self._gen(img_bgr)
        # 라인 분해(후처리)
        lines = [t for t in text.splitlines() if t.strip()] if text else []
        return {"engine": self.name, "text": text, "lines": lines, "count": len(lines), "device": self.device}

# =========================
# 5) ChartOCR (차트 특화 전처리 + kor→en 폴백 + JSON 저장)
# =========================
class ChartTextEngine(BaseEngine):
    name = "chartocr"
    def __init__(self, lang: str = "korean", debug: bool = False):
        self.debug = debug
        self.kor = PaddleTextEngine(lang="korean", debug=debug)
        self.eng = None  # 지연 생성

    def ocr_text(self, img_bgr: np.ndarray) -> str:
        proc = preprocess_chart(img_bgr)
        text_kor = self.kor.ocr_text(proc).strip()
        lines_kor = [t for t in text_kor.splitlines() if t.strip()]

        lines_eng: List[str] = []
        if len(lines_kor) == 0:
            if self.eng is None:
                self.eng = PaddleTextEngine(lang="en", debug=self.debug)
            text_eng = self.eng.ocr_text(proc).strip()
            lines_eng = [t for t in text_eng.splitlines() if t.strip()]

        final = lines_kor if len(lines_kor) >= len(lines_eng) else lines_eng
        return "\n".join(final)

    def ocr_json(self, img_bgr: np.ndarray) -> Dict[str, Any]:
        proc = preprocess_chart(img_bgr)
        text_kor = self.kor.ocr_text(proc).strip()
        lines_kor = [t for t in text_kor.splitlines() if t.strip()]

        lines_eng: List[str] = []
        if len(lines_kor) == 0:
            if self.eng is None:
                self.eng = PaddleTextEngine(lang="en", debug=self.debug)
            text_eng = self.eng.ocr_text(proc).strip()
            lines_eng = [t for t in text_eng.splitlines() if t.strip()]

        picked = "kor" if len(lines_kor) >= len(lines_eng) else "en"
        final = lines_kor if picked == "kor" else lines_eng
        return {
            "engine": self.name,
            "strategy": "kor-first-then-en",
            "picked": picked,
            "lines_kor": lines_kor,
            "lines_eng": lines_eng,
            "lines_final": final,
            "count": len(final)
        }

# =========================
# 6) 엔진 팩토리
# =========================
class BaseEngineFactory:
    def __init__(self, names: List[str], device_hint: Optional[str] = None, debug: bool = False):
        self.names = [n.lower() for n in names]
        self.device_hint = device_hint
        self.debug = debug

    def build(self) -> Dict[str, BaseEngine]:
        engines: Dict[str, BaseEngine] = {}
        LOGGER.info(f"Building engines: {self.names}")
        for n in self.names:
            if n in ("paddle", "paddleocr"):
                engines["paddleocr"] = PaddleTextEngine(lang="korean", debug=self.debug)
            elif n in ("trocr",):
                # fp16=False 기본화(충돌 방지); 내부에서 자동 폴백
                engines["trocr"] = TrOCREngine(device=self.device_hint or None, fp16=False, debug=self.debug)
            elif n in ("chart", "chartocr"):
                engines["chartocr"] = ChartTextEngine(lang="korean", debug=self.debug)
            else:
                LOGGER.warning(f"Unknown engine: {n}")
        if not engines:
            raise RuntimeError("No OCR engines could be initialized.")
        LOGGER.info(f"Engines built: {list(engines.keys())}")
        return engines

# =========================
# 7) 실행 루틴
# =========================
def _pix_to_bgr(pix: fitz.Pixmap) -> np.ndarray:
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
    if pix.n == 4:
        return cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
    elif pix.n == 3:
        return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    else:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

def run_ocr(pdf_path: str, out_dir: str, dpi: int, engine_names: List[str], debug: bool = False):
    os.makedirs(out_dir, exist_ok=True)
    if debug:
        os.makedirs(os.path.join(out_dir, "_debug"), exist_ok=True)

    try:
        doc = fitz.open(pdf_path)
        num_pages = len(doc)
    except Exception as e:
        LOGGER.error(f"Failed to open PDF {pdf_path}: {e}")
        return

    pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
    engines = BaseEngineFactory(engine_names, debug=debug).build()

    for page_num in range(num_pages):
        item_id = f"{pdf_name}_p{page_num}"
        LOGGER.info(f"Processing Page {page_num + 1}/{num_pages} of {pdf_path}")
        try:
            page = doc[page_num]
            pix = page.get_pixmap(dpi=dpi)
            img_bgr = _pix_to_bgr(pix).copy()
        except Exception as e:
            LOGGER.error(f"Failed to load page {page_num} from {pdf_path}: {e}")
            continue

        img_proc = preprocess(img_bgr, PreprocConfig(target_dpi=dpi))
        if debug:
            cv2.imwrite(os.path.join(out_dir, "_debug", f"{item_id}_proc.png"), img_proc)

        for name, eng in engines.items():
            LOGGER.info(f"  - Running engine: {name}")
            t0 = time.time()
            try:
                # 모든 엔진은 공통 JSON을 우선 받는다
                result_json = eng.ocr_json(img_proc)
                text = "\n".join(result_json.get("lines", [])) if "lines" in result_json else result_json.get("text", "")
            except Exception as e:
                LOGGER.error(f"Engine {name} failed on {item_id}: {e}")
                result_json = {"engine": name, "error": str(e)}
                text = f"ERROR: {e}"
            latency_ms = (time.time() - t0) * 1000
            LOGGER.info(f"    Engine {name} finished in {latency_ms:.1f} ms")

            # TXT 저장
            out_txt = os.path.join(out_dir, f"{item_id}__{name}.txt")
            try:
                with open(out_txt, "w", encoding="utf-8") as f:
                    f.write(text or "")
                LOGGER.info(f"    Result saved to {out_txt}")
            except Exception as e:
                LOGGER.error(f"Failed to write TXT: {e}")

            # JSON 저장 (페이지별/엔진별 표준화)
            out_json = os.path.join(out_dir, f"{item_id}__{name}.json")
            try:
                standard = {
                    "engine": name,
                    "page_index": page_num,
                    "pdf": os.path.abspath(pdf_path),
                    "dpi": dpi,
                    "elapsed_ms": round(latency_ms, 1),
                    **result_json
                }
                _save_json(out_json, standard)
                LOGGER.info(f"    JSON saved to {out_json}")
            except Exception as e:
                LOGGER.error(f"Failed to write JSON: {e}")

    doc.close()
    LOGGER.info(f"Done. All pages processed. Results are in {out_dir}")

# =========================
# 8) CLI
# =========================
def _cli():
    ap = argparse.ArgumentParser(description="Run OCR on a PDF file and save results.")
    ap.add_argument("--pdf", required=True, help="Path to the PDF file to process.")
    ap.add_argument("--out_dir", default="./ocr_results", help="Directory to save OCR output files.")
    ap.add_argument("--dpi", type=int, default=240, help="DPI for rendering PDF pages.")
    ap.add_argument(
        "--engine", nargs="+", default=["paddleocr", "trocr"],
        choices=["paddleocr", "trocr", "chartocr"], help="OCR engine(s) to use."
    )
    ap.add_argument("--debug", action="store_true", help="Dump debug images/raws.")
    args = ap.parse_args()
    run_ocr(args.pdf, args.out_dir, args.dpi, args.engine, debug=args.debug)

if __name__ == "__main__":
    _cli()
