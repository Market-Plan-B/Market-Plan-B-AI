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
    # 6) 벤치마크 스크립트 & 라벨 템플릿 생성
    # -----------------------------
    bench_code = r"""
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
"""
    (SELF_DIR / "ocr_benchmark.py").write_text(bench_code, encoding="utf-8")

    labels = {
        "items": [
            {
                "id": "sample_doc_p0",
                "type": "text",
                "source": "path/to/your.pdf#0",
                "gt_text": "여기에 0페이지의 정답 텍스트(요약 또는 단락) 일부를 넣으세요."
            },
            {
                "id": "sample_chart_img",
                "type": "chart_text",
                "source": "path/to/chart.png",
                "gt_text": "x-axis: Year, y-axis: Revenue ..."
            },
            {
                "id": "sample_table_p2",
                "type": "table_text",
                "source": "path/to/your.pdf#2",
                "gt_text": "표를 CSV 형태로 줄 단위로 붙여 넣어도 됩니다."
            }
        ]
    }
    (SELF_DIR / "labels_template.json").write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n[완료] 의존성 설치 & 스크립트 생성을 마쳤습니다.\n")
    print(f" - 생성: {SELF_DIR / 'ocr_benchmark.py'}")
    print(f" - 생성: {SELF_DIR / 'labels_template.json'}")
    print("\n다음 단계를 진행하세요:")
    print("1) labels_template.json을 복사해 labels.json을 만들고, 각 항목의 source/gt_text를 채웁니다.")
    print("2) 벤치마크 실행 예시:")
    print("   python ocr_benchmark.py --dataset /절대경로/labels.json --out_dir ./benchmark_out --dpi 240 --engine paddleocr trocr chart")
    print("3) 결과는 benchmark_out/results.csv와 per-item JSON으로 저장됩니다.")
    print("\n튜닝 팁:")
    print(" - DPI 220/240/300을 바꿔가며 CER/WER과 latency를 비교하세요.")
    print(" - PaddleOCR는 rec_batch_num=12~16 권장, 한국어 lang='korean'.")
    print(" - 차트는 텍스트 정확도만 평가합니다(데이터 복원은 별도 모델 필요).")
    print("\n[추가 참고/FAQ]")
    print(" - Paddle GPU 설치 오류 시: OS/CUDA별 전용 휠 인덱스 사용 또는 Docker 권장.")
    print(" - PyTorch CUDA 오류 시: --index-url을 cu121/cu124로 교체 시도, 드라이버를 최신으로 유지하세요.")
    print(" - Windows에서 VC++ 런타임 부족 오류 시 'Microsoft C++ Redistributable' 설치 후 재시도.")
if __name__ == "__main__":
    main()
