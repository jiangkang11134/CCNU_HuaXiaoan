"""图谱抽取域（本体白名单）与文件级路由的守卫。

这里钉的是**两条曾经断掉的链**：

1. 本体的两套常量一直存在，但没有任何链路能把它们接到抽取 prompt 上——
   管理端保存配置时整份替换 `extractor_options`、表单又不回填 `domain`，
   导入脚本则把族名塞进了 `extractor_type`。结果是入图走无约束的通用 prompt。
2. 落库路径对任何未知 label 都照收（`normalize_extraction_result` 回落 `"Entity"`），
   就算接上了 prompt，本体也只是装饰。

所以断言分三层：域能否解析、schema 与清单元组是否同源、越界 label 是否真被拦下。
"""
from __future__ import annotations

import contextlib
import re
from types import SimpleNamespace

import pytest
import pytest_asyncio

from yuxi.knowledge.graphs.extractors import (
    CHEMICAL_LABELS,
    CHEMICAL_RELATIONS,
    CHEMICAL_EXTRACTION_SCHEMA,
    DOMAIN_CHEMICAL,
    DOMAIN_GENERIC,
    DOMAIN_LABORATORY_MANAGEMENT,
    DOMAIN_POLICY,
    DOMAIN_TITLES,
    LAB_MANAGEMENT_LABELS,
    LAB_MANAGEMENT_RELATIONS,
    LAB_MANAGEMENT_EXTRACTION_SCHEMA,
    PENDING_DOMAINS,
    POLICY_EXTRACTION_SCHEMA,
    POLICY_LABELS,
    POLICY_RELATIONS,
    domain_schema,
    normalize_domain,
    normalize_extraction_result,
    valid_domains,
)
from yuxi.knowledge.graphs.extractors.llm import LLMGraphExtractor
from yuxi.knowledge.graphs.milvus_graph_service import MilvusGraphService
from yuxi.repositories import knowledge_file_repository as kf_repo_module
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

# 本仓 `asyncio_mode = "auto"`（backend/pyproject.toml），协程用例无需 asyncio marker；
# 挂上去反而会给同步用例刷一路 "not an async function" 的 PytestWarning。
pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# 一、域注册表
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("chemical", DOMAIN_CHEMICAL),
        ("  CHEMICAL  ", DOMAIN_CHEMICAL),
        ("laboratory_management", DOMAIN_LABORATORY_MANAGEMENT),
        # 历史别名：此前 llm.py 手写了这两个同义分支，必须继续认
        ("laboratory", DOMAIN_LABORATORY_MANAGEMENT),
        ("lab_management", DOMAIN_LABORATORY_MANAGEMENT),
        ("Lab_Management", DOMAIN_LABORATORY_MANAGEMENT),
        ("policy", DOMAIN_POLICY),
        ("POLICY", DOMAIN_POLICY),
        # `regulation` 曾是政策法规族建立前的占位名，族建好后转为别名
        ("regulation", DOMAIN_POLICY),
    ],
)
def test_normalize_domain_maps_aliases_to_canonical(raw, expected):
    assert normalize_domain(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_blank_domain_means_generic_not_error(raw):
    """通用是**显式**选项，不是写错时的兜底——所以空白值合法。"""
    assert normalize_domain(raw) == DOMAIN_GENERIC


@pytest.mark.parametrize("raw", ["checmical", "chemical_x", "化学", "generic", "llm"])
def test_unknown_domain_raises_instead_of_falling_back(raw):
    """静默回落是最坏的一种成功：配置写错了没人知道，图谱照样建出来、只是没有本体。"""
    with pytest.raises(ValueError, match="不支持的图谱抽取域"):
        normalize_domain(raw)


@pytest.mark.parametrize("raw", ["correction", "feedback"])
def test_pending_domain_says_the_family_is_not_built_yet(raw):
    """规划中但未建立的域要给出可执行的提示，而不是一句无信息量的"不支持"。"""
    with pytest.raises(ValueError, match="尚未建立"):
        normalize_domain(raw)
    assert raw in PENDING_DOMAINS


def test_valid_domains_and_titles_agree():
    """下拉选项与校验白名单必须是同一份——否则界面选得到的值会被后端拒掉。"""
    assert set(valid_domains()) == set(DOMAIN_TITLES) - {DOMAIN_GENERIC}
    for name in valid_domains():
        assert DOMAIN_TITLES[name].strip()


# --------------------------------------------------------------------------- #
# 二、schema 与清单元组同源（防漂移）
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "domain,labels,relations,schema",
    [
        (DOMAIN_CHEMICAL, CHEMICAL_LABELS, CHEMICAL_RELATIONS, CHEMICAL_EXTRACTION_SCHEMA),
        (
            DOMAIN_LABORATORY_MANAGEMENT,
            LAB_MANAGEMENT_LABELS,
            LAB_MANAGEMENT_RELATIONS,
            LAB_MANAGEMENT_EXTRACTION_SCHEMA,
        ),
        (DOMAIN_POLICY, POLICY_LABELS, POLICY_RELATIONS, POLICY_EXTRACTION_SCHEMA),
    ],
)
def test_domain_schema_covers_every_label_and_relation(domain, labels, relations, schema):
    """正向：清单里的每个标签都必须在 prompt 文本里出现。

    这条是"改元组忘了改文案"的守卫——schema 由元组拼装，所以它本该恒真；
    一旦有人把某段文案改回手写，这里立刻红。
    """
    assert domain_schema(domain) == schema
    missing_labels = [label for label in labels if label not in schema]
    missing_relations = [rel for rel in relations if rel not in schema]
    assert missing_labels == []
    assert missing_relations == []


