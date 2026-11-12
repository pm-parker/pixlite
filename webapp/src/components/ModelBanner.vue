<script setup>
/* 首次使用时的 AI 模型下载进度条 (模型由服务端后台自动下载, 用户无需任何操作)。
   全部就绪后自动隐藏; 下载失败时显示原因并可一键重试 (服务端也会每 5 分钟自动重试)。 */
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { API, modelState } from '../lib.js'

const busy = ref(false)
let timer = null

async function poll() {
  try {
    const s = await API.modelsStatus()
    Object.assign(modelState, s, { checked: true })
  } catch (e) { /* 服务重启中, 下次再试 */ }
  if (modelState.ready) { clearInterval(timer); timer = null }
}
async function retry() {
  busy.value = true
  try { await API.modelsRetry() } finally { busy.value = false; poll() }
}
const fmt = b => b >= 1e9 ? (b / 1e9).toFixed(2) + ' GB' : (b / 1e6).toFixed(0) + ' MB'
const eta = computed(() => {
  const s = modelState.speed_bps
  if (!s || modelState.phase !== 'downloading') return ''
  const left = (modelState.total_bytes - modelState.done_bytes) / s
  return left > 90 ? `约 ${Math.ceil(left / 60)} 分钟` : `约 ${Math.ceil(left)} 秒`
})

onMounted(() => { poll(); timer = setInterval(poll, 2000) })
onBeforeUnmount(() => timer && clearInterval(timer))
</script>

<template>
  <div v-if="modelState.checked && !modelState.ready" class="mb" :class="{ fail: modelState.phase === 'failed' }">
    <template v-if="modelState.phase !== 'failed'">
      <div class="mb-text">
        正在自动下载 AI 修图模型（首次使用，仅需一次）
        <b>{{ modelState.percent }}%</b>
        <span class="mb-sub">{{ fmt(modelState.done_bytes) }} / {{ fmt(modelState.total_bytes) }}
          <template v-if="eta"> · 剩余{{ eta }}</template></span>
      </div>
      <div class="mb-bar"><div class="mb-fill" :style="{ width: modelState.percent + '%' }"></div></div>
      <div class="mb-sub">下载完成前可以先浏览和整理照片，精修功能将在完成后自动可用</div>
    </template>
    <template v-else>
      <div class="mb-text">AI 模型下载暂未成功，系统会每 5 分钟自动重试
        <el-button size="small" text :loading="busy" @click="retry">立即重试</el-button></div>
      <div class="mb-sub">{{ modelState.error }}</div>
    </template>
  </div>
</template>

<style scoped>
.mb { position: fixed; left: 50%; bottom: 18px; transform: translateX(-50%); z-index: 3000;
      width: min(560px, calc(100vw - 32px)); padding: 12px 16px; border-radius: 10px;
      background: var(--px-bg-3, #191919); border: 1px solid var(--px-border, #2a2a2a);
      box-shadow: 0 8px 24px rgba(0,0,0,.45); color: var(--px-text, #fff); font-size: 13px; }
.mb.fail { border-color: #8a3a2a; }
.mb-text { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.mb-text b { color: var(--px-accent, #3187ff); }
.mb-sub { color: var(--px-text-3, #888); font-size: 12px; margin-top: 4px; }
.mb-bar { height: 4px; border-radius: 2px; background: var(--px-bg-4, #232323); margin-top: 8px; overflow: hidden; }
.mb-fill { height: 100%; background: var(--px-accent, #3187ff); transition: width .6s ease; }
</style>
