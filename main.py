import os
import argparse
from dotenv import load_dotenv
from fastapi import FastAPI
import uvicorn

from services import extract_pdf_data
from routers import router as ocr_router

def parse_args():
    p = argparse.ArgumentParser(description="PDF Data Extraction pipeline")
    p.add_argument("--pdf", type=str, help="Path to PDF file to process")
    p.add_argument("--out", type=str, default=os.getenv("OUTPUT_ROOT", "DOCS"))
    p.add_argument("--api", action="store_true", help="Run FastAPI server instead of CLI mode")
    p.add_argument("--host", type=str, default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    return p.parse_args()

def run_api(host: str, port: int):
    app = FastAPI(title="PDF Extraction API")
    app.include_router(ocr_router, prefix="/v1")
    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    load_dotenv()
    args = parse_args()
    if args.api:
        run_api(args.host, args.port)
    else:
        if not args.pdf:
            raise SystemExit("Please provide --pdf path (or run with --api).")
        extract_pdf_data(args.pdf, output_root=args.out)