from pathlib import Path
from typing import List
from pypdf import PdfReader

from config import settings

CHUNK_SIZE = settings.chunk_size
CHUNK_OVERLAP = settings.chunk_overlap


def split_text(text: str, chunk_size: int | None = None, chunk_overlap: int | None = None) -> List[str]:
    """简单中文递归分块，不依赖langchain。可覆盖全局 chunk_size/chunk_overlap，便于调参。"""
    size = chunk_size if chunk_size is not None else CHUNK_SIZE
    overlap_size = chunk_overlap if chunk_overlap is not None else CHUNK_OVERLAP
    separators = ["\n\n", "\n", "。", "！", "？", "，", " "]

    def _split(s: str, seps: list[str]) -> List[str]:
        if len(s) <= size:
            return [s]
        if not seps:
            return [s[:size]]
        sep = seps[0]
        parts = s.split(sep)
        res = []
        current = ""
        for p in parts:
            if not p.strip():
                continue
            if len(current + sep + p) > size and current:
                res.append(current.strip())
                current = p
            else:
                current = (current + sep + p) if current else p
        if current:
            res.append(current.strip())
        final = []
        for seg in res:
            if len(seg) > size:
                final.extend(_split(seg, seps[1:]))
            else:
                final.append(seg)
        return final

    raw_segments = _split(text, separators)
    # 增加overlap拼接
    if overlap_size <= 0:
        return raw_segments
    chunks = []
    for i, seg in enumerate(raw_segments):
        if i == 0:
            chunks.append(seg)
        else:
            prev = raw_segments[i-1]
            overlap = prev[-overlap_size:] if len(prev) > overlap_size else prev
            new_chunk = overlap + seg
            chunks.append(new_chunk)
    return chunks


def parse_file(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    content = ""
    if suffix == ".pdf":
        reader = PdfReader(file_path)
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                content += page_text + "\n"
    elif suffix in [".txt", ".md"]:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    return content.strip()


def parse_and_chunk(file_path: Path) -> List[str]:
    raw_text = parse_file(file_path)
    if not raw_text:
        return []
    chunks = split_text(raw_text)
    return chunks