<script setup lang="ts">
import { computed } from 'vue'

import { statusLabel } from '../utils/format'

const props = defineProps<{ status: string }>()
const stateClass = computed(() => `state-${props.status.toLowerCase().replace('_', '-')}`)
</script>

<template><span class="status-pill" :class="stateClass">{{ statusLabel(status) }}</span></template>

