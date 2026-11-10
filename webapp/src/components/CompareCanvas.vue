<script setup>

/* 结果图加载失败(服务重启/job失效) → 回退原图并提示, 不显示破图 */
function onImgError(e) {
  const im = e.target
  if (im.classList.contains('cv-fallback')) return
  im.classList.add('cv-fallback')
  if (props.srcUrl) im.src = props.srcUrl
  import('element-plus').then(({ ElMessage }) => ElMessage.warning('精修结果已失效（服务可能重启过），已回退原图'))
}

/* 画布：缩放平移 + 按住对比原图 + 处理进度遮罩 */
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import PxIcon from './PxIcon.vue'

const props = defineProps({
  srcUrl: String,        // 原图
  resultUrl: String,     // 精修结果（null = 处理中/未出图）
  processing: Number,    // 0-1 进度，null 不显示
})
const emit = defineEmits(['zoom-change'])

const wrap = ref(null)
const scale = ref(1)
const tx = ref(0), ty = ref(0)
const dragging = ref(false)
const showSrc = ref(false)
let dragStart = null

function fit() {
  const el = wrap.value
  if (!el) return
  // 等比适配: 以容器 85% 高度/宽度为限, 按原图宽高比算基准显示宽
  const im = el.querySelector('.cv-img')
  const iw = im?.naturalWidth || 1200, ih = im?.naturalHeight || 1800
  const s = Math.min((el.clientWidth * 0.92) / iw, (el.clientHeight * 0.92) / ih, 1)
  scale.value = Math.max(0.05, Math.round(s * 100) / 100)
  tx.value = 0; ty.value = 0
  emit('zoom-change', scale.value)
}
function clampScale(s) { return Math.min(8, Math.max(0.05, s)) }
function zoomAt(factor, cx, cy) {
  const old = scale.value
  const ns = clampScale(old * factor)
  const k = ns / old
  // 围绕鼠标点缩放
  const rect = wrap.value.getBoundingClientRect()
  const mx = cx - rect.left - rect.width / 2
  const my = cy - rect.top - rect.height / 2
  tx.value = mx - (mx - tx.value) * k
  ty.value = my - (my - ty.value) * k
  scale.value = ns
  emit('zoom-change', ns)
}
function onWheel(e) {
  e.preventDefault()
  zoomAt(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX, e.clientY)
}
function onDown(e) {
  dragging.value = true
  dragStart = { x: e.clientX, y: e.clientY, tx: tx.value, ty: ty.value }
}
function onMove(e) {
  if (!dragging.value) return
  tx.value = dragStart.tx + (e.clientX - dragStart.x)
  ty.value = dragStart.ty + (e.clientY - dragStart.y)
}
function onUp() { dragging.value = false }

function zoomIn() { zoomAt(1.25, wrap.value.getBoundingClientRect().left + wrap.value.getBoundingClientRect().width / 2, wrap.value.getBoundingClientRect().top + wrap.value.getBoundingClientRect().height / 2) }
function zoomOut() { zoomAt(1 / 1.25, wrap.value.getBoundingClientRect().left + wrap.value.getBoundingClientRect().width / 2, wrap.value.getBoundingClientRect().top + wrap.value.getBoundingClientRect().height / 2) }
function fitScreen() { fit() }
function actualSize() {
  scale.value = 1; tx.value = 0; ty.value = 0
  emit('zoom-change', 1)
}

onMounted(() => {
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
})
onBeforeUnmount(() => {
  window.removeEventListener('mousemove', onMove)
  window.removeEventListener('mouseup', onUp)
})

defineExpose({ fitScreen, zoomIn, zoomOut, actualSize })
const zoomPct = computed(() => Math.round(scale.value * 100) + '%')
</script>

