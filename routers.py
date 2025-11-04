from fastapi import APIRouter
from pydantic import BaseModel
from services import extract_pdf_data

router = APIRouter()

class PDFRequest(BaseModel):
    pdf_path: str
    output_root: str = "DOCS"

@router.get("/health")
def health():
    return {"status": "ok"}

@router.post("/extract")
def run_extraction(req: PDFRequest):
    summary = extract_pdf_data(req.pdf_path, output_root=req.output_root)
    return summary.model_dump()