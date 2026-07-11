export const PORTAL_MODES = {
  FRONT: 'front',
  BACK: 'back'
}

export function getPortalMode(pathname = window.location.pathname) {
  if (pathname === '/back' || pathname.startsWith('/back/')) return PORTAL_MODES.BACK
  return PORTAL_MODES.FRONT
}

export function getPortalBasePath(mode = getPortalMode()) {
  return mode === PORTAL_MODES.BACK ? '/back/' : '/front/'
}

export function getPortalLoginPath(mode = getPortalMode()) {
  return mode === PORTAL_MODES.BACK ? '/back/login' : '/front/login'
}

export function getLandingPathForRole(role) {
  return role === 'system_admin' ? '/back/dashboard' : '/front/agent'
}

export function isAdminRole(role) {
  return role === 'system_admin'
}
