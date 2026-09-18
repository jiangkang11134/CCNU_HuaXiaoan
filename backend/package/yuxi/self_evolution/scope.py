"""纠错工单的作用域分层（M1）：只有知识性偏差才允许进入全局口径。

背景
----
``CorrectionTicket`` 的注入查询原本**不过滤 uid**——这对"硫酸存放要求"这类
领域事实修正是正确设计（KB 级真理本就该全局生效），但对"回答要简短"这类
表达偏好是灾难：一旦审核通过，就成了污染全体用户的权威口径。

用户 2026-09-16 明确的硬边界：

    **进入全局口径的，只能是真正知识性的偏差，而不能是偏好。**

判定原则：偏好绝对优先
----------------------
误判为 ``user_pref`` 的代价是"这条修正只影响提反馈的人自己"，无害；
误判为 ``kb_truth`` 的代价是"个人口味变成全体用户的权威口径"，有害。
两者不对称，所以只要文本里出现**任何**偏好特征就降级为个人作用域，
即使同时含有知识性特征——混合文本（"废液处理说错了，而且太长了"）
也一律交给管理员人工拆分，绝不自动放行到全局。

未命中任何特征时默认 ``kb_truth``，但 ``source`` 为 None，审核 UI 上
标为"待确认"——不把不确定性藏起来，也不给管理员增加默认负担。
"""
from __future__ import annotations

import re

SCOPE_KB_TRUTH = "kb_truth"    # 知识性偏差：全局生效（唯一允许进全局的作用域）
SCOPE_USER_PREF = "user_pref"  # 表达/形式偏好：仅对该 uid 生效
SCOPE_DEPT_RULE = "dept_rule"  # 部门内部规则：仅同部门生效

VALID_SCOPES = (SCOPE_KB_TRUTH, SCOPE_USER_PREF, SCOPE_DEPT_RULE)

SCOPE_LABELS = {
    SCOPE_KB_TRUTH: "知识性偏差 · 全局生效",
    SCOPE_USER_PREF: "个人偏好 · 仅本人生效",
    SCOPE_DEPT_RULE: "部门规则 · 同部门生效",
}

# 来源：rule = 规则自动判定，admin = 管理员审核时改定，None = 未命中任何特征待确认
SCOPE_SOURCE_RULE = "rule"
SCOPE_SOURCE_ADMIN = "admin"

# --------------------------------------------------------------------------
# 偏好特征：表达形式 / 详略 / 格式 / 风格 / 主观诉求
# 命中任一即降级为 user_pref——这是"偏好不进全局"的第一道也是最强一道防线。
# --------------------------------------------------------------------------
_PREFERENCE_PATTERNS = (
    # 长度与详略
    r"太长|太短|太啰嗦|太冗长|太简略|简短[一点些]|简洁[一点些]|详细[一点些]|"
    r"精简|啰嗦|冗长|字数|篇幅|少写|多写|少说|多说|说重点|挑重点|重点就|"
    r"再解释|展开说|展开讲|不用展开|总结一下|概括一下|简而言之|一句话",
    # 输出格式
    r"用表格|表格形式|列个表|列表形式|分点|分条|要点| bullet|加粗|"
    r"小标题|标上序号|按步骤|流程图|思维导图|markdown",
    # 表达风格
    r"口语[一点些]|通俗[一点些]|白话|专业[一点些]|正式[一点些]|轻松[一点些]|"
    r"好懂[一点些]|别这么|不要这么",
    # 明确的主观诉求
    r"我更[喜欢想要希望]|我希望|我想要|我觉得|对我来说|我不[喜欢太]|"
    r"不太喜欢|希望能|能不能不|以后别|下次别|以后[能否]|习惯用|偏好",
)

