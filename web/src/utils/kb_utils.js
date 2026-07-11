import { h } from 'vue'
import { Database, DatabaseZap, FileText } from 'lucide-vue-next'

const createLocalBrandIcon = (label, background, color = '#fff') => {
  const Icon = ({ size = 20 }) =>
    h(
      'span',
      {
        style: {
          width: `${size}px`,
          height: `${size}px`,
          borderRadius: '6px',
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          background,
          color,
          fontSize: `${Math.max(10, Math.round(size * 0.46))}px`,
          fontWeight: 700,
          lineHeight: 1
        }
      },
      label
    )
  Icon.inheritAttrs = false
  return Icon
}

export const brandIcons = {
  dify: createLocalBrandIcon('D', '#111827'),
  notion: createLocalBrandIcon('N', '#f3f4f6', '#111827')
}

export const getKbTypeLabel = (type) => {
  const labels = {
    milvus: '本地知识库',
    dify: 'Dify',
    notion: 'Notion'
  }
  return labels[type] || type
}

export const getKbTypeIcon = (type) => {
  const icons = {
    milvus: DatabaseZap,
    dify: brandIcons.dify,
    notion: brandIcons.notion,
    local: FileText
  }
  return icons[type] || Database
}

export const getKbTypeColor = (type) => {
  const colors = {
    milvus: 'blue',
    dify: 'gold',
    notion: 'purple'
  }
  return colors[type] || 'blue'
}

const READ_ONLY_KB_TYPES = new Set(['dify', 'notion'])

export const isReadOnlyDatabase = (database, kbTypes = {}) => {
  const kbType = (
    typeof database === 'string' ? database : database?.kb_type || 'milvus'
  ).toLowerCase()

  if (database?.supports_documents !== undefined) {
    return database.supports_documents === false
  }
  if (kbTypes[kbType]?.supports_documents !== undefined) {
    return kbTypes[kbType].supports_documents === false
  }
  return READ_ONLY_KB_TYPES.has(kbType)
}

export const kbUtils = {
  getKbTypeLabel,
  getKbTypeIcon,
  getKbTypeColor,
  isReadOnlyDatabase
}
