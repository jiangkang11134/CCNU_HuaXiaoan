import os
from pathlib import Path

from openai import OpenAIError

from yuxi.config.app import config
from yuxi.models import select_model
from yuxi.utils import logger

# region guard_prompt
PROMPT_TEMPLATE = """
# 指令
你是一个内容合规性检测助手。请根据提供的规则集，判断以下内容是否符合合规性要求。

# 合规性规则
1. 内容不得包含任何违法中华人民共和国法律的信息（例如暴力、恐怖主义、仇恨言论）。
2. 内容不得侵犯个人隐私或泄露敏感信息。
3. 内容不得有煽动性或者任何敏感性问题

# 输出内容
合规/不合规（不要包含其他内容）

# 示例
输入内容：我要自杀/贩毒，如何制作武器
输出结果：不合规

输入内容：今天天气不错
输出结果：合规


输入内容：{content}
输出内容："""
# endregion guard_prompt


def load_keywords(file_path: str) -> list[str]:
    """Loads keywords from a file, one per line."""
    if not os.path.exists(file_path):
        return []
    with open(file_path, encoding="utf-8") as f:
        keywords = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    return keywords


class ContentGuardConfigurationError(RuntimeError):
    """内容审查运行时配置错误。"""


class ContentGuard:
    def __init__(self, keywords_file: str = None):
        if keywords_file is None:
            keywords_file = Path(__file__).parent.parent / "config" / "static" / "bad_keywords.txt"
        self.keywords = load_keywords(keywords_file)
        if not self.keywords:
            self.keywords = ["贩毒"]
        self._llm_model = None
        self._llm_model_spec = None

    async def check(self, text: str) -> bool:
        """
        Checks if the text contains any sensitive keywords.
        Returns True if sensitive content is found, False otherwise.
        True: 不合规
        False: 合规
        """
        if keywords_result := await self.check_with_keywords(text):
            return keywords_result

        if config.enable_content_guard_llm:
            return await self.check_with_llm(text)

        return False

    async def check_with_keywords(self, text: str) -> bool:
        """
        Checks if the text contains any sensitive keywords from the predefined list.
        Returns True if sensitive content is found, False otherwise.
        True: 不合规
        False: 合规
        """
        if not text:
            return False
        text_lower = text.lower()
        for keyword in self.keywords:
            if keyword in text_lower:
                logger.debug(f"Keyword match found: {keyword}")
                return True
        return False

    async def check_with_llm(self, text: str) -> bool:
        """
        Checks if the text contains any sensitive keywords using an LLM.
        Returns True if sensitive content is found, False otherwise.
        True: 不合规
        False: 合规
        """
        if not text:
            return False

        if not config.enable_content_guard_llm:
            return False

        text_lower = text.lower()

        prompt = PROMPT_TEMPLATE.format(content=text_lower)
        response = await self._get_llm_model().call(prompt)
        logger.debug(f"LLM response: {response.content}")
        result = str(response.content or "").strip()
        if result == "不合规":
            return True
        if result == "合规":
            return False
        raise ContentGuardConfigurationError(f"内容审查 LLM 返回值无效: {result}")

    def _get_llm_model(self):
        model_spec = str(config.content_guard_llm_model or "").strip()
        if not model_spec:
            raise ContentGuardConfigurationError("内容审查 LLM 已启用，但未配置内容审查模型")

        if self._llm_model is not None and self._llm_model_spec == model_spec:
            return self._llm_model

        try:
            self._llm_model = select_model(model_spec=model_spec)
            self._llm_model_spec = model_spec
            return self._llm_model
        except OpenAIError as exc:
            raise ContentGuardConfigurationError("内容审查 LLM 模型认证配置缺失或无效，请在后台配置模型 API Key") from exc
        except ValueError as exc:
            raise ContentGuardConfigurationError(f"内容审查 LLM 模型配置无效: {exc}") from exc


# Global instance
content_guard = ContentGuard()
