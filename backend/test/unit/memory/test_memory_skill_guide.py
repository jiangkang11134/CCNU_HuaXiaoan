"""抽取口径的技能文件（memory-extraction）的守护测试。

这个文件是**唯一口径来源**：抽取调用不再用硬编码 prompt，而是运行时去读
``agents/skills/buildin/memory-extraction/SKILL.md`` 里被 prompt 标记夹住的那一段。
好处是改口径不必改代码 + 发版；代价是**它能悄悄跑偏而没人发现**——白名单加了键、
文档没加，模型就永远抽不出那个键，而代码和测试全绿。

所以这里的用例不是"文档写得好不好"，而是钉死两类事故：

1. **漂移**：``MEMORY_FACT_KEYS`` 与文档里的键互为子集（正向 + 反向都查），
   且白名单的唯一定义处仍是 ``observation.py``。
2. **静默失效**：文件被挪走、标记被删/被重复引用（初版真踩到过——说明段里引用了
   标记字面量，``find`` 命中了更靠前的那处，截出 5 个字符的散文垃圾当 prompt，
   全程不报错），这些情况必须露成"空口径 + WARNING"，由 ``build_system_prompt``
   退回内置兜底，而不是拿一段废话去指挥模型。
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

import pytest
import yaml

from yuxi.memory import extraction
from yuxi.memory.extraction import (
    EXTRACTION_SYSTEM_PROMPT,
    GUIDE_PROMPT_END,
    GUIDE_PROMPT_START,
    GUIDE_SKILL_PATH,
    build_system_prompt,
    load_extraction_guide,
)
from yuxi.memory.observation import MEMORY_FACT_KEYS

#: 文档里出现的、长得像事实键的 token。用来做反向漂移检查（抓 user.hobby 这类笔误）。
_FACT_KEY_TOKEN_RE = re.compile(r"\b(?:user|project)\.[a-z_]+")


def _body_of(path: Path) -> str:
    return extraction._split_skill_frontmatter(path.read_text(encoding="utf-8"))


def _skill_md(prompt_body: str, *, extra_mention: str = "") -> str:
    """造一份最小可用的 SKILL.md：带 frontmatter、一段人看的内容、一对标记。"""
    return (
        "---\n"
        "name: 测试技能\n"
        "slug: memory-extraction\n"
        "description: 测试用\n"
        "---\n"
        "\n"
        "# 给人和 Agent 看的说明\n"
        "\n"
        f"{extra_mention}"
        "\n"
        f"{GUIDE_PROMPT_START}\n{prompt_body}\n{GUIDE_PROMPT_END}\n"
    )


# --------------------------------------------------------------------------
# 真实技能文件：存在性、结构、与白名单的一致性
# --------------------------------------------------------------------------

def test_real_skill_file_is_where_the_code_looks_for_it():
    assert GUIDE_SKILL_PATH.exists(), f"抽取口径文件不见了: {GUIDE_SKILL_PATH}"
    assert GUIDE_SKILL_PATH.name == "SKILL.md"


def test_real_skill_file_has_exactly_one_marker_pair():
    """标记必须恰好各一次：重复引用会让截取区间错位，且这是静默的。"""
    body = _body_of(GUIDE_SKILL_PATH)
    assert body.count(GUIDE_PROMPT_START) == 1, "prompt 起始标记必须恰好出现一次（说明段里别引用它）"
    assert body.count(GUIDE_PROMPT_END) == 1, "prompt 结束标记必须恰好出现一次"
    assert body.index(GUIDE_PROMPT_START) < body.index(GUIDE_PROMPT_END)


def test_real_guide_covers_every_whitelisted_fact_key():
    """正向漂移守卫：白名单里加了键，文档必须同步加，否则那个键永远抽不出来。"""
    guide = load_extraction_guide()
    assert guide, "抽取口径为空，说明标记或文件有问题"
    missing = sorted(k for k in MEMORY_FACT_KEYS if k not in guide)
    assert not missing, f"这些事实键在抽取口径里没有说明: {missing}"


def test_real_guide_mentions_no_unknown_fact_key():
    """反向漂移守卫：文档里写了个白名单没有的键（笔误或臆造），模型抽了也只会被丢弃。"""
    guide = load_extraction_guide()
    tokens = set(_FACT_KEY_TOKEN_RE.findall(guide))
    unknown = sorted(t for t in tokens if t not in MEMORY_FACT_KEYS)
    assert not unknown, f"抽取口径里出现了白名单外的键（会被归一化环节丢弃）: {unknown}"


def test_real_guide_excludes_human_only_sections():
    """只送标记内的段落给模型：机制说明、使用口径、排查步骤不该进 prompt（烧 token 还干扰模型）。"""
    guide = load_extraction_guide()
    body = _body_of(GUIDE_SKILL_PATH)
    assert len(guide) < len(body), "口径段不可能有整篇长——标记截取没生效"
    for human_only in ("## 五、记忆该怎么用（回答侧）", "## 六、怎么改", "## 七、出问题先看哪里", "## 二、白名单事实键"):
        assert human_only in body, f"人看的段落被误删了: {human_only}"
        assert human_only not in guide, f"人看的段落混进 prompt 了: {human_only}"


def test_prompt_section_states_the_easy_to_misread_cases():
    """四类最容易抽错的句式必须在口径里点名——它们都不会被服务端拦掉，只能靠 prompt 挡。

    否定句、替他人转述、将来时/假设、跨轮拼合这四种情形下，模型输出的是**合法 JSON、合法键**，
    白名单和敏感词都放行，落库后就是一条错误的画像。所以只能写进口径，并用这个用例钉住不被删。
    """
    guide = load_extraction_guide()
    for keyword, why in (
        ("否定句", "「我不做 X」不能记成研究方向"),
        ("替他人", "「我同学想问…」不是用户自己的画像"),
        ("将来时", "「以后如果要做 X」不是当前目标"),
        ("跨轮", "跨轮拼起来的信息要标首次出现的那一轮"),
    ):
        assert keyword in guide, f"抽取口径里缺了「{keyword}」这一类（{why}）"


def test_real_guide_states_confidence_threshold_that_actually_gates_activation():
    """>=0.85 是 ``arbitrate`` 里真实生效的门槛，口径里必须点明，否则模型会随手给低分。

    这不是文案讲究：``user.*`` 低于阈值只落 candidate，而 candidate 目前没有确认入口，
    等于这条记忆白抽。模型不知道这件事就会把明确陈述也标成 0.7。
    """
    from yuxi.memory.observation import AUTO_CONFIRM_MIN_CONFIDENCE

    guide = load_extraction_guide()
    assert str(AUTO_CONFIRM_MIN_CONFIDENCE) in guide


def _builtin_specs() -> list:
    """直接按文件加载 buildin 索引，**不走** ``import yuxi.agents.skills.buildin``。

    后者会连带 exec ``yuxi/agents/__init__.py``（langchain / deepagents 全家桶）。
    本机 deepagents 版本不匹配会让整轮收集中断，把这种依赖引进 memory 套件等于
    给一个纯业务测试装了颗地雷。那个模块本身只 import dataclasses/pathlib，可以单跑。
    """
    init_path = GUIDE_SKILL_PATH.parents[1] / "__init__.py"
    spec = importlib.util.spec_from_file_location("_yuxi_buildin_index_probe", init_path)
    module = importlib.util.module_from_spec(spec)
    # 必须先登记再 exec：@dataclass 会通过 cls.__module__ 回查 sys.modules 来解析注解，
    # 没登记时 dataclasses 内部拿到 None 直接 AttributeError——报的错与真实原因毫不相干。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return list(module.BUILTIN_SKILLS)


def _frontmatter_of(path: Path) -> dict:
    """解析 SKILL.md 的 YAML frontmatter。

    刻意不喂 ``_split_skill_frontmatter`` 的返回值给 yaml —— 那是**正文**，
    里面全是 markdown，safe_load 会直接炸。frontmatter 是文件开头到第二个 ``---``。
    """
    raw = path.read_text(encoding="utf-8")
    assert raw.startswith("---"), "SKILL.md 必须以 frontmatter 开头"
    return yaml.safe_load(raw.split("---", 2)[1]) or {}


def test_skill_is_registered_as_builtin_and_slug_matches_everywhere():
    """三处 slug 必须一致：目录名、frontmatter.slug、BUILTIN_SKILLS 的 slug。

    不一致不是"少个功能"——``list_builtin_skill_specs`` 会直接 raise，worker 起不来。
    """
    specs = _builtin_specs()
    matches = [s for s in specs if s.slug == "memory-extraction"]
    assert len(matches) == 1, "builtin 技能表里没有（或有重复的）memory-extraction"
    spec = matches[0]
    assert Path(spec.source_dir).resolve() == GUIDE_SKILL_PATH.parent.resolve()
    assert spec.description.strip(), "内置技能的 description 不能为空（Agent 只看得到它和 name）"

    parsed = _frontmatter_of(GUIDE_SKILL_PATH)
    assert parsed["slug"] == spec.slug == GUIDE_SKILL_PATH.parent.name
    assert str(parsed["name"]).strip()
    assert str(parsed["description"]).strip()


# --------------------------------------------------------------------------
# 加载器的行为：异常输入一律降级为空口径，绝不返回半截垃圾
# --------------------------------------------------------------------------

def test_missing_file_yields_empty_guide(tmp_path):
    assert load_extraction_guide(tmp_path / "nope" / "SKILL.md") == ""


def test_file_without_markers_yields_empty_guide(tmp_path):
    path = tmp_path / "SKILL.md"
    path.write_text("---\nname: x\n---\n只有正文，没有标记\n", encoding="utf-8")
    assert load_extraction_guide(path) == ""


def test_duplicated_marker_yields_empty_guide(tmp_path):
    """说明段里顺带引用了标记字面量 → 必须判为损坏，而不是截出一段散文当 prompt。"""
    path = tmp_path / "SKILL.md"
    path.write_text(
        _skill_md("真正的口径内容", extra_mention=f"这段说明里引用了 {GUIDE_PROMPT_START} 这个标记。\n"),
        encoding="utf-8",
    )
    assert load_extraction_guide(path) == ""


def test_only_the_marked_section_is_returned(tmp_path):
    path = tmp_path / "SKILL.md"
    path.write_text(_skill_md("真正的口径内容"), encoding="utf-8")
    guide = load_extraction_guide(path)
    assert guide == "真正的口径内容"
    assert "给人和 Agent 看的说明" not in guide


def test_guide_is_cached_until_the_file_changes(tmp_path):
    """按 (mtime, size) 缓存：内容没变就不重复解析，变了要立刻生效（不必重启进程）。"""
    path = tmp_path / "SKILL.md"
    path.write_text(_skill_md("第一版"), encoding="utf-8")
    os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    assert load_extraction_guide(path) == "第一版"

    # 改成等长内容并把 mtime 拨回去 —— 缓存应当仍然认为是同一份
    path.write_text(_skill_md("第二版"), encoding="utf-8")
    os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    assert load_extraction_guide(path) == "第一版"

    # mtime 变了 → 重新读
    os.utime(path, ns=(2_000_000_000, 2_000_000_000))
    assert load_extraction_guide(path) == "第二版"


# --------------------------------------------------------------------------
# 组装：口径优先，兜底不得失效，占位符必须替换
# --------------------------------------------------------------------------

def test_build_system_prompt_uses_the_skill_guide():
    prompt = build_system_prompt()
    guide = load_extraction_guide()
    assert guide, "抽取口径为空——技能文件或标记有问题"
    # 口径是**主体**（不是附在兜底 prompt 后面的补充说明）；两者唯一的差别是
    # 收尾统一替换的长度占位符，所以能这样逐字对齐。
    assert prompt == guide.replace("{" + "limit" + "}", str(extraction.MAX_CONTENT_LEN))
    assert prompt != EXTRACTION_SYSTEM_PROMPT.strip(), "读到了口径却没用它"
    assert "{" + "limit" + "}" not in prompt, "占位符没替换，模型会看到字面量"


def test_build_system_prompt_falls_back_when_guide_is_gone(monkeypatch):
    """技能文件被删/写坏时，抽取必须还能跑（口径旧一点也不能功能整体失效）。"""
    monkeypatch.setattr(extraction, "load_extraction_guide", lambda *a, **k: "")
    prompt = build_system_prompt()
    assert prompt == EXTRACTION_SYSTEM_PROMPT.strip()
    assert "{" + "limit" + "}" not in prompt
    for key in MEMORY_FACT_KEYS:
        assert key in prompt, f"兜底 prompt 漏了事实键 {key}"


@pytest.mark.parametrize("path_kind", ["missing", "no_markers"])
def test_degradation_never_leaks_the_whole_document(tmp_path, path_kind):
    """降级的定义是"空口径"，不是"把整篇文档当 prompt"——后者会把机制说明喂给模型。"""
    if path_kind == "missing":
        target = tmp_path / "absent.md"
    else:
        target = tmp_path / "SKILL.md"
        target.write_text("---\nname: x\n---\n# 六、出问题先看哪里\n内部排查步骤\n", encoding="utf-8")
    assert load_extraction_guide(target) == ""
