import type { AutomationJob, AutomationJobOutcome, AutomationRun } from '../types'
import { statusLabel } from './format'

export const automationOutcomeLabels: Record<AutomationJobOutcome, string> = {
  downloaded: '新增下载',
  existing_download: '已有下载跳过',
  awaiting_library: '待入库核查',
  no_match: '未匹配',
  dry_run: '演练',
  skipped: '其他跳过',
  unknown: '结果待核实',
  failed: '失败',
}

export function automationOutcomeEntries(run: Pick<AutomationRun, 'succeeded' | 'outcomes'>): Array<{ key: string; label: string; count: number }> {
  // An older API only knows that a job finished, not whether it added a download.
  if (!run.outcomes) return [{ key: 'unknown', label: '结果待核实', count: run.succeeded }]
  return Object.entries(run.outcomes)
    .filter(([key, count]) => key === 'downloaded' || count > 0)
    .map(([key, count]) => ({ key, label: automationOutcomeLabels[key as AutomationJobOutcome], count }))
}

export function automationJobLabel(job: AutomationJob): string {
  if (job.superseded_at) return statusLabel('SUPERSEDED')
  if (job.state !== 'SUCCEEDED') return statusLabel(job.state)
  return automationOutcomeLabels[job.outcome ?? job.decision.outcome ?? 'unknown']
}

export function automationRunResultText(run: AutomationRun): string {
  const results = automationOutcomeEntries(run).map(({ label, count }) => `${label} ${count} 项`)
  results.push(`失败 ${run.failed} 项`)
  if (run.deferred) results.push(`等待后续执行 ${run.deferred} 项`)
  return results.join('，')
}
