import { apiAdminDelete, apiAdminGet, apiAdminPost } from './base'

const buildQuery = (params = {}) => {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') query.set(key, value)
  })
  return query.toString()
}

/**
 * 自进化（问题反馈 -> 纠错工单 -> 图谱写回）管理端接口。
 *
 * 与 memoryApi（会话事实 / 长期记忆）**完全独立**：那边是「我的记忆」，
 * 这边是纠错工单的审核、复核与图谱写回，二者的作用域与审核流都不同。
 *
 * 2026-09-17 从 memory_api.js 拆出，后端路径同步由 `/api/memory/corrections*`
 * 迁到 `/api/self-evolution/corrections*`。
 * 注意：所有写操作均需 system_admin 角色（后端 get_admin_user）。
 */
export const selfEvolutionApi = {
  // params: { status: 'pending,ready_for_review' | ..., needs_review: true|false, limit, offset }
  list(params = {}) {
    const query = buildQuery(params)
    return apiAdminGet(`/api/self-evolution/corrections${query ? `?${query}` : ''}`)
  },
  // payload: { approved: bool, note, override_content, confidence, expires_at, scope }
  review(id, payload) {
    return apiAdminPost(`/api/self-evolution/corrections/${id}/review`, payload)
  },
  // payload: { note }
  ackReview(id, payload = {}) {
    return apiAdminPost(`/api/self-evolution/corrections/${id}/review-ack`, payload)
  },
  // P4：把已审核通过的修正改写成图块写入 Graph RAG（幂等，失败可重试）
  // forceRewrite=true 丢弃缓存的改写结果重新改写，用于「改写校验未通过」后的重试
  writeGraph(id, forceRewrite = false) {
    return apiAdminPost(`/api/self-evolution/corrections/${id}/graph?force_rewrite=${forceRewrite}`, {})
  },
  // P4：从 Graph RAG 撤回（删除图块与向量，级联清理只属于它的实体/三元组）；工单保留
  withdrawGraph(id) {
    return apiAdminDelete(`/api/self-evolution/corrections/${id}/graph`)
  }
}
