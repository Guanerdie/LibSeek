<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import PageState from '../components/PageState.vue'
import { ApiError } from '../api/client'
import { useAuthStore } from '../stores/auth'
import { useConfigurationStore } from '../stores/configuration'
import { useSystemStore } from '../stores/system'
import type {
  AvistaZConfigurationSnapshot,
  ConfigurationSection,
  ConfigurationSnapshot,
  ConfigurationTestResult,
  ConfigurationUpdateRequest,
  NexusPhpConfigurationSnapshot,
  PtSiteArchitecture,
  PtSiteArchitectureOption,
} from '../types'
import { formatShanghai } from '../utils/format'

const auth = useAuthStore()
const store = useConfigurationStore()
const system = useSystemStore()

const fallbackPtArchitectures: PtSiteArchitectureOption[] = [
  {
    architecture: 'avistaz',
    label: 'AvistaZ',
    runtime_supported: true,
    connection_test_supported: true,
    fields: [],
  },
  {
    architecture: 'nexusphp',
    label: 'NexusPHP',
    runtime_supported: false,
    connection_test_supported: true,
    fields: [],
  },
]

const form = reactive({
  nextfindBaseUrl: 'https://nextfind.example',
  nextfindUsername: '',
  ptArchitecture: 'avistaz' as PtSiteArchitecture,
  avistazBaseUrl: 'https://avistaz.to',
  avistazUsername: '',
  nexusSiteId: '',
  nexusDisplayName: '',
  nexusBaseUrl: '',
  qbUrl: '',
  qbUsername: '',
  qbSavePath: '',
  qbCategory: '',
  qbAllowInsecureHttp: false,
})
const nextfindPassword = ref('')
const tmdbToken = ref('')
const avistazPassword = ref('')
const avistazPid = ref('')
const nexusCookie = ref('')
const nexusPasskey = ref('')
const qbPassword = ref('')
type ActionKind = 'save' | 'test'
type ActionPhase = 'saving' | 'testing'
type FeedbackStatus = 'pending' | 'success' | 'error'
type SectionFeedback = {
  kind: ActionKind
  status: FeedbackStatus
  message: string
  target?: string
  occurredAt?: string
  errorCode?: string | null
}
const activeAction = ref<{ section: ConfigurationSection; kind: ActionKind } | null>(null)
const activePhase = ref<ActionPhase | null>(null)
const feedback = reactive<Partial<Record<ConfigurationSection, SectionFeedback>>>({})
const dirty = reactive<Record<ConfigurationSection, boolean>>({
  nextfind: false,
  tmdb: false,
  pt_site: false,
  qbittorrent: false,
})
let hydrating = false
let initialized = false

const ptArchitectures = computed(() =>
  store.data?.pt_site_architectures?.length
    ? store.data.pt_site_architectures
    : fallbackPtArchitectures,
)
const selectedPtArchitecture = computed(() =>
  ptArchitectures.value.find((item) => item.architecture === form.ptArchitecture),
)
const selectedAvistaZSnapshot = computed<AvistaZConfigurationSnapshot | null>(() => {
  if (form.ptArchitecture !== 'avistaz') return null
  const stored = store.data?.pt_sites?.avistaz
  if (stored) return stored
  const active = store.data?.pt_site
  return active?.architecture === 'avistaz' ? active : null
})
const selectedNexusSnapshot = computed<NexusPhpConfigurationSnapshot | null>(() => {
  if (form.ptArchitecture !== 'nexusphp') return null
  const stored = store.data?.pt_sites?.nexusphp
  if (stored) return stored
  const active = store.data?.pt_site
  return active?.architecture === 'nexusphp' ? active : null
})
const ptConfigured = computed(() => {
  const snapshot =
    form.ptArchitecture === 'avistaz'
      ? selectedAvistaZSnapshot.value
      : selectedNexusSnapshot.value
  return snapshot?.configured ?? false
})
const ptSearchReady = computed(() => {
  const snapshot =
    form.ptArchitecture === 'avistaz'
      ? selectedAvistaZSnapshot.value
      : selectedNexusSnapshot.value
  return snapshot?.search_ready ?? false
})
const ptRuntimeSupported = computed(
  () => selectedPtArchitecture.value?.runtime_supported ?? form.ptArchitecture === 'avistaz',
)
const ptStateLabel = computed(() => {
  if (!ptRuntimeSupported.value) return '保存后待适配'
  if (ptSearchReady.value) return '可搜索'
  return ptConfigured.value ? '已配置' : '未配置'
})
const ptStateClass = computed(() => {
  if (!ptRuntimeSupported.value) return 'disabled'
  return ptConfigured.value ? 'configured' : 'pending'
})
const nextfindIdentityChanged = computed(
  () =>
    form.nextfindBaseUrl.trim() !== store.data?.nextfind.base_url ||
    form.nextfindUsername.trim() !== store.data?.nextfind.username,
)
const avistazIdentityChanged = computed(
  () =>
    form.avistazBaseUrl.trim() !== selectedAvistaZSnapshot.value?.base_url ||
    form.avistazUsername.trim() !== selectedAvistaZSnapshot.value?.username,
)
const nexusIdentityChanged = computed(
  () =>
    form.nexusSiteId.trim() !== selectedNexusSnapshot.value?.site_id ||
    form.nexusBaseUrl.trim() !== selectedNexusSnapshot.value?.base_url,
)
const qbIdentityChanged = computed(
  () =>
    form.qbUrl.trim() !== store.data?.qbittorrent.url ||
    form.qbUsername.trim() !== store.data?.qbittorrent.username,
)

