"""docx/pptx 的内嵌图必须走 OCR。

背景：`_convert_with_docling` 原先只把内嵌图上传 MinIO、替换成 ``![图片](url)``，
**图里的文字直接丢弃**。于是"正文就是一张扫描图/表格截图"的文档（实测知识库里的
`文件框架.docx`：714 KB 里 700 KB 是 `word/media/image1.png`）会静默解析成 0 字符——
用户以为文档进了库，实际对检索和图谱零贡献，而且全程没有任何报错。

这组用例把"送去 OCR"这条契约钉住，同时钉住两条边界：
- xlsx 里的"图片"多是图表，OCR 出来是碎片，**不该**识别；
- 单张图 OCR 失败只记日志，不能打断整篇解析。
"""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

import yuxi.knowledge.parser.unified as parser_unified
from yuxi import config
from yuxi.knowledge.parser.factory import DocumentProcessorFactory

# 1x1 PNG，够 dataclass 里走通 `_parse_data_uri` 与后缀推导
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
_DATA_URI = "data:image/png;base64," + base64.b64encode(_PNG_BYTES).decode()

_RECOGNIZED = "制度体系组成：纲领制度、分项制度、指导文件、实操标准"


class _FakePicture:
    def __init__(self, uri: str) -> None:
        self.image = SimpleNamespace(uri=uri)


class _FakeDocument:
    def __init__(self, pictures: list[_FakePicture]) -> None:
        self.pictures = pictures

    def export_to_markdown(self) -> str:
        return "\n".join("<!-- image -->" for _ in self.pictures)


class _FakeConverter:
    status = SimpleNamespace(name="SUCCESS")

    def __init__(self, document: _FakeDocument) -> None:
        self.document = document

    def convert(self, file_path):  # noqa: ANN001, ARG002
        return self


def _patch_docling(monkeypatch: pytest.MonkeyPatch, pictures: list[_FakePicture]) -> None:
    """让 docling 返回一张带 data URI 的图片，并挡住真实 MinIO 上传。"""
    document = _FakeDocument(pictures)
    monkeypatch.setattr(parser_unified, "_get_docling_converter", lambda: _FakeConverter(document))
    monkeypatch.setattr(
        parser_unified,
        "_upload_image_to_minio",
        lambda data, filename, bucket, prefix: f"http://minio/{filename}",  # noqa: ARG005
    )


def _patch_ocr(
    monkeypatch: pytest.MonkeyPatch, *, result: str = _RECOGNIZED, raises: Exception | None = None
) -> list[dict]:
    calls: list[dict] = []

    def fake_process_file(engine, file, params=None):  # noqa: ANN001, ARG001
        calls.append({"engine": engine, "path": Path(file), "existed": Path(file).exists()})
        if raises is not None:
            raise raises
        return result

    monkeypatch.setattr(DocumentProcessorFactory, "process_file", fake_process_file)
    return calls


def _run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str, params=None) -> tuple[str, list[dict]]:
    _patch_docling(monkeypatch, [_FakePicture(_DATA_URI)])
    calls = _patch_ocr(monkeypatch)
    target = tmp_path / name
    target.write_bytes(b"placeholder")
    markdown = parser_unified._convert_with_docling(target, params=params)
    return markdown, calls


def test_docx_embedded_image_text_reaches_the_markdown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """核心契约：图内文字必须落到正文里，否则"图片型文档"永远是 0 字符。"""
    markdown, calls = _run(monkeypatch, tmp_path, "文件框架.docx")

    assert len(calls) == 1
    assert calls[0]["engine"] == config.default_ocr_engine
    assert _RECOGNIZED in markdown
    # 图片链接仍在（前端还要展示原图）
    assert "![image_" in markdown


def test_pptx_embedded_image_is_also_ocr_ed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    markdown, calls = _run(monkeypatch, tmp_path, "deck.pptx")

    assert len(calls) == 1
    assert _RECOGNIZED in markdown


def test_xlsx_embedded_images_are_not_ocr_ed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """xlsx 的"图片"多为图表，识别结果对正文是噪声，刻意跳过。"""
    markdown, calls = _run(monkeypatch, tmp_path, "台账.xlsx")

    assert calls == []
    assert _RECOGNIZED not in markdown


def test_disable_engine_skips_embedded_image_ocr(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """显式关掉 OCR 时不得偷偷跑一遍——否则 `disable` 语义就废了。"""
    markdown, calls = _run(monkeypatch, tmp_path, "x.docx", params={"ocr_engine": "disable"})

    assert calls == []
    assert _RECOGNIZED not in markdown
    assert "![image_" in markdown


def test_ocr_failure_keeps_the_document_parseable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """单张图识别失败不能把整篇文档带崩（这是最容易被写成裸 raise 的地方）。"""
    _patch_docling(monkeypatch, [_FakePicture(_DATA_URI)])
    _patch_ocr(monkeypatch, raises=RuntimeError("ocr engine exploded"))

    target = tmp_path / "x.docx"
    target.write_bytes(b"placeholder")
    markdown = parser_unified._convert_with_docling(target)

    assert "![image_" in markdown


def test_image_bytes_are_written_to_a_temp_file_and_cleaned_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """OCR 走的是路径接口，所以必须落临时文件；用完必须删，否则每张图漏一个文件。"""
    _, calls = _run(monkeypatch, tmp_path, "x.docx")

    assert len(calls) == 1
    assert calls[0]["existed"] is True
    assert calls[0]["path"].suffix == ".png"
    assert not calls[0]["path"].exists()
