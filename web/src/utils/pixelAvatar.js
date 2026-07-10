export const AVATAR_BACKGROUND_TOKENS = [
  {
    background: 'linear-gradient(135deg, var(--main-600), var(--color-info-500))',
    primaryColor: '#047992',
    secondaryColor: '#3b82f6',
    color: '#fff'
  },
  {
    background: 'linear-gradient(135deg, var(--chart-palette-5), var(--chart-palette-9))',
    primaryColor: '#2563eb',
    secondaryColor: '#0891b2',
    color: '#fff'
  },
  {
    background: 'linear-gradient(135deg, var(--chart-palette-7), var(--chart-palette-3))',
    primaryColor: '#16a34a',
    secondaryColor: '#f59e0b',
    color: '#fff'
  },
  {
    background: 'linear-gradient(135deg, var(--color-accent-500), var(--chart-palette-6))',
    primaryColor: '#dc2626',
    secondaryColor: '#7c3aed',
    color: '#fff'
  },
  {
    background: 'linear-gradient(135deg, var(--chart-palette-4), var(--color-error-500))',
    primaryColor: '#0f766e',
    secondaryColor: '#e11d48',
    color: '#fff'
  }
]

const normalizeSeed = (id) => {
  if (id === null || id === undefined || String(id).trim() === '') {
    throw new Error('generatePixelAvatar requires an id')
  }
  return String(id).trim()
}

const hashSeed = (seed) => {
  let hash = 0
  for (const char of seed) {
    hash = (hash * 31 + char.codePointAt(0)) >>> 0
  }
  return hash
}

export const generatePixelAvatar = (id) => {
  const seed = normalizeSeed(id)
  const style = getAvatarFallbackStyle(seed)
  const initials = getAvatarInitials(seed)
  const rotation = (hashSeed(seed) % 24) - 12
  const svg = `
<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96">
  <defs>
    <linearGradient id="avatar-bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="${style.primaryColor}"/>
      <stop offset="1" stop-color="${style.secondaryColor}"/>
    </linearGradient>
  </defs>
  <rect width="96" height="96" rx="24" fill="url(#avatar-bg)"/>
  <circle cx="72" cy="22" r="18" fill="rgba(255,255,255,0.18)"/>
  <circle cx="20" cy="76" r="24" fill="rgba(255,255,255,0.12)"/>
  <text x="48" y="56" text-anchor="middle" font-family="Arial, sans-serif" font-size="26" font-weight="700" fill="${style.color}" transform="rotate(${rotation} 48 48)">${initials}</text>
</svg>`.trim()
  return `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`
}

export const getAvatarInitials = (name, kind = 'user') => {
  const fallback = kind === 'agent' ? '智能' : '用户'
  const normalizedName = String(name || '').trim()
  if (!normalizedName) return fallback
  return Array.from(normalizedName).slice(0, 2).join('')
}

export const getAvatarColorIndex = (seed) => {
  const normalizedSeed = String(seed || '').trim()
  const value = normalizedSeed || 'avatar'
  return hashSeed(value) % AVATAR_BACKGROUND_TOKENS.length
}

export const getAvatarFallbackStyle = (seed) => AVATAR_BACKGROUND_TOKENS[getAvatarColorIndex(seed)]