const configuredCount = computed(() => {
  if (!store.data) return 0
  return [
    store.data.nextfind.configured,
    store.data.tmdb.configured,
    store.data.pt_site?.configured ?? false,
    store.data.qbittorrent.configured,
  ].filter(Boolean).length
})

function applySnapshot(snapshot: ConfigurationSnapshot | null): void {
  if (!snapshot) return
  auth.configurationComplete = snapshot.configuration_complete
  if (initialized) return
  hydrating = true
  form.nextfindBaseUrl = snapshot.nextfind.base_url
  form.nextfindUsername = snapshot.nextfind.username
  form.ptArchitecture =
    snapshot.pt_site?.architecture ?? snapshot.pt_site_architectures?.[0]?.architecture ?? 'avistaz'
  const avistaz =
    snapshot.pt_sites?.avistaz ??
    (snapshot.pt_site?.architecture === 'avistaz' ? snapshot.pt_site : null)
  const nexusphp =
    snapshot.pt_sites?.nexusphp ??
    (snapshot.pt_site?.architecture === 'nexusphp' ? snapshot.pt_site : null)
  if (avistaz) {
    form.avistazBaseUrl = avistaz.base_url
    form.avistazUsername = avistaz.username
  }
  if (nexusphp) {
    form.nexusSiteId = nexusphp.site_id
    form.nexusDisplayName = nexusphp.display_name
    form.nexusBaseUrl = nexusphp.base_url
  }
  form.qbUrl = snapshot.qbittorrent.url
  form.qbUsername = snapshot.qbittorrent.username
  form.qbSavePath = snapshot.qbittorrent.save_path
  form.qbCategory = snapshot.qbittorrent.category
  form.qbAllowInsecureHttp = snapshot.qbittorrent.allow_insecure_http
  for (const section of Object.keys(dirty) as ConfigurationSection[]) dirty[section] = false
  initialized = true
  hydrating = false
}

function applySectionSnapshot(
  section: ConfigurationSection,
  snapshot: ConfigurationSnapshot,
): void {
  if (section === 'nextfind') {
    form.nextfindBaseUrl = snapshot.nextfind.base_url
    form.nextfindUsername = snapshot.nextfind.username
    return
  }
  if (section === 'tmdb') return
  if (section === 'pt_site') {
    const site = snapshot.pt_site
    if (!site) return
    form.ptArchitecture = site.architecture
    if (site.architecture === 'avistaz') {
      form.avistazBaseUrl = site.base_url
      form.avistazUsername = site.username
    } else {
      form.nexusSiteId = site.site_id
      form.nexusDisplayName = site.display_name
      form.nexusBaseUrl = site.base_url
    }
    return
  }
  form.qbUrl = snapshot.qbittorrent.url
  form.qbUsername = snapshot.qbittorrent.username
  form.qbSavePath = snapshot.qbittorrent.save_path
  form.qbCategory = snapshot.qbittorrent.category
  form.qbAllowInsecureHttp = snapshot.qbittorrent.allow_insecure_http
}

