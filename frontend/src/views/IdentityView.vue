<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import { useIdentityStore } from '../stores/identity'

const route = useRoute()
const store = useIdentityStore()
const operator = ref('')
const mediaId = computed(() => String(route.params.id))

onMounted(() => store.load(mediaId.value))

function posterUrl(path: string | null): string | null {
  return path ? `https://image.tmdb.org/t/p/w342${path}` : null
}

function confirm(matchId: string): void {
  if (!operator.value.trim()) return
  void store.confirm(mediaId.value, matchId, operator.value.trim())
}
</script>

<template>
  <section class="page">
    <PageHeader
      eyebrow="IDENTITY REVIEW"
      title="影视身份确认"
      description="人工核对 NextFind 条目与 TMDB 候选。"
    >
      <div class="header-actions">
        <a class="button secondary" href="/media">返回列表</a>
        <button class="button primary" :disabled="store.working" @click="store.resolve(mediaId)">
          {{ store.working ? '处理中…' : '重新解析' }}
        </button>
      </div>
    </PageHeader>

    <div class="phase-banner"><span>只读</span>身份不会自动批准，必须由操作者明确确认。</div>
    <PageState :loading="store.loading" :error="store.error" />
    <div v-if="store.notice" class="notice-state">{{ store.notice }}</div>

    <template v-if="store.media && !store.loading">
      <section class="source-strip">
        <div><span>NextFind 标题</span><strong>{{ store.media.title }}</strong></div>
        <div><span>类型</span><strong>{{ store.media.media_type === 'movie' ? '电影' : '电视剧' }}</strong></div>
        <div><span>年份</span><strong>{{ store.media.year ?? '未知' }}</strong></div>
        <div><span>原 TMDB ID</span><strong class="mono">{{ store.media.tmdb_id ?? '未提供' }}</strong></div>
        <div><span>状态</span><strong>{{ store.media.workflow_status }}</strong></div>
      </section>

      <div class="operator-bar">
        <label>操作者<input v-model="operator" maxlength="120" placeholder="记录本次确认人" /></label>
        <small>确认记录会写入审计时间线</small>
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
              <div><dt>IMDb</dt><dd class="mono">{{ match.candidate.imdb_id || '—' }}</dd></div>
              <div><dt>季 / 集</dt><dd>{{ match.candidate.number_of_seasons ?? '—' }} / {{ match.candidate.number_of_episodes ?? '—' }}</dd></div>
            </dl>
            <div class="reason-list"><span v-for="reason in match.match_reasons" :key="reason">{{ reason }}</span></div>
            <div v-if="match.conflicts.length" class="warning-list"><span v-for="conflict in match.conflicts" :key="conflict">{{ conflict }}</span></div>
            <button
              class="button primary confirm-button"
              :disabled="store.working || !operator.trim() || store.media.workflow_status === 'IDENTITY_CONFIRMED'"
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
