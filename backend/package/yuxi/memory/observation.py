"""记忆事实的白名单、校验与状态裁决。

抽取已改为**独立调用**（见 :mod:`yuxi.memory.extraction`）：回答链路不再产出任何
协议块，因此这里不再需要跨 chunk 的围栏剥离器。本模块只保留三件事：

1. **白名单** :data:`MEMORY_FACT_KEYS` —— key 的唯一定义处，同时也是通道归属的依据：
   ``user.*`` 是跨会话画像（进长期记忆），``project.*`` 是会话内约束（进事实状态机）。
2. **校验** :func:`normalize_op` —— 白名单外、含敏感信息、结构非法的 op 一律丢弃。
3. **裁决** :func:`arbitrate` —— 决定长期记忆落在哪个状态。
4. **有效期** :func:`memory_fact_ttl` —— 分键 TTL（多数长期有效，研究方向 30 天、
   工作环境 7 天），再次提及即续期。

"LLM 只能提议，不能决定"的边界由这些闸在**服务端**维持。
prompt 可以搬位置，闸门不能搬位置。
"""
from __future__ import annotations

import re
from datetime import timedelta

# 与设计文档 §19 白名单一致；LLM 抽取到的 key 必须落在其中。
MEMORY_FACT_KEYS = {
    "user.major",
    "user.education_stage",
    "user.research_direction",
    "user.work_environment",
    "user.lab_role",
    "user.response_preference",
    "project.current_goal",
    "project.phase_scope",
    "project.constraints",
    "project.acceptance_criteria",
    "project.rejected_approach",
}

#: 稳定用户画像：跨会话几乎不变，高置信度时可直接确认。
AUTO_CONFIRM_KEYS = {
    "user.major",
    "user.education_stage",
    "user.research_direction",
    "user.work_environment",
    "user.lab_role",
    "user.response_preference",
}
AUTO_CONFIRM_MIN_CONFIDENCE = 0.85

#: 长期记忆的**分键有效期**（天）。不在此表的键视为长期有效（``expires_at`` 留空）。
#:
#: 为什么不是所有 ``user.*`` 都长期有效：画像的稳定度并不一样。
#: 「研究方向」会随课题推进换方向，「工作环境」更是可能换实验室、换学期——
#: 把这两类当永久画像，三个月后模型还在按旧实验室的场景给建议，比不记更危险。
#: （用户 2026-09-20 明确口径：研究方向 30 天、工作环境 7 天。）
#:
#: 到期**不是删除**：``MemoryService.expire_memories`` 只把状态改成 ``expired``，
#: 行与审计事件都留着，所以「这条为什么不再生效」永远查得到。
#:
#: **续期规则**：同一条内容被再次提及会把有效期往后推满一个 TTL
#: （``MemoryService._apply_memory_fact`` 的 skipped / confirmed 分支）——
#: 「还在说」就等于「还有效」。没有这一步，TTL 会退化成"最后一次提及的随机时刻 + TTL"。
MEMORY_FACT_TTL_DAYS: dict[str, int] = {
    "user.research_direction": 30,
    "user.work_environment": 7,
}

#: 由天换算一次，避免每轮热路径都构造 ``timedelta``。
MEMORY_FACT_TTL: dict[str, timedelta] = {
    key: timedelta(days=days) for key, days in MEMORY_FACT_TTL_DAYS.items()
}


def memory_fact_ttl(fact_key: str) -> timedelta | None:
    """取该键的有效期；不在表内返回 ``None``，语义是**不过期**。

    返回 ``None`` 而不是某个大数：``expires_at`` 为空在 SQL 侧就是"永不过期"
    （``expires_at IS NULL OR expires_at > now()``），两边语义一致，
    不需要再约定一个"足够大的天数"当哨兵。
    """
    return MEMORY_FACT_TTL.get(fact_key)

#: 通道裁定：key 前缀决定它该进哪张表，不由模型自由填。
#: 让模型自己选通道会重现"方向对穿"——偏好写进会话事实表、会话事实写进长期记忆表。
SESSION_FACT_KEYS = {key for key in MEMORY_FACT_KEYS if key.startswith("project.")}
LONG_TERM_FACT_KEYS = {key for key in MEMORY_FACT_KEYS if key.startswith("user.")}

CHANNEL_SESSION = "session"
CHANNEL_MEMORY = "memory"

MAX_CONTENT_LEN = 200

SENSITIVE_PATTERN = re.compile(
    r"(?:身份证|手机号|电话|邮箱|住址|银行卡|密码|api[_ -]?key|secret)", re.IGNORECASE
)


def canonical_channel(fact_key: str) -> str | None:
    """按 fact_key 裁定该条事实的目标通道；白名单外的 key 返回 None。"""
    if fact_key in SESSION_FACT_KEYS:
        return CHANNEL_SESSION
    if fact_key in LONG_TERM_FACT_KEYS:
        return CHANNEL_MEMORY
    return None


def arbitrate(fact_key: str, confidence: float) -> str:
    """裁决一条长期记忆应落在哪个状态。

    - 白名单外的 key 直接 ``rejected``（LLM 不认识它，不能自创事实类型）。
    - 稳定画像且置信度达标 → ``confirmed``，可跨会话注入。
    - 其余（含项目类上下文，时效性强、易随任务变化）→ ``candidate``，等确认。
    """
    if fact_key not in MEMORY_FACT_KEYS:
        return "rejected"
    if fact_key in AUTO_CONFIRM_KEYS and confidence >= AUTO_CONFIRM_MIN_CONFIDENCE:
        return "confirmed"
    return "candidate"


def normalize_op(op: object) -> tuple[str, str, float] | None:
    """把一条抽取结果规范成 ``(fact_key, content, confidence)``。

    任一校验不过就返回 None——宁可漏存，也不把脏数据写进记忆表。
    """
    if not isinstance(op, dict):
        return None
    fact_key = str(op.get("fact_key") or "").strip()
    content = str(op.get("content") or "").strip()
    if not fact_key or not content:
        return None
    if fact_key not in MEMORY_FACT_KEYS:
        return None
    if SENSITIVE_PATTERN.search(content):
        return None
    content = content[:MAX_CONTENT_LEN]
    try:
        confidence = float(op.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return fact_key, content, max(0.0, min(1.0, confidence))
