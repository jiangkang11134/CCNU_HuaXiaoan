import { apiAdminDelete, apiAdminGet, apiAdminPost, apiDelete, apiGet, apiPost } from './base'

const buildQuery = (params = {}) => {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') query.set(key, value)
  })
  return query.toString()
}

/**
 * 普通用户「我的记忆」通道（user_memory_facts 的本人视角）。
 *
 * 与 memoryApi（管理员审计）访问同一批后端接口，但必须走非管理员的 apiGet/apiPost/apiDelete：
 * apiAdmin* 内部会先调 checkAdminPermission()，普通用户请求还没发出去就被拦下，
 * 结果就是「所有普通用户都看不到自己的记忆」——这正是本通道存在的理由。
 *
 * 与管理员通道的差别不只是权限：GET /memory 只返回可用状态
 * （candidate / pending_confirmation / confirmed，且未过期），
 * 所以「已撤回 / 已删除」的历史要从 events() 读，不在列表里。
 */
export const myMemoryApi = {
  // 只需传 limit（后端上限 100；无 status 过滤，前端按状态分组）
  list(params = {}) {
    const query = buildQuery(params)
    return apiGet(`/api/memory${query ? `?${query}` : ''}`)
  },
  // candidate / pending_confirmation -> confirmed。
  // 幂等性由后端状态机把关：已 confirmed 再调会 409（MEMORY_TRANSITIONS 无 confirmed->confirmed），
  // 前端据此只在「待确认」状态展示确认按钮。
  confirm(memoryId) {
    return apiPost(`/api/memory/${memoryId}/confirm`, {})
  },
  // 撤回：仅 confirmed 可撤回（状态机不允许 candidate -> retracted），
  // 撤回后保留变更记录、只是不再注入对话。
  retract(memoryId) {
    return apiPost(`/api/memory/${memoryId}/retract`, {})
  },
  // 软删除：confirmed -> tombstoned，其余 -> rejected。任何可用状态都可调用。
  remove(memoryId) {
    return apiDelete(`/api/memory/${memoryId}`)
  },
  // 变更记录（谁把这条记忆改成了什么状态），可用于回答"系统为什么记住了这个"
  // params: { target_type: 'user_memory', target_id, limit }
  events(params = {}) {
    const query = buildQuery(params)
    return apiGet(`/api/memory/events${query ? `?${query}` : ''}`)
  }
}

export const memoryApi = {
  listAll(params = {}) {
    const query = buildQuery(params)
    return apiAdminGet(`/api/memory/admin/all${query ? `?${query}` : ''}`)
  },
  remove(uid, memoryId) {
    return apiAdminDelete(`/api/memory/admin/${encodeURIComponent(uid)}/${memoryId}`)
  }
}
