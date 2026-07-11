"""OCR 解析引擎配置常量。"""

from __future__ import annotations

# 处理器类型映射: processor_type -> (module_path, class_name)
PROCESSOR_TYPES = {
    "rapid_ocr": ("yuxi.knowledge.parser.rapid_ocr", "RapidOCRParser"),
    "mineru_ocr": ("yuxi.knowledge.parser.mineru", "MinerUParser"),
    "mineru_official": ("yuxi.knowledge.parser.mineru_official", "MinerUOfficialParser"),
    "pp_structure_v3_ocr": ("yuxi.knowledge.parser.pp_structure_v3", "PPStructureV3Parser"),
    "deepseek_ocr": ("yuxi.knowledge.parser.deepseek_ocr", "DeepSeekOCRParser"),
    "paddleocr_vl_1_6": ("yuxi.knowledge.parser.paddleocr_api", "PaddleOCRVLParser"),
    "paddleocr_pp_ocrv6": ("yuxi.knowledge.parser.paddleocr_api", "PaddleOCRPPOCRv6Parser"),
}


def get_available_processor_types() -> list[str]:
    """返回所有可用文档解析处理器类型。"""
    return list(PROCESSOR_TYPES.keys())
