<script setup>
/* 统一图标组件 — Iconify 接 Lucide 线性图标集（API 按需加载 + 浏览器 localStorage 缓存）。
   用法：<PxIcon name="zoom-in" :size="16" />  name 即 lucide 图标名 */
import { computed } from 'vue'
import { Icon } from '@iconify/vue'

const props = defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 16 },
})
const icon = computed(() => props.name.includes(':') ? props.name : 'lucide:' + props.name)
</script>

<template>
  <Icon :icon="icon" :width="size" :height="size" class="pxi" />
</template>

<style>
.pxi { display: inline-block; vertical-align: -0.125em; flex: none; }
</style>