function secretPlaceholder(configured: boolean): string {
  return configured ? '已保存，留空则保持不变' : '尚未配置'
}

function clearPtSecrets(): void {
  avistazPassword.value = ''
  avistazPid.value = ''
  nexusCookie.value = ''
  nexusPasskey.value = ''
}

function ptSitePayload(): NonNullable<ConfigurationUpdateRequest['pt_site']> {
  const ptSite: NonNullable<ConfigurationUpdateRequest['pt_site']> =
    form.ptArchitecture === 'avistaz'
      ? {
          architecture: 'avistaz',
          base_url: form.avistazBaseUrl.trim(),
          username: form.avistazUsername.trim(),
        }
      : {
          architecture: 'nexusphp',
          site_id: form.nexusSiteId.trim(),
          display_name: form.nexusDisplayName.trim(),
          base_url: form.nexusBaseUrl.trim(),
        }
  if (ptSite.architecture === 'avistaz') {
    if (avistazPassword.value) ptSite.password = avistazPassword.value
    if (avistazPid.value) ptSite.pid = avistazPid.value
  } else {
    if (nexusCookie.value) ptSite.cookie = nexusCookie.value
    if (nexusPasskey.value) ptSite.passkey = nexusPasskey.value
  }
  return ptSite
}

function sectionPayload(section: ConfigurationSection): ConfigurationUpdateRequest {
  if (section === 'nextfind') {
    const nextfind: NonNullable<ConfigurationUpdateRequest['nextfind']> = {
      base_url: form.nextfindBaseUrl.trim(),
      username: form.nextfindUsername.trim(),
    }
    if (nextfindPassword.value) nextfind.password = nextfindPassword.value
    return { nextfind }
  }
  if (section === 'tmdb') {
    return { tmdb: tmdbToken.value ? { token: tmdbToken.value } : {} }
  }
  if (section === 'pt_site') return { pt_site: ptSitePayload() }
  const qbittorrent: NonNullable<ConfigurationUpdateRequest['qbittorrent']> = {
      url: form.qbUrl.trim(),
      username: form.qbUsername.trim(),
      save_path: form.qbSavePath.trim(),
      category: form.qbCategory.trim(),
      allow_insecure_http: form.qbAllowInsecureHttp,
  }
  if (qbPassword.value) qbittorrent.password = qbPassword.value
  return { qbittorrent }
}

function clearSectionSecrets(section: ConfigurationSection): void {
  if (section === 'nextfind') nextfindPassword.value = ''
  else if (section === 'tmdb') tmdbToken.value = ''
  else if (section === 'pt_site') clearPtSecrets()
  else qbPassword.value = ''
}

function configured(section: ConfigurationSection): boolean {
  if (!store.data) return false
  if (section === 'nextfind') return store.data.nextfind.configured
  if (section === 'tmdb') return store.data.tmdb.configured
  if (section === 'qbittorrent') return store.data.qbittorrent.configured
  return ptConfigured.value
}

function displayOrigin(value: string): string {
  const trimmed = value.trim()
  if (!trimmed) return '尚未填写'
  try {
    return new globalThis.URL(trimmed).origin
  } catch {
    return trimmed
  }
}

function connectionTarget(section: ConfigurationSection): string {
  if (section === 'nextfind') return displayOrigin(form.nextfindBaseUrl)
  if (section === 'tmdb') return 'https://api.themoviedb.org'
  if (section === 'qbittorrent') return displayOrigin(form.qbUrl)
  return displayOrigin(
    form.ptArchitecture === 'avistaz' ? form.avistazBaseUrl : form.nexusBaseUrl,
  )
}

function errorDetails(caught: unknown, fallback: string): { message: string; errorCode?: string } {
  if (caught instanceof ApiError) {
    return { message: caught.message, errorCode: caught.errorCode }
  }
  return { message: caught instanceof Error ? caught.message : fallback }
}

