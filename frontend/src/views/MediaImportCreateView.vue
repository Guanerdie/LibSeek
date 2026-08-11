<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import PageHeader from '../components/PageHeader.vue'
import { useMediaImportStore } from '../stores/mediaImports'
import type { MediaImportCreateRequest, MediaImportOperation } from '../types'

interface FileDraft {
  sourcePath: string
  sizeBytes: number | null
  targetPath: string
  includeInMapping: boolean
}

const route = useRoute()
const router = useRouter()
const store = useMediaImportStore()
const downloadJobId = ref(String(route.query.download_job_id ?? ''))
const sourceRootRef = ref(String(route.query.source_root_ref ?? ''))
const targetRootRef = ref('')
const operation = ref<MediaImportOperation>('HARDLINK')
const files = reactive<FileDraft[]>([
  { sourcePath: '', sizeBytes: null, targetPath: '', includeInMapping: true },
])
const submitted = ref(false)

const validationError = computed(() => {
  if (!downloadJobId.value.trim()) return '请填写下载任务 ID'
  if (!validRootRef(sourceRootRef.value)) return '源根引用格式无效'
  if (!validRootRef(targetRootRef.value)) return '目标根引用格式无效'
  if (sourceRootRef.value.trim() === targetRootRef.value.trim()) {
    return '目标根引用必须与下载源引用分离'
  }
  if (!files.length) return '至少需要一个文件映射'
  if (!files.some((file) => file.includeInMapping)) return '至少选择一个文件加入目标映射'
  const sourcePaths = new Set<string>()
  const targetPaths = new Set<string>()
  for (const file of files) {
    if (!validRelativePath(file.sourcePath)) {
      return '源路径必须是使用正斜杠的规范相对路径，且不能包含 . 或 .. 段'
    }
    if (!Number.isSafeInteger(file.sizeBytes) || (file.sizeBytes ?? -1) < 0) {
      return '每个文件都需要填写非负整数大小'
    }
    const sourceKey = file.sourcePath.normalize('NFKC').toLocaleLowerCase()
    if (sourcePaths.has(sourceKey)) return '源路径不能重复或仅大小写不同'
    sourcePaths.add(sourceKey)
    if (file.includeInMapping) {
      if (!validRelativePath(file.targetPath)) {
        return '已加入映射的目标路径必须是规范相对路径'
      }
      const targetKey = file.targetPath.normalize('NFKC').toLocaleLowerCase()
      if (targetPaths.has(targetKey)) return '目标路径不能重复或仅大小写不同'
      targetPaths.add(targetKey)
    }
  }
  return null
})

function validRootRef(value: string): boolean {
  return /^[A-Za-z0-9][A-Za-z0-9._:-]{0,179}$/.test(value.trim())
}

function validRelativePath(value: string): boolean {
  const normalized = value.trim()
  if (!normalized || normalized !== value || normalized.startsWith('/') || normalized.includes('\\')) {
    return false
  }
  if (/^[A-Za-z]:/.test(normalized)) return false
  return normalized.split('/').every((part) => part !== '' && part !== '.' && part !== '..')
}

function addFile(): void {
  files.push({ sourcePath: '', sizeBytes: null, targetPath: '', includeInMapping: true })
}

function removeFile(index: number): void {
  if (files.length > 1) files.splice(index, 1)
}

async function submit(): Promise<void> {
  submitted.value = true
  if (validationError.value) return
  const payload: MediaImportCreateRequest = {
    download_job_id: downloadJobId.value.trim(),
    proposed_operation: operation.value,
    source_manifest: {
      source_root_ref: sourceRootRef.value.trim(),
      files: files.map((file) => ({
        relative_path: file.sourcePath,
        size_bytes: file.sizeBytes as number,
      })),
    },
    target_mapping: {
      target_root_ref: targetRootRef.value.trim(),
      files: files
        .filter((file) => file.includeInMapping)
        .map((file) => ({
          source_relative_path: file.sourcePath,
          target_relative_path: file.targetPath,
        })),
      source_retention: true,
      overwrite: false,
    },
  }
  const created = await store.create(payload)
  if (created) await router.push(`/media-imports/${created.id}`)
}
</script>