def test_domain_schema_does_not_leak_the_other_ontology():
    """反向：化学品 schema 里不该出现管理规范的标签。防的是跨本体复制粘贴。"""
    chemical = domain_schema(DOMAIN_CHEMICAL)
    lab = domain_schema(DOMAIN_LABORATORY_MANAGEMENT)

    for label in LAB_MANAGEMENT_LABELS:
        assert label not in chemical, label
    for label in CHEMICAL_LABELS:
        assert label not in lab, label


def test_generic_domain_returns_only_the_custom_schema():
    assert domain_schema(DOMAIN_GENERIC) == ""
    assert domain_schema("", "实体类型只能是 Person") == "实体类型只能是 Person"


def test_ontology_schema_precedes_custom_schema():
    """域约束是硬边界，本地补充在后——顺序反了模型会先读到宽松的要求。"""
    custom = "本库额外要求：只抽中文名"
    composed = domain_schema(DOMAIN_CHEMICAL, custom)

    assert composed.startswith(CHEMICAL_EXTRACTION_SCHEMA)
    assert composed.endswith(custom)


# --------------------------------------------------------------------------- #
# 三、白名单过滤（越界 label 不静默入库）
# --------------------------------------------------------------------------- #

def _clean_payload() -> dict:
    return {
        "entities": [
            {"text": "浓硫酸", "label": "Chemical", "attributes": []},
            {"text": "禁配物", "label": "IncompatibleSubstance", "attributes": []},
        ],
        "relations": [
            {
                "source": {"text": "浓硫酸", "label": "Chemical"},
                "target": {"text": "禁配物", "label": "IncompatibleSubstance"},
                "text": "禁配",
                "label": "INCOMPATIBLE_WITH",
            }
        ],
    }


def test_clean_result_is_kept_and_marks_that_validation_ran():
    """零丢弃 ≠ 没校验。`ontology` 段存在才说明本体真的生效了。"""
    result = normalize_extraction_result(_clean_payload(), "llm", domain=DOMAIN_CHEMICAL)

    assert [e["label"] for e in result["entities"]] == ["Chemical", "IncompatibleSubstance"]
    assert len(result["relations"]) == 1
    assert result["metadata"]["ontology"] == {
        "domain": DOMAIN_CHEMICAL,
        "dropped_entities": 0,
        "dropped_relations": 0,
        "unknown_labels": [],
    }


