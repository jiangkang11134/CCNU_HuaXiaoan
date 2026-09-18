import { apiAdminGet, apiAdminPost, apiAdminPut } from './base'

/**
 * Dashboard API模块
 * 用于管理员查看所有用户的对话记录
 */

export const dashboardApi = {
  /**
   * 获取所有对话记录
   * @param {Object} params - 查询参数
   * @param {string} params.uid - 用户 UID 过滤
   * @param {string} params.agent_id - 智能体ID过滤
   * @param {string} params.status - 状态过滤 (active/deleted/all)
   * @param {number} params.limit - 每页数量
   * @param {number} params.offset - 偏移量
   * @returns {Promise<Array>} - 对话列表
   */
  getConversations: (params = {}) => {
    const queryParams = new URLSearchParams()
    if (params.uid) queryParams.append('uid', params.uid)
    if (params.agent_id) queryParams.append('agent_id', params.agent_id)
    if (params.status) queryParams.append('status', params.status)
    if (params.limit) queryParams.append('limit', params.limit)
    if (params.offset) queryParams.append('offset', params.offset)

    return apiAdminGet(`/api/dashboard/conversations?${queryParams.toString()}`)
  },

  /**
   * 获取对话详情
   * @param {string} threadId - 对话线程ID
   * @returns {Promise<Object>} - 对话详情
   */
  getConversationDetail: (threadId) => {
    return apiAdminGet(`/api/dashboard/conversations/${threadId}`)
  },

  /**
   * 获取Dashboard统计信息
   * @returns {Promise<Object>} - 统计信息
   */
  getStats: () => {
    return apiAdminGet('/api/dashboard/stats')
  },

  /**
   * 获取用户反馈列表
   * @param {Object} params - 查询参数
   * @param {string} params.rating - 反馈类型过滤 (like/dislike/all)
   * @param {string} params.agent_id - 智能体ID过滤
   * @param {string} params.processing_status - 处置状态过滤
   * @returns {Promise<Array>} - 反馈列表
   */
  getFeedbacks: (params = {}) => {
    const queryParams = new URLSearchParams()
    if (params.rating && params.rating !== 'all') queryParams.append('rating', params.rating)
    if (params.agent_id) queryParams.append('agent_id', params.agent_id)
    if (params.processing_status) queryParams.append('processing_status', params.processing_status)

    return apiAdminGet(`/api/dashboard/feedbacks?${queryParams.toString()}`)
  },

  /**
   * 反馈处置状态词表（值 + 中文标签）
   *
   * 后端是这套词表的唯一口径，前端不要自己硬编码——后端加状态时前端会自动跟上。
   * @returns {Promise<Array<{value: string, label: string}>>}
   */
  getFeedbackStatuses: () => apiAdminGet('/api/dashboard/feedbacks/statuses'),

  /**
   * 处置一条反馈（改处置状态 / 优先级 / 备注），仅系统管理员
   * @param {number} feedbackId
   * @param {Object} payload - { processing_status?, priority?, processing_note? }
   * @returns {Promise<Object>} - 更新后的处置字段
   */
  updateFeedback: (feedbackId, payload = {}) =>
    apiAdminPut(`/api/dashboard/feedbacks/${feedbackId}`, payload),

  /**
   * 把一条反馈转成待审核纠错工单，仅系统管理员
   *
   * 这是"反馈收集层 → 工单处置层"的人工闸门；学生的点踩不会自动建单。
   * @param {number} feedbackId
   * @param {Object} payload - { scope?: 'kb_truth'|'user_pref'|'dept_rule', note?: string }
   * @returns {Promise<Object>} - { id, ticket_id, processing_status, ... }
   */
  convertFeedbackToTicket: (feedbackId, payload = {}) =>
    apiAdminPost(`/api/dashboard/feedbacks/${feedbackId}/ticket`, payload),

  // ========== 新增并行API接口 ==========

  /**
   * 获取用户活跃度统计
   * @returns {Promise<Object>} - 用户活跃度统计信息
   */
  getUserStats: () => {
    return apiAdminGet('/api/dashboard/stats/users')
  },

  /**
   * 获取工具调用统计
   * @returns {Promise<Object>} - 工具调用统计信息
   */
  getToolStats: () => {
    return apiAdminGet('/api/dashboard/stats/tools')
  },

  /**
   * 获取知识库统计
   * @returns {Promise<Object>} - 知识库统计信息
   */
  getKnowledgeStats: () => {
    return apiAdminGet('/api/dashboard/stats/knowledge')
  },

  /**
   * 获取AI智能体分析数据
   * @returns {Promise<Object>} - AI智能体分析信息
   */
  getAgentStats: () => {
    return apiAdminGet('/api/dashboard/stats/agents')
  },

  /**
   * 批量获取所有统计数据（并行请求）
   * @returns {Promise<Object>} - 所有统计数据
   */
  getAllStats: async () => {
    try {
      const [basicStats, userStats, toolStats, knowledgeStats, agentStats] = await Promise.all([
        apiAdminGet('/api/dashboard/stats'),
        apiAdminGet('/api/dashboard/stats/users'),
        apiAdminGet('/api/dashboard/stats/tools'),
        apiAdminGet('/api/dashboard/stats/knowledge'),
        apiAdminGet('/api/dashboard/stats/agents')
      ])

      return {
        basic: basicStats,
        users: userStats,
        tools: toolStats,
        knowledge: knowledgeStats,
        agents: agentStats
      }
    } catch (error) {
      console.error('批量获取统计数据失败:', error)
      throw error
    }
  },

  /**
   * 获取调用统计时间序列数据
   * @param {string} type - 数据类型 (models/agents/tokens/tools)
   * @param {string} timeRange - 时间范围 (14hours/14days/14weeks)
   * @returns {Promise<Object>} - 时间序列统计数据
   */
  getCallTimeseries: (type = 'models', timeRange = '14days') => {
    return apiAdminGet(`/api/dashboard/stats/calls/timeseries?type=${type}&time_range=${timeRange}`)
  }
}
