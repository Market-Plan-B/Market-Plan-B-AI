#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
One‑Shot OCR Benchmark Bootstrapper
===================================
팀장님 실행만 하시면 됩니다. 이 파일은 아래를 한 번에 처리합니다.

1) pip 패키지 설치 (GPU/딥러닝 스택 포함)
   - PyTorch (CUDA 12.x 휠), PaddleOCR(+paddlepaddle-gpu), Transformers, 기타 유틸
2) 벤치마크 스크립트(ocr_benchmark.py) 생성
3) 라벨 템플릿(labels_template.json) 생성
4) 다음 단계(실행 방법) 콘솔 출력

권장 Python: 3.11.x (3.12도 동작 가능)
GPU: NVIDIA CUDA 드라이버 최신(12.x 계열), 4060Ti 16GB 기준 튜닝 값 내장

사용법
------
$ python ocr_setup_and_benchmark_bootstrap.py

실행이 끝나면 같은 폴더에 ocr_benchmark.py, labels_template.json 이 생성됩니다.
labels_template.json을 복사/수정해 labels.json을 만들고, 아래 커맨드로 벤치마크를 실행하세요.

$ python ocr_benchmark.py --dataset /절대경로/labels.json --out_dir ./benchmark_out --dpi 240 --engine paddleocr trocr chart

