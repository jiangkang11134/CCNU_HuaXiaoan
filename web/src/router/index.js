import { createRouter, createWebHistory } from 'vue-router'
import AppLayout from '@/layouts/AppLayout.vue'
import { useUserStore } from '@/stores/user'
import { useAgentStore } from '@/stores/agent'
import { sanitizeRedirect } from '@/utils/oidcAutoStart'
import { getLandingPathForRole, getPortalLoginPath, getPortalMode, isAdminRole } from '@/utils/portal'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      redirect: '/front/agent'
    },
    {
      path: '/front',
      redirect: '/front/agent'
    },
    {
      path: '/back',
      redirect: '/back/dashboard'
    },
    {
      path: '/front/login',
      name: 'frontLogin',
      component: () => import('../views/LoginView.vue'),
      meta: { requiresAuth: false, portalMode: 'front' }
    },
    {
      path: '/back/login',
      name: 'backLogin',
      component: () => import('../views/LoginView.vue'),
      meta: { requiresAuth: false, portalMode: 'back' }
    },
    {
      path: '/home',
      name: 'Home',
      component: () => import('../views/HomeView.vue'),
      meta: { keepAlive: true, requiresAuth: false }
    },
    {
      path: '/login',
      redirect: () => getPortalLoginPath(getPortalMode())
    },
    {
      path: '/auth/oidc/callback', // oidc登录回调页面
      name: 'OIDCCallback',
      component: () => import('@/views/OIDCCallbackView.vue'),
      meta: { public: true }
    },
    {
      path: '/auth/cli/authorize',
      name: 'CLIAuthAuthorize',
      component: () => import('@/views/CLIAuthAuthorizeView.vue'),
      meta: { requiresAuth: true }
    },
    {
      path: '/agent',
      redirect: '/front/agent'
    },
    {
      path: '/front/agent',
      name: 'AgentMain',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'AgentComp',
          component: () => import('../views/AgentView.vue'),
          meta: { keepAlive: true, requiresAuth: true }
        },
        {
          path: ':thread_id',
          name: 'AgentCompWithThreadId',
          component: () => import('../views/AgentView.vue'),
          meta: { keepAlive: true, requiresAuth: true }
        }
      ]
    },
    {
      path: '/front/lab-safety',
      name: 'LabSafetyRecognitionMain',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'LabSafetyRecognition',
          component: () => import('../views/LabSafetyRecognitionView.vue'),
          meta: { keepAlive: false, requiresAuth: true }
        }
      ]
    },
    {
      path: '/dashboard',
      redirect: '/back/dashboard'
    },
    {
      path: '/conversations',
      redirect: '/back/conversations'
    },
    {
      path: '/back/conversations',
      name: 'conversations',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'ConversationDataComp',
          component: () => import('../views/ConversationDataView.vue'),
          meta: { keepAlive: false, requiresAuth: true, requiresAdmin: true }
        }
      ]
    },
    {
      path: '/back/dashboard',
      name: 'dashboard',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'DashboardComp',
          component: () => import('../views/DashboardView.vue'),
          meta: { keepAlive: false, requiresAuth: true, requiresAdmin: true }
        }
      ]
    },
    {
      path: '/back/debug',
      name: 'debug',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'RuntimeLogsComp',
          component: () => import('../views/DebugView.vue'),
          meta: { keepAlive: false, requiresAuth: true, requiresAdmin: true }
        }
      ]
    },
    {
      path: '/back/basic-settings',
      name: 'basic-settings',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'BasicSettingsComp',
          component: () => import('../views/BasicSettingsView.vue'),
          meta: { keepAlive: false, requiresAuth: true, requiresAdmin: true }
        }
      ]
    },
    {
      path: '/back/frontend-config',
      name: 'frontend-config',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'FrontendConfigComp',
          component: () => import('../views/FrontendConfigView.vue'),
          meta: { keepAlive: false, requiresAuth: true, requiresAdmin: true }
        }
      ]
    },
    {
      path: '/back/users',
      name: 'user-management',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'UserManagementComp',
          component: () => import('../views/UserManagementView.vue'),
          meta: { keepAlive: false, requiresAuth: true, requiresAdmin: true }
        }
      ]
    },
    {
      path: '/back/departments',
      name: 'department-management',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'DepartmentManagementComp',
          component: () => import('../views/DepartmentManagementView.vue'),
          meta: { keepAlive: false, requiresAuth: true, requiresAdmin: true }
        }
      ]
    },
    {
      path: '/model-manage',
      redirect: '/back/model-manage'
    },
    {
      path: '/back/model-manage',
      name: 'model-manage',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'ModelManageComp',
          component: () => import('../views/ModelManageView.vue'),
          meta: { keepAlive: false, requiresAuth: true }
        }
      ]
    },
    {
      path: '/extensions',
      redirect: '/back/extensions'
    },
    {
      path: '/back/extensions',
      name: 'extensions',
      component: AppLayout,
      children: [
        {
          path: '',
          name: 'ExtensionsComp',
          component: () => import('../views/ExtensionsView.vue'),
          meta: {
            keepAlive: false,
            requiresAuth: true
          },
          children: [
            {
              path: 'knowledgebase/:kbId',
              name: 'ExtensionKnowledgeBaseDetail',
              component: () => import('../views/DataBaseInfoView.vue'),
              meta: {
                keepAlive: false,
                requiresAuth: true,
                requiresAdmin: true
              }
            },
            {
              path: 'mcp/:slug',
              name: 'ExtensionMcpDetail',
              component: () => import('../components/extensions/McpDetailView.vue'),
              meta: {
                keepAlive: false,
                requiresAuth: true,
                requiresAdmin: true
              }
            },
            {
              path: 'skill/:slug',
              name: 'ExtensionSkillDetail',
              component: () => import('../components/extensions/SkillDetailView.vue'),
              meta: {
                keepAlive: false,
                requiresAuth: true
              }
            }
          ]
        }
      ]
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'NotFound',
      component: () => import('../views/EmptyView.vue'),
      meta: { requiresAuth: false }
    }
  ]
})

