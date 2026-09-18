"""可选 DeepSeek/GLM 轻量意图分类；失败时返回 None，由规则/主链路降级。"""
import json, os
import httpx

async def classify_with_api(text: str, routing: dict | None = None) -> dict | None:
    routing = routing or {}
    spec = routing.get("intent_model_spec")
    info = None
    if spec:
        try:
            from yuxi.models.providers.cache import model_cache
            info = model_cache.get_model_info(spec)
        except Exception:
            info = None
    provider = (os.getenv("YUXI_INTENT_PROVIDER") or "").lower()
    endpoint = None
    if info:
        enabled = [a for a in info.accounts if a.get("enabled", True) and a.get("api_key")]
        if enabled:
            endpoint = max(enabled, key=lambda a: int(a.get("weight", 1)))
    key = (endpoint or {}).get("api_key") or (info.api_key if info else None) or (os.getenv("DEEPSEEK_API_KEY") if provider == "deepseek" else os.getenv("GLM_API_KEY") if provider in {"glm", "zhipuai"} else None)
    if not key: return None
    base = (endpoint or {}).get("base_url") or (info.base_url if info else None) or (os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com") if provider == "deepseek" else os.getenv("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"))
    model = (info.model_id if info else None) or os.getenv("YUXI_INTENT_MODEL", "deepseek-chat" if provider == "deepseek" else "glm-4-flash")
    prompt = 'Classify into graph_rag_query, memory_candidate, correction, simple_task. Return JSON only: {"intent":"...","confidence":0.0}'
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.5, connect=0.8)) as client:
            response = await client.post(f"{base.rstrip('/')}/chat/completions", headers={"Authorization": f"Bearer {key}"}, json={"model": model, "temperature": 0, "max_tokens": 80, "messages": [{"role":"system","content":prompt},{"role":"user","content":text[:2000]}]})
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            data = json.loads(content[content.find("{"):content.rfind("}")+1])
            if data.get("intent") not in {"graph_rag_query","memory_candidate","correction","simple_task"}: return None
            data["confidence"] = max(0.0, min(float(data.get("confidence", 0.0)), 1.0))
            return data
    except Exception:
        return None
