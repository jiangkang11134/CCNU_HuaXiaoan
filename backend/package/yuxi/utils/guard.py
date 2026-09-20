import os
import re
from pathlib import Path

from openai import OpenAIError

from yuxi.config.app import config
from yuxi.models import select_model
from yuxi.utils import logger

# region guard_prompt
PROMPT_TEMPLATE = """
# 指令
你是一个内容合规性检测助手。只需判断内容是否属于法律明确禁止的少数类别，
**不要把正常的业务回答误判为不合规**。

# 判为「不合规」的情形（仅限以下类别，其余一律判为合规）
1. 宣扬或教唆暴力、恐怖主义、极端主义，煽动对特定群体的仇恨与歧视。
2. 涉及未成年人的色情内容，或索取、提供色情低俗内容的获取渠道。
3. 提供犯罪的具体实施方法或教程（制毒、制爆、制造武器、诈骗话术、入侵系统、伪造证件等）。
4. 买卖违禁品、毒品、枪支弹药、公民个人信息。
5. 教唆或指导自残、自杀，或邀约共同自伤。
6. 直接给出真实的账号密码、API Key、访问令牌等凭证明文。

# 必须判为「合规」的情形（重点，防止误伤）
- 回答中引用**用户本人**的资料（专业、年级、实验室名称、研究项目、院系、兴趣爱好等）。
  这是系统自身的业务功能，**不属于**泄露隐私。
- 用户**询问**「账号密码是什么」「API Key 在哪里」这类问题，提问本身合规，
  应由主模型礼貌拒绝，不要在这里拦截。
- 安全教育、普法宣传、风险提示、心理健康教育类内容（反诈骗、禁毒、反邪教、
  防网络暴力、化学品危险性、爆炸风险识别、防护与应急处置、心理危机干预等），
  即使出现相关名词，只要不是教唆或提供操作方法，一律判为合规。

# 输出内容
合规/不合规（不要包含其他内容）

# 示例
输入内容：我要自杀/贩毒，如何制作武器
输出结果：不合规

输入内容：用户在第九教学楼天体实验室负责物理学天体实验项目
输出结果：合规

输入内容：用户问系统管理员的密码是多少
输出结果：合规

输入内容：常见毒品有哪些，分别有什么危害
输出结果：合规

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


def normalize_verdict(raw: str) -> bool | None:
    """把审查模型的返回归一化为 True(不合规) / False(合规) / None(无法判定)。

    模型常带 markdown、引号、句号或「回答：」前缀，原先的等值比较会把它们
    误判成配置错误，进而给用户弹一条报错。注意「不合规」包含「合规」，必须先判前者。
    """
    if not raw:
        return None
    cleaned = re.sub(r"[\s`'\"“”‘’。，,：:；;！!？?*#\-]+", "", raw)
    if not cleaned:
        return None
    if "不合规" in cleaned:
        return True
    if "合规" in cleaned:
        return False
    return None


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
                # 用 info 级：以前是 debug，出事时日志里查不到是谁命中的，无法定位误伤。
                logger.info(f"内容审查命中关键词: {keyword}")
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
        raw = str(response.content or "")
        logger.debug(f"LLM response: {raw}")
        result = normalize_verdict(raw)
        if result is None:
            raise ContentGuardConfigurationError(f"内容审查 LLM 返回值无效: {raw.strip()}")
        return result

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
