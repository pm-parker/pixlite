<script setup>
/* 右侧功能面板 — 实机对齐（pixcake 10.0.5）：
   多板块折叠列表（人像美化/调色/局部/.../历史）
   人像美化 = 人物属性 chips + 11 横向 Tab
   调色 = 17 纵向子 tab + EXIF 条 + 多图色彩统一 */
import { ref, reactive, watch, onMounted, computed } from 'vue'
import { BEAUTY_TABS, RIGHT_PANEL_SECTIONS, COLOR_SUBTABS,
         HSL_BANDS, WB_PRESETS, defaultOverrides } from '../lib.js'
import PxIcon from './PxIcon.vue'

const props = defineProps({
  modelValue: { type: Object, required: true }, // overrides
  preset: String,
  presets: Array,
  resultUrl: String,
  srcUrl: String,
  exif: { type: Object, default: null },   // {iso, focal, aperture, shutter}
})
const emit = defineEmits(['update:modelValue', 'change', 'update:preset'])

/* 板块折叠：默认展开人像美化。AI色彩/调色共用一个面板，开一个关另一个 */
const openSections = reactive({ beauty: true })
const SHARED = { color: 'color', adjust: 'color' }   // adjust 与 color 指向同一 key
function secKeyOf(id) { return SHARED[id] || id }
function isOpen(id) { return !!openSections[secKeyOf(id)] }
function toggleSection(id) {
  const k = secKeyOf(id)
  if (k === 'color') {
    // AI色彩/调色共用面板：同一板块再点=收起；另一个板块=保持展开并切换归属
    const wasThis = openSections._opened === id && openSections.color
    openSections.color = !wasThis
    openSections._opened = id
  } else openSections[k] = !openSections[k]
}
/* 人像美化内部状态 */
const beautyTab = ref('bt1')
const curTab = computed(() => BEAUTY_TABS.find(t => t.id === beautyTab.value) || BEAUTY_TABS[0])
const secOpen = reactive({})          // `${tabId}.${secName}` -> bool（默认开）
const smoothMethod = ref('neutral_flat') // 磨皮质感方式
function secKey(t, s) { return t + '.' + s }
function isSecOpen(k) { return secOpen[k] !== false }
function toggleSec(k) { secOpen[k] = !secOpen[k] }

function val(k, def = 0) { return props.modelValue[k] ?? def }
function setVal(k, v) {
  emit('update:modelValue', { ...props.modelValue, [k]: v })
  emit('change', k)
}

function isWip(p) { return !p || p.key.startsWith('__wip') }
const curSmooth = computed(() => curTab.value.sections.flatMap(s => s.method || []).find(m => m.id === smoothMethod.value))

/* 图像板块: 画质修复 + 背景（engine 已实装） */
const imageParams = [
  { key: 'quality_enhance.degree', label: '画质修复(超清)', min: 0, max: 100 },
]
const bgParams = [
  { key: 'blur_background.degree', label: '背景虚化', min: 0, max: 100 },
  { key: 'sky_enhance.degree', label: '天空增强', min: 0, max: 100 },
  { key: 'sky_replace.degree', label: '天空替换(日落)', min: 0, max: 100 },
]

/* 色彩子 tab */
const colorTab = ref('tone')

function resetTab() {
  const patch = {}
  for (const sec of curTab.value.sections) {
    for (const p of (sec.params || [])) patch[p.key] = p.def
    for (const m of (sec.method || [])) patch[m.param.key] = m.param.def
  }
  emit('update:modelValue', { ...props.modelValue, ...patch })
  emit('change', 'reset:tab')
}

/* 人物属性（实机：女/男/儿童/长辈女/长辈男 + 单人/所有人） */
const persons = [
  { id: 'female', label: '女' }, { id: 'male', label: '男' }, { id: 'child', label: '儿童' },
  { id: 'elder_f', label: '长辈女' }, { id: 'elder_m', label: '长辈男' },
]
const scopes = [{ id: 'single', label: '单人' }, { id: 'all', label: '所有人' }]

/* HSL */
const hslBand = ref(0)
function setHsl(f, v) { setVal(`hsl.${hslBand.value}.${f}`, v) }

/* 配置文件 */
const profiles = [{ id: 'standard', label: '标准' }, { id: 'texture', label: '质感' },
                  { id: 'bw', label: '黑白' }, { id: 'basic', label: '基础' }]
const curProfile = computed(() => val('color.profile', 'standard'))
function pickProfile(id) { setVal('color.profile', id) }

/* 滤镜 */
const filters = ref([])
onMounted(async () => {
  try {
    filters.value = await fetch('/api/filters').then(r => r.json())
  } catch (e) { filters.value = [] }
})

/* 原厂 24 套整套妆 */
const makeupSuits = ref([])
onMounted(async () => {
  try {
    makeupSuits.value = await fetch('/api/makeup_suits').then(r => r.json())
  } catch (e) { makeupSuits.value = [] }
})
const suitOpen = computed(() => val('makeup_suit.suit_id', '') !== '')
function pickSuit(id) { setVal('makeup_suit.suit_id', id); if (id && !val('makeup_suit.degree', 0)) setVal('makeup_suit.degree', 70) }
const shownFilters = computed(() => filters.value.slice(0, 60))
function pickFilter(f) { setVal('lut.filter', f.id) }
function clearFilter() { setVal('lut.filter', '') }

/* F6 曲线编辑器（SVG 拖点） */
const curveChannels = [{ id: 'rgb', name: 'RGB' }, { id: 'r', name: '红' }, { id: 'g', name: '绿' }, { id: 'b', name: '蓝' }]
const curveColor = { rgb: '#e8eaf0', r: '#e05555', g: '#59c26a', b: '#5588e0' }
const curveCh = ref('rgb')
const IDENT_PTS = [[0, 0], [64, 64], [128, 128], [192, 192], [255, 255]]
function curveKey() { return 'curves.' + curveCh.value }
function loadPts() {
  const v = props.modelValue[curveKey()]
  if (Array.isArray(v) && v.length >= 2) return v.map(p => [p[0], p[1]])
  return IDENT_PTS.map(p => [p[0], p[1]])
}
const curvePts = computed(() => loadPts())
const curvePath = computed(() => curvePts.value.map(p => `${p[0]},${255 - p[1]}`).join(' '))
const curveSvg = ref(null)
function savePts(pts) { setVal(curveKey(), pts.map(p => [Math.round(p[0]), Math.round(p[1])])) }
function curveMouseDown(ev) {
  const svg = curveSvg.value; if (!svg) return
  const rect = svg.getBoundingClientRect()
  const toPt = (e) => {
    const x = (e.clientX - rect.left) / rect.width * 256
    const y = 255 - (e.clientY - rect.top) / rect.height * 255
    return [Math.max(0, Math.min(255, x)), Math.max(0, Math.min(255, y))]
  }
  let pts = loadPts()
  const start = toPt(ev)
  let idx = -1, best = 20
  pts.forEach((p, i) => { const d = Math.hypot(p[0] - start[0], p[1] - start[1]); if (d < best) { best = d; idx = i } })
  if (idx === -1) { pts.push(start); pts.sort((a, b) => a[0] - b[0]); idx = pts.findIndex(p => p[0] === start[0] && p[1] === start[1]) }
  const move = (e) => {
    const [x, y] = toPt(e)
    const lo = idx === 0 ? 0 : pts[idx - 1][0] + 2
    const hi = idx === pts.length - 1 ? 255 : pts[idx + 1][0] - 2
    pts[idx] = [Math.max(lo, Math.min(hi, x)), y]
    savePts(pts)
  }
  const up = () => {
    window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up)
  }
  window.addEventListener('mousemove', move); window.addEventListener('mouseup', up)
}
function resetCurve() { setVal(curveKey(), '') }

