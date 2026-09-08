<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import StatusPill from '../components/StatusPill.vue'
import { ApiError, statsApi } from '../api/client'
import type { AutomationStats } from '../types'
import { statusLabel } from '../utils/format'

const stats = ref<AutomationStats | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
const hoveredIndex = ref<number | null>(null)

// Plot geometry.  Fixed viewBox with a responsive width: the SVG scales, the
// stroke widths do not, so a 2px line stays 2px on any screen.
const PLOT = { width: 640, height: 200, left: 44, right: 16, top: 16, bottom: 28 }

const trend = computed(() => stats.value?.search_trend ?? [])

/** Points that actually have a rate; days with no searches leave a gap. */
const plotted = computed(() =>
  trend.value
    .map((point, index) => ({ point, index }))
    .filter((entry) => entry.point.success_rate !== null),
)

function xFor(index: number): number {
  const span = Math.max(1, trend.value.length - 1)
  return PLOT.left + ((PLOT.width - PLOT.left - PLOT.right) * index) / span
}

function yFor(rate: number): number {
  return PLOT.top + (PLOT.height - PLOT.top - PLOT.bottom) * (1 - rate)
}

/** One path per run of consecutive days.
 *
 * Drawing a single path through the gaps would put a line segment over a day
 * that had no searches at all, which reads as a measured value.  Breaking the
 * line leaves the gap visible.
 */
const lineSegments = computed(() => {
  const segments: string[] = []
  let current: string[] = []
  for (const day of trend.value) {
    const index = trend.value.indexOf(day)
    if (day.success_rate === null) {
      if (current.length > 1) segments.push(current.join(' '))
      current = []
      continue
    }
    const command = current.length === 0 ? 'M' : 'L'
    current.push(`${command}${xFor(index).toFixed(1)} ${yFor(day.success_rate).toFixed(1)}`)
  }
  if (current.length > 1) segments.push(current.join(' '))
  return segments
})

const lastPlotted = computed(() => plotted.value.at(-1) ?? null)

const hovered = computed(() =>
  hoveredIndex.value === null ? null : (trend.value[hoveredIndex.value] ?? null),
)

const weekTotals = computed(() => {
  const total = trend.value.reduce((sum, day) => sum + day.total, 0)
  const succeeded = trend.value.reduce((sum, day) => sum + day.succeeded, 0)
  return { total, succeeded, rate: total ? succeeded / total : null }
})

const slowestSiteSeconds = computed(() =>
  Math.max(1, ...(stats.value?.site_latency ?? []).map((site) => site.average_seconds)),
)

function percent(value: number | null): string {
  return value === null ? '—' : `${Math.round(value * 100)}%`
}

function shortDate(iso: string): string {
  const [, month, day] = iso.split('-')
  return `${Number(month)}/${Number(day)}`
}

function dateTime(iso: string): string {
  const parsed = new Date(iso)
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString()
}

function seconds(value: number | null): string {
  return value === null ? '—' : `${value.toFixed(1)}s`
}

