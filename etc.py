import os
import logging
import json

class PathBundle:
    def __init__(self, doc_root: str, raw_dir: str, text_dir: str, table_dir: str, graph_dir: str):
        self.doc_root = doc_root
        self.raw_dir = raw_dir
        self.text_dir = text_dir
        self.table_dir = table_dir
        self.graph_dir = graph_dir

def setup_logger():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(name)s | %(message)s')
    return logging.getLogger("ocr")

def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path

def build_output_dirs(output_root: str, pdf_stem: str) -> PathBundle:
    doc_root = ensure_dir(os.path.join(output_root, pdf_stem))
    raw_dir = ensure_dir(os.path.join(doc_root, "raw"))
    text_dir = ensure_dir(os.path.join(doc_root, "text"))
    table_dir = ensure_dir(os.path.join(doc_root, "table"))
    graph_dir = ensure_dir(os.path.join(doc_root, "graph"))
    return PathBundle(doc_root, raw_dir, text_dir, table_dir, graph_dir)

def save_json(data, file_path):
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
