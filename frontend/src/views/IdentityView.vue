<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from 'vue'
import { useRoute } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import { useAuthStore } from '../stores/auth'
import { useIdentityStore } from '../stores/identity'
import { formatCountryCodes } from '../utils/format'

const route = useRoute()
const auth = useAuthStore()
const store = useIdentityStore()
const mediaId = computed(() => String(route.params.id))
const canOperate = computed(() => auth.hasRole('operator'))

watch(mediaId, (value) => void store.load(value), { immediate: true })
onBeforeUnmount(() => store.cancelResolution())

function posterUrl(path: string | null): string | null {
  return path ? `https://image.tmdb.org/t/p/w342${path}` : null
}

function confirm(matchId: string): void {
  if (!canOperate.value) return
  void store.confirm(mediaId.value, matchId)
}
</script>

<template>
  <section class="page">
    <PageHeader
      eyebrow="IDENTITY REVIEW"
      title="影视身份确认"
      description="核对 NextFind 条目与 TMDB 候选；这里只处理无法自动确认的条目。"
    >
      <div class="header-actions">
        <a class="button secondary" href="/media">返回列表</a>
        <button class="button secondary" :disabled="store.loading || store.working" @click="store.load(mediaId)">刷新候选</button>
        <button class="button primary" :disabled="store.loading || store.working || !canOperate || store.identityGatePassed" @click="store.resolve(mediaId)">
          {{ store.working ? '处理中…' : '重新解析' }}
        </button>
        <a
          v-if="store.identityGatePassed"
          class="button primary"
          data-testid="continue-pt-search"
          :href="`/media/${mediaId}/torrents`"
        >查看 PT 候选</a>
      </div>
    </PageHeader>

    <div class="phase-banner"><span>自动识别</span>信息完全一致时系统自动确认；存在缺失或冲突时才需要人工选择。</div>
    <PageState :loading="store.loading" :error="store.error" />
    <div v-if="store.notice" class="notice-state">{{ store.notice }}</div>

    <template v-if="store.media && !store.loading">
      <section class="source-strip">
        <div><span>NextFind 标题</span><strong>{{ store.media.title }}</strong></div>
        <div><span>类型</span><strong>{{ store.media.media_type === 'movie' ? '电影' : '电视剧' }}</strong></div>
        <div><span>年份</span><strong>{{ store.media.year ?? '未知' }}</strong></div>
        <div data-testid="source-country"><span>国家 / 地区</span><strong>{{ formatCountryCodes(store.media.country_codes) || '待确认' }}</strong></div>
        <div><span>原 TMDB ID</span><strong class="mono">{{ store.media.tmdb_id ?? '未提供' }}</strong></div>
        <div><span>元数据状态</span><strong>{{ store.media.metadata_status }}</strong></div>
        <div><span>状态</span><strong>{{ store.media.workflow_status }}</strong></div>
      </section>

      <div class="permission-bar">
        <strong>当前账号：{{ auth.principal?.username }} · {{ auth.roleLabel }}</strong>
        <small>{{ canOperate ? '身份确认将使用当前登录账号写入审计时间线' : '当前角色仅可查看，身份解析与确认需要操作者权限' }}</small>
      </div>

      <PageState
        :empty="!store.error && store.candidates.length === 0"
        empty-text="尚无 TMDB 候选；可创建只读解析任务后刷新"
      />
      <div v-if="store.candidates.length" class="identity-grid">
        <article v-for="match in store.candidates" :key="match.id" class="identity-candidate">
          <div class="poster-frame">
            <img v-if="posterUrl(match.candidate.poster_path)" :src="posterUrl(match.candidate.poster_path) || ''" :alt="match.candidate.title" />
            <span v-else>NO POSTER</span>
          </div>
          <div class="identity-content">
            <div class="candidate-heading">
              <div><span class="eyebrow">TMDB {{ match.tmdb_id }}</span><h2>{{ match.candidate.chinese_title || match.candidate.title }}</h2></div>
              <strong class="score-value">{{ Math.round(match.score * 100) }}</strong>
            </div>
            <p class="original-line">{{ match.candidate.english_title || '—' }} · {{ match.candidate.original_title || '—' }}</p>
            <dl class="candidate-facts">
              <div><dt>年份</dt><dd>{{ match.candidate.year ?? '—' }}</dd></div>
              <div><dt>类型</dt><dd>{{ match.candidate.media_type === 'movie' ? '电影' : '电视剧' }}</dd></div>
              <div><dt>国家 / 地区</dt><dd data-testid="candidate-country">{{ formatCountryCodes(match.candidate.country_codes) || '—' }}</dd></div>
              <div><dt>IMDb</dt><dd class="mono">{{ match.candidate.imdb_id || '—' }}</dd></div>
              <div><dt>季 / 集</dt><dd>{{ match.candidate.number_of_seasons ?? '—' }} / {{ match.candidate.number_of_episodes ?? '—' }}</dd></div>
            </dl>
            <div class="reason-list"><span v-for="reason in match.match_reasons" :key="reason">{{ reason }}</span></div>
            <div v-if="match.conflicts.length" class="warning-list"><span v-for="conflict in match.conflicts" :key="conflict">{{ conflict }}</span></div>
            <button
              class="button primary confirm-button"
              :disabled="store.working || !canOperate || store.identityGatePassed"
              @click="confirm(match.id)"
            >
              确认此身份
            </button>
          </div>
        </article>
      </div>
    </template>
  </section>
</template>