def test_unknown_entity_label_is_dropped_with_its_relations():
    """越界实体连同它的边一起丢。

    只丢实体、把边留着，图上就会出现指向不存在节点的悬空边，
    PPR 遍历时是纯噪声。
    """
    payload = {
        "entities": [
            {"text": "浓硫酸", "label": "Chemical", "attributes": []},
            {"text": "毒得很", "label": "危险化学品", "attributes": []},
        ],
        "relations": [
            {
                "source": {"text": "浓硫酸", "label": "Chemical"},
                "target": {"text": "毒得很", "label": "危险化学品"},
                "text": "定性为",
                "label": "HAS_HAZARD",
            }
        ],
    }
    result = normalize_extraction_result(payload, "llm", domain=DOMAIN_CHEMICAL)

    assert [e["text"] for e in result["entities"]] == ["浓硫酸"]
    assert result["relations"] == []
    verdict = result["metadata"]["ontology"]
    assert verdict["dropped_entities"] == 1
    assert verdict["dropped_relations"] == 1
    assert verdict["unknown_labels"] == ["危险化学品"]


def test_unknown_relation_label_is_dropped_while_entities_survive():
    payload = _clean_payload()
    payload["relations"].append(
        {
            "source": {"text": "浓硫酸", "label": "Chemical"},
            "target": {"text": "禁配物", "label": "IncompatibleSubstance"},
            "text": "胡编的",
            "label": "TOTALLY_MADE_UP",
        }
    )
    result = normalize_extraction_result(payload, "llm", domain=DOMAIN_CHEMICAL)

    assert len(result["entities"]) == 2
    assert [r["label"] for r in result["relations"]] == ["INCOMPATIBLE_WITH"]
    assert result["metadata"]["ontology"]["unknown_labels"] == ["TOTALLY_MADE_UP"]


def test_unknown_labels_are_listed_once_even_when_repeated():
    """同一越界 label 出现多次只记一次——否则日志会被长文本淹没。"""
    payload = {
        "entities": [{"text": f"词{i}", "label": "胡编类型", "attributes": []} for i in range(5)],
        "relations": [],
    }
    result = normalize_extraction_result(payload, "llm", domain=DOMAIN_CHEMICAL)

    verdict = result["metadata"]["ontology"]
    assert verdict["dropped_entities"] == 5
    assert verdict["unknown_labels"] == ["胡编类型"]


def test_legacy_label_default_still_applies_before_filtering():
    """没给 label 的实体先回落 `Entity`，随后作为越界 label 被丢。

    顺序很重要：先归一化再过滤，否则"没写 label"和"写了非法 label"会被混为一谈。
    """
    payload = {"entities": [{"text": "张三", "attributes": []}], "relations": []}
    result = normalize_extraction_result(payload, "llm", domain=DOMAIN_CHEMICAL)

    assert result["entities"] == []
    assert result["metadata"]["ontology"]["unknown_labels"] == ["Entity"]


@pytest.mark.parametrize("domain", [None, "", DOMAIN_GENERIC])
def test_no_domain_means_no_filtering_and_no_ontology_metadata(domain):
    """通用抽取不校验，也就**不该**报一个 dropped=0——那是假的安心。"""
    result = normalize_extraction_result(_clean_payload(), "llm", domain=domain)

    assert len(result["entities"]) == 2
    assert "ontology" not in result["metadata"]
    assert result["metadata"] == {"extractor_type": "llm", "schema_version": 1}


def test_dropping_unknown_labels_never_raises():
    """一个越界 label 不该让整块文本的抽取全部报废——丢它，但记账。"""
    payload = {
        "entities": [{"text": "乱码", "label": "????", "attributes": []}],
        "relations": [{"source": {"text": "乱码", "label": "????"}, "target": {"text": "乱码", "label": "????"},
                       "text": "自环", "label": "????"}],
    }
    result = normalize_extraction_result(payload, "llm", domain=DOMAIN_LABORATORY_MANAGEMENT)

    assert result["entities"] == []
    assert result["relations"] == []
    assert result["metadata"]["ontology"]["unknown_labels"] == ["????"]