/* F6 颜色分级色轮（二维拖盘） */
const gradingZones = [{ id: 'shadow', name: '阴影' }, { id: 'mid', name: '中间调' }, { id: 'high', name: '高光' }]
const wheelEls = reactive({})
const wheelBg = 'background: conic-gradient(#e05555,#e0a655,#d5e055,#7ac95f,#55c9a8,#557fe0,#9a6ae0,#e05555)'
function wheelDot(z) {
  const h = val(`color_grading.${z}.h`, 0), s = val(`color_grading.${z}.s`, 0) / 100
  const ang = (h - 90) * Math.PI / 180
  const r = s * 42
  return { left: `calc(50% + ${Math.cos(ang) * r}px)`, top: `calc(50% + ${Math.sin(ang) * r}px)` }
}
function wheelDown(ev, z) {
  const el = wheelEls[z]; if (!el) return
  const rect = el.getBoundingClientRect()
  const move = (e) => {
    const dx = e.clientX - rect.left - rect.width / 2
    const dy = e.clientY - rect.top - rect.height / 2
    const r = Math.hypot(dx, dy) / (rect.width / 2)
    let h = Math.atan2(dy, dx) * 180 / Math.PI + 90
    if (h > 180) h -= 360
    setVal(`color_grading.${z}.h`, Math.round(h))
    setVal(`color_grading.${z}.s`, Math.round(Math.min(1, r) * 100))
  }
  const up = () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up) }
  window.addEventListener('mousemove', move); window.addEventListener('mouseup', up)
  move(ev)
}

/* F6 可选颜色 */
const SC_COLORS = [
  { id: 'red', name: '红色', c: '#e05555' }, { id: 'yellow', name: '黄色', c: '#e0d155' },
  { id: 'green', name: '绿色', c: '#7ac95f' }, { id: 'cyan', name: '青色', c: '#55c9a8' },
  { id: 'blue', name: '蓝色', c: '#557fe0' }, { id: 'magenta', name: '洋红', c: '#e05ab0' },
  { id: 'white', name: '白色', c: '#f0f0f0' }, { id: 'neutral', name: '中性色', c: '#9a9a9a' },
  { id: 'black', name: '黑色', c: '#333' },
]
const scColor = ref('red')
const scAbsolute = ref(false)
watch(scAbsolute, v => {
  for (const c of SC_COLORS) setVal(`selective_color.${c.id}.absolute`, v)
})

/* F6 色彩编辑器 */
const ceBand = ref(0)

/* F5 局部调色蒙版 */
const MASKS = [
  { id: '', name: '全局' }, { id: 'subject', name: '主体' }, { id: 'background', name: '背景' },
  { id: 'skin', name: '皮肤' }, { id: 'hair', name: '头发' }, { id: 'face', name: '面部' },
  { id: 'sky', name: '天空' },
]
const curMask = ref('')
const MASKED_PREFIXES = ['tone.', 'levels.', 'curves.', 'color_grading.', 'selective_color.', 'color_editor.', 'white_balance.', 'hsl.']
const _setValBase = setVal
setVal = (k, v) => {
  // F5: 蒙版模式下，调色类参数写入 mask.<region>.<key>
  if (curMask.value && MASKED_PREFIXES.some(p => k.startsWith(p))) {
    if (v === 0 || v === '' || v === undefined) return
    return _setValBase(`mask.${curMask.value}.${k}`, v)
  }
  return _setValBase(k, v)
}

/* 直方图（真实采样） */
const histCanvas = ref(null)
let histSeq = 0
async function drawHistogram() {
  const cv = histCanvas.value
  if (!cv) return
  const my = ++histSeq
  const ctx = cv.getContext('2d')
  const w = cv.width, h = cv.height
  ctx.fillStyle = '#14151a'
  ctx.fillRect(0, 0, w, h)
  const url = props.resultUrl || props.srcUrl
  let bins = null
  if (url) {
    try {
      const img = await new Promise((res, rej) => {
        const i = new Image(); i.crossOrigin = 'anonymous'
        i.onload = () => res(i); i.onerror = rej; i.src = url
      })
      const c = document.createElement('canvas')
      const sw = 120, sh = Math.max(1, Math.round(img.height / img.width * 120))
      c.width = sw; c.height = sh
      c.getContext('2d').drawImage(img, 0, 0, sw, sh)
      const d = c.getContext('2d').getImageData(0, 0, sw, sh).data
      bins = [new Array(64).fill(0), new Array(64).fill(0), new Array(64).fill(0)]
      for (let i = 0; i < d.length; i += 4) {
        bins[0][d[i] >> 2]++; bins[1][d[i + 1] >> 2]++; bins[2][d[i + 2] >> 2]++
      }
    } catch (e) { bins = null }
  }
  if (my !== histSeq || !bins) return
  const colors = ['#e05555', '#59c26a', '#5588e0']
  const max = Math.max(...bins[0], ...bins[1], ...bins[2], 1)
  ctx.globalCompositeOperation = 'lighter'
  for (let ch = 0; ch < 3; ch++) {
    ctx.beginPath()
    ctx.moveTo(0, h)
    for (let i = 0; i < 64; i++) {
      const x = i / 63 * w
      const y = h - Math.pow(bins[ch][i] / max, 0.5) * (h - 4)
      ctx.lineTo(x, y)
    }
    ctx.lineTo(w, h)
    ctx.closePath()
    ctx.fillStyle = colors[ch] + '88'
    ctx.fill()
  }
  ctx.globalCompositeOperation = 'source-over'
}
watch(() => [props.resultUrl, props.srcUrl], () => {
  if (colorTab.value === 'tone') setTimeout(drawHistogram, 150)
})
watch(colorTab, t => { if (t === 'tone') setTimeout(drawHistogram, 100) })

