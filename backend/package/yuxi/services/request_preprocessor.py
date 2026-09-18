"""Graph RAG 问答助手请求预处理：规则优先、无外部依赖、失败可降级。"""
import ast
import operator
from dataclasses import dataclass

@dataclass(frozen=True)
class PreprocessResult:
    intent: str
    confidence: float
    fact_key: str | None = None
    fact_content: str | None = None
    direct_answer: str | None = None

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}

def _math_value(text: str):
    candidate = text.strip().replace("等于几", "").replace("等于多少", "").replace("？", "").replace("?", "")
    if len(candidate) > 64 or not candidate or any(c not in "0123456789+-*/(). " for c in candidate):
        return None
    try:
        node = ast.parse(candidate, mode="eval").body
        def ev(n):
            if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)): return n.value
            if isinstance(n, ast.BinOp) and type(n.op) in _OPS: return _OPS[type(n.op)](ev(n.left), ev(n.right))
            if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)): return ev(n.operand) * (1 if isinstance(n.op, ast.UAdd) else -1)
            raise ValueError
        value = ev(node)
        return value if abs(value) < 10**12 else None
    except Exception:
        return None

def preprocess(text: str) -> PreprocessResult:
    raw = (text or "").strip()
    value = _math_value(raw)
    if value is not None:
        return PreprocessResult("simple_task", 1.0, direct_answer=str(value))
    markers = (("我的专业是", "user.major"), ("我的研究方向是", "user.research_direction"), ("研究方向是", "user.research_direction"), ("回答要", "user.response_preference"))
    for marker, key in markers:
        if marker in raw:
            content = raw.split(marker, 1)[1].strip(" ：:，,。")
            if content and len(content) <= 200:
                return PreprocessResult("memory_candidate", 0.95, key, content)
    if any(k in raw for k in ("记住", "保存到记忆", "别忘了")):
        return PreprocessResult("memory_candidate", 0.8)
    if any(k in raw for k in ("回答错", "纠正", "不准确", "没有依据")):
        return PreprocessResult("correction", 0.75)
    return PreprocessResult("graph_rag_query", 0.5)