# --------------------------------------------------------------------------- #
# 四、文件级域覆盖知识库级域
# --------------------------------------------------------------------------- #

def test_file_level_domain_overrides_kb_level():
    extractor = LLMGraphExtractor(
        {
            "model_spec": "test/model",
            "domain": DOMAIN_CHEMICAL,
            "domain_by_file": {"f_lab": DOMAIN_LABORATORY_MANAGEMENT},
        }
    )
    prompt = extractor._build_prompt("文本", {"file_id": "f_lab"})

    assert "REQUIRES_RECTIFICATION" in prompt
    assert "REQUIRES_STORAGE" not in prompt


def test_file_without_override_falls_back_to_kb_domain():
    extractor = LLMGraphExtractor(
        {
            "model_spec": "test/model",
            "domain": DOMAIN_CHEMICAL,
            "domain_by_file": {"f_lab": DOMAIN_LABORATORY_MANAGEMENT},
        }
    )
    prompt = extractor._build_prompt("文本", {"file_id": "f_chem"})

    assert "REQUIRES_STORAGE" in prompt
    assert "REQUIRES_RECTIFICATION" not in prompt


def test_file_override_works_even_without_kb_domain():
    """知识库级是通用时，配了域的文件仍应按自己的本体抽——否则文件级形同虚设。"""
    extractor = LLMGraphExtractor(
        {"model_spec": "test/model", "domain_by_file": {"f_chem": DOMAIN_CHEMICAL}}
    )

    assert "REQUIRES_STORAGE" in extractor._build_prompt("文本", {"file_id": "f_chem"})
    assert "抽取 Schema 约束" not in extractor._build_prompt("文本", {"file_id": "f_other"})


def test_missing_chunk_metadata_uses_kb_domain():
    """`chunk_metadata` 是可选的——没给时不能炸，按知识库级域走。"""
    extractor = LLMGraphExtractor({"model_spec": "test/model", "domain": DOMAIN_CHEMICAL})

    assert "REQUIRES_STORAGE" in extractor._build_prompt("文本")
    assert "REQUIRES_STORAGE" in extractor._build_prompt("文本", None)


def test_invalid_file_domain_names_the_file():
    """报错必须带 file_id，否则"哪个文件配错了"只能靠逐个二分排查。"""
    extractor = LLMGraphExtractor(
        # 用"规划中但尚未建立"的族：它同样非法，顺带钉住未建的族在文件级也会被拦。
        # 这里原本写的是 policy——它已转正，再用就会变成"拦不住"的假绿。
        {"model_spec": "test/model", "domain_by_file": {"f_bad": "correction"}}
    )

    with pytest.raises(ValueError, match="f_bad"):
        extractor._build_prompt("文本", {"file_id": "f_bad"})


def test_validate_options_rejects_unknown_domain():
    """在配置接口那一刻就失败，而不是等图谱建完才发现没有本体。"""
    with pytest.raises(ValueError, match="不支持的图谱抽取域"):
        LLMGraphExtractor({"model_spec": "test/model", "domain": "checmical"}).validate_options()


@pytest.mark.parametrize("bad", ["chemical", ["chemical"], 3])
def test_validate_options_requires_domain_by_file_to_be_a_mapping(bad):
    with pytest.raises(ValueError, match="domain_by_file"):
        LLMGraphExtractor({"model_spec": "test/model", "domain_by_file": bad}).validate_options()


def test_validate_options_accepts_a_wellformed_domain_by_file():
    LLMGraphExtractor(
        {
            "model_spec": "test/model",
            "domain": DOMAIN_CHEMICAL,
            "domain_by_file": {"f_lab": DOMAIN_LABORATORY_MANAGEMENT},
        }
    ).validate_options()


# --------------------------------------------------------------------------- #
# 五、服务接线：域真的传下去了，丢弃真的留痕了
# --------------------------------------------------------------------------- #

