export function formatShanghai(value: string | null | undefined): string {
  if (!value) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(new Date(value))
}

const regionNames = new Intl.DisplayNames(['zh-CN'], { type: 'region' })

export function formatCountryCodes(value: string[] | null | undefined): string {
  if (!value?.length) return ''

  return value
    .map((code) => code.trim().toUpperCase())
    .filter(Boolean)
    .map((code) => {
      try {
        return regionNames.of(code) || code
      } catch {
        return code
      }
    })
    .join(' / ')
}

export function statusLabel(status: string): string {
  return (
    {
      PENDING: '等待中',
      RUNNING: '运行中',
      SUCCEEDED: '已完成',
      FAILED: '失败',
      RETRY_WAIT: '等待重试',
      CANCELLED: '已取消',
      METADATA_PENDING: '等待元数据',
      PT_SEARCH_PENDING: '等待 PT 搜索',
      PT_SEARCHING: 'PT 搜索中',
      TORRENT_REVIEW: '等待候选确认',
      NO_CANDIDATE: '无候选',
      SEARCH_FAILED: '搜索失败',
      APPROVED: '已批准',
      EXECUTING: '执行中',
      REJECTED: '已拒绝',
      EXPIRED: '已过期',
      REVOKED: '已撤销',
      CONSUMED: '已消费',
      ACTIVE: '有效',
      VALIDATING: '校验中',
      SUBMITTING: '提交中',
      SUBMITTED: '已提交',
      ALREADY_PRESENT: '已存在',
      OUTCOME_UNKNOWN: '结果未知',
      RECONCILIATION_REQUIRED: '需要对账',
      RECONCILIATION_PENDING: '等待对账',
      QUEUED: '已排队',
      DOWNLOADING: '下载中',
      PAUSED: '已暂停',
      CHECKING: '校验中',
      SEEDING: '做种中',
      COMPLETED: '已完成',
      MISSING: '任务缺失',
      ERROR: '任务异常',
      PASS: '通过',
      WARNING: '警告',
      BLOCKED: '已阻止',
      UNKNOWN: '未知',
      ENABLED: '已启用',
      DISABLED: '已关闭',
      MANUAL: '人工',
      AUTO_IF_ELIGIBLE: '条件自动',
      ACTION_CREATED: '已创建动作',
      MANUAL_REQUIRED: '需要人工',
      STALE: '上下文已过期',
      NOOP: '无需动作',
      PREFLIGHT_REQUIRED: '等待预检',
      REVIEW_REQUIRED: '等待审核',
      APPROVED_PLAN_ONLY: '已批准纯规划',
    }[status] ?? status
  )
}

const candidateReasonLabels: Record<string, string> = {
  TMDB_ID_EXACT: 'TMDB 编号精确匹配',
  IMDB_ID_EXACT: 'IMDb 编号精确匹配',
  TITLE_EXACT: '标题精确匹配',
  MEDIA_TYPE_MATCH: '影视类型匹配',
  SEASON_EXACT: '季数匹配',
  SEASON_PACK_COVERS_TARGET_SEASON: '整季资源覆盖缺失内容',
  EPISODE_COVERAGE_EXACT: '缺集范围精确匹配',
  EPISODE_COVERAGE_COMPLETE: '完整覆盖缺失集数',
  YEAR_MATCH: '年份匹配',
  PREFERRED_RESOLUTION: '符合偏好分辨率',
  PREFERRED_SOURCE: '符合偏好来源',
  PREFERRED_AUDIO: '符合偏好音轨',
  PREFERRED_SUBTITLE: '包含偏好字幕',
  ACTIVE_SEEDERS: '当前有做种',
  PROMOTION_ACTIVE: '免费或促销中',
  SIZE_WITHIN_LIMIT: '体积在限制内',
}

const candidateWarningLabels: Record<string, string> = {
  ID_MISMATCH: '影视身份不匹配',
  ID_UNVERIFIED: '无法验证站点提供的影视编号',
  MEDIA_TYPE_CONFLICT: '影视类型冲突',
  YEAR_CONFLICT: '年份不匹配',
  YEAR_MISMATCH: '年份不匹配',
  PARTIAL_PACK: '未完整覆盖缺失内容',
  EPISODE_OVERLAP: '包含已存在的集数',
  NO_SEEDERS: '当前无做种',
  OVERSIZED: '资源体积超出限制',
  HNR_UNKNOWN: '无法确认站点考核规则',
  POSSIBLE_DUPLICATE: '可能重复下载',
}

export function candidateReasonLabel(code: string): string {
  return candidateReasonLabels[code] ?? `未识别匹配项（${code}）`
}

export function candidateWarningLabel(code: string): string {
  return candidateWarningLabels[code] ?? `未识别风险提示（${code}）`
}
