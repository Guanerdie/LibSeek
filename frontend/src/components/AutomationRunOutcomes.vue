<script setup lang="ts">
import type { AutomationRun } from '../types'
import { automationOutcomeEntries } from '../utils/automation'

defineProps<{ run: AutomationRun }>()
</script>

<template>
  <span
    v-for="entry in automationOutcomeEntries(run)"
    :key="entry.key"
    :class="entry.key === 'downloaded' ? 'success-text' : 'muted'"
  >{{ entry.label }} {{ entry.count }} 项</span>
  <span v-if="run.failed" class="danger-text">失败 {{ run.failed }} 项</span>
  <span v-if="run.deferred" class="warning-text">等待后续 {{ run.deferred }} 项</span>
</template>
