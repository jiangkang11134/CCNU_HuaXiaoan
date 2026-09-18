"""化学品安全知识图谱本体定义。

该模块只定义受控节点/关系名称和提示模板，不负责写入图数据库。

节点与关系清单是**唯一的真源**：schema 文本由它们拼装（见 `ontology.format_label_list`），
校验侧的白名单也从同一份元组取。改本体只需改下面的元组，不会出现
"prompt 里写了 10 种、校验里只认 8 种"这种漂移。
"""

from .ontology import format_label_list, format_relation_list

#: 化学品本体允许的实体 label。第一个是主类型，所有关系的 source 优先取它。
CHEMICAL_LABELS = (
    "Chemical",
    "HazardCharacteristic",
    "PhysicalProperty",
    "StorageRequirement",
    "IncompatibleSubstance",
    "ProtectionMeasure",
    "EmergencyMeasure",
    "DisposalRequirement",
    "Regulation",
    "HazardStatement",
    "GhsPictogram",
)

#: 兼容旧引用：主类型单独取一个名字。
CHEMICAL_LABEL = CHEMICAL_LABELS[0]

CHEMICAL_RELATIONS = (
    "HAS_ALIAS",
    "HAS_CAS",
    "HAS_UN_NUMBER",
    "HAS_HAZARD",
    "HAS_PHYSICAL_PROPERTY",
    "HAS_HAZARD_STATEMENT",
    "HAS_GHS_PICTOGRAM",
    "REQUIRES_STORAGE",
    "INCOMPATIBLE_WITH",
    "REQUIRES_PROTECTION",
    "HAS_EMERGENCY_MEASURE",
    "REQUIRES_DISPOSAL",
    "LISTED_IN",
)

CHEMICAL_EXTRACTION_SCHEMA = f"""
仅抽取原文明确出现的化学品安全事实，禁止常识推断。实体 label 只能使用：
{format_label_list(CHEMICAL_LABELS)}
关系 label 只能使用：
{format_relation_list(CHEMICAL_RELATIONS)}
所有关系的 source 优先是 Chemical；每个 Chemical 必须尽量抽取 name、alias、CAS、UN 属性。
未知、无资料、问号和 OCR 噪声不要作为事实实体或属性。

安全技术说明书（MSDS/SDS，一份文档对应一种物质）按下列字段归属抽取：
1. 基本信息中的名称、别名、英文名、CAS 号、UN 号属于该 Chemical；主键、更新时间、
   创建时间、登记企业数、是否 SDS、是否显示 PDF、说明书文件 ID/文件名/文件地址都是
   平台内部元数据，一律不抽。
2. 危险性类别（如"急性毒性-经口,类别2*"）按逗号拆成多个 HazardCharacteristic，
   并去掉尾部的逗号与星号；危险性说明按 H 码逐条建 HazardStatement（保留 H3xx: 原文）；
   象形图（如 GHS06,GHS08）按逗号拆成多个 GhsPictogram。三者都用对应的 HAS_ 边
   指向该 Chemical。
3. 理化特性里的每一项（外观与性状、pH、熔点、沸点/沸程、相对密度、相对蒸气密度、
   饱和蒸气压、燃烧热、临界温度、临界压力、辛醇/水分配系数、闪点、自燃温度、
   爆炸下限、爆炸上限、分解温度、黏度、溶解性）各建一个 PhysicalProperty 节点，
   其 text 用"参数名：数值"的形式（例如"闪点：-4℃"），不要只写参数名——只写参数名
   会让所有物质共用一个高度数节点。参数值前的前导"？"是数据来源标注，去掉后照抄数值
   （"？-1"就是"-1"）；PARAMETERFEATURES 字段是这些参数的重复汇总，不要据此重复抽取。
4. 应急处置与防护：急救措施、泄漏处置、消防灭火方法各建 EmergencyMeasure（用属性标明
   措施类型）；职业接触限值（中国标准/ACGIH TLV）与急性毒性数据（LD50、LC50）作为该
   Chemical 的属性，不要单独建节点；禁配物（禁配/避免接触的物质）建 IncompatibleSubstance，
   用 INCOMPATIBLE_WITH 从 Chemical 指向它。
5. 整格为"？"、"？？"、"无资料"、"无意义"、"data unavailable"的，视为没有数据，不抽。

管制名录类文档（如危险化学品目录、剧毒化学品目录、易制毒化学品的分类和品种目录）按
"一行一个化学品"抽取：
1. 品名与别名各建一个 Chemical 节点（同一行的多个名称指同一物质时合并为一个节点），
   CAS 号作为属性；同一物质在名录里出现的英文名与简称也作为别名属性。
2. 管制类别（如剧毒、第一类易制毒化学品、第二类易制毒化学品）与列管时间作为该
   Chemical 的属性，不要为它们单独建节点。
3. 名录本身（目录名称与年份版本，如危险化学品目录 2015 版）建 Regulation 节点，
   每个 Chemical 用 LISTED_IN 指向它。
4. 表头、序号、页码、备注列中的空白不是实体。同一化学品被多份名录收录时，
   Chemical 只建一个节点，分别用 LISTED_IN 指向各名录。
5. 名录说明里"某类物质属于/不属于另一名录"的表述，只对正向收录关系建 LISTED_IN 边；
   负向表述（如"第一类易制毒化学品均不在《危险化学品目录》中"）不建边。
""".strip()


def chemical_prompt_schema(custom_schema: str | None = None) -> str:
    return f"{CHEMICAL_EXTRACTION_SCHEMA}\n{custom_schema.strip()}" if custom_schema and custom_schema.strip() else CHEMICAL_EXTRACTION_SCHEMA
