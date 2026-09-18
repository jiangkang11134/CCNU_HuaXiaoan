"""个人资料与回答风格：个性化的第三级。

冲突仲裁（用户明确口径，**适用于所有个性化信息，不只是风格**）
----------------------------------------------------------------
**当前会话事实 > 个人资料 > 长期记忆保存的同类信息**

用户原话："冲突的，除了语言风格外，其他的都是当前会话事实状态机 > 个人资料 >
长期记忆保存的。"——即这条顺序是**通用规则**，风格只是它的一个应用场景。
越靠近当下、越明确，优先级越高。

落在代码上是两件事，缺一不可：
1. **声明**：`[个人资料]` 段首写明顺序（见 :data:`PROFILE_PRECEDENCE_NOTE`）。
2. **执行**：个人资料里有值的字段，要把它对应的长期记忆事实**从注入里摘掉**
   （见 :data:`PROFILE_OVERRIDDEN_FACT_KEYS` 与 :func:`overridden_fact_keys`）。
   只声明不执行，模型会同时看到两个答案（例如「专业：应用化学」与
   `[long_term] user.major: 化学工程`），等于把仲裁权又推回给模型。
   注意**只摘长期记忆，绝不摘会话事实**——会话事实在这一规则里是最高层。

为什么"正常风格"不产出任何指令
------------------------------
用户原话："回答风格默认正常，所谓正常，你就无需它什么风格了，正常就是不定义，
以会话事实+长期记忆为主。"

所以 ``normal`` **刻意不在** :data:`STYLE_INSTRUCTIONS` 里——不注入任何提示词。
理由有两条，都不是偷懒：
1. 一句"请正常回答"既占每轮 prompt，又会把回答往"平均值"上压，
   反而盖掉会话事实和长期记忆里已有的具体偏好。
2. "正常"是**缺省态**而不是**一种风格**。把它写成指令等于凭空造了一个档位，
   还得维护它与其他两级的优先级关系——不写就没有这个负担。

与三级结论优先级的关系
----------------------
本模块只影响**表达层**（怎么说），绝不影响**结论层**（说什么）。
身份、专业可用于指代消歧与检索提示；风格只作用于措辞与篇幅。
安全规范、化学品性质、相容性禁忌、合规判定一律按
「已批准并重新入库的修正 > 知识库/Graph RAG 可追溯证据」取依据，
个人资料这一级**不参与结论**。
"""
from __future__ import annotations

from typing import Final

#: 个人资料字段 -> 它覆盖掉的长期记忆事实键。
#: 目前真正撞车的只有专业：长期记忆白名单里有 `user.major`（且在 `AUTO_CONFIRM_KEYS`
#: 里，≥0.85 会自动 confirmed），个人资料里也有「专业」，两者会同时进 prompt。
#: 新增会撞车的个人资料字段时，**必须同步这里**，否则又会出现两个答案。
PROFILE_OVERRIDDEN_FACT_KEYS: Final[dict[str, str]] = {
    "major": "user.major",
}

#: 回答风格取值
STYLE_CONCISE = "concise"     # 简单清晰
STYLE_NORMAL = "normal"       # 正常风格（不注入任何指令）
STYLE_THOROUGH = "thorough"   # 详细严密

RESPONSE_STYLE_CHOICES: Final[tuple[str, ...]] = (STYLE_CONCISE, STYLE_NORMAL, STYLE_THOROUGH)
DEFAULT_RESPONSE_STYLE = STYLE_NORMAL

STYLE_LABELS: Final[dict[str, str]] = {
    STYLE_CONCISE: "简单清晰",
    STYLE_NORMAL: "正常风格",
    STYLE_THOROUGH: "详细严密",
}

#: 业务角色（注册时确定，用户不可改）-> 注入时使用的中文标签。
BUSINESS_ROLE_LABELS: Final[dict[str, str]] = {
    "student": "学生",
    "faculty": "教师",
    "system_admin": "管理员",
}

#: 风格 -> 注入给模型的**表达层**指令。``normal`` 不在此表中（见模块 docstring）。
STYLE_INSTRUCTIONS: Final[dict[str, str]] = {
    STYLE_CONCISE: (
        "回答风格：简单清晰。\n"
        "- 先给结论，再按需补一句理由；不要铺垫，不要复述问题。\n"
        "- 用短句，单段不超过三句；能用一句话说清就不要分点。\n"
        "- 能不用术语就不用；必须用时顺带一句白话解释。\n"
        "- 整体控制在 150 字以内，除非问题本身确实需要更长。\n"
        "边界：这只约束**表达方式**。**涉及安全风险的警示、禁用条件、"
        "必须佩戴的防护、废液与应急处理要求，一条都不能省**——"
        "宁可多写一句，也不能因为要简短而漏掉。"
    ),
    STYLE_THOROUGH: (
        "回答风格：详细严密。\n"
        "- 先说明适用前提与边界（本回答在什么条件下成立、什么情况下不适用）。\n"
        "- 再分步骤给出操作要点，每步标注依据（适用规范或原理）。\n"
        "- 涉及数值或条件时写清单位、范围与容差；有多种做法时说明取舍理由与风险差异。\n"
        "- 不确定的地方**明确标注不确定**，不要给出看起来确定的答案。\n"
        "- 最后补一句兜底提示：出现何种情况应立即停止并向谁咨询。\n"
        "边界：详细不等于啰嗦，**更不等于放宽安全要求**——写清前提与依据只是让你说得准，"
        "规范要求的审批、防护与监护一项都不能少。"
        "不要罗列与问题无关的背景，不要用大段免责声明开头，也不要把结论藏在推导后面。"
    ),
}