function aiCalibrate(kind) {
  setVal('ai_calibrate.' + kind, !val('ai_calibrate.' + kind, false))
}

/* AI 追色上传 */
const refUp = ref(null)
const refInfo = ref(null)
async function onRefFile(ev) {
  const f = ev.target.files?.[0]
  if (!f) return
  const fd = new FormData(); fd.append('file', f, f.name)
  try {
    const r = await fetch('/api/ref', { method: 'POST', body: fd }).then(r => r.json())
    refInfo.value = r
    setVal('color_match.ref_path', r.path)
  } catch (e) { /* noop */ }
  ev.target.value = ''
}

/* 多图色彩统一 */
const unifyBusy = ref(false)
async function unifyMulti() {
  unifyBusy.value = true
  try { await new Promise(r => setTimeout(r, 900)); emit('change', 'unify_multi') } finally { unifyBusy.value = false }
}

function resetAll() {
  emit('update:modelValue', defaultOverrides())
  emit('change', 'reset:all')
}
</script>

<template>
  <div class="pm">
    <!-- ============ 板块折叠列表 ============ -->
    <el-scrollbar class="pm-scroll">
      <div v-for="sec in RIGHT_PANEL_SECTIONS" :key="sec.id" class="pm-sec">
        <div class="pm-sec-head" @click="toggleSection(sec.id)">
          <PxIcon name="chevron-right"/>
          <span class="pm-sec-name">{{ sec.name }}</span>
          <span v-if="sec.id === 'beauty' || sec.id === 'color' || sec.id === 'adjust'" class="pm-sec-badge">AI</span>
        </div>

        <!-- ===== 人像美化 ===== -->
        <div v-if="openSections.beauty && sec.id === 'beauty'" class="pm-sec-body">
          <!-- 人物属性 -->
          <div class="pm-person">
            <div class="pm-chips">
              <span v-for="p in persons" :key="p.id" class="px-chip"
                    :class="{ on: val('__person','female') === p.id }"
                    @click="setVal('__person', p.id)">{{ p.label }}</span>
              <span class="pm-scope-sep"></span>
              <span v-for="s in scopes" :key="s.id" class="px-chip sm"
                    :class="{ on: val('__scope','single') === s.id }"
                    @click="setVal('__scope', s.id)">{{ s.label }}</span>
            </div>
          </div>

          <!-- 11 横向 Tab（可横滑） -->
          <div class="pm-tabs-x">
            <div v-for="t in BEAUTY_TABS" :key="t.id" class="pm-tab-x"
                 :class="{ on: beautyTab === t.id }" @click="beautyTab = t.id">{{ t.name }}</div>
          </div>

          <!-- 当前 Tab 内容 -->
          <div class="pm-tab-body">
            <div class="pm-tab-toolbar">
              <span class="pm-tab-name">{{ curTab.name }}</span>
              <el-button size="small" text @click="resetTab">重置本页</el-button>
            </div>

            <div v-for="s in curTab.sections" :key="s.name" class="pm-card">
              <div class="pm-card-head" @click="toggleSec(secKey(curTab.id, s.name))">
                <PxIcon name="chevron-right"/>
                <span class="pm-card-name">{{ s.name }}</span>
              </div>
              <div v-show="isSecOpen(secKey(curTab.id, s.name))" class="pm-params">

                <!-- 磨皮质感：7 种方式 -->
                <template v-if="s.method">
                  <div class="pm-methods">
                    <span v-for="m in s.method" :key="m.id" class="px-chip sm"
                          :class="{ on: smoothMethod === m.id }"
                          :title="isWip(m.param) ? '引擎开发中' : ''"
                          @click="smoothMethod = m.id">{{ m.label }}</span>
                  </div>
                  <div class="pm-param" :class="{ wip: isWip(curSmooth?.param) }">
                    <div class="pm-param-row">
                      <span class="pm-param-label">程度</span>
                      <span v-if="isWip(curSmooth?.param)" class="pm-wip-tag">开发中</span>
                      <el-input-number v-else-if="curSmooth" :model-value="val(curSmooth.param.key, 0)"
                        :min="-100" :max="100" size="small" controls-position="right" style="width: 88px"
                        @update:model-value="v => setVal(curSmooth.param.key, v)" />
                    </div>
                    <el-slider :model-value="isWip(curSmooth?.param) ? 0 : val(curSmooth?.param?.key, 0)"
                               :min="-100" :max="100" :disabled="isWip(curSmooth?.param)"
                               @update:model-value="v => curSmooth && setVal(curSmooth.param.key, v)" />
                  </div>
                </template>

                <!-- 整套妆选择器 (原厂 24 套) -->
                <template v-if="s.select">
                  <div class="pm-param">
                    <div class="pm-param-row">
                      <span class="pm-param-label">选择整套妆</span>
                      <el-select :model-value="val(s.select, '')" size="small"
                                 style="width: 150px" clearable placeholder="选择套装"
                                 @update:model-value="v => pickSuit(v || '')">
                        <el-option v-for="su in makeupSuits" :key="su.id" :value="su.id"
                                   :label="su.id + (su.colored ? ' ★' : '')"
                                   :title="`条目 ${su.n_entries} · 有色配方 ${su.colored} · ${su.parts.join('/')}`" />
                      </el-select>
                    </div>
                    <div class="pm-suit-hint" v-if="suitOpen">★ = 含 MixColor 原色配方 · 其余用部位默认妆色</div>
                  </div>
                </template>

                <!-- 普通参数组：Slider + 数值框 -->
                <template v-if="s.params">
                  <div v-for="p in s.params" :key="p.key + p.label" class="pm-param" :class="{ wip: isWip(p) }">
                    <div class="pm-param-row">
                      <span class="pm-param-label">{{ p.label }}</span>
                      <span v-if="isWip(p)" class="pm-wip-tag" title="引擎开发中">开发中</span>
                      <el-input-number v-else :model-value="val(p.key, p.def)" :min="p.min" :max="p.max"
                        :step="p.step || 1" size="small" controls-position="right" style="width: 88px"
                        @update:model-value="v => setVal(p.key, v)" />
                    </div>
                    <el-slider :model-value="isWip(p) ? p.def : val(p.key, p.def)"
                               :min="p.min" :max="p.max" :step="p.step || 1"
                               :disabled="isWip(p)"
                               @update:model-value="v => !isWip(p) && setVal(p.key, v)" />
                  </div>
                </template>

                <!-- 保护开关 -->
                <div v-if="s.toggles" class="pm-toggles">
                  <div v-for="tg in s.toggles" :key="tg" class="pm-toggle">
                    <span class="pm-param-label">{{ tg }}</span>
                    <el-switch :model-value="false" disabled />
                    <span class="pm-wip-tag">开发中</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- ===== 调色（17 子 tab，AI色彩+调色两个板块共用） ===== -->
        <div v-if="openSections.color && openSections._opened === sec.id && (sec.id === 'color' || sec.id === 'adjust')" class="pm-sec-body pm-color">
          <!-- 打开另一个共用板块时关闭另一个 -->
          <!-- eslint-disable-next-line -->
          <!-- EXIF 条 (F2 真实数据) -->
          <div class="pm-exif">
            <span v-if="exif && exif.iso">
              {{ exif.model || exif.camera || '' }} · ISO {{ exif.iso }} · {{ exif.focal || '-' }}mm · F{{ exif.aperture || '-' }} · {{ exif.shutter || '-' }}秒
            </span>
            <span v-else-if="exif" class="pm-exif-none">{{ exif.width }}×{{ exif.height }} · 无EXIF信息</span>
            <span v-else class="pm-exif-none">EXIF 未读取</span>
            <el-button size="small" text type="primary" :loading="unifyBusy" @click="unifyMulti">多图色彩统一</el-button>
          </div>
          <!-- F5 局部调色蒙版选择器 -->
          <div class="pm-maskbar">
            <span class="pm-maskbar-label">调色范围</span>
            <span v-for="mk in MASKS" :key="mk.id" class="px-chip sm"
                  :class="{ on: curMask === mk.id }" @click="curMask = mk.id">{{ mk.name }}</span>
          </div>
          <div class="pm-color-wrap">
            <!-- 纵向子 tab -->
            <el-scrollbar class="pm-subtabs-y">
              <div v-for="st in COLOR_SUBTABS" :key="st.id" class="pm-subtab"
                   :class="{ on: colorTab === st.id }" @click="colorTab = st.id">{{ st.name }}</div>
            </el-scrollbar>
            <div class="pm-color-body">
              <el-scrollbar class="pm-color-scroll">

                <!-- AI色彩 -->
                <template v-if="colorTab === 'ai_color'">
                  <div class="pm-card">
                    <div class="pm-card-head"><span class="pm-card-name">AI追色</span>
                      <span v-if="refInfo" class="mod-tag">已选参考</span></div>
                    <div class="pm-params">
                      <div class="pm-ref" @click="refUp && refUp.click()">
                        <template v-if="refInfo">
                          <img :src="refInfo.url" class="pm-ref-img" />
                          <span class="pm-ref-txt">点击更换参考图</span>
                        </template>
                        <template v-else>
                          <PxIcon name="plus"/>
                          <span class="pm-ref-txt">上传参考图</span>
                        </template>
                      </div>
                      <input ref="refUp" type="file" accept="image/*" style="display:none" @change="onRefFile" />
                      <div class="pm-param">
                        <div class="pm-param-row"><span class="pm-param-label">程度</span>
                          <span class="pm-num">{{ val('color_match.strength') }}</span></div>
                        <el-slider :model-value="val('color_match.strength')" :min="0" :max="100"
                                   @update:model-value="v => setVal('color_match.strength', v)" />
                      </div>
                      <div class="pm-param wip"><div class="pm-param-row"><span class="pm-param-label">影调</span><span class="pm-wip-tag">开发中</span></div><el-slider :model-value="0" disabled /></div>
                      <div class="pm-param wip"><div class="pm-param-row"><span class="pm-param-label">色彩</span><span class="pm-wip-tag">开发中</span></div><el-slider :model-value="0" disabled /></div>
                    </div>
                  </div>
                  <div class="pm-card wip"><div class="pm-card-head"><span class="pm-card-name">色彩风格</span><span class="pm-wip-tag">开发中</span></div></div>
                </template>

                <!-- 配置文件 -->
                <template v-else-if="colorTab === 'profile'">
                  <div class="pm-card">
                    <div class="pm-card-head"><span class="pm-card-name">配置文件</span></div>
                    <div class="pm-params">
                      <div class="pm-methods">
                        <span v-for="pr in profiles" :key="pr.id" class="px-chip"
                              :class="{ on: curProfile === pr.id }" @click="pickProfile(pr.id)">{{ pr.label }}</span>
                      </div>
                      <div class="pm-param wip"><div class="pm-param-row"><span class="pm-param-label">强度</span><span class="pm-wip-tag">开发中</span></div><el-slider :model-value="0" disabled /></div>
                    </div>
                  </div>
                </template>

                <!-- 滤镜 -->
                <template v-else-if="colorTab === 'filter'">
                  <div class="pm-card">
                    <div class="pm-card-head">
                      <span class="pm-card-name">滤镜</span>
                      <span v-if="val('lut.filter','')" class="mod-tag">已选</span>
                      <el-button v-if="val('lut.filter','')" size="small" text @click="clearFilter">清除</el-button>
                    </div>
                    <div class="pm-params">
                      <div class="pm-luts">
                        <div class="pm-lut" :class="{ on: val('lut.filter','') === '' }" @click="clearFilter">
                          <div class="pm-lut-none">无</div><span class="pm-lut-name">原图</span>
                        </div>
                        <div v-for="f in shownFilters" :key="f.id" class="pm-lut"
                             :class="{ on: val('lut.filter','') === f.id }" @click="pickFilter(f)"
                             :title="f.name_cn || f.id">
                          <img :src="'/api/filter-thumb/' + f.id" loading="lazy" />
                          <span class="pm-lut-name">{{ f.name_cn || f.id }}</span>
                        </div>
                      </div>
                      <div class="pm-param">
                        <div class="pm-param-row"><span class="pm-param-label">强度</span>
                          <span class="pm-num">{{ val('lut.degree', 70) }}</span></div>
                        <el-slider :model-value="val('lut.degree', 70)" :min="0" :max="100"
                                   @update:model-value="v => setVal('lut.degree', v)" />
                      </div>
                      <div class="pm-lut-import wip">我的 LUT（CUBE 导入）<span class="pm-wip-tag">开发中</span></div>
                    </div>
                  </div>
                </template>

                <!-- 白平衡 -->
                <template v-else-if="colorTab === 'wb'">
                  <div class="pm-card">
                    <div class="pm-card-head"><span class="pm-card-name">白平衡</span></div>
                    <div class="pm-params">
                      <div class="pm-methods">
                        <span v-for="w in WB_PRESETS" :key="w.label" class="px-chip sm"
                              :class="{ on: w.ai ? val('ai_calibrate.white_balance', false) : (val('white_balance.temperature')===w.t && val('white_balance.tint')===w.g) }"
                              @click="w.ai ? aiCalibrate('white_balance') : (setVal('white_balance.temperature', w.t) || setVal('white_balance.tint', w.g))">
                          {{ w.label }}</span>
                      </div>
                      <div class="pm-param">
                        <div class="pm-param-row"><span class="pm-param-label">色温</span>
                          <span class="pm-num">{{ val('white_balance.temperature') }}</span></div>
                        <el-slider :model-value="val('white_balance.temperature')" :min="-100" :max="100"
                                   @update:model-value="v => setVal('white_balance.temperature', v)" />
                      </div>
                      <div class="pm-param">
                        <div class="pm-param-row"><span class="pm-param-label">色调</span>
                          <span class="pm-num">{{ val('white_balance.tint') }}</span></div>
                        <el-slider :model-value="val('white_balance.tint')" :min="-100" :max="100"
                                   @update:model-value="v => setVal('white_balance.tint', v)" />
                      </div>
                    </div>
                  </div>
                </template>

                <!-- 影调 -->
                <template v-else-if="colorTab === 'tone'">
                  <div class="pm-card">
                    <div class="pm-card-head"><span class="pm-card-name">直方图</span></div>
                    <div class="pm-params">
                      <canvas ref="histCanvas" width="228" height="80" class="pm-hist" @click="drawHistogram"></canvas>
                    </div>
                  </div>
                  <div class="pm-card">
                    <div class="pm-card-head"><span class="pm-card-name">AI校准曝光</span></div>
                    <div class="pm-btnrow">
                      <el-button size="small" :type="val('ai_calibrate.exposure',false) ? 'primary' : ''"
                                 @click="aiCalibrate('exposure')">AI校准曝光</el-button>
                    </div>
                  </div>
                  <div class="pm-card">
                    <div class="pm-card-head"><span class="pm-card-name">影调</span></div>
                    <div class="pm-params">
                      <div v-for="t in [['exposure','曝光'],['contrast','对比度'],['brightness','亮度'],['highlight','高光'],
                                        ['shadow','阴影'],['whites','白色'],['blacks','黑色'],['texture','纹理'],
                                        ['clarity','清晰度'],['dehaze','祛雾'],['vibrance','鲜艳度'],['saturation','饱和度']]"
                           :key="t[0]" class="pm-param">
                        <div class="pm-param-row"><span class="pm-param-label">{{ t[1] }}</span>
                          <span class="pm-num">{{ val('tone.' + t[0]) }}</span></div>
                        <el-slider :model-value="val('tone.' + t[0])" :min="-100" :max="100"
                                   @update:model-value="v => setVal('tone.' + t[0], v)" />
                      </div>
                    </div>
                  </div>
                </template>

                <!-- HSL -->
                <template v-else-if="colorTab === 'hsl'">
                  <div class="pm-card">
                    <div class="pm-card-head"><span class="pm-card-name">HSL（8色带）</span></div>
                    <div class="pm-params">
                      <div class="pm-bands">
                        <span v-for="(b, i) in HSL_BANDS" :key="b" class="pm-band" :class="{ on: hslBand === i }"
                              :style="{ background: ['#e05a5a','#e09a55','#e0d155','#7ac95f','#55c9a8','#557fe0','#9a6ae0','#e05ab0'][i] }"
                              @click="hslBand = i"></span>
                      </div>
                      <div class="pm-band-name">{{ HSL_BANDS[hslBand] }}</div>
                      <div class="pm-param"><div class="pm-param-row"><span class="pm-param-label">色相</span>
                        <span class="pm-num">{{ val(`hsl.${hslBand}.h`) }}</span></div>
                        <el-slider :model-value="val(`hsl.${hslBand}.h`)" :min="-100" :max="100" @update:model-value="v => setHsl('h', v)" /></div>
                      <div class="pm-param"><div class="pm-param-row"><span class="pm-param-label">饱和度</span>
                        <span class="pm-num">{{ val(`hsl.${hslBand}.s`) }}</span></div>
                        <el-slider :model-value="val(`hsl.${hslBand}.s`)" :min="-100" :max="100" @update:model-value="v => setHsl('s', v)" /></div>
                      <div class="pm-param"><div class="pm-param-row"><span class="pm-param-label">明亮度</span>
                        <span class="pm-num">{{ val(`hsl.${hslBand}.l`) }}</span></div>
                        <el-slider :model-value="val(`hsl.${hslBand}.l`)" :min="-100" :max="100" @update:model-value="v => setHsl('l', v)" /></div>
                    </div>
                  </div>
                </template>

                <!-- 颗粒 -->
                <template v-else-if="colorTab === 'grain'">
                  <div class="pm-card">
                    <div class="pm-card-head"><span class="pm-card-name">颗粒</span></div>
                    <div class="pm-params">
                      <div class="pm-param"><div class="pm-param-row"><span class="pm-param-label">数量</span>
                        <span class="pm-num">{{ val('grain.density') }}</span></div>
                        <el-slider :model-value="val('grain.density')" :min="0" :max="100"
                                   @update:model-value="v => setVal('grain.density', v)" /></div>
                      <div class="pm-param wip"><div class="pm-param-row"><span class="pm-param-label">大小</span>
                        <span class="pm-wip-tag">开发中</span></div><el-slider :model-value="30" disabled /></div>
                      <div class="pm-param wip"><div class="pm-param-row"><span class="pm-param-label">粗糙度</span>
                        <span class="pm-wip-tag">开发中</span></div><el-slider :model-value="0" disabled /></div>
                    </div>
                  </div>
                </template>

                <!-- F6 色阶 -->
                <template v-else-if="colorTab === 'levels'">
                  <div class="pm-card">
                    <div class="pm-card-head"><span class="pm-card-name">色阶</span></div>
                    <div class="pm-params">
                      <div class="pm-param"><div class="pm-param-row"><span class="pm-param-label">输入黑场</span>
                        <span class="pm-num">{{ val('levels.in_black') }}</span></div>
                        <el-slider :model-value="val('levels.in_black')" :min="0" :max="120"
                                   @update:model-value="v => setVal('levels.in_black', v)" /></div>
                      <div class="pm-param"><div class="pm-param-row"><span class="pm-param-label">输入白场</span>
                        <span class="pm-num">{{ val('levels.in_white') }}</span></div>
                        <el-slider :model-value="val('levels.in_white')" :min="135" :max="255"
                                   @update:model-value="v => setVal('levels.in_white', v)" /></div>
                      <div class="pm-param"><div class="pm-param-row"><span class="pm-param-label">Gamma</span>
                        <span class="pm-num">{{ val('levels.gamma', 1) }}</span></div>
                        <el-slider :model-value="val('levels.gamma', 1)" :min="0.3" :max="2.5" :step="0.05"
                                   @update:model-value="v => setVal('levels.gamma', v)" /></div>
                      <div class="pm-param"><div class="pm-param-row"><span class="pm-param-label">输出黑场</span>
                        <span class="pm-num">{{ val('levels.out_black') }}</span></div>
                        <el-slider :model-value="val('levels.out_black')" :min="0" :max="80"
                                   @update:model-value="v => setVal('levels.out_black', v)" /></div>
                      <div class="pm-param"><div class="pm-param-row"><span class="pm-param-label">输出白场</span>
                        <span class="pm-num">{{ val('levels.out_white', 255) }}</span></div>
                        <el-slider :model-value="val('levels.out_white', 255)" :min="175" :max="255"
                                   @update:model-value="v => setVal('levels.out_white', v)" /></div>
                    </div>
                  </div>
                </template>

                <!-- F6 曲线（SVG 可拖点） -->
                <template v-else-if="colorTab === 'curve'">
                  <div class="pm-card">
                    <div class="pm-card-head">
                      <span class="pm-card-name">曲线</span>
                      <span class="px-chip sm" v-for="cch in curveChannels" :key="cch.id"
                            :class="{ on: curveCh === cch.id }" @click="curveCh = cch.id">{{ cch.name }}</span>
                    </div>
                    <div class="pm-params">
                      <svg ref="curveSvg" viewBox="0 0 256 256" class="pm-curve"
                           @mousedown="curveMouseDown">
                        <rect x="0" y="0" width="256" height="256" fill="#14151a" />
                        <line v-for="g in [64,128,192]" :key="g" :x1="g" y1="0" :x2="g" y2="256" stroke="#26282f" />
                        <line v-for="g in [64,128,192]" :key="'h'+g" x1="0" :y1="g" x2="256" :y2="g" stroke="#26282f" />
                        <polyline :points="curvePath" fill="none" :stroke="curveColor[curveCh]" stroke-width="2" />
                        <circle v-for="(p, i) in curvePts" :key="i" :cx="p[0]" :cy="255 - p[1]"
                                r="5" fill="#fff" stroke-width="12" stroke="transparent" style="cursor:grab" />
                      </svg>
                      <div class="pm-curve-hint">拖动控制点调整曲线（{{ curveChannels.find(c => c.id === curveCh)?.name }}通道）</div>
                      <el-button size="small" text @click="resetCurve">重置曲线</el-button>
                    </div>
                  </div>
                </template>

                <!-- F6 色彩编辑器（8色带 色相环+HSL） -->
                <template v-else-if="colorTab === 'color_editor'">
                  <div class="pm-card">
                    <div class="pm-card-head"><span class="pm-card-name">色彩编辑器</span></div>
                    <div class="pm-params">
                      <div class="pm-bands">
                        <span v-for="(b, i) in HSL_BANDS" :key="b" class="pm-band" :class="{ on: ceBand === i }"
                              :style="{ background: ['#e05a5a','#e09a55','#e0d155','#7ac95f','#55c9a8','#557fe0','#9a6ae0','#e05ab0'][i] }"
                              @click="ceBand = i"></span>
                      </div>
                      <div class="pm-band-name">{{ HSL_BANDS[ceBand] }}</div>
                      <div class="pm-param"><div class="pm-param-row"><span class="pm-param-label">色相偏移</span>
                        <span class="pm-num">{{ val(`color_editor.bands.${ceBand}.h`) }}</span></div>
                        <el-slider :model-value="val(`color_editor.bands.${ceBand}.h`)" :min="-100" :max="100"
                                   @update:model-value="v => setVal(`color_editor.bands.${ceBand}.h`, v)" /></div>
                      <div class="pm-param"><div class="pm-param-row"><span class="pm-param-label">饱和度</span>
                        <span class="pm-num">{{ val(`color_editor.bands.${ceBand}.s`) }}</span></div>
                        <el-slider :model-value="val(`color_editor.bands.${ceBand}.s`)" :min="-100" :max="100"
                                   @update:model-value="v => setVal(`color_editor.bands.${ceBand}.s`, v)" /></div>
                      <div class="pm-param"><div class="pm-param-row"><span class="pm-param-label">明亮度</span>
                        <span class="pm-num">{{ val(`color_editor.bands.${ceBand}.l`) }}</span></div>
                        <el-slider :model-value="val(`color_editor.bands.${ceBand}.l`)" :min="-100" :max="100"
                                   @update:model-value="v => setVal(`color_editor.bands.${ceBand}.l`, v)" /></div>
                      <div class="pm-param"><div class="pm-param-row"><span class="pm-param-label">全局色相</span>
                        <span class="pm-num">{{ val('color_editor.global_hue') }}</span></div>
                        <el-slider :model-value="val('color_editor.global_hue')" :min="-180" :max="180"
                                   @update:model-value="v => setVal('color_editor.global_hue', v)" /></div>
                    </div>
                  </div>
                </template>

                <!-- F6 颜色分级（三区色轮：简化二维拖盘） -->
                <template v-else-if="colorTab === 'grading'">
                  <div class="pm-card" v-for="z in gradingZones" :key="z.id">
                    <div class="pm-card-head"><span class="pm-card-name">{{ z.name }}</span></div>
                    <div class="pm-params">
                      <div class="pm-wheel" :ref="el => wheelEls[z.id] = el"
                           @mousedown="e => wheelDown(e, z.id)" @touchstart.prevent="e => wheelDown(e, z.id)">
                        <div class="pm-wheel-ring" :style="wheelBg"></div>
                        <div class="pm-wheel-dot" :style="wheelDot(z.id)"></div>
                      </div>
                      <div class="pm-param"><div class="pm-param-row"><span class="pm-param-label">色相</span>
                        <span class="pm-num">{{ val(`color_grading.${z.id}.h`) }}</span></div>
                        <el-slider :model-value="val(`color_grading.${z.id}.h`)" :min="-180" :max="180"
                                   @update:model-value="v => setVal(`color_grading.${z.id}.h`, v)" /></div>
                      <div class="pm-param"><div class="pm-param-row"><span class="pm-param-label">饱和度</span>
                        <span class="pm-num">{{ val(`color_grading.${z.id}.s`) }}</span></div>
                        <el-slider :model-value="val(`color_grading.${z.id}.s`)" :min="-100" :max="100"
                                   @update:model-value="v => setVal(`color_grading.${z.id}.s`, v)" /></div>
                      <div class="pm-param"><div class="pm-param-row"><span class="pm-param-label">明度</span>
                        <span class="pm-num">{{ val(`color_grading.${z.id}.l`) }}</span></div>
                        <el-slider :model-value="val(`color_grading.${z.id}.l`)" :min="-100" :max="100"
                                   @update:model-value="v => setVal(`color_grading.${z.id}.l`, v)" /></div>
                    </div>
                  </div>
                </template>

                <!-- F6 可选颜色 -->
                <template v-else-if="colorTab === 'selective'">
                  <div class="pm-card">
                    <div class="pm-card-head">
                      <span class="pm-card-name">可选颜色</span>
                      <span class="px-chip sm" :class="{ on: !scAbsolute }" @click="scAbsolute = false">相对</span>
                      <span class="px-chip sm" :class="{ on: scAbsolute }" @click="scAbsolute = true">绝对</span>
                    </div>
                    <div class="pm-params">
                      <div class="pm-bands">
                        <span v-for="c in SC_COLORS" :key="c.id" class="pm-band" :class="{ on: scColor === c.id }"
                              :style="{ background: c.c }" @click="scColor = c.id"></span>
                      </div>
                      <div class="pm-band-name">{{ SC_COLORS.find(c => c.id === scColor)?.name }}</div>
                      <div class="pm-param" v-for="comp in [['c','青色'],['m','洋红'],['y','黄色'],['k','黑色']]"
                           :key="comp[0]"><div class="pm-param-row"><span class="pm-param-label">{{ comp[1] }}</span>
                        <span class="pm-num">{{ val(`selective_color.${scColor}.${comp[0]}`) }}</span></div>
                        <el-slider :model-value="val(`selective_color.${scColor}.${comp[0]}`)" :min="-100" :max="100"
                                   @update:model-value="v => setVal(`selective_color.${scColor}.${comp[0]}`, v)" /></div>
                    </div>
                  </div>
                </template>

                <!-- 其余子 tab：置灰占位 -->
                <template v-else>
                  <div class="pm-card wip">
                    <div class="pm-card-head">
                      <span class="pm-card-name">{{ COLOR_SUBTABS.find(t => t.id === colorTab)?.name }}</span>
                      <span class="pm-wip-tag">引擎开发中</span>
                    </div>
                    <div class="pm-params">
                      <div v-for="i in 4" :key="i" class="pm-param wip">
                        <div class="pm-param-row"><span class="pm-param-label">参数 {{ i }}</span><span class="pm-wip-tag">开发中</span></div>
                        <el-slider :model-value="0" disabled />
                      </div>
                    </div>
                  </div>
                </template>
              </el-scrollbar>
            </div>
          </div>
        </div>

        <!-- ===== 未实现板块：置灰占位 ===== -->
        <!-- 图像: 画质修复 + 背景（已实装） -->
        <div v-if="sec.id === 'image' && openSections.image" class="pm-sec-body">
          <div class="pm-card">
            <div class="pm-card-title">画质</div>
            <div class="pm-param-row" v-for="p in imageParams" :key="p.key">
              <span class="pm-param-name" :title="p.label">{{ p.label }}</span>
              <el-slider v-model="params[p.key]" size="small" :min="p.min" :max="p.max"
                :disabled="false" style="flex:1; margin: 0 10px;"
                @input="onParam(p)" />
              <span class="pm-param-val">{{ params[p.key] }}</span>
            </div>
          </div>
          <div class="pm-card">
            <div class="pm-card-title">背景</div>
            <div class="pm-param-row" v-for="p in bgParams" :key="p.key">
              <span class="pm-param-name" :title="p.label">{{ p.label }}</span>
              <el-slider v-model="params[p.key]" size="small" :min="p.min" :max="p.max"
                style="flex:1; margin: 0 10px;" @input="onParam(p)" />
              <span class="pm-param-val">{{ params[p.key] }}</span>
            </div>
          </div>
        </div>
        <div v-else-if="openSections[sec.id] && !['beauty','color','adjust'].includes(sec.id)" class="pm-sec-body">
          <div class="pm-placeholder">
            <PxIcon name="package"/>
            <span>「{{ sec.name }}」模块开发中</span>
          </div>
        </div>
      </div>
      <div style="height: 20px"></div>
    </el-scrollbar>

    <div class="pm-foot">
      <el-button size="small" text title="重置全部" @click="resetAll">
        <PxIcon name="rotate-ccw"/>&nbsp;重置全部
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.pm { display: flex; flex-direction: column; height: 100%; background: var(--px-bg-2); }
.pm-scroll { flex: 1; }
.pm-foot { flex: none; padding: 4px 10px; border-top: 1px solid var(--px-line); display: flex; justify-content: flex-end; }

