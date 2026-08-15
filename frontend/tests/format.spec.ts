import { describe, expect, it } from 'vitest'

import { formatCountryCodes } from '../src/utils/format'
import { formatMediaRegions } from '../src/utils/mediaRegions'

describe('formatCountryCodes', () => {
  it('formats ISO country codes as Chinese region names', () => {
    expect(formatCountryCodes(['jp', 'US'])).toBe('日本 / 美国')
  })

  it('leaves empty values to the caller fallback', () => {
    expect(formatCountryCodes(null)).toBe('')
    expect(formatCountryCodes([])).toBe('')
  })

  it('falls back to the normalized code when Intl cannot format it', () => {
    expect(formatCountryCodes(['not-a-region'])).toBe('NOT-A-REGION')
  })
})

describe('formatMediaRegions', () => {
  it('uses the NextFind country groups with multi-region membership', () => {
    expect(formatMediaRegions(['JP', 'US'])).toBe('欧美 / 日本')
    expect(formatMediaRegions(['CN'])).toBe('大陆')
    expect(formatMediaRegions(['HK', 'TW'])).toBe('港台')
    expect(formatMediaRegions(['KR'])).toBe('韩国')
    expect(formatMediaRegions(['PH', 'VN', 'ID', 'MM'])).toBe('亚太')
    expect(formatMediaRegions(['NZ'])).toBe('其他')
  })

  it('leaves unknown countries to the caller fallback', () => {
    expect(formatMediaRegions(null)).toBe('')
  })
})