function markSaved(
  section: ConfigurationSection,
  snapshot: ConfigurationSnapshot,
  clearSecrets = true,
): void {
  hydrating = true
  applySectionSnapshot(section, snapshot)
  if (clearSecrets) clearSectionSecrets(section)
  dirty[section] = false
  hydrating = false
}

async function saveSection(section: ConfigurationSection): Promise<void> {
  if (!store.data || activeAction.value) return
  const target = connectionTarget(section)
  activeAction.value = { section, kind: 'save' }
  activePhase.value = 'saving'
  feedback[section] = {
    kind: 'save',
    status: 'pending',
    message: '正在写入本机持久配置…',
    target,
  }
  try {
    const snapshot = await store.update(sectionPayload(section))
    markSaved(section, snapshot)
    feedback[section] = {
      kind: 'save',
      status: 'success',
      message: '后端已确认写入本机持久配置，尚未测试外部连接。',
      target,
      occurredAt: new Date().toISOString(),
    }
  } catch (caught) {
    const details = errorDetails(caught, '保存配置失败')
    feedback[section] = {
      kind: 'save',
      status: 'error',
      message: details.message,
      target,
      errorCode: details.errorCode,
      occurredAt: new Date().toISOString(),
    }
  } finally {
    activePhase.value = null
    activeAction.value = null
  }
}

async function testSection(
  section: ConfigurationSection,
  event?: { currentTarget: unknown },
): Promise<void> {
  if (!store.data || activeAction.value || !testSupported(section)) return
  const needsSave = dirty[section] || !configured(section)
  const architecture = section === 'pt_site' ? form.ptArchitecture : undefined
  const target = connectionTarget(section)
  const eventTarget = event?.currentTarget as {
    closest?: (selector: string) => { reportValidity: () => boolean } | null
  } | null
  const sectionForm = eventTarget?.closest?.('form')
  if (needsSave && sectionForm && !sectionForm.reportValidity()) return
  activeAction.value = { section, kind: 'test' }
  try {
    if (needsSave) {
      activePhase.value = 'saving'
      feedback[section] = {
        kind: 'test',
        status: 'pending',
        message: '正在保存当前输入，保存成功后将立即测试真实连接…',
        target,
      }
      const snapshot = await store.update(sectionPayload(section))
      markSaved(section, snapshot)
    }

    activePhase.value = 'testing'
    feedback[section] = {
      kind: 'test',
      status: 'pending',
      message: `正在连接 ${target}…`,
      target,
    }
    const result: ConfigurationTestResult = await store.testConnection(
      section,
      architecture,
    )
    feedback[section] = {
      kind: 'test',
      status: result.healthy ? 'success' : 'error',
      message: result.message,
      target,
      errorCode: result.error_code,
      occurredAt: new Date().toISOString(),
    }
  } catch (caught) {
    const details = errorDetails(caught, '连接测试失败')
    feedback[section] = {
      kind: 'test',
      status: 'error',
      message: details.message,
      target,
      errorCode: details.errorCode,
      occurredAt: new Date().toISOString(),
    }
  } finally {
    activePhase.value = null
    activeAction.value = null
  }
}

function isActing(section: ConfigurationSection, kind: ActionKind): boolean {
  return activeAction.value?.section === section && activeAction.value.kind === kind
}

function testSupported(section: ConfigurationSection): boolean {
  if (section !== 'pt_site') return true
  return selectedPtArchitecture.value?.connection_test_supported ?? false
}

function feedbackTarget(section: ConfigurationSection): string {
  return feedback[section]?.target ?? connectionTarget(section)
}

function testHint(section: ConfigurationSection): string {
  if (!testSupported(section)) return '该站点架构暂不支持连接测试'
  if (section === 'pt_site' && form.ptArchitecture === 'nexusphp') {
    return dirty[section] || !configured(section)
      ? '保存后验证 Cookie 会话；搜索功能待适配'
      : '验证已保存 Cookie 会话；搜索功能待适配'
  }
  if (dirty[section] || !configured(section)) return '测试时会先保存当前输入'
  return '使用已保存配置发起真实连接'
}

function feedbackLabel(item: SectionFeedback): string {
  if (item.status === 'pending') return item.kind === 'save' ? '保存中' : '执行中'
  if (item.kind === 'save') return item.status === 'success' ? '保存成功' : '保存失败'
  return item.status === 'success' ? '真实连接成功' : '真实连接失败'
}

