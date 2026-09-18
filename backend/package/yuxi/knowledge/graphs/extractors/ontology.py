"""本体定义的公共构件。

存在的理由很具体：节点/关系清单此前只以散文形式**内联**在 schema 字符串里，
代码侧拿不到"这个域到底允许哪些 label"。于是 prompt 里写着"实体 label 只能使用 A/B/C"，
而落库路径（`normalize_extraction_result`）对任何未知 label 都默认收下——
文档与实现各说各话，本体形同虚设。

这里把清单提成元组、再**由元组生成** schema 文本，白名单与文档就成了同一份数据，
想漂移都漂移不了。
"""
from __future__ import annotations

#: schema 文本里标签清单的折行宽度（按字符数估，不追求像素级对齐）
_WRAP_WIDTH = 76


def format_label_list(labels: tuple[str, ...] | list[str], *, width: int = _WRAP_WIDTH) -> str:
    """把标签清单排成若干行并以句号收尾。

    纯排版，不改动标签本身。逗号留在行尾（与原手写文案的风格一致），
    这样逐行 diff 时新增/删除哪个标签一眼可见。
    """
    normalized = [str(label).strip() for label in labels if str(label).strip()]
    if not normalized:
        return "。"

    lines: list[str] = []
    current = ""
    for label in normalized:
        candidate = label if not current else f"{current}, {label}"
        if current and len(candidate) > width:
            lines.append(current)
            current = label
        else:
            current = candidate
    if current:
        lines.append(current)
    return ",\n".join(lines) + "。"


def format_relation_list(labels: tuple[str, ...] | list[str], *, width: int = _WRAP_WIDTH) -> str:
    """关系清单的排版。与 `format_label_list` 同实现，单独留名以便日后分别调整。"""
    return format_label_list(labels, width=width)
