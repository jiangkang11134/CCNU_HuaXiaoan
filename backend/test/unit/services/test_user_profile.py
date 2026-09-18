"""个人资料与回答风格（个性化的第三级）。

这里守的是三条用户明确口径：
1. **正常风格不产出任何指令**——"正常"是缺省态，不是一种风格。
2. **回答风格优先级**：本轮会话事实 > 个人资料 > 长期记忆偏好。
3. **只有身份/专业/风格进 prompt**——姓名、性别、学工号进去了只是白烧 token。
"""
import pytest

from yuxi.memory.observation import MEMORY_FACT_KEYS
from yuxi.services.user_profile import (
    BUSINESS_ROLE_LABELS,
    DEFAULT_RESPONSE_STYLE,
    PROFILE_OVERRIDDEN_FACT_KEYS,
    PROFILE_PRECEDENCE_NOTE,
    RESPONSE_STYLE_CHOICES,
    STYLE_CONCISE,
    STYLE_INSTRUCTIONS,
    STYLE_LABELS,
    STYLE_NORMAL,
    STYLE_PRECEDENCE_NOTE,
    STYLE_THOROUGH,
    build_profile_context,
    normalize_response_style,
    overridden_fact_keys,
    style_instruction,
)


# --- 取值收敛 ---------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("concise", "concise"),
    ("normal", "normal"),
    ("thorough", "thorough"),
    ("CONCISE", "concise"),       # 库里可能出现历史上写入的大写
    (" Thorough ", "thorough"),
    ("", "normal"),               # 空串不是合法值
    (None, "normal"),
    ("详细严密", "normal"),        # 前端误传中文标签
    ("ultra", "normal"),
    (123, "normal"),
])
def test_normalize_response_style(raw, expected):
    assert normalize_response_style(raw) == expected


def test_default_style_is_normal_and_is_a_legal_choice():
    assert DEFAULT_RESPONSE_STYLE == STYLE_NORMAL
    assert DEFAULT_RESPONSE_STYLE in RESPONSE_STYLE_CHOICES
    assert set(RESPONSE_STYLE_CHOICES) == {STYLE_CONCISE, STYLE_NORMAL, STYLE_THOROUGH}
    # 每个取值都要有前端可直接用的中文标签，否则下拉框会露出英文 key
    assert set(STYLE_LABELS) == set(RESPONSE_STYLE_CHOICES)


# --- 风格指令 ---------------------------------------------------------------

def test_normal_style_emits_no_instruction_at_all():
    """用户原话：正常就是不定义。写成"请正常回答"反而会压平已有的具体偏好。"""
    assert style_instruction(STYLE_NORMAL) == ""
    assert style_instruction("乱七八糟") == ""
    assert style_instruction(None) == ""


def test_normal_is_deliberately_absent_from_the_instruction_table():
    assert STYLE_NORMAL not in STYLE_INSTRUCTIONS
    assert set(STYLE_INSTRUCTIONS) == {STYLE_CONCISE, STYLE_THOROUGH}


@pytest.mark.parametrize("style", [STYLE_CONCISE, STYLE_THOROUGH])
def test_style_instructions_stay_in_the_expression_layer(style):
    """风格只能约束"怎么说"，绝不能改写"说什么"。

    简短风格漏掉安全警示是这套个性化最危险的失效方式，所以两种风格的提示词
    都必须自带边界声明。
    """
    text = STYLE_INSTRUCTIONS[style]
    assert text
    assert "边界" in text
    assert "安全" in text


def test_concise_instruction_forbids_dropping_safety_warnings():
    text = STYLE_INSTRUCTIONS[STYLE_CONCISE]
    assert "不能因为要简短而漏掉" in text


def test_thorough_instruction_forbids_padding_and_hiding_the_conclusion():
    text = STYLE_INSTRUCTIONS[STYLE_THOROUGH]
    assert "啰嗦" in text
    assert "不要把结论藏在推导后面" in text


# --- 段落拼装 ---------------------------------------------------------------

def test_build_profile_context_returns_empty_when_nothing_usable():
    """没有任何可注入信息就不要塞一个空段——空段本身就是噪声。"""
    assert build_profile_context(business_role=None, major=None, response_style="normal") == ""
    assert build_profile_context(business_role="", major="  ", response_style=None) == ""
    assert build_profile_context() == ""


def test_build_profile_context_emits_identity_and_major_even_when_style_is_normal():
    """normal 只是"不加风格指令"，不是"整段都不要"。

    身份与专业服务于指代消歧和检索提示，跟风格无关。
    """
    ctx = build_profile_context(business_role="student", major="应用化学", response_style="normal")
    assert "身份：学生" in ctx
    assert "专业：应用化学" in ctx
    assert "回答风格" not in ctx
    assert STYLE_PRECEDENCE_NOTE not in ctx


