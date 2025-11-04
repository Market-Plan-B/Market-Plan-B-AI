from typing import List, Tuple, Optional
from pydantic import BaseModel

class TextSpan(BaseModel):
    bbox: Tuple[int, int, int, int]
    text: str
    score: float

class TableMeta(BaseModel):
    bbox: Tuple[int, int, int, int]
    html_path: Optional[str] = None
    csv_path: Optional[str] = None

class GraphMeta(BaseModel):
    bbox: Tuple[int, int, int, int]
    image_path: Optional[str] = None

class PageResult(BaseModel):
    page_index: int
    width: int
    height: int
    raw_image_path: Optional[str] = None
    texts: List[TextSpan] = []
    tables: List[TableMeta] = []
    graphs: List[GraphMeta] = []

class OCRSummary(BaseModel):
    pdf_path: str
    output_dir: str
    pages: List[PageResult]