#: 通用冲突顺序（用户明确口径）：会话事实 > 个人资料 > 长期记忆。
#: 放在段首而不是只在风格那一条里，是因为**身份/专业/风格都会与长期记忆撞车**，
#: 只对风格声明等于把专业这类冲突留给模型自己猜。
PROFILE_PRECEDENCE_NOTE = (
    "信息冲突时的优先级：本轮会话事实 > 本段个人资料 > 长期记忆中记下的同类信息。"
)

#: 回答风格：**只讲怎么判断，不再重复通用顺序**（顺序由上面那句统一说）。
#: 重复一遍顺序既占 token，又会让模型以为风格有一套独立规则。
STYLE_PRECEDENCE_NOTE = (
    "回答风格：若上文 [当前可用事实] 里出现了与回答形式有关的要求（例如「这次简短点」"
    "「不要分点」「展开讲」），以它为准；没有则按下面的风格执行。"
)


def normalize_response_style(value: object) -> str:
    """把任意输入收敛到合法风格值；无法识别的一律回落 `normal`。

    回落而不是报错：风格只是一层表达偏好，读脏了不该让整轮问答失败。
    """
    text = str(value or "").strip().lower()
    return text if text in RESPONSE_STYLE_CHOICES else DEFAULT_RESPONSE_STYLE


def style_instruction(style: object) -> str:
    """取该风格对应的指令；`normal`（或无法识别）返回空串——**不定义即不约束**。"""
    return STYLE_INSTRUCTIONS.get(normalize_response_style(style), "")


def overridden_fact_keys(*, major: object = None) -> set[str]:
    """个人资料里有值的字段所覆盖的长期记忆键——摘掉它们，别让模型看到两个答案。

    只覆盖**长期记忆**（`user.*`）。会话事实是这条规则里的**最高层**，
    永远不该被个人资料压制，所以这里一个 `project.*` 都不返回。
    """
    keys: set[str] = set()
    if str(major or "").strip():
        keys.add(PROFILE_OVERRIDDEN_FACT_KEYS["major"])
    return keys


def build_profile_context(
    *,
    business_role: object = None,
    major: object = None,
    response_style: object = "normal",
) -> str:
    """拼出注入给模型的 `[个人资料]` 段；没有任何可用信息时返回空串（不注入）。

    只放**会影响回答**的三项：身份、专业、回答风格。
    姓名/性别/学工号不进 prompt——它们对回答没有帮助，只会白烧 token
    （要展示给人看的话在前端渲染即可）。

    身份与专业**照常注入**即使风格是 normal：它们服务于指代消歧和检索提示，
    跟风格无关；而 normal 只是"不加风格指令"，不是"整个资料段都不要"。
    """
    lines: list[str] = []

    role = BUSINESS_ROLE_LABELS.get(str(business_role or "").strip())
    if role:
        lines.append(f"- 身份：{role}")
    major_text = str(major or "").strip()
    if major_text:
        lines.append(f"- 专业：{major_text}")

    instruction = style_instruction(response_style)
    if instruction:
        lines.append(f"- {STYLE_PRECEDENCE_NOTE}")
        lines.append(f"- {instruction}")

    if not lines:
        return ""

    return (
        "以下为用户在个人资料中填写的信息，仅用于个性化、指代消歧与检索提示，"
        "不得作为实验室安全规范、化学品性质或合规结论的依据。\n"
        + PROFILE_PRECEDENCE_NOTE
        + "\n"
        + "\n".join(lines)
    )


__all__ = [
    "BUSINESS_ROLE_LABELS",
    "DEFAULT_RESPONSE_STYLE",
    "RESPONSE_STYLE_CHOICES",
    "STYLE_CONCISE",
    "STYLE_INSTRUCTIONS",
    "STYLE_LABELS",
    "STYLE_NORMAL",
    "STYLE_PRECEDENCE_NOTE",
    "PROFILE_OVERRIDDEN_FACT_KEYS",
    "PROFILE_PRECEDENCE_NOTE",
    "STYLE_THOROUGH",
    "build_profile_context",
    "normalize_response_style",
    "overridden_fact_keys",
    "style_instruction",
]
