# services.py
import os
from typing import List, Tuple
from tqdm import tqdm
import fitz  # PyMuPDF
import pandas as pd

from etc import setup_logger, build_output_dirs, save_json
from schemas import TextSpan, TableMeta, GraphMeta, PageResult, OCRSummary

log = setup_logger()

def extract_pdf_data(
    pdf_path: str, output_root: str = "DOCS", lang: str = None, use_gpu: bool | None = None
) -> OCRSummary:
    """
    PDF Data Extraction Pipeline (using PyMuPDF)
    1. Open PDF with PyMuPDF.
    2. Extract text blocks, tables, and images directly from each page.
    3. Save results and generate a final summary.json
    """
    pb = build_output_dirs(
        output_root, pdf_stem=os.path.splitext(os.path.basename(pdf_path))[0]
    )
    doc = fitz.open(pdf_path)

    pages: List[PageResult] = []

    for pi, page in enumerate(tqdm(doc, desc="Pages")):
        w, h = int(page.rect.width), int(page.rect.height)
        page_res = PageResult(page_index=pi, width=w, height=h, raw_image_path=None)

        # --- Extract Text ---
        text_blocks = page.get_text("blocks")
        for block in text_blocks:
            x1, y1, x2, y2, text, _, _ = block
            bbox = (int(x1), int(y1), int(x2), int(y2))
            # Simple score of 1.0 since it's direct extraction
            page_res.texts.append(TextSpan(bbox=bbox, text=text.strip(), score=1.0))

        # --- Extract Tables ---
        tables = page.find_tables()
        for ti, table in enumerate(tables):
            df = table.to_pandas()
            bbox = tuple(int(x) for x in table.bbox)
            basename = f"page{pi:03d}_table{ti:03d}"
            
            html_path = os.path.join(pb.table_dir, f"{basename}.html")
            df.to_html(html_path, index=False)

            csv_path = os.path.join(pb.table_dir, f"{basename}.csv")
            df.to_csv(csv_path, index=False)

            page_res.tables.append(
                TableMeta(bbox=bbox, html_path=html_path, csv_path=csv_path)
            )

        # --- Extract Images ---
        images = page.get_images(full=True)
        for ii, img in enumerate(images):
            xref = img[0]
            base_image = doc.extract_image(xref)
            image_bytes = base_image["image"]
            image_ext = base_image["ext"]
            bbox = tuple(int(x) for x in page.get_image_bbox(img).irect)

            graph_path = os.path.join(pb.graph_dir, f"page{pi:03d}_fig{ii:03d}.{image_ext}")
            with open(graph_path, "wb") as f:
                f.write(image_bytes)
            page_res.graphs.append(GraphMeta(bbox=bbox, image_path=graph_path))

        pages.append(page_res)

    summary = OCRSummary(pdf_path=pdf_path, output_dir=pb.doc_root, pages=pages)
    save_json(summary.model_dump(), os.path.join(pb.doc_root, "summary.json"))
    log.info(f"Saved results under: {pb.doc_root}")
    return summary
