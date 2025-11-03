
from __future__ import annotations
import os, io, time, json, csv, math, argparse, hashlib, tempfile, logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import cv2
from PIL import Image
import fitz  # PyMuPDF
from pypdf import PdfReader
from rapidfuzz.distance import Levenshtein

# GPU mem probe (optional)
try:
    import pynvml
    pynvml.nvmlInit()
    _NV_OK = True
except Exception:
    _NV_OK = False

LOGGER = logging.getLogger("ocr_bench")
if not LOGGER.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s"))
    LOGGER.addHandler(h)
LOGGER.setLevel(logging.INFO)

@dataclass
class PreprocConfig:
    target_dpi: int = 240
    deskew: bool = True
    clahe: bool = False
    adaptive_threshold: bool = False

def _estimate_skew_angle(gray: np.ndarray) -> float:
    blur = cv2.GaussianBlur(gray, (3,3), 0)
    edges = cv2.Canny(blur, 50, 150, apertureSize=3)
    lines = cv2.HoughLines(edges, 1, np.pi/180, threshold=120)
    if lines is None: return 0.0
    angles = []
    for rho_theta in lines[:200]:
        for _rho, theta in rho_theta:
            angle = (theta - np.pi/2) * 180 / np.pi
            if -30 <= angle <= 30:
                angles.append(angle)
    return float(np.median(angles)) if angles else 0.0

def _rotate(img: np.ndarray, deg: float) -> np.ndarray:
    if abs(deg) < 0.5: return img
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
        bin_img = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                        cv2.THRESH_BINARY, 35, 10)
        img_bgr = cv2.cvtColor(bin_img, cv2.COLOR_GRAY2BGR)
    return img_bgr

class BaseEngine:
    name: str = "base"
    def ocr_text(self, img_bgr: np.ndarray) -> str:
        raise NotImplementedError

# PaddleOCR
try:
    from paddleocr import PaddleOCR
except Exception:
    PaddleOCR = None

class PaddleTextEngine(BaseEngine):
    name = "paddleocr"
    def __init__(self, lang="korean", rec_batch_num=12, use_gpu=True, show_log=False):
        if PaddleOCR is None:
            raise RuntimeError("paddleocr not installed")
        self.client = PaddleOCR(use_angle_cls=True, lang=lang, show_log=show_log,
                                use_gpu=use_gpu, rec_batch_num=rec_batch_num)
    def ocr_text(self, img_bgr: np.ndarray) -> str:
        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        res = self.client.ocr(img_rgb)
        texts = []
        for line in (res[0] or []):
            _pts, (txt, score) = line
            if txt: texts.append(txt.strip())
        return "\n".join(texts)

# TrOCR
class TrOCREngine(BaseEngine):
    name = "trocr"
    def __init__(self, model_name="microsoft/trocr-base-printed", device=None, fp16=True):
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel
        import torch
        self.processor = TrOCRProcessor.from_pretrained(model_name)
        self.model = VisionEncoderDecoderModel.from_pretrained(model_name)
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model.to(self.device)
        if fp16 and self.device.startswith("cuda"):
            self.model.half()
        self.torch = torch
    def ocr_text(self, img_bgr: np.ndarray) -> str:
        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(img_rgb)
        pixel_values = self.processor(images=pil, return_tensors="pt").pixel_values.to(self.device)
        with self.torch.no_grad():
            generated_ids = self.model.generate(pixel_values, max_length=512)
        text = self.processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
        return text

# Chart OCR (text only)
class ChartTextEngine(BaseEngine):
    name = "chart"
    def __init__(self, lang="korean", use_gpu=True):
        self.inner = PaddleTextEngine(lang=lang, use_gpu=use_gpu, rec_batch_num=12)
    def ocr_text(self, img_bgr: np.ndarray) -> str:
        return self.inner.ocr_text(img_bgr)

def load_image_or_pdf(src: str, dpi: int = 240) -> np.ndarray:
    if ".pdf" in src.lower():
        if "#" not in src:
            page_index = 0; pdf_path = src
        else:
            pdf_path, page_str = src.split("#", 1); page_index = int(page_str)
        with fitz.open(pdf_path) as doc:
            page = doc[page_index]
            pix = page.get_pixmap(dpi=dpi)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
            if pix.n == 4: img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
            return img.copy()
    else:
        img = cv2.imread(src, cv2.IMREAD_COLOR)
        if img is None: raise FileNotFoundError(f"Cannot read image: {src}")
        return img

