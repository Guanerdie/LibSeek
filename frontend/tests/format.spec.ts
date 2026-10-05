import { describe, expect, it } from 'vitest'

import {
  candidateReasonLabel,
  candidateWarningLabel,
  formatCountryCodes,
  formatShanghai,
  parseApiTime,
} from '../src/utils/format'

describe('API timestamps', () => {
  it('reads a time without an offset as UTC, which is what the server stores', () => {
    expect(parseApiTime('2026-10-05T04:45:57.359910').toISOString()).toBe(
      '2026-10-05T04:45:57.359Z',
    )
    expect(formatShanghai('2026-10-05T04:45:57')).toBe('2026/10/05 12:45:57')
  })

  it('leaves a time that already carries an offset alone', () => {
    expect(formatShanghai('2026-10-05T04:45:57Z')).toBe('2026/10/05 12:45:57')
    expect(formatShanghai('2026-10-05T04:45:57+00:00')).toBe('2026/10/05 12:45:57')
    expect(formatShanghai('2026-10-05T12:45:57+08:00')).toBe('2026/10/05 12:45:57')
  })

  it('shows a dash when there is no time', () => {
    expect(formatShanghai(null)).toBe('—')
  })
})

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

describe('candidate matching labels', () => {
  it('translates matching reasons and warnings into Chinese', () => {
    expect(candidateReasonLabel('TMDB_ID_EXACT')).toBe('TMDB 编号精确匹配')
    expect(candidateReasonLabel('PROMOTION_ACTIVE')).toBe('免费或促销中')
    expect(candidateWarningLabel('NO_SEEDERS')).toBe('当前无做种')
    expect(candidateWarningLabel('PARTIAL_PACK')).toBe('未完整覆盖缺失内容')
  })

  it('keeps an unknown backend code visible for diagnosis', () => {
    expect(candidateReasonLabel('NEW_REASON')).toBe('未识别匹配项（NEW_REASON）')
    expect(candidateWarningLabel('NEW_WARNING')).toBe('未识别风险提示（NEW_WARNING）')
  })
})