/* 板块折叠列表 */
.pm-sec { border-bottom: 1px solid var(--px-line); }
.pm-sec-head { display: flex; align-items: center; gap: 6px; padding: 10px 12px; cursor: pointer;
  transition: background var(--px-dur) var(--px-ease); }
.pm-sec-head:hover { background: rgba(255,255,255,.015); }
.pm-sec-head:hover .pm-sec-name { color: var(--px-accent-2); }
.pm-sec-name { flex: 1; font-size: 13px; font-weight: 500; letter-spacing: .02em; }
.pm-sec-badge { font-size: 9px; color: var(--px-accent-2); background: var(--px-accent-weak);
  padding: 0 5px; border-radius: 3px; }
.pm-sec-body { padding: 0 12px 10px; }

.pm-arrow { color: var(--px-text-3); font-size: 12px; transition: transform .2s; flex: none; }
.pm-arrow.open { transform: rotate(90deg); }
.pm-arrow.sm { font-size: 10px; }

/* 人像美化 */
.pm-person { padding: 2px 0 8px; }
.pm-chips { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; }
.px-chip { padding: 3px 12px; border-radius: 12px; font-size: 11px; cursor: pointer;
  background: var(--px-bg-1); border: 1px solid var(--px-line); color: var(--px-text-2);
  transition: all var(--px-dur) var(--px-ease); }
