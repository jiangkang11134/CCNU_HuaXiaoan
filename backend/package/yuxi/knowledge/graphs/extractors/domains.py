"""图谱抽取的领域本体注册表。

**为什么要单独有这一层。** 本体此前只有两个常量模块，但没有任何链路能把它们
接到抽取 prompt 上，实际入图走的是无任何类型约束的通用 prompt：

- 管理端保存配置时**整份替换** `extractor_options`，而表单只回填 4 个字段，
  `domain` 就在这一步被悄悄抹掉；
- 导入脚本把族名塞进了 `extractor_type`（工厂只注册了 `llm`），直接报错。

两条断链的后果是一样的：**图谱里的节点 label 变成模型自由发挥**，
而这件事在图上完全看不出来——不会报错，不会有日志，只会慢慢长出一堆
`Entity` 和"危险化学品""化学品安全"这类近义重复的 label。

**这里的两个约定：**

1. **域 → schema 只有一个解析入口**（`domain_schema`），调用方不再各拼各的。
2. **未知域一律抛错，绝不静默回落到通用 prompt**（`normalize_domain`）。
   回落是最坏的一种"成功"：配置写错了没人知道，图谱照样建出来，只是没有本体。

**当前可选的三族**（外加不做约束的通用）：化学品安全、实验室管理规范、政策法规。
族的划分依据是**文档形态**——同一批文件彼此可共享节点，跨族则节点不复用，
所以"一个真实实体只应该有一个族"是选族的第一原则（化学品尤其：名录类文档也走
化学品族，否则同一个试剂在不同族里会变成两个节点，跨文档聚合立刻失效）。
"""
from __future__ import annotations

#: 不做领域约束的通用抽取。它是**显式**选项，不是错误路径的兜底。
DOMAIN_GENERIC = ""

#: 化学品安全。
DOMAIN_CHEMICAL = "chemical"

#: 高校实验室安全管理。
DOMAIN_LABORATORY_MANAGEMENT = "laboratory_management"

#: 政策法规与管理制度（法律、部委规范性文件、校内制度）。
DOMAIN_POLICY = "policy"

#: 中文显示名，给管理端下拉与报错信息用。
DOMAIN_TITLES: dict[str, str] = {
    DOMAIN_GENERIC: "通用（不做领域约束）",
    DOMAIN_CHEMICAL: "化学品安全",
    DOMAIN_LABORATORY_MANAGEMENT: "实验室管理规范",
    DOMAIN_POLICY: "政策法规",
}

#: 历史别名 → 规范名。此前 `llm.py` 手写了这三个同义分支，收敛到这里。
_DOMAIN_ALIASES: dict[str, str] = {
    "laboratory": DOMAIN_LABORATORY_MANAGEMENT,
    "lab_management": DOMAIN_LABORATORY_MANAGEMENT,
    # `regulation` 曾在规划里作为"政策法规族"的占位名，族建好后它就是别名。
    "regulation": DOMAIN_POLICY,
}

#: 用户规划中、但本体尚未建立的域。写错时给出可执行的提示，而不是一句"不支持"。
PENDING_DOMAINS: dict[str, str] = {
    "correction": "反馈修正族本体尚未建立",
    "feedback": "反馈修正族本体尚未建立",
}


def valid_domains() -> tuple[str, ...]:
    """当前**真正可用**的域（不含通用）。管理端下拉与校验共用。"""
    return (DOMAIN_CHEMICAL, DOMAIN_LABORATORY_MANAGEMENT, DOMAIN_POLICY)


def is_known_domain(value: object) -> bool:
    """该值能否被解析（含通用与历史别名）。不抛异常，供配置页预校验。"""
    try:
        normalize_domain(value)
    except ValueError:
        return False
    return True


def normalize_domain(value: object) -> str:
    """把任意输入收敛到规范域名；空值表示通用；**无法识别则抛 ValueError**。

    不回落是有意的：域名配错时，唯一能让人发现的方式就是让它在配置那一刻
    就失败。静默降级到通用 prompt 会产出"看起来建好了、其实没有本体"的图谱。
    """
    text = str(value or "").strip().lower()
    if not text:
        return DOMAIN_GENERIC
    if text in DOMAIN_TITLES:
        return text
    if text in _DOMAIN_ALIASES:
        return _DOMAIN_ALIASES[text]
    pending = PENDING_DOMAINS.get(text)
    if pending:
        raise ValueError(f"不支持的图谱抽取域: {value}（{pending}）")
    raise ValueError(
        f"不支持的图谱抽取域: {value}；可用值为 "
        + "、".join(valid_domains())
        + "，留空表示不做领域约束"
    )


def domain_ontology(domain: object) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """取该域的 (实体 label 元组, 关系 label 元组)；通用域返回两个空元组。

    空元组是"不校验"的信号，由调用方区分——不在这里替调用方决定放行还是拒绝。
    """
    canonical = normalize_domain(domain)
    if canonical == DOMAIN_CHEMICAL:
        from .chemical import CHEMICAL_LABELS, CHEMICAL_RELATIONS

        return CHEMICAL_LABELS, CHEMICAL_RELATIONS
    if canonical == DOMAIN_LABORATORY_MANAGEMENT:
        from .laboratory_management import LAB_MANAGEMENT_LABELS, LAB_MANAGEMENT_RELATIONS

        return LAB_MANAGEMENT_LABELS, LAB_MANAGEMENT_RELATIONS
    if canonical == DOMAIN_POLICY:
        from .policy import POLICY_LABELS, POLICY_RELATIONS

        return POLICY_LABELS, POLICY_RELATIONS
    return (), ()


def domain_entity_labels(domain: object) -> frozenset[str]:
    """该域允许的实体 label 白名单。通用域为空集（表示不校验）。"""
    labels, _ = domain_ontology(domain)
    return frozenset(labels)


def domain_schema(domain: object, custom_schema: str | None = None) -> str:
    """拼出最终交给模型的 schema 文本。

    领域本体在前、自定义补充在后：域约束是硬边界，补充要求是这份知识库的本地口径，
    顺序反了模型会先读到宽松的要求。通用域下只返回自定义补充（可能为空串）。
    """
    canonical = normalize_domain(domain)
    suffix = (custom_schema or "").strip()
    if canonical == DOMAIN_CHEMICAL:
        from .chemical import chemical_prompt_schema

        return chemical_prompt_schema(suffix)
    if canonical == DOMAIN_LABORATORY_MANAGEMENT:
        from .laboratory_management import LAB_MANAGEMENT_EXTRACTION_SCHEMA

        return f"{LAB_MANAGEMENT_EXTRACTION_SCHEMA}\n{suffix}" if suffix else LAB_MANAGEMENT_EXTRACTION_SCHEMA
    if canonical == DOMAIN_POLICY:
        from .policy import POLICY_EXTRACTION_SCHEMA

        return f"{POLICY_EXTRACTION_SCHEMA}\n{suffix}" if suffix else POLICY_EXTRACTION_SCHEMA
    return suffix


__all__ = [
    "DOMAIN_CHEMICAL",
    "DOMAIN_GENERIC",
    "DOMAIN_LABORATORY_MANAGEMENT",
    "DOMAIN_POLICY",
    "DOMAIN_TITLES",
    "PENDING_DOMAINS",
    "domain_entity_labels",
    "domain_ontology",
    "domain_schema",
    "is_known_domain",
    "normalize_domain",
    "valid_domains",
]
