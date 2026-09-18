from __future__ import annotations

from types import SimpleNamespace

from yuxi.agents.context import BaseContext


def test_request_intent_survives_context_update():
    """chat_service 写入的 request_intent 不能被 update() 静默丢弃。

    回归：BaseContext 曾缺少 request_intent / request_intent_confidence 两个字段，
    而 update() 用 hasattr 守卫，导致意图分类结果被无声丢弃、全程无报错。
    """
    context = BaseContext()
    context.update({"request_intent": "correction", "request_intent_confidence": 0.75})
    assert context.request_intent == "correction"
    assert context.request_intent_confidence == 0.75


def test_request_intent_survives_update_from_dict():
    """agent base 走的是 update_from_dict，与 update 语义一致，同样不能丢。"""
    context = BaseContext()
    context.update_from_dict({"request_intent": "graph_rag_query", "request_intent_confidence": 0.5})
    assert context.request_intent == "graph_rag_query"
    assert context.request_intent_confidence == 0.5


def test_request_intent_defaults_are_neutral():
    context = BaseContext()
    assert context.request_intent == ""
    assert context.request_intent_confidence == 0.0


def test_request_intent_is_not_exposed_as_agent_config():
    """意图是运行时输入，不应出现在 agent 配置 UI 的可配置项里。"""
    items = BaseContext.get_configurable_items(user_role="system_admin")
    assert "request_intent" not in items
    assert "request_intent_confidence" not in items
    assert "intent_caliber" not in items


# ---------------------------------------------------------------------------
# U1：意图必须真的改变回答口径，否则上面这些"字段没被丢掉"的测试毫无意义
# ---------------------------------------------------------------------------

def _load_chatbot_prompt_module():
    """按文件路径加载 prompt.py。

    走包导入会拉起 ``yuxi.agents.buildin.chatbot`` 的整条依赖链（deepagents 等），
    本机依赖版本不齐；这里只测口径映射，不需要那条链。
    （同一手法见 test/unit/memory/test_memory_observation.py。）
    """
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "package" / "yuxi" / "agents" / "buildin" / "chatbot" / "prompt.py"
    spec = importlib.util.spec_from_file_location("chatbot_prompt_under_test_u1", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _context(intent="", confidence=0.0, **extra):
    fields = dict(
        system_prompt="AGENT_ROLE_PROMPT",
        request_intent=intent,
        request_intent_confidence=confidence,
        memory_observe=False,
    )
    fields.update(extra)
    return SimpleNamespace(**fields)


def test_caliber_covers_every_intent_in_the_taxonomy():
    """口径表必须覆盖 request_preprocessor / intent_classifier 的全部意图。

    分类法加新意图却忘了加口径，会让该意图静默退回默认口径——这里把它钉住，
    加意图时这条会失败，提醒一起改。
    """
    module = _load_chatbot_prompt_module()
    assert set(module.INTENT_CALIBERS) == {
        "graph_rag_query", "correction", "memory_candidate", "simple_task"}
    assert all(text.strip() for text in module.INTENT_CALIBERS.values())


def test_caliber_switches_with_high_confidence_intent():
    module = _load_chatbot_prompt_module()
    for intent in ("correction", "memory_candidate", "simple_task"):
        prompt = module.build_prompt_with_context(_context(intent, 0.9))
        assert module.INTENT_CALIBERS[intent].strip() in prompt
        # 不能同时带上别的口径，否则模型收到互相矛盾的要求
        others = [t for k, t in module.INTENT_CALIBERS.items() if k != intent]
        assert all(t.strip() not in prompt for t in others)


def test_caliber_falls_back_to_default_below_confidence_gate():
    """置信度不足时退回默认（更严的）口径。

    误判成"纠错"会让模型转而去顺从用户说法，比"口径不生效"糟得多。
    """
    module = _load_chatbot_prompt_module()
    low = module.build_prompt_with_context(_context("correction", 0.69))
    assert module.KB_GROUNDED_CALIBER.strip() in low
    assert module.CORRECTION_CALIBER.strip() not in low
    # 边界值本身视为通过
    at_gate = module.build_prompt_with_context(
        _context("correction", module.INTENT_CALIBER_MIN_CONFIDENCE))
    assert module.CORRECTION_CALIBER.strip() in at_gate


def test_caliber_default_for_unknown_missing_or_garbage_intent():
    module = _load_chatbot_prompt_module()
    for intent, conf in (("", 0.0), ("not_an_intent", 0.99), (None, None), ("correction", "NaN")):
        prompt = module.build_prompt_with_context(_context(intent, conf))
        assert module.KB_GROUNDED_CALIBER.strip() in prompt


def test_caliber_survives_context_without_intent_fields():
    """subagent 等调用方可能不写这两个字段，缺字段不能炸。"""
    module = _load_chatbot_prompt_module()
    prompt = module.build_prompt_with_context(SimpleNamespace(system_prompt="SP"))
    assert module.KB_GROUNDED_CALIBER.strip() in prompt
    assert "SP" in prompt


def test_caliber_switch_off_restores_single_caliber_behavior():
    """SystemKV 关掉开关后必须完全回到改造前：一句口径都不注入。"""
    module = _load_chatbot_prompt_module()
    prompt = module.build_prompt_with_context(_context("correction", 0.9, intent_caliber=False))
    assert all(text.strip() not in prompt for text in module.INTENT_CALIBERS.values())
    assert "回答口径" not in prompt
    # 开关字段缺省视为开（口径是新默认行为），只有显式 False 才关
    assert module.KB_GROUNDED_CALIBER.strip() in module.build_prompt_with_context(_context())


def test_caliber_sits_after_agent_prompt():
    """顺序即优先级：口径是安全纪律，须在 agent 角色设定之后。顺序变了要有人知道。"""
    module = _load_chatbot_prompt_module()
    prompt = module.build_prompt_with_context(_context("correction", 0.9))
    assert prompt.index("AGENT_ROLE_PROMPT") < prompt.index(module.CORRECTION_CALIBER.strip())


def test_observe_block_is_retired_from_answer_prompt():
    """抽取已改成回答结束后的独立调用，回答 prompt 里不能再出现围栏协议。

    这里同时钉住"开关残留"：即便调用方仍传 ``memory_observe=True``，
    也不能有任何内容被注进去——协议下线必须是彻底的。
    """
    module = _load_chatbot_prompt_module()
    prompt = module.build_prompt_with_context(_context("correction", 0.9, memory_observe=True))
    assert "yuxi-memory" not in prompt
    assert not hasattr(module, "MEMORY_OBSERVE_PROMPT")


def test_preference_caliber_never_authorises_changing_safety_conclusions():
    """红线守卫：偏好只能影响表达方式，不得影响结论层。

    这条不在测实现，而是在钉住 M1 的硬边界——口径措辞被改写时若丢掉这句约束，
    "回答要简短"这类偏好就可能被模型理解成"安全提示也可以省略"。
    """
    module = _load_chatbot_prompt_module()
    text = module.MEMORY_CANDIDATE_CALIBER
    assert "偏好不是安全依据" in text
    assert "不得据此改变安全结论" in text
    # 默认口径必须要求检索依据、且禁止无依据外推
    grounded = module.KB_GROUNDED_CALIBER
    assert "必须先检索知识库" in grounded
    assert "不得凭印象" in grounded
    assert "未检索到相关依据" in grounded
