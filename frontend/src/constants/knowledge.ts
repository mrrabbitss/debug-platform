export const knowledgeDeviceTypeOptions = [
  { label: 'GW', value: 'GW' },
  { label: 'AP', value: 'AP' },
  { label: '通用', value: 'GENERAL' },
  { label: '其他', value: 'OTHER' }
] as const

const knowledgeDeviceTypeLabels = new Map<string, string>(
  knowledgeDeviceTypeOptions.map(item => [item.value, item.label])
)

export function knowledgeDeviceTypeLabel(value?: string): string {
  if (!value) return '—'
  return knowledgeDeviceTypeLabels.get(value) || value
}