@pytest.mark.parametrize("role, label", list(BUSINESS_ROLE_LABELS.items()))
def test_business_role_labels_cover_every_registration_identity(role, label):
    assert build_profile_context(business_role=role).count("身份：") == 1
    assert f"身份：{label}" in build_profile_context(business_role=role)


def test_unknown_business_role_is_dropped_not_guessed():
    """身份认不出来就缺一项，不要编一个"用户"糊过去。"""
    assert "身份" not in build_profile_context(business_role="guest", major="化学")


def test_build_profile_context_carries_the_style_precedence_and_the_style():
    ctx = build_profile_context(business_role="faculty", response_style="concise")
    # 通用顺序在段首（PROFILE_PRECEDENCE_NOTE），风格那一条只讲怎么判断
    assert PROFILE_PRECEDENCE_NOTE in ctx
    assert STYLE_PRECEDENCE_NOTE in ctx
    assert STYLE_INSTRUCTIONS[STYLE_CONCISE] in ctx


def test_build_profile_context_never_carries_identity_documents():
    """学工号、姓名、性别都不该出现在 prompt 里。"""
    ctx = build_profile_context(business_role="student", major="化学", response_style="thorough")
    for leaked in ("学工号", "姓名", "性别"):
        assert leaked not in ctx


def test_build_profile_context_states_it_is_not_an_evidence_source():
    """与 [当前可用事实]、[权威修正] 同一套纪律：这一段不参与结论。"""
    ctx = build_profile_context(business_role="student", response_style="concise")
    assert "不得作为实验室安全规范、化学品性质或合规结论的依据" in ctx
    # 声明必须排在内容之前，否则模型可能先读到事实再读到免责
    assert ctx.index("不得作为") < ctx.index("身份：")


def test_style_precedence_note_references_the_upstream_block_by_name():
    """风格说明指向 [当前可用事实]，所以该段必须存在且排在个人资料之前。

    这条断言是给 chat_service 的注入顺序做旁证：顺序改了，这句话就指错地方了。
    通用顺序不在这里重复（由 PROFILE_PRECEDENCE_NOTE 统一说），所以这里不断言它。
    """
    assert "[当前可用事实]" in STYLE_PRECEDENCE_NOTE
    assert "本轮会话事实 >" not in STYLE_PRECEDENCE_NOTE


# --- 冲突仲裁：会话事实 > 个人资料 > 长期记忆 --------------------------------

def test_general_precedence_note_covers_every_kind_of_conflict():
    """用户口径：除了语言风格，其他冲突也按 会话事实 > 个人资料 > 长期记忆。

    所以顺序声明必须写在段首、覆盖全段，而不是只挂在风格那一条上。
    """
    assert "本轮会话事实 > 本段个人资料 > 长期记忆中记下的同类信息" in PROFILE_PRECEDENCE_NOTE


def test_precedence_declaration_is_emitted_even_when_style_is_normal():
    """normal 不加风格指令，但优先级声明照常在——专业、身份照样会撞车。"""
    ctx = build_profile_context(business_role="student", major="化学", response_style="normal")
    assert PROFILE_PRECEDENCE_NOTE in ctx
    # 声明排在内容之前
    assert ctx.index(PROFILE_PRECEDENCE_NOTE) < ctx.index("身份：")


def test_profile_overridden_fact_keys_maps_major_to_the_memory_key():
    """长期记忆白名单里的确有这个键，映射才成立（键名改了这里要跟着改）。"""
    assert PROFILE_OVERRIDDEN_FACT_KEYS["major"] == "user.major"
    assert PROFILE_OVERRIDDEN_FACT_KEYS["major"] in MEMORY_FACT_KEYS


def test_overridden_fact_keys_only_fires_when_the_field_has_a_value():
    assert overridden_fact_keys(major="应用化学") == {"user.major"}
    assert overridden_fact_keys(major="   ") == set()
    assert overridden_fact_keys(major=None) == set()
    assert overridden_fact_keys() == set()


def test_overridden_fact_keys_never_touches_session_facts():
    """会话事实是最高层，个人资料永远压制不了它。

    这条是防"将来图省事把 project.* 也加进映射"——那会颠倒用户定的顺序。
    """
    keys = overridden_fact_keys(major="化学")
    assert not any(key.startswith("project.") for key in keys)
    assert all(key.startswith("user.") for key in keys)