<template>
  <section class="page wide-page media-import-page">
    <PageHeader
      eyebrow="MEDIA IMPORT PLAN"
      title="创建入库规划"
      description="为已核验的下载任务提交不可变文件清单和目标映射。"
    >
      <RouterLink class="button secondary" to="/media-imports">返回规划列表</RouterLink>
    </PageHeader>

    <div class="phase-banner media-import-safety"><span>仅规划</span>仅规划，不操作媒体文件</div>

    <form class="media-import-create" @submit.prevent="submit">
      <section class="media-import-form-section">
        <div class="media-import-section-heading">
          <div><span class="eyebrow">BINDING</span><h2>任务与根引用</h2></div>
          <small>operator 可创建；服务端会重新核对下载、执行、审批和目标白名单。</small>
        </div>
        <div class="media-import-field-grid">
          <label>
            <span>下载任务 ID</span>
            <input v-model="downloadJobId" aria-label="下载任务 ID" autocomplete="off" />
          </label>
          <label>
            <span>源根引用</span>
            <input v-model="sourceRootRef" aria-label="源根引用" autocomplete="off" placeholder="与下载任务 save_path_ref 一致" />
          </label>
          <label>
            <span>目标根引用</span>
            <input v-model="targetRootRef" aria-label="目标根引用" autocomplete="off" placeholder="服务端白名单中的内部引用" />
          </label>
        </div>
      </section>

      <section class="media-import-form-section">
        <div class="media-import-section-heading">
          <div><span class="eyebrow">OPERATION</span><h2>规划方式</h2></div>
          <small>这里只记录建议操作，不授权执行。</small>
        </div>
        <div class="segmented-control import-operation-control" role="group" aria-label="规划方式">
          <button type="button" :class="{ active: operation === 'HARDLINK' }" @click="operation = 'HARDLINK'">
            <strong>硬链接</strong><small>HARDLINK</small>
          </button>
          <button type="button" :class="{ active: operation === 'COPY' }" @click="operation = 'COPY'">
            <strong>复制</strong><small>COPY</small>
          </button>
        </div>
        <div class="fixed-safety-settings">
          <label><input type="checkbox" checked disabled /> 保留下载源文件</label>
          <label><input type="checkbox" :checked="false" disabled /> 允许覆盖目标文件</label>
        </div>
      </section>

      <section class="media-import-form-section file-mapping-section">
        <div class="media-import-section-heading">
          <div><span class="eyebrow">FILE MAPPING</span><h2>文件清单与目标映射</h2></div>
          <button type="button" class="button secondary small" @click="addFile">+ 添加文件</button>
        </div>
        <div class="mapping-heading" aria-hidden="true">
          <span>源相对路径</span><span>大小（字节）</span><span>加入映射</span><span>目标相对路径</span><span></span>
        </div>
        <div v-for="(file, index) in files" :key="index" class="mapping-row">
          <label><span>源相对路径</span><input v-model="file.sourcePath" :aria-label="`源相对路径 ${index + 1}`" autocomplete="off" /></label>
          <label><span>大小（字节）</span><input v-model.number="file.sizeBytes" :aria-label="`文件大小 ${index + 1}`" type="number" min="0" step="1" /></label>
          <label class="mapping-choice"><span>加入目标映射</span><input v-model="file.includeInMapping" :aria-label="`加入目标映射 ${index + 1}`" type="checkbox" /></label>
          <label><span>目标相对路径</span><input v-model="file.targetPath" :aria-label="`目标相对路径 ${index + 1}`" autocomplete="off" :disabled="!file.includeInMapping" /></label>
          <button type="button" class="mapping-remove" :disabled="files.length === 1" :aria-label="`移除文件 ${index + 1}`" title="移除文件" @click="removeFile(index)">×</button>
        </div>
      </section>

      <div v-if="submitted && validationError" class="notice-state warning-state" role="alert">{{ validationError }}</div>
      <div v-if="store.actionError" class="notice-state error-state" role="alert">{{ store.actionError }}</div>

      <footer class="media-import-submit-bar">
        <div><strong>输出：不可变入库计划</strong><small>创建后等待内部受信只读预检；不会扫描或改动媒体文件。</small></div>
        <button class="button primary" :disabled="store.working" type="submit">{{ store.working ? '创建中…' : '创建入库规划' }}</button>
      </footer>
    </form>
  </section>
</template>
