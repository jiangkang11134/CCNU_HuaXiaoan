from __future__ import annotations

from typing import Any

import json_repair

from yuxi.models.chat import select_model

from .base import GraphExtractor
from .domains import domain_schema, normalize_domain

DEFAULT_TRIPLE_EXTRACTION_PROMPT = """请从下面文本中抽取实体和实体关系，返回严格 JSON，不要输出解释。
JSON 格式：
{
  "relations": [
    {
      "source": {"text": "实体文本", "label": "实体类型", "attributes": [{"text": "属性值", "label": "属性名称"}]},
      "target": {"text": "实体文本", "label": "实体类型", "attributes": [{"text": "属性值", "label": "属性名称"}]},
      "text": "关系显示文本",
      "label": "关系类型"
    }
  ]
}
"""

SCHEMA_INSTRUCTION = """抽取 Schema 约束：
{schema}
"""


class LLMGraphExtractor(GraphExtractor):
    extractor_type = "llm"

    def validate_options(self) -> None:
        if not self.options.get("model_spec"):
            raise ValueError("LLM 抽取器需要 model_spec")
        if self.options.get("prompt"):
            raise ValueError("LLM 图谱抽取器不支持自定义完整 Prompt，请使用 schema 配置抽取约束")
        concurrency_count = self.options.get("concurrency_count", 1)
        try:
            concurrency_count = int(concurrency_count)
        except (TypeError, ValueError) as exc:
            raise ValueError("LLM 抽取器 concurrency_count 必须是整数") from exc
        if concurrency_count < 1 or concurrency_count > 1000:
            raise ValueError("LLM 抽取器 concurrency_count 必须在 1 到 1000 之间")
        if self.options.get("model_params") is not None and not isinstance(self.options["model_params"], dict):
            raise ValueError("LLM 抽取器 model_params 必须是对象")
        # 域名在这里就校验：配置接口调 GraphExtractorFactory.create 时会走到，
        # 于是错误在管理员点"保存"的那一刻暴露，而不是等图谱建完了才发现没有本体。
        normalize_domain(self.options.get("domain"))
        domain_by_file = self.options.get("domain_by_file")
        if domain_by_file is not None and not isinstance(domain_by_file, dict):
            raise ValueError("LLM 抽取器 domain_by_file 必须是 {file_id: 域} 对象")

    def resolved_domain(self, chunk_metadata: dict[str, Any] | None = None) -> str:
        """确定这一块文本该按哪个域抽取。

        文件级覆盖知识库级：同一个知识库里化学品手册与管理办法可以各自成群，
        而跨族连边仍发生在同一张图上（这正是按域分库做不到的）。
        覆盖值非法时**直接报错并带上 file_id**——静默退回知识库级域会让
        "某个文件配错了"这件事永远查不出来。
        """
        file_id = str((chunk_metadata or {}).get("file_id") or "").strip()
        overrides = self.options.get("domain_by_file") or {}
        if file_id and file_id in overrides:
            try:
                return normalize_domain(overrides[file_id])
            except ValueError as exc:
                raise ValueError(f"文件 {file_id} 的抽取域非法：{exc}") from exc
        return normalize_domain(self.options.get("domain"))

    async def extract(self, text: str, *, chunk_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        self.validate_options()
        model = select_model(
            model_spec=self.options["model_spec"],
            timeout=60.0,
            model_params=self.options.get("model_params") or {},
        )
        prompt = self._build_prompt(text, chunk_metadata)
        response = await model.call(prompt, stream=False)
        parsed = json_repair.loads(response.content if response else "")
        return parsed

    def _build_prompt(self, text: str, chunk_metadata: dict[str, Any] | None = None) -> str:
        extraction_prompt = DEFAULT_TRIPLE_EXTRACTION_PROMPT
        custom_schema = str(self.options.get("schema") or "").strip()
        schema = domain_schema(self.resolved_domain(chunk_metadata), custom_schema)
        if schema:
            extraction_prompt = f"{extraction_prompt}\n{SCHEMA_INSTRUCTION.format(schema=schema)}"
        return f"{extraction_prompt}\n\n文本：\n{text}"
