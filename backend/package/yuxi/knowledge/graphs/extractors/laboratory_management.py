"""高校实验室安全管理知识图谱本体定义。

与化学品本体同构：清单元组是真源，schema 文本由它拼装。
"""

from .ontology import format_label_list, format_relation_list

LAB_MANAGEMENT_LABELS = (
    "Laboratory",
    "LaboratoryType",
    "ManagementRequirement",
    "Responsibility",
    "TrainingRequirement",
    "InspectionRequirement",
    "RectificationRequirement",
    "RiskControl",
    "Scope",
    "Organization",
    "Document",
)

LAB_MANAGEMENT_RELATIONS = (
    "APPLIES_TO",
    "REQUIRES",
    "RESPONSIBLE_FOR",
    "MUST_COMPLETE",
    "MUST_INSPECT",
    "REQUIRES_RECTIFICATION",
    "CONTROLS_RISK",
    "REPORTS_TO",
    "ISSUED_BY",
    "VALID_DURING",
)

LAB_MANAGEMENT_EXTRACTION_SCHEMA = f"""
面向高校实验室安全管理抽取，严格依据原文，不做常识推断。
实体 label 只能使用：{format_label_list(LAB_MANAGEMENT_LABELS)}
关系 label 只能使用：{format_relation_list(LAB_MANAGEMENT_RELATIONS)}
具体师生账号不是实体；学生和教师只允许作为角色文本，不建立具体用户关系。
管理要求、培训、检查和整改必须绑定原文 Chunk 证据。
""".strip()
