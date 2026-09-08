<script setup lang="ts">
import { computed } from 'vue'

import { statusLabel } from '../utils/format'

/**
 * `label` overrides the default lookup.  Callers have been passing it for a
 * while and it was silently ignored, which is why library items kept showing
 * READY / CANDIDATES in English: the same key means different things for a
 * download and for a media item, so the caller has to be able to say which.
 */
const props = defineProps<{ status: string; label?: string }>()
const stateClass = computed(() => `state-${props.status.toLowerCase().replaceAll('_', '-')}`)
const text = computed(() => props.label ?? statusLabel(props.status))
</script>

<template><span class="status-pill" :class="stateClass">{{ text }}</span></template>
