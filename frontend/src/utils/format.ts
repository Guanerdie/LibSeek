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
    }[status] ?? status
  )
}