※ 에러가 나면, 맨 아래 '추가 참고/FAQ' 주석을 확인하세요.
"""
from __future__ import annotations
import sys, subprocess, platform, shutil, json, os, textwrap, pathlib

SELF_DIR = pathlib.Path(__file__).resolve().parent

def pip_install(args: list[str]) -> None:
    cmd = [sys.executable, "-m", "pip", "install"] + args
    print(f"[pip] {' '.join(args)}")
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(res.stdout)
    if res.returncode != 0:
        raise RuntimeError(f"pip install failed: {' '.join(args)}")

def main():
    pyver = tuple(sys.version_info[:3])
    print(f"[info] Python {pyver[0]}.{pyver[1]}.{pyver[2]} on {platform.system()} {platform.machine()}")

    # -----------------------------
    # 1) Base tooling
    # -----------------------------
    pip_install(["-U", "pip", "setuptools", "wheel"])
    pip_install(["-U", "opencv-python", "pillow", "pymupdf", "pypdf", "rapidfuzz", "pynvml"])

    # -----------------------------
    # 2) PyTorch (CUDA 12.x wheels)
    # -----------------------------
    # cu124 휠이 CUDA 12.x 드라이버에서 광범위하게 동작(드라이버만 최신이면 OK)
    # 문제가 있으면 cu121로 교체 시도하도록 try/except 처리
    torch_ok = False
    for idx_url in ["https://download.pytorch.org/whl/cu124", "https://download.pytorch.org/whl/cu121"]:
        try:
            pip_install(["torch", "torchvision", "torchaudio", "--index-url", idx_url])
            torch_ok = True
            print(f"[info] Installed PyTorch from {idx_url}")
            break
        except Exception as e:
            print(f"[warn] PyTorch install from {idx_url} failed: {e}")
    if not torch_ok:
        print("[ERROR] PyTorch GPU 설치 실패. CPU 휠로 임시 진행합니다.")
        pip_install(["torch", "torchvision", "torchaudio"])  # CPU fallback

    # -----------------------------
    # 3) Transformers/Accelerate (for TrOCR)
    # -----------------------------
    pip_install(["-U", "transformers", "accelerate"])

    # -----------------------------
    # 4) PaddleOCR + paddlepaddle-gpu
    # -----------------------------
    # 기본 시도: PyPI의 paddlepaddle-gpu 3.x (리눅스/윈도 환경에 맞는 휠)
    # 만약 CUDA/OS 매칭 이슈 발생 시, 스크립트 마지막 주석의 FAQ 참고(전용 인덱스/도커 권장)
    try:
        pip_install(["paddlepaddle-gpu==3.2.0"])
    except Exception as e:
        print(f"[warn] paddlepaddle-gpu 기본 설치 실패: {e}")
        print("[warn] 일단 CPU 전용 paddlepaddle로 대체 후 계속 진행합니다. (GPU 사용 시 FAQ 참고)")
        pip_install(["paddlepaddle==3.2.0"])

    pip_install(["paddleocr>=3.0.1"])

    # -----------------------------
    # 5) (선택) ONNX Runtime GPU - 차트/후처리 가속용
    # -----------------------------
    try:
        pip_install(["onnxruntime-gpu"])
    except Exception as e:
        print(f"[warn] onnxruntime-gpu 설치 실패(선택 항목): {e}")

    # -----------------------------
    # 6) OCR 실행 스크립트 생성
    # -----------------------------
    ocr_runner_code = r'''
from __future__ import annotations
import os, io, time, json, csv, math, argparse, hashlib, tempfile, logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import cv2
from PIL import Image
import fitz  # PyMuPDF

# GPU mem probe (optional)
try:
    import pynvml
    pynvml.nvmlInit()
    _NV_OK = True
except Exception:
    _NV_OK = False

LOGGER = logging.getLogger("ocr_run")
if not LOGGER.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s"))
    LOGGER.addHandler(h)
LOGGER.setLevel(logging.INFO) # Default to INFO, can be overridden by --debug

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
            LOGGER.debug(f"Deskewing image by {angle:.2f} degrees.")
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
        res = self.client.ocr(img_rgb, cls=True)
        LOGGER.debug(f"PaddleOCR raw result: {res}")
        
        if not res or not res[0]:
            LOGGER.warning("PaddleOCR returned no result for this page.")
            return ""
        
        texts = []
        for line in res[0]:
            if line and len(line) == 2:
                txt, score = line[1]
                if txt:
                    texts.append(txt.strip())
                    LOGGER.debug(f'  - Detected text: "{txt.strip()}" with confidence {score:.2f}')
            else:
                LOGGER.warning(f"Unexpected line format from PaddleOCR: {line}")
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
        LOGGER.debug(f'TrOCR detected text: "{text}'')
        return text

# Chart OCR (text only)
class ChartTextEngine(BaseEngine):
    name = "chart"
    def __init__(self, lang="korean", use_gpu=True):
        self.inner = PaddleTextEngine(lang=lang, use_gpu=use_gpu, rec_batch_num=12)
    def ocr_text(self, img_bgr: np.ndarray) -> str:
        return self.inner.ocr_text(img_bgr)

class BaseEngineFactory:
    def __init__(self, names: list[str], device_hint: str | None = None):
        self.names = [n.lower() for n in names]
        self.device_hint = device_hint
    def build(self):
        engines = {}
        LOGGER.info(f"Building engines: {self.names}")
        for n in self.names:
            if n in ("paddle","paddleocr"):
                engines["paddleocr"] = PaddleTextEngine(lang="korean", use_gpu=True, show_log=False)
            elif n in ("trocr",):
                engines["trocr"] = TrOCREngine(device=self.device_hint or None, fp16=True)
            elif n in ("chart","chartocr"):
                engines["chartocr"] = ChartTextEngine(lang="korean", use_gpu=True, show_log=False)
            else:
                raise ValueError(f"Unknown engine: {n}")
        LOGGER.info(f"Engines built: {list(engines.keys())}")
        return engines

def run_ocr(pdf_path: str, out_dir: str, dpi: int, engine_names: list[str]):
    os.makedirs(out_dir, exist_ok=True)
    
    try:
        LOGGER.debug(f"Opening PDF: {pdf_path}")
        doc = fitz.open(pdf_path)
        num_pages = len(doc)
    except Exception as e:
        LOGGER.error(f"Failed to open PDF {pdf_path}: {e}")
        return

    pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
    engines = BaseEngineFactory(engine_names).build()

    for page_num in range(num_pages):
        item_id = f"{pdf_name}_p{page_num}"
        LOGGER.info(f"Processing Page {page_num + 1}/{num_pages} of {pdf_path}")
        
        try:
            page = doc[page_num]
            pix = page.get_pixmap(dpi=dpi)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
            if pix.n == 4: img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
            img = img.copy()
        except Exception as e:
            LOGGER.error(f"Failed to load page {page_num} from {pdf_path}: {e}"); continue

        img_proc = preprocess(img, PreprocConfig(target_dpi=dpi))

        for name, eng in engines.items():
            LOGGER.info(f"  - Running engine: {name}")
            t0 = time.time()
            try:
                text = eng.ocr_text(img_proc)
            except Exception as e:
                LOGGER.error(f"    Engine {name} failed on {item_id}: {e}", exc_info=True); text = f"ERROR: {e}"
            latency_ms = (time.time() - t0) * 1000
            LOGGER.info(f"    Engine {name} finished in {latency_ms:.1f} ms")

            output_filename = os.path.join(out_dir, f"{item_id}__{name}.txt")
            try:
                with open(output_filename, "w", encoding="utf-8") as f:
                    f.write(text)
                LOGGER.info(f"    Result saved to {output_filename}")
            except Exception as e:
                LOGGER.error(f"    Failed to write output file {output_filename}: {e}")

    doc.close()
    LOGGER.info(f"Done. All pages processed. Results are in {out_dir}")

def _cli():
    import argparse
    ap = argparse.ArgumentParser(description="Run OCR on a PDF file and save results.")
    ap.add_argument("--pdf", required=True, help="Path to the PDF file to process.")
    ap.add_argument("--out_dir", default="./ocr_results", help="Directory to save OCR output files.")
    ap.add_argument("--dpi", type=int, default=240, help="DPI for rendering PDF pages.")
    ap.add_argument("--engine", nargs="+", default=["paddleocr", "trocr"], help="OCR engine(s) to use.")
    ap.add_argument('--debug', action='store_true', help='Enable debug logging')
    args = ap.parse_args()

    if args.debug:
        LOGGER.setLevel(logging.DEBUG)

    run_ocr(args.pdf, args.out_dir, args.dpi, args.engine)

if __name__ == "__main__":
    _cli()
'''
    (SELF_DIR / "ocr_runner.py").write_text(ocr_runner_code, encoding="utf-8")

    print("\n[완료] 의존성 설치 & 스크립트 생성을 마쳤습니다.\n")
    print(f" - 생성: {SELF_DIR / 'ocr_runner.py'}")
    print("\n다음 단계를 진행하세요:")
    print("1) 아래 명령어를 사용하여 ocr_runner.py를 실행합니다.")
    print("   python ocr_runner.py --pdf /절대경로/파일명.pdf --engine paddleocr --debug")
    print("\n   - --pdf: OCR을 적용할 PDF 파일의 절대 경로")
    print("   - --engine: 사용할 OCR 엔진")
    print("   - --debug: 실행 과정의 상세 로그를 출력합니다.")
    print("\n2) 결과는 이전과 동일하게 ocr_results/ 폴더에 저장됩니다.")
    print("3) 문제가 계속되면 --debug 플래그로 실행한 후 콘솔에 출력되는 전체 로그를 알려주세요.")

if __name__ == "__main__":
    main()