function actionLabel(section: ConfigurationSection, kind: ActionKind, idle: string): string {
  if (!isActing(section, kind)) return idle
  if (kind === 'save' || activePhase.value === 'saving') return '正在保存…'
  return '正在连接…'
}

function markSectionDirty(section: ConfigurationSection): void {
  dirty[section] = true
  if (feedback[section]) delete feedback[section]
}

watch([() => form.nextfindBaseUrl, () => form.nextfindUsername, nextfindPassword], () => {
  if (initialized && !hydrating) markSectionDirty('nextfind')
}, { flush: 'sync' })
watch(tmdbToken, () => {
  if (initialized && !hydrating) markSectionDirty('tmdb')
}, { flush: 'sync' })
watch(
  [
    () => form.ptArchitecture,
    () => form.avistazBaseUrl,
    () => form.avistazUsername,
    () => form.nexusSiteId,
    () => form.nexusDisplayName,
    () => form.nexusBaseUrl,
    avistazPassword,
    avistazPid,
    nexusCookie,
    nexusPasskey,
  ],
  () => {
    if (initialized && !hydrating) markSectionDirty('pt_site')
  },
  { flush: 'sync' },
)
watch(
  [
    () => form.qbUrl,
    () => form.qbUsername,
    () => form.qbSavePath,
    () => form.qbCategory,
    () => form.qbAllowInsecureHttp,
    qbPassword,
  ],
  () => {
    if (initialized && !hydrating) markSectionDirty('qbittorrent')
  },
  { flush: 'sync' },
)

watch(() => store.data, applySnapshot, { immediate: true })
watch(
  () => form.ptArchitecture,
    (architecture, previousArchitecture) => {
      if (previousArchitecture && architecture !== previousArchitecture) clearPtSecrets()
  },
)
void store.refresh()
void system.refresh()
</script>

