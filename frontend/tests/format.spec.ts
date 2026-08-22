import { describe, expect, it } from 'vitest'

import { formatCountryCodes } from '../src/utils/format'

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