.px-chip:hover { border-color: var(--px-line-2); color: var(--px-text); }
.px-chip.on { background: var(--px-accent-weak); border-color: var(--px-accent); color: var(--px-accent-2); font-weight: 500;
  box-shadow: 0 0 0 1px var(--px-accent-weak); }
.px-chip.sm { padding: 2px 9px; font-size: 10px; }
.pm-scope-sep { width: 1px; height: 14px; background: var(--px-line-2); margin: 0 2px; }

.pm-tabs-x { display: flex; gap: 4px; overflow-x: auto; padding-bottom: 6px; scrollbar-width: thin; }
.pm-tab-x { flex: none; padding: 5px 12px; border-radius: 14px; font-size: 12px; white-space: nowrap;
  background: var(--px-bg-1); border: 1px solid var(--px-line); color: var(--px-text-2); cursor: pointer; }
.pm-tab-x:hover { color: var(--px-text); }
.pm-tab-x.on { background: var(--px-accent-weak); border-color: var(--px-accent); color: var(--px-accent-2); font-weight: 500; }

.pm-tab-body { margin-top: 4px; }
.pm-tab-toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.pm-tab-name { font-size: 12px; color: var(--px-text-3); }

.pm-card { border: 1px solid var(--px-line); border-radius: var(--px-radius); margin-bottom: 8px;
  background: linear-gradient(180deg, #16181d, var(--px-bg-1));
  box-shadow: 0 1px 2px rgba(0,0,0,.25); overflow: hidden; }
.pm-card.wip { opacity: .6; }
.pm-card-head { display: flex; align-items: center; gap: 6px; padding: 7px 10px; cursor: pointer;
  border-bottom: 1px solid rgba(255,255,255,.03); }
.pm-card-name { flex: 1; font-size: 12px; color: var(--px-text-2); }
.mod-tag { color: var(--px-accent-2); background: var(--px-accent-weak); padding: 1px 7px;
  border-radius: 4px; font-size: 10px; flex: none; }
.pm-params { padding: 4px 10px 10px; }
.pm-param { margin-bottom: 4px; }
.pm-param :deep(.el-slider) { height: 24px; }
.pm-param :deep(.el-slider__runway) { margin: 10px 0; }
.pm-param.wip { opacity: .55; }
.pm-param-row { display: flex; align-items: center; justify-content: space-between;
  min-height: 29px;              /* 对齐像素蛋糕 63px 总行高: 29 label行 + 24 滑杆 + 间距 */
  padding: 0 2px; margin-bottom: 2px; }
.pm-param-label { font-size: 11px; color: var(--px-text-2); }
.pm-num { font-size: 11px; color: var(--px-accent-2); min-width: 28px; text-align: right; }
.pm-param.wip .pm-num { color: var(--px-text-3); }
.pm-wip-tag { font-size: 9px; color: #c79a76; border: 1px solid #5a4436; border-radius: 3px; padding: 0 5px; flex: none;
  background: rgba(176,131,106,.08); }
.pm-methods { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 8px; }
.pm-toggles { display: flex; flex-direction: column; gap: 8px; padding: 4px 0; }
.pm-toggle { display: flex; align-items: center; gap: 8px; }
.pm-toggle .pm-param-label { flex: 1; }

/* 调色 */
.pm-color { padding: 0; }
.pm-maskbar { display: flex; align-items: center; gap: 5px; padding: 4px 12px 7px; border-bottom: 1px solid var(--px-line); flex-wrap: wrap; }
.pm-maskbar-label { color: var(--px-text-3); font-size: 11px; margin-right: 2px; }
.pm-curve { width: 100%; max-width: 256px; display: block; border: 1px solid var(--px-line-2);
  border-radius: var(--px-radius-sm); cursor: crosshair; box-shadow: inset 0 1px 4px rgba(0,0,0,.4); }
.pm-curve-hint { color: var(--px-text-3); font-size: 11px; margin: 6px 0 4px; }
.pm-wheel { position: relative; width: 96px; height: 96px; margin: 4px auto 10px; cursor: crosshair; }
.pm-wheel-ring { position: absolute; inset: 6px; border-radius: 50%; opacity: .9;
  border: 1px solid rgba(0,0,0,.5); filter: saturate(.92) blur(.3px);
  box-shadow: inset 0 0 12px rgba(0,0,0,.35), 0 1px 3px rgba(0,0,0,.4); }
.pm-wheel::after { content: ''; position: absolute; left: 50%; top: 50%; width: 6px; height: 6px;
  margin: -3px; border-radius: 50%; background: #fff8; }
.pm-wheel-dot { position: absolute; width: 12px; height: 12px; margin: -6px; border-radius: 50%;
  background: #fff; border: 2px solid var(--px-accent); box-shadow: 0 0 0 3px rgba(0,0,0,.35), 0 0 8px var(--px-accent-glow);
  pointer-events: none; }
.pm-exif { display: flex; align-items: center; justify-content: space-between; padding: 6px 12px;
  font-size: 11px; color: var(--px-text-3); border-bottom: 1px solid var(--px-line);
  font-variant-numeric: tabular-nums; letter-spacing: .01em; }
.pm-exif-none { opacity: .6; }
.pm-color-wrap { display: flex; height: 460px; }
.pm-subtabs-y { width: 84px; flex: none; border-right: 1px solid var(--px-line); }
.pm-subtab { padding: 7px 10px; font-size: 11px; color: var(--px-text-2); cursor: pointer;
  border-left: 2px solid transparent; white-space: nowrap; }
.pm-subtab:hover { color: var(--px-text); }
.pm-subtab.on { color: var(--px-accent-2); background: linear-gradient(90deg, var(--px-accent-weak), transparent); border-left-color: var(--px-accent); font-weight: 500; }
.pm-color-body { flex: 1; min-width: 0; }
.pm-color-scroll { height: 100%; }

.pm-btnrow { display: flex; gap: 8px; padding: 8px 10px; }
.pm-ref { height: 76px; border: 1px dashed #3a3c46; border-radius: 8px;
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px;
  cursor: pointer; margin-bottom: 8px; overflow: hidden; position: relative; }
.pm-ref:hover { border-color: var(--px-accent); }
.pm-ref-img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
.pm-ref-txt { font-size: 11px; color: var(--px-text-3); z-index: 1; background: rgba(0,0,0,.45);
  padding: 2px 8px; border-radius: 4px; }
.pm-hist { width: 100%; border-radius: var(--px-radius-sm); cursor: pointer; border: 1px solid var(--px-line); }
.pm-luts { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; margin-bottom: 8px; }
.pm-lut { cursor: pointer; border-radius: var(--px-radius-sm); overflow: hidden; border: 2px solid transparent;
  transition: border-color var(--px-dur), transform var(--px-dur) var(--px-ease); }
.pm-lut:hover { transform: translateY(-1px); }
.pm-lut img { width: 100%; height: 38px; object-fit: cover; display: block; }
.pm-lut.on { border-color: var(--px-accent); box-shadow: 0 0 0 1px var(--px-accent-weak); }
.pm-lut-none { height: 38px; display: flex; align-items: center; justify-content: center;
  font-size: 11px; color: var(--px-text-3); background: var(--px-bg-2); }
.pm-lut-name { display: block; font-size: 8px; color: var(--px-text-3); text-align: center;
  padding: 1px 0; background: var(--px-bg-2); overflow: hidden; white-space: nowrap; }
.pm-lut-import { font-size: 11px; color: var(--px-text-3); padding: 8px 0 2px; display: flex; align-items: center; gap: 6px; }
.pm-bands { display: flex; gap: 5px; margin: 4px 0 2px; }
.pm-band { flex: 1; height: 14px; border-radius: 7px; cursor: pointer; opacity: .5;
  border: 2px solid transparent; }
.pm-band.on { opacity: 1; border-color: #fff; box-shadow: 0 0 0 2px rgba(255,255,255,.12); }
.pm-band-name { font-size: 11px; color: var(--px-text-2); margin-bottom: 4px; }

.pm-placeholder { display: flex; align-items: center; gap: 8px; padding: 14px 4px;
  font-size: 12px; color: var(--px-text-3); opacity: .7; }
.pm-suit-hint { color: var(--px-text-3); font-size: 11px; margin: 2px 0 6px; }
</style>