# --------------------------------------------------------------------------
# 知识性特征：事实纠错 / 规范主张 / 领域实体
# 仅在完全不含偏好特征时才据此判为 kb_truth。
# --------------------------------------------------------------------------
_KNOWLEDGE_PATTERNS = (
    # 明确的事实性纠错
    r"错误|错了|不对|不准确|有误|写反|弄错|说错|瞎说|胡说|搞错|"
    r"不严谨|不合规|违规|违反|搞反|颠倒|混淆|张冠李戴",
    # 规范与标准要求
    r"应该是|应当是|正确的(?:是|做法|要求)|标准为|规定|条例|国标|GB[/ ]?\d|"
    r"规程|规范要求|按照.{0,6}(?:标准|规定|规范)|依据.{0,8}(?:标准|规定)",
    # 实验室安全领域实体
    r"禁忌|相容|配伍|储存|存放|保质期|浓度|闪点|爆炸|爆燃|毒|腐蚀|"
    r"防护|处置|废液|试剂|气瓶|高压|灭菌|离心|通风柜|危化品|"
    r"护目镜|手套|洗眼|喷淋|灭火|泄漏|通风|隔离|标识|台账",
)

_PREFERENCE_RE = re.compile("|".join(_PREFERENCE_PATTERNS))
_KNOWLEDGE_RE = re.compile("|".join(_KNOWLEDGE_PATTERNS))

# 判定置信度：规则命中偏好（最强信号）> 规则命中知识 > 未命中待确认
CONFIDENCE_PREF_RULE = 0.9
CONFIDENCE_TRUTH_RULE = 0.8
CONFIDENCE_UNDECIDED = 0.0


def classify_scope(proposed: str, original: str | None = None) -> tuple[str, str | None, float]:
    """判定工单作用域，返回 ``(scope, source, confidence)``。

    ``proposed`` 是反馈人提出的修正内容（判定主依据）；``original`` 是被质疑的
    原文，只作辅助——同一特征出现在任一侧都算命中，避免"原文里写了'太长了'
    但修正里没写"时漏判。

    ``source`` 为 None 表示未命中任何特征，审核 UI 应提示管理员确认，
    不要静默当成知识性偏差放行到全局。
    """
    text = f"{proposed or ''}\n{original or ''}"
    if not text.strip():
        return SCOPE_KB_TRUTH, None, CONFIDENCE_UNDECIDED

    if _PREFERENCE_RE.search(text):
        return SCOPE_USER_PREF, SCOPE_SOURCE_RULE, CONFIDENCE_PREF_RULE
    if _KNOWLEDGE_RE.search(text):
        return SCOPE_KB_TRUTH, SCOPE_SOURCE_RULE, CONFIDENCE_TRUTH_RULE
    return SCOPE_KB_TRUTH, None, CONFIDENCE_UNDECIDED


def normalize_scope(value: str | None, *, default: str = SCOPE_KB_TRUTH) -> str:
    """把外部传入的 scope 收敛到合法枚举；非法值回落到 ``default``。"""
    if value in VALID_SCOPES:
        return value
    return default


def scope_condition(*, uid: str | None = None, dept_id: int | str | None = None):
    """构造注入查询的作用域门槛：全局放行，其余按归属过滤。

    这是 M1 的执行点——``kb_truth`` 无额外条件（本就该全局生效），
    ``user_pref`` 必须 uid 匹配，``dept_rule`` 必须同部门。

    归属信息缺失时对应作用域**完全不注入**（而不是放宽成全局）：uid 为空
    就是匿名链路，此时宁可一条偏好都不注入，也不能泄漏给不该看到的人。
    """
    from sqlalchemy import and_, or_

    from yuxi.storage.postgres.models_business import CorrectionTicket

    clauses = [CorrectionTicket.scope == SCOPE_KB_TRUTH]
    if uid:
        clauses.append(and_(CorrectionTicket.scope == SCOPE_USER_PREF,
                            CorrectionTicket.uid == uid))
    if dept_id is not None:
        clauses.append(and_(CorrectionTicket.scope == SCOPE_DEPT_RULE,
                            CorrectionTicket.dept_id == dept_id))
    return or_(*clauses)


def scope_prefix(scope: str) -> str:
    """注入文本里对偏好的显式标注——让模型知道它不是事实。

    安全红线：结论层（安全规范、相容性禁忌、合规结论）禁止个性化。给偏好类
    修正打上"表达偏好"前缀，模型就不会把它当成可影响结论的事实依据。
    """
    if scope == SCOPE_USER_PREF:
        return "[表达偏好] "
    if scope == SCOPE_DEPT_RULE:
        return "[本部门规则] "
    return ""
