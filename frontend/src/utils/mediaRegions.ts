export type MediaRegion =
  | 'western'
  | 'mainland'
  | 'hong-kong-taiwan'
  | 'korea'
  | 'japan'
  | 'asia-pacific'

export interface MediaRegionOption {
  value: MediaRegion
  label: string
  countryCodes: readonly string[]
}

export const NEXTFIND_MEDIA_REGIONS: readonly MediaRegionOption[] = [
  {
    value: 'western',
    label: '欧美',
    countryCodes: ['US', 'GB', 'FR', 'DE', 'IT', 'ES', 'CA', 'AU', 'RU', 'BR', 'MX', 'AR'],
  },
  { value: 'mainland', label: '大陆', countryCodes: ['CN'] },
  { value: 'hong-kong-taiwan', label: '港台', countryCodes: ['HK', 'TW', 'MO'] },
  { value: 'korea', label: '韩国', countryCodes: ['KR'] },
  { value: 'japan', label: '日本', countryCodes: ['JP'] },
  {
    value: 'asia-pacific',
    label: '亚太',
    countryCodes: ['IN', 'TH', 'SG', 'MY', 'PH', 'VN', 'ID', 'MM'],
  },
]

export function formatMediaRegions(countryCodes: string[] | null | undefined): string {
  if (!countryCodes?.length) return ''
  const normalized = new Set(countryCodes.map((code) => code.trim().toUpperCase()))
  const labels = NEXTFIND_MEDIA_REGIONS
    .filter((region) => region.countryCodes.some((code) => normalized.has(code)))
    .map((region) => region.label)
  return labels.length ? labels.join(' / ') : '其他'
}
