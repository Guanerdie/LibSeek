import type { ActivityCategory, ActivityEntry } from '../types'
import { formatShanghai } from './format'

const eventLabels: Record<string, string> = {
  DOWNLOAD_QUEUED: '选择资源',
  DOWNLOAD_RETRYING: '重新提交下载',
  DOWNLOAD_SUBMITTED: '提交下载',
  DOWNLOAD_FAILED: '下载提交失败',
  DOWNLOAD_CLEANUP_MARKED: '标记待清理',
  DOWNLOAD_CLEANUP_DELETED: '删除种子和文件',
  DOWNLOAD_CLEANUP_HELD: '设为保留',
  DOWNLOAD_CLEANUP_RELEASED: '允许自动清理',
  DOWNLOAD_CLEANUP_UNMARKED: '取消清理标记',
  DOWNLOAD_CLEANUP_SKIPPED: '本轮清理已跳过',
  DOWNLOAD_CLEANUP_PREVIEW: '清理演练',
  LIBRARY_CONFIRMED: '确认入库',
  LIBRARY_CONFIRMATION_REVOKED: '撤销入库确认',
  NEXTFIND_SYNCED: '同步缺失列表',
  AUTOMATION_SYNC_FAILED: '缺失列表同步失败',
  TMDB_IDENTIFIED: '确认影视信息',
  SEARCH_CREATED: '开始搜索',
  SEARCH_COMPLETED: '搜索完成',
  AUTOMATION_POLICY_UPDATED: '修改自动化策略',
}

const categoryLabels: Record<ActivityCategory, string> = {
  download: '下载',
  cleanup: '删种',
  library: '入库',
  search: '搜索',
  settings: '设置',
}

const systemActors: Record<string, string> = {
  system: '系统',
  'system:scheduler': '定时调度',
  'system:cleanup': '空间清理',
}

export function eventLabel(event: string): string {
  return eventLabels[event] ?? event
}

export function categoryLabel(category: ActivityCategory): string {
  return categoryLabels[category]
}

export function actorLabel(actor: string | null): string {
  if (actor === null) return '未记录'
  return systemActors[actor] ?? actor
}

export function triggerLabel(trigger: ActivityEntry['trigger']): string {
  if (trigger === 'MANUAL') return '手动'
  if (trigger === 'AUTO') return '自动'
  return '未记录'
}

/** What to show under "why".  A person's own action needs no justification. */
export function reasonText(entry: ActivityEntry): string {
  if (entry.reason) return entry.reason
  if (entry.trigger === 'MANUAL') return '用户手动操作'
  if (entry.trigger === 'AUTO') return '系统按既定流程执行'
  return '这条记录产生时还没有记录原因'
}

export function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let size = value / 1024
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return `${size.toFixed(size >= 100 ? 0 : 1)} ${units[unit]}`
}

const detailLabels: Record<string, string> = {
  name: '种子名称',
  site_id: '站点',
  size_bytes: '体积',
  score: '综合评分',
  seeding_days: '已做种',
  required_seeding_days: '要求做种',
  completed_at: '下载完成时间',
  library_confirmed_at: '入库确认时间',
  marked_at: '标记时间',
  grace_days: '观察期',
  files_deleted: '文件',
  info_hash: 'Info hash',
  category: 'qB 分类',
  error_code: '错误代码',
  candidate_count: '候选数量',
  created: '新增影视',
  updated: '更新影视',
  warnings: '列表读取警告',
  newly_confirmed: '新确认入库',
  confirmation_revoked: '撤销入库确认',
  id_collisions: '电影与剧集同号',
  rejected_confirmations: '未采信的入库数量',
  confirmation_limit: '单次确认上限',
  markable: '可标记',
  deletable: '可删除',
  would_reclaim_bytes: '预计释放',
  run_trigger: '运行方式',
  run_id: '自动化运行',
  job_id: '自动化任务',
  download_id: '下载记录',
  candidate_id: '候选资源',
}

const policyFieldLabels: Record<string, string> = {
  enabled: '启用自动化',
  dry_run: '仅试运行',
  cleanup_enabled: '启用空间清理',
  cleanup_dry_run: '清理演练模式',
  cleanup_after_days: '保留天数',
  cleanup_min_seeding_days: '最短做种天数',
  cleanup_grace_days: '标记后观察天数',
  cleanup_require_library_confirmed: '必须已确认入库',
  cleanup_daily_limit: '每日最多清理',
  daily_download_limit: '每日自动下载数量',
  minimum_score: '最低评分',
  scope_mode: '自动化范围',
  regions: '地区',
  years: '年份',
  media_types: '影视类型',
}

function plain(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (Array.isArray(value)) return value.length ? value.join('、') : '（空）'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function detailValue(key: string, value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'number') {
    if (key.endsWith('_bytes')) return formatBytes(value)
    if (key === 'seeding_days' || key === 'required_seeding_days' || key === 'grace_days') {
      return `${value} 天`
    }
    if (key === 'score') return value.toFixed(2)
  }
  if (typeof value === 'string') {
    if (key.endsWith('_at')) return formatShanghai(value)
    if (key === 'run_trigger') return value === 'scheduled' ? '定时运行' : '手动启动'
  }
  if (key === 'files_deleted') return value ? '已连同文件删除' : '未删除文件'
  return plain(value)
}

export interface DetailRow {
  key: string
  label: string
  value: string
}

/** The details object as labelled rows, most useful first. */
export function detailRows(details: Record<string, unknown>): DetailRow[] {
  const rows: DetailRow[] = []
  const changes = details.changes
  if (changes && typeof changes === 'object') {
    for (const [field, change] of Object.entries(changes as Record<string, unknown>)) {
      const { from, to } = (change ?? {}) as { from?: unknown; to?: unknown }
      rows.push({
        key: `changes.${field}`,
        label: policyFieldLabels[field] ?? field,
        value: `${plain(from)} → ${plain(to)}`,
      })
    }
  }
  const known = Object.keys(detailLabels).filter((key) => key in details)
  const unknown = Object.keys(details).filter(
    (key) => key !== 'changes' && !(key in detailLabels),
  )
  for (const key of [...known, ...unknown]) {
    rows.push({
      key,
      label: detailLabels[key] ?? key,
      value: detailValue(key, details[key]),
    })
  }
  return rows
}