function trackPointer(event: PointerEvent): void {
  const target = event.currentTarget as SVGRectElement | null
  if (!target || trend.value.length === 0) return
  const bounds = target.getBoundingClientRect()
  if (bounds.width === 0) return
  const ratio = (event.clientX - bounds.left) / bounds.width
  const plotRatio =
    (ratio * PLOT.width - PLOT.left) / (PLOT.width - PLOT.left - PLOT.right)
  const index = Math.round(plotRatio * Math.max(1, trend.value.length - 1))
  hoveredIndex.value = Math.min(trend.value.length - 1, Math.max(0, index))
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    stats.value = await statsApi.overview()
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : '无法读取运行统计'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="page">
    <PageHeader
      eyebrow="MONITORING"
      title="运行监控"
      description="过去 7 天的搜索表现、站点耗时和自动化执行历史。"
    >
      <button class="button primary" :disabled="loading" @click="load">刷新</button>
    </PageHeader>

    <PageState :loading="loading" :error="error" />

    <template v-if="stats && !loading">
      <div class="summary-grid">
        <article class="panel summary-card">
          <span class="summary-icon" aria-hidden="true">▦</span>
          <div>
            <span class="eyebrow">资源覆盖率</span>
            <strong>{{ percent(stats.library_coverage.coverage_rate) }}</strong>
            <p class="muted">
              {{ stats.library_coverage.covered }} / {{ stats.library_coverage.total }}
              条已下载或下载中
            </p>
          </div>
        </article>
        <article class="panel summary-card">
          <span class="summary-icon" aria-hidden="true">⌕</span>
          <div>
            <span class="eyebrow">7 日搜索成功率</span>
            <strong>{{ percent(weekTotals.rate) }}</strong>
            <p class="muted">{{ weekTotals.succeeded }} / {{ weekTotals.total }} 次搜索成功</p>
          </div>
        </article>
        <article class="panel summary-card">
          <span class="summary-icon" aria-hidden="true">!</span>
          <div>
            <span class="eyebrow">出错下载</span>
            <strong>{{ stats.download_health.errored }}</strong>
            <p class="muted">共 {{ stats.download_health.total }} 条下载记录</p>
          </div>
        </article>
      </div>

      <div class="section-heading">
        <div>
          <span class="eyebrow">SEARCH TREND</span>
          <h2>搜索成功率趋势</h2>
          <p class="muted">按天统计，只计入已结束的搜索；当天没有搜索时不画点。</p>
        </div>
      </div>

      <div class="panel chart-panel">
        <div class="chart-frame">
          <svg
            :viewBox="`0 0 ${PLOT.width} ${PLOT.height}`"
            class="trend-chart"
            role="img"
            aria-label="过去 7 天每日搜索成功率折线图"
          >
            <g class="chart-grid">
              <line
                v-for="tick in [0, 0.5, 1]"
                :key="tick"
                :x1="PLOT.left"
                :x2="PLOT.width - PLOT.right"
                :y1="yFor(tick)"
                :y2="yFor(tick)"
              />
            </g>
            <g class="chart-axis-label">
              <text
                v-for="tick in [0, 0.5, 1]"
                :key="tick"
                :x="PLOT.left - 8"
                :y="yFor(tick) + 4"
                text-anchor="end"
              >
                {{ Math.round(tick * 100) }}%
              </text>
            </g>
            <g class="chart-axis-label">
              <text
                v-for="(day, index) in trend"
                :key="day.date"
                :x="xFor(index)"
                :y="PLOT.height - 8"
                text-anchor="middle"
              >
                {{ shortDate(day.date) }}
              </text>
            </g>

            <path
              v-for="(segment, position) in lineSegments"
              :key="position"
              :d="segment"
              class="trend-line"
            />
            <circle
              v-for="entry in plotted"
              :key="entry.point.date"
              :cx="xFor(entry.index)"
              :cy="yFor(entry.point.success_rate ?? 0)"
              :r="hoveredIndex === entry.index ? 6 : 4.5"
              class="trend-marker"
            />
            <line
              v-if="hoveredIndex !== null"
              class="trend-crosshair"
              :x1="xFor(hoveredIndex)"
              :x2="xFor(hoveredIndex)"
              :y1="PLOT.top"
              :y2="PLOT.height - PLOT.bottom"
            />
            <text
              v-if="lastPlotted"
              class="trend-endpoint-label"
              :x="xFor(lastPlotted.index) - 6"
              :y="yFor(lastPlotted.point.success_rate ?? 0) - 12"
              text-anchor="end"
            >
              {{ percent(lastPlotted.point.success_rate) }}
            </text>

            <rect
              :x="PLOT.left"
              :y="PLOT.top"
              :width="PLOT.width - PLOT.left - PLOT.right"
              :height="PLOT.height - PLOT.top - PLOT.bottom"
              fill="transparent"
              @pointermove="trackPointer"
              @pointerleave="hoveredIndex = null"
            />
          </svg>
        </div>
        <p v-if="hovered" class="chart-tooltip">
          <strong>{{ hovered.date }}</strong>
          成功率 {{ percent(hovered.success_rate) }} · {{ hovered.succeeded }} /
          {{ hovered.total }} 次搜索
        </p>
        <p v-else class="muted chart-hint">把指针移到图上查看某一天的明细。</p>
      </div>

      <div class="section-heading">
        <div>
          <span class="eyebrow">SITE LATENCY</span>
          <h2>各站点平均搜索耗时</h2>
          <p class="muted">从创建搜索到搜索结束的时间，也就是你实际等待的时长。</p>
        </div>
      </div>

      <PageState :empty="stats.site_latency.length === 0" empty-text="最近 7 天还没有已完成的搜索" />

      <div v-if="stats.site_latency.length" class="panel latency-panel">
        <div v-for="site in stats.site_latency" :key="site.site_id" class="latency-row">
          <span class="latency-name">{{ site.site_id }}</span>
          <span class="latency-track">
            <span
              class="latency-fill"
              :style="{ width: `${(site.average_seconds / slowestSiteSeconds) * 100}%` }"
            />
          </span>
          <span class="latency-value">
            平均 {{ seconds(site.average_seconds) }}
            <span class="muted">· 最慢 {{ seconds(site.slowest_seconds) }} · {{ site.searches }} 次</span>
          </span>
        </div>
      </div>

      <div class="section-heading">
        <div>
          <span class="eyebrow">RUN HISTORY</span>
          <h2>自动化执行历史</h2>
        </div>
      </div>

      <PageState :empty="stats.recent_runs.length === 0" empty-text="还没有自动化执行记录" />

      <div v-if="stats.recent_runs.length" class="panel run-table-panel">
        <table class="run-table">
          <thead>
            <tr>
              <th scope="col">时间</th>
              <th scope="col">触发</th>
              <th scope="col">状态</th>
              <th scope="col">创建</th>
              <th scope="col">成功</th>
              <th scope="col">失败</th>
              <th scope="col">推迟</th>
              <th scope="col">耗时</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="run in stats.recent_runs" :key="run.id">
              <td>{{ dateTime(run.created_at) }}</td>
              <td>{{ run.trigger }}</td>
              <td><StatusPill :status="run.state" :label="statusLabel(run.state)" /></td>
              <td>{{ run.created_count }}</td>
              <td>{{ run.succeeded_count }}</td>
              <td>{{ run.failed_count }}</td>
              <td>{{ run.deferred_count }}</td>
              <td>{{ seconds(run.duration_seconds) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </section>
</template>