<template>
  <div ref="wrap" class="cv" :class="{ grab: !dragging, grabbing: dragging }"
       @wheel="onWheel" @mousedown.prevent="onDown"
       @mousedown.middle.prevent="showSrc = true" @mouseup.middle="showSrc = false">
    <div class="cv-inner" :style="{ transform: `translate(${tx}px, ${ty}px) scale(${scale})` }">
      <img v-if="resultUrl && !showSrc" :src="resultUrl" class="cv-img" draggable="false"
           @error="onImgError" />
      <img v-else-if="srcUrl" :src="srcUrl" class="cv-img" draggable="false"
           @error="onImgError" />
    </div>

    <!-- 处理中遮罩 -->
    <div v-if="processing != null" class="cv-mask">
      <div class="cv-mask-card">
        <div class="cv-spinner"></div>
        <div class="cv-mask-label">AI 精修中… {{ Math.round((processing || 0) * 100) }}%</div>
        <div class="cv-bar"><div class="cv-bar-fill" :style="{ width: ((processing || 0) * 100) + '%' }"></div></div>
      </div>
    </div>

    <!-- 缩放指示 -->
    <div class="cv-zoom">{{ zoomPct }}</div>

    <!-- 按住对比按钮 -->
    <button class="cv-compare" @mousedown.prevent="showSrc = true"
            @mouseup="showSrc = false" @mouseleave="showSrc = false"
            @touchstart.prevent="showSrc = true" @touchend="showSrc = false">
      <PxIcon name="rotate-ccw" :size="14"/> 按住对比
    </button>
    <div v-if="showSrc" class="cv-src-tag">原图</div>
    <div v-else-if="resultUrl" class="cv-src-tag out">效果</div>
  </div>
</template>

<style scoped>
.cv { position: absolute; inset: 0; overflow: hidden; background:
  radial-gradient(1200px 700px at 50% 40%, #15171c 0%, var(--px-bg-0) 70%);
  user-select: none; }
.cv.grab { cursor: grab; }
.cv.grabbing { cursor: grabbing; }
.cv-inner { position: absolute; left: 50%; top: 50%;
  transform-origin: center center; display: flex; align-items: center; justify-content: center; }
.cv-img { max-width: 78vw; max-height: 68vh; object-fit: contain;
  border-radius: 2px; box-shadow: 0 8px 40px rgba(0,0,0,.6), 0 0 0 1px rgba(255,255,255,.05);
  transform: translate(-50%, -50%); position: relative; left: -0px; }
.cv-zoom { position: absolute; left: 12px; bottom: 10px; font-size: 11px; color: var(--px-text-2);
  background: rgba(0,0,0,.5); padding: 2px 8px; border-radius: 4px; border: 1px solid rgba(255,255,255,.06);
  backdrop-filter: blur(4px); }
.cv-compare { position: absolute; right: 14px; bottom: 12px; display: inline-flex; align-items: center; gap: 6px;
  background: rgba(20,21,27,.88); border: 1px solid var(--px-line-2); color: var(--px-text);
  border-radius: 18px; padding: 7px 16px; font-size: 12px; cursor: pointer; backdrop-filter: blur(8px);
  box-shadow: var(--px-shadow-2); transition: all var(--px-dur) var(--px-ease); }
.cv-compare:hover { border-color: var(--px-accent); color: var(--px-accent-2); }
.cv-compare:active { background: var(--px-accent-weak); }
.cv-src-tag { position: absolute; right: 14px; bottom: 52px; font-size: 11px; padding: 2px 10px;
  border-radius: 10px; background: rgba(0,0,0,.55); color: #f2c97d; }
.cv-src-tag.out { color: #9ce7b4; }
.cv-mask { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  background: rgba(8,9,12,.55); backdrop-filter: blur(2px); z-index: 5; }
.cv-mask-card { display: flex; flex-direction: column; align-items: center; gap: 10px;
  background: rgba(18,19,25,.94); border: 1px solid var(--px-line-2); border-radius: var(--px-radius-lg);
  padding: 22px 34px; min-width: 240px; box-shadow: var(--px-shadow-3);
  backdrop-filter: blur(10px); }
.cv-spinner { width: 30px; height: 30px; border-radius: 50%;
  border: 3px solid var(--px-accent-weak); border-top-color: var(--px-accent); animation: spin 0.9s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.cv-mask-label { font-size: 13px; color: var(--px-text); }
.cv-bar { width: 100%; height: 4px; border-radius: 2px; background: #2c303b; overflow: hidden; }
.cv-bar-fill { height: 100%; background: linear-gradient(90deg, #8a78ff, #6c5ce7); transition: width .3s; }
</style>
