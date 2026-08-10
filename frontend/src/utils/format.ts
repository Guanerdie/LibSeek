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
    }[status] ?? status
  )
}

