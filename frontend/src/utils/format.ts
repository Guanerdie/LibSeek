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
      REJECTED: '已拒绝',
      EXPIRED: '已过期',
      REVOKED: '已撤销',
      CONSUMED: '已消费',
      PASS: '通过',
      WARNING: '警告',
      BLOCKED: '已阻止',
      UNKNOWN: '未知',
    }[status] ?? status
  )
}