<template>
  <section class="page configuration-page">
    <PageHeader
      eyebrow="CONNECTION SETTINGS"
      title="连接配置"
      description="登录后在这里配置影视来源、元数据、PT 站点和下载器。保存不会测试外部连接。"
    >
      <div class="configuration-progress" aria-label="配置完成度">
        <strong>{{ configuredCount }} / 4</strong><small>已配置连接</small>
      </div>
    </PageHeader>

    <PageState :loading="store.loading && !store.data" :error="!store.data ? store.error : null" />

    <div v-if="store.data" class="configuration-form">
      <form class="integration-config-section" autocomplete="off" aria-labelledby="nextfind-config-title" @submit.prevent="saveSection('nextfind')">
        <header>
          <div><span class="eyebrow">MEDIA SOURCE</span><h2 id="nextfind-config-title">NextFind</h2></div>
          <span :class="['config-state', store.data.nextfind.configured ? 'configured' : 'pending']">
            {{ store.data.nextfind.configured ? '已配置' : '未配置' }}
          </span>
        </header>
        <div class="configuration-field-grid three-fields">
          <label><span>服务地址</span><input v-model="form.nextfindBaseUrl" name="nextfind_base_url" type="url" maxlength="2048" placeholder="https://nextfind.example" required /></label>
          <label><span>用户名</span><input v-model="form.nextfindUsername" name="nextfind_username" maxlength="120" required /></label>
          <label><span>密码</span><input v-model="nextfindPassword" name="nextfind_password" type="password" autocomplete="new-password" :placeholder="secretPlaceholder(store.data.nextfind.password_configured)" :required="!store.data.nextfind.password_configured || nextfindIdentityChanged" maxlength="8192" /></label>
        </div>
        <div v-if="feedback.nextfind" :class="['configuration-message', feedback.nextfind.status]" :role="feedback.nextfind.status === 'error' ? 'alert' : 'status'">
          <strong>{{ feedbackLabel(feedback.nextfind) }}</strong>
          <span>{{ feedback.nextfind.message }}</span>
          <small>目标：{{ feedbackTarget('nextfind') }}<template v-if="feedback.nextfind.errorCode"> · 错误码：{{ feedback.nextfind.errorCode }}</template><template v-if="feedback.nextfind.occurredAt"> · {{ formatShanghai(feedback.nextfind.occurredAt) }}</template></small>
        </div>
        <div class="configuration-section-actions">
          <span>{{ testHint('nextfind') }}</span>
          <button class="button secondary" type="button" :disabled="Boolean(activeAction)" @click="testSection('nextfind', $event)">{{ actionLabel('nextfind', 'test', configured('nextfind') && !dirty.nextfind ? '测试真实连接' : '保存并测试') }}</button>
          <button class="button primary" type="submit" :disabled="Boolean(activeAction)">{{ actionLabel('nextfind', 'save', '保存 NextFind') }}</button>
        </div>
      </form>

      <form class="integration-config-section" autocomplete="off" aria-labelledby="tmdb-config-title" @submit.prevent="saveSection('tmdb')">
        <header>
          <div><span class="eyebrow">METADATA</span><h2 id="tmdb-config-title">TMDB</h2></div>
          <span :class="['config-state', store.data.tmdb.configured ? 'configured' : 'pending']">
            {{ store.data.tmdb.configured ? '已配置' : '未配置' }}
          </span>
        </header>
        <div class="configuration-field-grid single-field">
          <label><span>Access Token</span><input v-model="tmdbToken" name="tmdb_token" type="password" autocomplete="new-password" :placeholder="secretPlaceholder(store.data.tmdb.configured)" :required="!store.data.tmdb.configured" maxlength="8192" /></label>
        </div>
        <div v-if="feedback.tmdb" :class="['configuration-message', feedback.tmdb.status]" :role="feedback.tmdb.status === 'error' ? 'alert' : 'status'">
          <strong>{{ feedbackLabel(feedback.tmdb) }}</strong>
          <span>{{ feedback.tmdb.message }}</span>
          <small>目标：{{ feedbackTarget('tmdb') }}<template v-if="feedback.tmdb.errorCode"> · 错误码：{{ feedback.tmdb.errorCode }}</template><template v-if="feedback.tmdb.occurredAt"> · {{ formatShanghai(feedback.tmdb.occurredAt) }}</template></small>
        </div>
        <div class="configuration-section-actions">
          <span>{{ testHint('tmdb') }}</span>
          <button class="button secondary" type="button" :disabled="Boolean(activeAction)" @click="testSection('tmdb', $event)">{{ actionLabel('tmdb', 'test', configured('tmdb') && !dirty.tmdb ? '测试真实连接' : '保存并测试') }}</button>
          <button class="button primary" type="submit" :disabled="Boolean(activeAction)">{{ actionLabel('tmdb', 'save', '保存 TMDB') }}</button>
        </div>
      </form>

      <form class="integration-config-section" autocomplete="off" aria-labelledby="pt-config-title" @submit.prevent="saveSection('pt_site')">
        <header>
          <div><span class="eyebrow">PT SITE</span><h2 id="pt-config-title">PT 站点</h2></div>
          <span :class="['config-state', ptStateClass]">{{ ptStateLabel }}</span>
        </header>
        <div class="configuration-field-grid single-field pt-architecture-selection">
          <label>
            <span>站点架构</span>
            <select v-model="form.ptArchitecture" name="pt_architecture" aria-label="PT 站点架构">
              <option v-for="option in ptArchitectures" :key="option.architecture" :value="option.architecture">
                {{ option.label }}{{ option.runtime_supported ? '' : '（搜索待适配）' }}
              </option>
            </select>
          </label>
        </div>
        <div v-if="form.ptArchitecture === 'avistaz'" class="configuration-field-grid three-fields pt-dynamic-fields">
          <label><span>站点地址</span><input v-model="form.avistazBaseUrl" name="avistaz_base_url" type="url" maxlength="2048" placeholder="https://avistaz.to" required /></label>
          <label><span>用户名</span><input v-model="form.avistazUsername" name="avistaz_username" maxlength="120" required /></label>
          <label><span>密码</span><input v-model="avistazPassword" name="avistaz_password" type="password" autocomplete="new-password" :placeholder="secretPlaceholder(selectedAvistaZSnapshot?.password_configured ?? false)" :required="!(selectedAvistaZSnapshot?.password_configured ?? false) || avistazIdentityChanged" maxlength="8192" /></label>
          <label><span>PID</span><input v-model="avistazPid" name="avistaz_pid" type="password" autocomplete="new-password" :placeholder="secretPlaceholder(selectedAvistaZSnapshot?.pid_configured ?? false)" :required="!(selectedAvistaZSnapshot?.pid_configured ?? false) || avistazIdentityChanged" maxlength="8192" /></label>
        </div>
        <div v-else class="pt-dynamic-fields">
          <div class="configuration-message warning pt-adapter-notice" role="status">
            连接测试会只读访问站点并验证 Cookie 登录状态；当前尚未适配搜索 Profile，不会搜索、取种或下载。
          </div>
          <div class="configuration-field-grid three-fields">
            <label><span>站点标识</span><input v-model="form.nexusSiteId" name="nexus_site_id" maxlength="24" pattern="[a-z0-9](?:[a-z0-9-]{0,22}[a-z0-9])?" placeholder="例如 hdchina" required /></label>
            <label><span>显示名称</span><input v-model="form.nexusDisplayName" name="nexus_display_name" maxlength="100" required /></label>
            <label><span>HTTPS 地址</span><input v-model="form.nexusBaseUrl" name="nexus_base_url" type="url" maxlength="2048" placeholder="https://pt.example.com" required /></label>
            <label><span>Cookie</span><input v-model="nexusCookie" name="nexus_cookie" type="password" autocomplete="new-password" :placeholder="secretPlaceholder(selectedNexusSnapshot?.cookie_configured ?? false)" :required="!(selectedNexusSnapshot?.cookie_configured ?? false) || nexusIdentityChanged" maxlength="8192" /></label>
            <label><span>Passkey（可选）</span><input v-model="nexusPasskey" name="nexus_passkey" type="password" autocomplete="new-password" :placeholder="secretPlaceholder(selectedNexusSnapshot?.passkey_configured ?? false)" maxlength="8192" /></label>
          </div>
        </div>
        <div v-if="feedback.pt_site" :class="['configuration-message', feedback.pt_site.status]" :role="feedback.pt_site.status === 'error' ? 'alert' : 'status'">
          <strong>{{ feedbackLabel(feedback.pt_site) }}</strong>
          <span>{{ feedback.pt_site.message }}</span>
          <small>目标：{{ feedbackTarget('pt_site') }}<template v-if="feedback.pt_site.errorCode"> · 错误码：{{ feedback.pt_site.errorCode }}</template><template v-if="feedback.pt_site.occurredAt"> · {{ formatShanghai(feedback.pt_site.occurredAt) }}</template></small>
        </div>
        <div class="configuration-section-actions">
          <span>{{ testHint('pt_site') }}</span>
          <button class="button secondary" type="button" :disabled="Boolean(activeAction) || !testSupported('pt_site')" @click="testSection('pt_site', $event)">{{ actionLabel('pt_site', 'test', configured('pt_site') && !dirty.pt_site ? '测试真实连接' : '保存并测试') }}</button>
          <button class="button primary" type="submit" :disabled="Boolean(activeAction)">{{ actionLabel('pt_site', 'save', '保存 PT 站点') }}</button>
        </div>
      </form>

      <form class="integration-config-section" autocomplete="off" aria-labelledby="qb-config-title" @submit.prevent="saveSection('qbittorrent')">
        <header>
          <div><span class="eyebrow">DOWNLOADER</span><h2 id="qb-config-title">qBittorrent</h2></div>
          <span :class="['config-state', store.data.qbittorrent.configured ? 'configured' : 'pending']">
            {{ store.data.qbittorrent.configured ? '已配置' : '未配置' }}
          </span>
        </header>
        <div class="configuration-field-grid">
          <label><span>Web 地址</span><input v-model="form.qbUrl" name="qb_url" type="url" maxlength="2048" placeholder="https://qb.example.internal" required /></label>
          <label><span>用户名</span><input v-model="form.qbUsername" name="qb_username" maxlength="120" required /></label>
          <label><span>密码</span><input v-model="qbPassword" name="qb_password" type="password" autocomplete="new-password" :placeholder="secretPlaceholder(store.data.qbittorrent.configured)" :required="!store.data.qbittorrent.configured || qbIdentityChanged" maxlength="8192" /></label>
          <label><span>保存路径</span><input v-model="form.qbSavePath" name="qb_save_path" maxlength="2048" /></label>
          <label><span>分类（可选）</span><input v-model="form.qbCategory" name="qb_category" maxlength="180" /></label>
          <label class="configuration-checkbox"><input v-model="form.qbAllowInsecureHttp" name="qb_allow_http" type="checkbox" /><span>允许受信内网使用 HTTP 明文连接</span></label>
        </div>
        <div v-if="feedback.qbittorrent" :class="['configuration-message', feedback.qbittorrent.status]" :role="feedback.qbittorrent.status === 'error' ? 'alert' : 'status'">
          <strong>{{ feedbackLabel(feedback.qbittorrent) }}</strong>
          <span>{{ feedback.qbittorrent.message }}</span>
          <small>目标：{{ feedbackTarget('qbittorrent') }}<template v-if="feedback.qbittorrent.errorCode"> · 错误码：{{ feedback.qbittorrent.errorCode }}</template><template v-if="feedback.qbittorrent.occurredAt"> · {{ formatShanghai(feedback.qbittorrent.occurredAt) }}</template></small>
        </div>
        <div class="configuration-section-actions">
          <span>{{ testHint('qbittorrent') }}</span>
          <button class="button secondary" type="button" :disabled="Boolean(activeAction)" @click="testSection('qbittorrent', $event)">{{ actionLabel('qbittorrent', 'test', configured('qbittorrent') && !dirty.qbittorrent ? '测试真实连接' : '保存并测试') }}</button>
          <button class="button primary" type="submit" :disabled="Boolean(activeAction)">{{ actionLabel('qbittorrent', 'save', '保存 qBittorrent') }}</button>
        </div>
      </form>
    </div>

    <template v-if="system.data">
      <section class="configuration-band" aria-labelledby="runtime-status-title">
        <div class="panel-title"><span class="eyebrow">RUNTIME STATUS</span><h2 id="runtime-status-title">运行状态</h2></div>
        <div class="status-grid compact-status-grid">
          <article v-for="(component, name) in { API: system.data.api, Worker: system.data.worker, PostgreSQL: system.data.postgres }" :key="name" class="status-card">
            <span class="health-dot" :class="component.healthy ? 'healthy' : 'unhealthy'"></span>
            <strong>{{ name }}</strong><p>{{ component.message }}</p><small>检查于 {{ formatShanghai(component.checked_at) }}</small>
          </article>
        </div>
      </section>

      <section class="configuration-band" aria-labelledby="download-gates-title">
        <div class="panel-title"><span class="eyebrow">DOWNLOAD SAFETY GATES</span><h2 id="download-gates-title">下载执行门禁</h2><p>连接配置不会开启取种、写入 qBittorrent、下载监控或自动执行。</p></div>
        <div
          v-for="gate in [
            ['人工执行控制面', system.data.download_control_plane_enabled],
            ['下载执行器', system.data.download_executor_enabled],
            ['PT 站点取种', system.data.avistaz_torrent_fetch_enabled],
            ['qBittorrent 写入', system.data.qb_write_enabled],
            ['下载监控', system.data.download_monitor_enabled],
            ['自动化引擎', system.data.automation_engine_enabled],
          ]"
          :key="String(gate[0])"
          class="config-row"
        >
          <div><strong>{{ gate[0] }}</strong><p>{{ gate[1] ? '服务端开关已启用' : '默认安全关闭' }}</p></div>
          <span :class="['config-state', gate[1] ? 'configured' : 'disabled']">{{ gate[1] ? '已启用' : '关闭' }}</span>
        </div>
      </section>
    </template>

    <nav class="configuration-links" aria-label="配置详情">
      <RouterLink to="/adapters"><strong>适配器能力</strong><small>查看站点与元数据适配器声明</small></RouterLink>
      <RouterLink to="/automation"><strong>自动化策略</strong><small>查看当前策略与安全闸门</small></RouterLink>
      <RouterLink to="/qbittorrent"><strong>qBittorrent 状态</strong><small>查看只读连接与任务状态</small></RouterLink>
    </nav>
  </section>
</template>