class _SpyExtractor:
    """最小抽取器替身：记录收到的 chunk_metadata，返回一份可预期的越界结果。"""

    extractor_type = "llm"

    def __init__(self, domain: str = DOMAIN_CHEMICAL):
        self.domain = domain
        self.seen_metadata: dict | None = None
        self.payload = {
            "entities": [
                {"text": "浓硫酸", "label": "Chemical", "attributes": []},
                {"text": "毒得很", "label": "危险化学品", "attributes": []},
            ],
            "relations": [],
        }

    async def extract(self, text: str, *, chunk_metadata=None):
        self.seen_metadata = chunk_metadata
        return self.payload

    def resolved_domain(self, chunk_metadata=None) -> str:
        return self.domain


class _SpyChunkRepo:
    def __init__(self):
        self.updated: dict = {}

    async def update_extraction_result(self, chunk_id, result):
        self.updated[chunk_id] = result


def _chunk(**overrides):
    base = dict(
        chunk_id="c1",
        content="浓硫酸应储存于阴凉通风处",
        file_id="f1",
        chunk_index=0,
        extraction_result=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


async def test_extraction_result_is_filtered_by_resolved_domain():
    repo = _SpyChunkRepo()
    service = MilvusGraphService(chunk_repo=repo)
    extractor = _SpyExtractor(domain=DOMAIN_CHEMICAL)

    result = await service._get_chunk_extraction_result("kb1", _chunk(), extractor)

    assert [e["label"] for e in result["entities"]] == ["Chemical"]
    assert result["metadata"]["ontology"]["unknown_labels"] == ["危险化学品"]
    # 过滤后的结果才是落库的那份，不是模型原始输出
    assert repo.updated["c1"] is result


async def test_chunk_metadata_carries_file_id_so_file_routing_can_work():
    extractor = _SpyExtractor()
    await MilvusGraphService(chunk_repo=_SpyChunkRepo())._get_chunk_extraction_result(
        "kb1", _chunk(file_id="f_abc"), extractor
    )

    assert extractor.seen_metadata["file_id"] == "f_abc"
    assert extractor.seen_metadata["kb_id"] == "kb1"


async def test_cached_extraction_result_is_not_refiltered():
    """缓存结果是**上一次**抽取的产物，重新过滤会把已落库的数据悄悄改小。"""
    cached = {
        "entities": [{"text": "毒得很", "label": "危险化学品", "attributes": []}],
        "relations": [],
    }
    result = await MilvusGraphService(chunk_repo=_SpyChunkRepo())._get_chunk_extraction_result(
        "kb1", _chunk(extraction_result=cached), _SpyExtractor(domain=DOMAIN_CHEMICAL)
    )

    assert [e["label"] for e in result["entities"]] == ["危险化学品"]
    assert "ontology" not in result["metadata"]


async def test_ontology_drops_are_logged():
    """静默丢弃是本设计里唯一真正危险的地方，必须有日志兜着。"""
    from yuxi.utils import logger

    captured: list[str] = []
    sink_id = logger.add(lambda message: captured.append(message), level="WARNING")
    try:
        await MilvusGraphService(chunk_repo=_SpyChunkRepo())._get_chunk_extraction_result(
            "kb1", _chunk(), _SpyExtractor(domain=DOMAIN_CHEMICAL)
        )
    finally:
        logger.remove(sink_id)

    joined = "".join(captured)
    assert "本体过滤" in joined
    assert "危险化学品" in joined
    # loguru 用 {} 而不是 %s：写成 %s 会原样打印，且多余参数被静默忽略
    assert "%s" not in joined


async def test_ontology_drops_are_not_logged_when_nothing_was_dropped():
    from yuxi.utils import logger

    captured: list[str] = []
    sink_id = logger.add(lambda message: captured.append(message), level="WARNING")
    extractor = _SpyExtractor(domain=DOMAIN_CHEMICAL)
    extractor.payload = {"entities": [{"text": "浓硫酸", "label": "Chemical", "attributes": []}],
                         "relations": []}
    try:
        await MilvusGraphService(chunk_repo=_SpyChunkRepo())._get_chunk_extraction_result(
            "kb1", _chunk(), extractor
        )
    finally:
        logger.remove(sink_id)

    assert not [m for m in captured if "本体过滤" in m]


async def test_load_domain_by_file_returns_empty_when_lookup_fails(monkeypatch):
    """读失败不阻断建图，但要留下异常记录（不是静默回退）。"""

    async def boom(self, *, kb_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(KnowledgeFileRepository, "get_domain_by_file_ids", boom)

    assert await MilvusGraphService()._load_domain_by_file("kb1") == {}


async def test_load_domain_by_file_returns_the_mapping(monkeypatch):
    async def fake(self, *, kb_id):
        return {"f1": DOMAIN_CHEMICAL}

    monkeypatch.setattr(KnowledgeFileRepository, "get_domain_by_file_ids", fake)

    assert await MilvusGraphService()._load_domain_by_file("kb1") == {"f1": DOMAIN_CHEMICAL}


# --------------------------------------------------------------------------- #
# 六、仓储：写入前校验、整批拒绝、往返可读
# --------------------------------------------------------------------------- #

async def test_set_doc_domain_rejects_unknown_domain_before_touching_db():
    """非法域整批拒绝，且**一行都不写**——部分成功会让调用方拿到一个无法解读的数字。"""
    with pytest.raises(ValueError, match="不支持的图谱抽取域"):
        await KnowledgeFileRepository().set_doc_domain(
            kb_id="kb1", file_ids=["f1", "f2"], doc_domain="correction"
        )


async def test_set_doc_domain_with_no_ids_is_a_noop():
    assert await KnowledgeFileRepository().set_doc_domain(
        kb_id="kb1", file_ids=[], doc_domain=DOMAIN_CHEMICAL
    ) == 0


@pytest_asyncio.fixture()
async def file_repo(monkeypatch):
    """把仓储的会话上下文换成 sqlite 内存库，其余逻辑原样跑。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from yuxi.storage.postgres.models_business import Base
    from yuxi.storage.postgres.models_knowledge import KnowledgeFile

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all([
            KnowledgeFile(file_id="f_chem", kb_id="kb1", filename="化学品.pdf", file_type="pdf"),
            KnowledgeFile(file_id="f_lab", kb_id="kb1", filename="管理办法.pdf", file_type="pdf"),
            KnowledgeFile(file_id="f_other", kb_id="kb2", filename="别库.pdf", file_type="pdf"),
        ])
        await db.commit()

        @contextlib.asynccontextmanager
        async def fake_context():
            yield db

        monkeypatch.setattr(kf_repo_module.pg_manager, "get_async_session_context", fake_context)
        yield db
    await engine.dispose()


async def test_set_doc_domain_roundtrips_and_is_scoped_to_one_kb(file_repo):
    repo = KnowledgeFileRepository()
    changed = await repo.set_doc_domain(kb_id="kb1", file_ids=["f_chem"], doc_domain=DOMAIN_CHEMICAL)
    await repo.set_doc_domain(kb_id="kb1", file_ids=["f_lab"], doc_domain=DOMAIN_LABORATORY_MANAGEMENT)

    assert changed == 1
    assert await repo.get_domain_by_file_ids(kb_id="kb1") == {
        "f_chem": DOMAIN_CHEMICAL,
        "f_lab": DOMAIN_LABORATORY_MANAGEMENT,
    }
    # 跨库的 file_id 不该被误改
    assert await repo.get_domain_by_file_ids(kb_id="kb2") == {}


async def test_set_doc_domain_blank_clears_the_override(file_repo):
    """空值 = 回到"跟随知识库配置"，此时不应再出现在覆盖表里。"""
    repo = KnowledgeFileRepository()
    await repo.set_doc_domain(kb_id="kb1", file_ids=["f_chem"], doc_domain=DOMAIN_CHEMICAL)
    await repo.set_doc_domain(kb_id="kb1", file_ids=["f_chem"], doc_domain=None)

    assert await repo.get_domain_by_file_ids(kb_id="kb1") == {}


async def test_set_doc_domain_accepts_an_alias(file_repo):
    """别名与规范名等价——否则前端换个写法就会被拒。"""
    repo = KnowledgeFileRepository()
    await repo.set_doc_domain(kb_id="kb1", file_ids=["f_lab"], doc_domain="lab_management")

    assert await repo.get_domain_by_file_ids(kb_id="kb1") == {
        "f_lab": DOMAIN_LABORATORY_MANAGEMENT
    }


# --------------------------------------------------------------------------- #
# 七、政策法规族（文件 → 条款 → 责任主体）
# --------------------------------------------------------------------------- #

def _policy_payload() -> dict:
    """一份真实文档的形状：教科信厅函〔2021〕38号 + 发文机关 + 任务条目 + 危险源。"""
    notice = "教育部办公厅关于开展加强高校实验室安全专项行动的通知"
    return {
        "entities": [
            {
                "text": notice,
                "label": "PolicyDocument",
                "attributes": [{"text": "教科信厅函〔2021〕38号", "label": "Attribute"}],
            },
            {"text": "教育部办公厅", "label": "IssuingAuthority", "attributes": []},
            {"text": "学校党政主要负责人是第一责任人", "label": "PolicyRequirement", "attributes": []},
            {"text": "剧毒", "label": "RiskSource", "attributes": []},
        ],
        "relations": [
            {
                "source": {"text": notice, "label": "PolicyDocument"},
                "target": {"text": "教育部办公厅", "label": "IssuingAuthority"},
                "text": "印发",
                "label": "ISSUED_BY",
            }
        ],
    }


def test_policy_clean_result_is_kept_and_marks_that_validation_ran():
    result = normalize_extraction_result(_policy_payload(), "llm", domain=DOMAIN_POLICY)

    assert [e["label"] for e in result["entities"]] == [
        "PolicyDocument",
        "IssuingAuthority",
        "PolicyRequirement",
        "RiskSource",
    ]
    assert [r["label"] for r in result["relations"]] == ["ISSUED_BY"]
    assert result["metadata"]["ontology"] == {
        "domain": DOMAIN_POLICY,
        "dropped_entities": 0,
        "dropped_relations": 0,
        "unknown_labels": [],
    }


def test_policy_domain_rejects_other_families_labels():
    """"选错域"唯一能被发现的地方就在这里。

    政策库里混进管理族的 `Laboratory`、或者用管理族的关系名，都不该静默入库——
    否则图谱上会长出一批"看起来合理、其实不属于这族"的节点。
    """
    payload = {
        "entities": [
            {"text": e["text"], "label": e["label"], "attributes": []}
            for e in _policy_payload()["entities"]
        ]
        + [{"text": "化学实验室", "label": "Laboratory", "attributes": []}],
        "relations": [
            {
                "source": {"text": "化学实验室", "label": "Laboratory"},
                "target": {"text": "剧毒", "label": "RiskSource"},
                "text": "需检查",
                "label": "MUST_INSPECT",
            }
        ],
    }
    result = normalize_extraction_result(payload, "llm", domain=DOMAIN_POLICY)

    assert "Laboratory" not in [e["label"] for e in result["entities"]]
    assert result["relations"] == []
    verdict = result["metadata"]["ontology"]
    assert verdict["dropped_entities"] == 1
    assert verdict["dropped_relations"] == 1
    assert set(verdict["unknown_labels"]) == {"Laboratory", "MUST_INSPECT"}


@pytest.mark.parametrize(
    "other,other_labels",
    [
        (DOMAIN_CHEMICAL, CHEMICAL_LABELS),
        (DOMAIN_LABORATORY_MANAGEMENT, LAB_MANAGEMENT_LABELS),
    ],
)
def test_policy_labels_do_not_leak_across_families(other, other_labels):
    """防跨本体复制粘贴。这里必须用词边界匹配，不能用裸 `in`。

    `PolicyDocument` 里含 `Document`，而 `Document` 正是管理族的实体标签：
    裸包含判断会报假警。`\bDocument\b` 不匹配 `PolicyDocument`（`y` 与 `D`
    之间没有词边界），才是这条守卫想要的语义。

    只查**实体**标签，不查关系：`APPLIES_TO` / `ISSUED_BY` / `RESPONSIBLE_FOR`
    在三族里语义相同，共用是设计意图而非泄漏。
    """
    policy = domain_schema(DOMAIN_POLICY)
    other_schema = domain_schema(other)

    for label in POLICY_LABELS:
        assert not re.search(rf"\b{re.escape(label)}\b", other_schema), f"{label} 泄漏进 {other}"
    for label in other_labels:
        assert not re.search(rf"\b{re.escape(label)}\b", policy), f"{label} 泄漏进 policy"


def test_policy_schema_precedes_custom_schema():
    """域约束是硬边界，本地补充在后——顺序反了模型会先读到宽松的要求。"""
    custom = "本库额外要求：只抽部委文件"
    composed = domain_schema(DOMAIN_POLICY, custom)

    assert composed.startswith(POLICY_EXTRACTION_SCHEMA)
    assert composed.endswith(custom)


# --------------------------------------------------------------------------- #
# 五、安全技术说明书（MSDS）字段归属（2026-09-17 按 13 份真实 MSDS 补齐）
# --------------------------------------------------------------------------- #

def test_msds_physical_property_is_allowed_in_chemical_family():
    """理化特性必须能独立成节点，不能被塞进 HazardCharacteristic。

    13 份真实 MSDS 全都带「三、理化特性」一节，闪点 / 爆炸极限直接决定储存与通风要求，
    合并进危险性节点就没法按参数检索了。
    """
    payload = {
        "entities": [
            {"text": "乙醇", "label": "Chemical", "attributes": []},
            {"text": "闪点：13℃", "label": "PhysicalProperty", "attributes": []},
        ],
        "relations": [
            {
                "source": {"text": "乙醇", "label": "Chemical"},
                "target": {"text": "闪点：13℃", "label": "PhysicalProperty"},
                "text": "闪点",
                "label": "HAS_PHYSICAL_PROPERTY",
            }
        ],
    }
    result = normalize_extraction_result(payload, "llm", domain=DOMAIN_CHEMICAL)

    assert [e["label"] for e in result["entities"]] == ["Chemical", "PhysicalProperty"]
    assert [r["label"] for r in result["relations"]] == ["HAS_PHYSICAL_PROPERTY"]
    assert result["metadata"]["ontology"] == {
        "domain": DOMAIN_CHEMICAL,
        "dropped_entities": 0,
        "dropped_relations": 0,
        "unknown_labels": [],
    }


def test_msds_physical_property_is_rejected_in_lab_management():
    """理化特性只属于化学品本体；管理族的文档里出现它就是选错域。"""
    payload = {
        "entities": [
            {"text": "化学实验室", "label": "Laboratory", "attributes": []},
            {"text": "闪点：13℃", "label": "PhysicalProperty", "attributes": []},
        ],
        "relations": [],
    }
    result = normalize_extraction_result(payload, "llm", domain=DOMAIN_LABORATORY_MANAGEMENT)

    assert [e["label"] for e in result["entities"]] == ["Laboratory"]
    assert result["metadata"]["ontology"]["unknown_labels"] == ["PhysicalProperty"]


def test_chemical_schema_pins_msds_field_rules():
    """MSDS 的字段归属规则写在 schema 里，删掉就会红。

    这几条不是排版：平台元数据不该入库、`？` 是来源标注要保留数值、PARAMETERFEATURES
    是理化特性的重复汇总——少任何一条，13 份 MSDS 就会抽出脏事实。
    """
    schema = domain_schema(DOMAIN_CHEMICAL)

    assert "MSDS" in schema
    assert "PARAMETERFEATURES" in schema
    assert "？-1" in schema
    for noise in ("无资料", "无意义", "data unavailable"):
        assert noise in schema