def normalize_text(s: str) -> str:
    s = s.replace("\u200b","").replace("\xa0"," ").strip()
    s = "\n".join(line.strip() for line in s.splitlines() if line.strip())
    return s

def cer(ref: str, hyp: str) -> float:
    ref, hyp = normalize_text(ref), normalize_text(hyp)
    if not ref and not hyp: return 0.0
    if not ref: return float(len(hyp))
    return Levenshtein.distance(ref, hyp) / max(1, len(ref))

def wer(ref: str, hyp: str) -> float:
    ref = normalize_text(ref).split()
    hyp = normalize_text(hyp).split()
    if not ref and not hyp: return 0.0
    if not ref: return float(len(hyp))
    return Levenshtein.distance(" ".join(ref), " ".join(hyp)) / max(1, len(" ".join(ref)))

def gpu_mem_mb() -> int | None:
    try:
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        mem = pynvml.nvmlDeviceGetMemoryInfo(h)
        return int(mem.used / (1024*1024))
    except Exception:
        return None

class BaseEngineFactory:
    def __init__(self, names: list[str], device_hint: str | None = None):
        self.names = [n.lower() for n in names]
        self.device_hint = device_hint
    def build(self):
        engines = {}
        for n in self.names:
            if n in ("paddle","paddleocr"):
                engines["paddleocr"] = PaddleTextEngine(lang="korean", use_gpu=True)
            elif n in ("trocr",):
                engines["trocr"] = TrOCREngine(device=self.device_hint or None, fp16=True)
            elif n in ("chart","chartocr"):
                engines["chartocr"] = ChartTextEngine(lang="korean", use_gpu=True)
            else:
                raise ValueError(f"Unknown engine: {n}")
        return engines

def run_benchmark(dataset_path: str, out_dir: str, dpi: int, engine_names: list[str]):
    os.makedirs(out_dir, exist_ok=True)
    with open(dataset_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("items", [])
    engines = BaseEngineFactory(engine_names).build()

    csv_path = os.path.join(out_dir, "results.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as cf:
        import csv as _csv
        writer = _csv.writer(cf)
        writer.writerow(["id","type","engine","latency_ms","gpu_mem_mb","cer","wer"])

    for it in items:
        item_id = it["id"]; typ = it["type"]; src = it["source"]; gt = it.get("gt_text","")
        LOGGER.info(f"Item {item_id} ({typ}) - {src}")
        try:
            img = load_image_or_pdf(src, dpi=dpi)
        except Exception as e:
            LOGGER.error(f"Failed to load {src}: {e}"); continue

        img_proc = preprocess(img, PreprocConfig())

        for name, eng in engines.items():
            before_mem = gpu_mem_mb()
            t0 = time.time()
            try:
                text = eng.ocr_text(img_proc)
            except Exception as e:
                LOGGER.error(f"{name} failed on {item_id}: {e}"); text = ""
            latency_ms = (time.time() - t0) * 1000
            after_mem = gpu_mem_mb()
            used_mb = (None if (before_mem is None or after_mem is None) else max(0, after_mem - before_mem))

            c = cer(gt, text); w = wer(gt, text)
            dump = {
                "id": item_id, "type": typ, "engine": name,
                "source": src, "gt": gt, "pred": text,
                "metrics": {"cer": c, "wer": w, "latency_ms": latency_ms, "gpu_mem_mb": used_mb}
            }
            with open(os.path.join(out_dir, f"{item_id}__{name}.json"), "w", encoding="utf-8") as jf:
                json.dump(dump, jf, ensure_ascii=False, indent=2)

            with open(csv_path, "a", encoding="utf-8", newline="") as cf:
                import csv as _csv
                writer = _csv.writer(cf)
                writer.writerow([item_id, typ, name, f"{latency_ms:.1f}", used_mb if used_mb is not None else "", f"{c:.4f}", f"{w:.4f}"])

    LOGGER.info(f"Done. See CSV: {csv_path}")

def _cli():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True, help="Path to labels.json")
    ap.add_argument("--out_dir", default="./benchmark_out")
    ap.add_argument("--dpi", type=int, default=240)
    ap.add_argument("--engine", nargs="+", default=["paddleocr","trocr","chart"])
    args = ap.parse_args()
    run_benchmark(args.dataset, args.out_dir, args.dpi, args.engine)

if __name__ == "__main__":
    _cli()
