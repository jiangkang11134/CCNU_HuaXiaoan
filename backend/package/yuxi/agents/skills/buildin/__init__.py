from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BuiltinSkillSpec:
    slug: str
    source_dir: Path
    description: str = ""
    version: str = "1.0.0"
    tool_dependencies: tuple[str, ...] = ()
    mcp_dependencies: tuple[str, ...] = ()
    skill_dependencies: tuple[str, ...] = ()


_SKILLS_ROOT = Path(__file__).resolve().parent

BUILTIN_SKILLS: list[BuiltinSkillSpec] = [
    BuiltinSkillSpec(
        slug="image-gen",
        source_dir=_SKILLS_ROOT / "image-gen",
        description="在 Agent 沙盒中生成图片并保存到 outputs，默认支持 Qwen-Image，也可接入其它图片生成接口。",
        version="2026.06.02",
        tool_dependencies=("present_artifacts",),
    ),
    BuiltinSkillSpec(
        slug="deep-research",
        source_dir=_SKILLS_ROOT / "deep-research",
        description="深度研究编排方法论：澄清范围、拆解规划、并行调度子智能体调研、对抗式核验、综合成带引用的结构化报告。",
        version="2026.06.05",
        tool_dependencies=("tavily_search",),
    ),
    BuiltinSkillSpec(
        slug="knowledge-base",
        source_dir=_SKILLS_ROOT / "knowledge-base",
        description="使用 Yuxi 知识库进行检索、打开文档、文档内定位和查看思维导图。",
        version="2026.06.24",
        tool_dependencies=(
            "list_kbs",
            "query_kb",
            "find_kb_document",
            "open_kb_document",
            "get_mindmap",
            "search_file",
        ),
    ),
    BuiltinSkillSpec(
        slug="mysql-reporter",
        source_dir=_SKILLS_ROOT / "mysql-reporter",
        description="基于 MySQL 数据库生成查询报表和可视化图表，适合分析业务指标、统计趋势，并用 Charts MCP 展示结果。",
        version="2026.06.05",
        mcp_dependencies=("mcp-server-chart",),
    ),
    # 记忆抽取是**无工具**的技能：它的 SKILL.md 既是给人和 Agent 看的机制说明，
    # 也是后台抽取调用（memory/extraction.build_system_prompt）真正读取的抽取口径。
    # 因此刻意不声明 tool_dependencies —— 它不需要任何 agent 工具，声明了反而会
    # 让 SkillsMiddleware 的门控去放出无关工具。
    BuiltinSkillSpec(
        slug="memory-extraction",
        source_dir=_SKILLS_ROOT / "memory-extraction",
        description="会话事实与长期记忆的抽取规范：从多轮问答中识别值得留存的用户画像与会话约束，按白名单事实键和通道归属落库。改动记忆行为、排查记忆为何没生效时查阅。",
        version="2026.09.17",
    ),
]
