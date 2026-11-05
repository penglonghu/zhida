import logging
import re
from pathlib import Path
from typing import List

from pypdf import PdfReader

from config import settings

logger = logging.getLogger(__name__)

CHUNK_SIZE = settings.chunk_size
CHUNK_OVERLAP = settings.chunk_overlap

# RapidOCR 引擎单例（懒加载，避免每次上传都重载 onnx 模型）
_ocr_engine = None

# ---- OCR 精确 token 后处理 ----

# 竖线 "|" 分隔符常被 OCR 误读成 1/l/I。仅当「完整 11 位手机号后紧贴一个多余的
# 1/l/I 再跟汉字」时判定为分隔符（如 "18600470217|邮箱" 读成 "186004702171邮箱"）。
# 用「先匹配完整手机号、再要求多余字符」保证安全：真实以 1 结尾的手机号
# （如 "13800138001张三"）后面没有多余字符，不会被误改。
_SEP_AFTER_PHONE = re.compile(r"(1[3-9]\d{9})[1lI](?=[一-龥])")

# 手机号字段：标签 + 数字串（容忍 OCR 字母混淆），用于提取并归一
_PHONE_LABEL = r"(?:手机|电话|联系电话|联系方式|手机号|Mobile|Tel|Phone)\s*[:：]?\s*"
_PHONE_NUM = r"[0-9OolISZBq\- ]{7,13}"
_PHONE_FIELD_RE = re.compile(_PHONE_LABEL + "(" + _PHONE_NUM + ")")

# 数字/字母 OCR 混淆表（只在手机号数字串内归一，避免污染姓名/邮箱里的字母）
_DIGIT_FIX = str.maketrans("OolISZBq", "00115289")

# 12 位手机号：疑似噪声混入但无法可靠还原（不知道多的是哪一位），只能人工复核
_SUSPECT_PHONE_RE = re.compile(r"1[3-9]\d{10}")

# 邮箱后紧跟 "1/l/I"（"|" 误读）再跟汉字：penglonghu@126.com|现居 → …com1现居
_EMAIL_RE = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
_SEP_AFTER_EMAIL = re.compile(rf"({_EMAIL_RE})[1lI](?=[一-龥])")

# 汉字与拉丁字母之间夹 "1"（"|" 误读）：高级项目经理|PMO → 经理1PMO
# 只匹配 "1" 而非 l/I，避免把罗马数字 I 误当分隔符
_SEP_CJK_LATIN = re.compile(r"(?<=[一-龥])1(?=[A-Za-z])")


def _fix_phone_field(m):
    prefix = m.group(0)[: m.start(1) - m.start(0)]
    suffix = m.group(0)[m.end(1) - m.start(0):]
    num = m.group(1).translate(_DIGIT_FIX).replace(" ", "")
    if re.fullmatch(r"1[3-9]\d{9}", num):
        return prefix + num + suffix
    return m.group(0)  # 校验不过保持原样，交给告警层


def _normalize_precise_tokens(text: str) -> str:
    """OCR 精确 token 后处理（保守）：先归一手机号字母混淆，再恢复被误读的 "|"。"""
    text = _PHONE_FIELD_RE.sub(_fix_phone_field, text)
    text = _SEP_AFTER_PHONE.sub(lambda m: f"{m.group(1)} | ", text)
    text = _SEP_AFTER_EMAIL.sub(lambda m: f"{m.group(1)} | ", text)
    text = _SEP_CJK_LATIN.sub(" | ", text)
    return text


def _warn_phone_anomalies(text: str) -> None:
    """可观测性：长度异常的手机号打 warning，提示人工复核。"""
    for m in _SUSPECT_PHONE_RE.finditer(text):
        logger.warning("OCR 疑似手机号噪声（12位，无法可靠还原）：%r", m.group(0))


def _get_ocr_engine():
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_engine = RapidOCR()
    return _ocr_engine


def _extract_pdf_text(file_path: Path) -> str:
    """用 pypdf 提取 PDF 文本层。扫描件/图片型 PDF 会返回空。"""
    reader = PdfReader(file_path)
    content = ""
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            content += page_text + "\n"
    return content


def _ocr_pdf(file_path: Path) -> str:
    """扫描件 OCR：PyMuPDF 渲染每页为图片 + RapidOCR 中文识别。"""
    import numpy as np
    import pymupdf

    engine = _get_ocr_engine()
    doc = pymupdf.open(file_path)
    parts = []
    for page in doc:
        pix = page.get_pixmap(dpi=200)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        result = engine(img)
        ocr_result = result[0] if isinstance(result, tuple) else result
        parts.append("\n".join(item[1] for item in ocr_result))
    doc.close()
    return "\n".join(parts)


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
        content = _extract_pdf_text(file_path)
        if not content.strip():
            # 文本层为空 → 扫描件/图片型 PDF，回退 OCR
            content = _ocr_pdf(file_path)
    elif suffix in [".txt", ".md"]:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    if content:
        content = _normalize_precise_tokens(content)
        _warn_phone_anomalies(content)
    return content.strip()


def parse_and_chunk(file_path: Path) -> List[str]:
    raw_text = parse_file(file_path)
    if not raw_text:
        return []
    chunks = split_text(raw_text)
    return chunks