// 全局前置守卫
router.beforeEach(async (to) => {
  // 检查路由是否需要认证
  const requiresAuth = to.matched.some((record) => record.meta.requiresAuth === true)
  const requiresAdmin = to.matched.some((record) => record.meta.requiresAdmin)
  const targetPortal = to.path.startsWith('/back') ? 'back' : 'front'

  const userStore = useUserStore()

  // 如果有 token 但用户信息未加载，先获取用户信息
  if (userStore.token && !userStore.userId) {
    try {
      await userStore.getCurrentUser()
    } catch (error) {
      // 如果获取用户信息失败（如 token 过期），清除 token
      console.error('获取用户信息失败:', error)
      userStore.logout()
    }
  }

  const isLoggedIn = userStore.isLoggedIn
  const isAdmin = userStore.isAdmin

  // 如果路由需要认证但用户未登录
  if (requiresAuth && !isLoggedIn) {
    // 保存尝试访问的路径，登录后跳转
    sessionStorage.setItem('redirect', to.fullPath)
    return getPortalLoginPath(targetPortal)
  }

  if (isLoggedIn && targetPortal === 'front' && isAdminRole(userStore.userRole)) {
    return '/back/dashboard'
  }

  if (isLoggedIn && targetPortal === 'back' && !isAdminRole(userStore.userRole)) {
    return '/front/agent'
  }

  if (isLoggedIn && to.path.startsWith('/front/lab-safety') && !isAdminRole(userStore.userRole)) {
    const agentStore = useAgentStore()
    if (!agentStore.isInitialized) {
      await agentStore.initialize()
    }
    if (agentStore.frontendChatConfig?.show_lab_safety_recognition === false) {
      return '/front/agent'
    }
  }

  // 如果路由需要管理员权限但用户不是管理员
  if (requiresAdmin && !isAdmin) {
    // 如果是普通用户，跳转到聊天页空态
    try {
      const agentStore = useAgentStore()
      // 等待 store 初始化完成
      if (!agentStore.isInitialized) {
        await agentStore.initialize()
      }
      return '/front/agent'
    } catch (error) {
      console.error('获取智能体信息失败:', error)
      return '/front/agent'
    }
  }

  // 如果用户已登录但访问登录页，按 redirect 参数跳转
  if (to.path === '/login' && isLoggedIn) {
    return sanitizeRedirect(to.query.redirect)
  }

  if ((to.path === '/front/login' || to.path === '/back/login') && isLoggedIn) {
    return getLandingPathForRole(userStore.userRole)
  }

  // 其他情况正常导航
  return true
})

export default router
