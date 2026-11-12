<script setup>
/* 工作台：顶部(返回/项目名/图库/精修 + 导出) · 左工具条 · 画布 · 底缩略条 · 右参数面板 · 图库网格 · 导出弹窗 */
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { API, store, defaultOverrides, toEngineOverrides, PRESET_TREE, trackError, _h } from '../lib.js'
import CompareCanvas from '../components/CompareCanvas.vue'
import FilmStrip from '../components/FilmStrip.vue'
import ParamPanel from '../components/ParamPanel.vue'
import PathPicker from '../components/PathPicker.vue'
import PxIcon from '../components/PxIcon.vue'

const props = defineProps({ projectId: String })
const emit = defineEmits(['back'])

const project = computed(() => store.getProject(props.projectId))
const presets = ref([])

const tab = ref('retouch')            // gallery | retouch
const curIdx = ref(0)
const tool = ref('hand')

const canvas = ref(null)

/* 当前图 */
const img = computed(() => project.value?.images[curIdx.value] || null)
const imgName = computed(() => img.value?.name || '')

/* 参数状态（每图独立） */
const overrides = ref(defaultOverrides())

/* ---------- 撤销/重做（滑杆参数快照栈, Ctrl+Z / Ctrl+Shift+Z） ---------- */
const undoStack = ref([])   // [{snap, label}]
const redoStack = ref([])
const _inUndo = { v: false }
function pushUndo(label) {
  if (_inUndo.v) return
  undoStack.value.push({ snap: JSON.stringify(overrides.value), label })
  if (undoStack.value.length > 50) undoStack.value.shift()
  redoStack.value = []
}
function undo() {
  if (!undoStack.value.length) return
  _inUndo.v = true
  redoStack.value.push({ snap: JSON.stringify(overrides.value), label: undoStack.value.at(-1).label })
  const u = undoStack.value.pop()
  overrides.value = JSON.parse(u.snap)
  _inUndo.v = false
  _lastSnap = u.snap
  onParamChange(true)
  ElMessage({ message: `撤销：${u.label}`, type: 'info', duration: 1200 })
}
function redo() {
  if (!redoStack.value.length) return
  _inUndo.v = true
  undoStack.value.push({ snap: JSON.stringify(overrides.value), label: redoStack.value.at(-1).label })
  const r = redoStack.value.pop()
  overrides.value = JSON.parse(r.snap)
  _inUndo.v = false
  _lastSnap = r.snap
  onParamChange(true)
  ElMessage({ message: `重做：${r.label}`, type: 'info', duration: 1200 })
}
// 滑杆每档变化入栈（防抖 400ms 合并连续拖动为一步）
let _undoTimer = null
let _lastSnap = JSON.stringify(defaultOverrides())
function onSliderTouched() {
  clearTimeout(_undoTimer)
  _undoTimer = setTimeout(() => {
    const cur = JSON.stringify(overrides.value)
    if (cur !== _lastSnap) {
      // 入栈的是变化【前】快照: 用当前栈顶后的基准 —— 简化: 直接存旧值
      undoStack.value.push({ snap: _lastSnap, label: '参数调整' })
      if (undoStack.value.length > 50) undoStack.value.shift()
      redoStack.value = []
      _lastSnap = cur
    }
  }, 400)
}
const preset = ref('wedding_basic')
const presetLib = ref([])
const resultJob = ref(null)      // 当前显示的结果 job id
const processing = ref(null)
let debounceTimer = null
let seq = 0

watch(curIdx, () => loadSettings())
watch(() => props.projectId, () => { curIdx.value = 0; loadSettings() }, { immediate: true })

/* F2: EXIF 读取 */
const exifInfo = ref(null)
watch(img, async (im) => {
  exifInfo.value = null
  if (!im) return
  try {
    exifInfo.value = await fetch('/api/exif?path=' + encodeURIComponent(im.path), { headers: _h() }).then(r => r.json())
  } catch (e) { exifInfo.value = null }
}, { immediate: true })

function loadSettings() {
  const s = project.value?.settings?.[imgName.value]
  if (s) {
    overrides.value = { ...defaultOverrides(), ...(s.overrides || {}) }
    preset.value = s.preset || project.value.preset
    resultJob.value = s.jobId || null
  } else {
    overrides.value = defaultOverrides()
    preset.value = project.value?.preset || 'wedding_basic'
    resultJob.value = null
  }
  if (tab.value === 'retouch' && !resultJob.value) runRetouch()
}

function saveSettings(jobId) {
  if (!project.value) return
  project.value.settings[imgName.value] = {
    preset: preset.value,
    overrides: { ...overrides.value },
    jobId: jobId ?? resultJob.value,
  }
  store.save()
}

onMounted(async () => {
  try { presets.value = await API.presets() } catch (e) {}
  try { presetLib.value = await fetch('/api/preset_library', { headers: _h() }).then(r => r.json()) } catch (e) {}
})

/* ---------- retouch 执行（debounce 600ms） ---------- */
async function runRetouch() {
  if (!img.value) return
  const mySeq = ++seq
  clearTimeout(debounceTimer)
  processing.value = 0
  try {
    const file = await fetch(API.imageUrl(img.value.path)).then(r => r.blob())
      .then(b => new File([b], img.value.name, { type: b.type || 'image/jpeg' }))
    const ov = toEngineOverrides(overrides.value)
    const p = overrides.value.__person || ''
    const jobId = await API.retouch(file, preset.value, ov, curRouting.value, p)
    await API.poll(jobId, j => { if (seq === mySeq) processing.value = j.progress ?? 0 })
    if (seq !== mySeq) return
    resultJob.value = jobId
    processing.value = null
    saveSettings(jobId)
  } catch (e) {
    processing.value = null
    ElMessage.error(e.message || '精修失败')
  }
}
function onParamChange(fromUndo = false) {
  if (!fromUndo) onSliderTouched()
  if (tab.value !== 'retouch') return
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(runRetouch, 600)
}

const resultUrl = computed(() => resultJob.value ? API.resultUrl(resultJob.value) : null)

/* ---------- 图库标记（meta: {star 0-5, pick 'keep'|'reject'|null, color}） ---------- */
function metaOf(name) { return project.value?.meta?.[name] }
function ensureMeta() {
  if (!project.value.meta) project.value.meta = {}
  if (!project.value.meta[imgName.value]) project.value.meta[imgName.value] = {}
  return project.value.meta[imgName.value]
}
function onStripMark({ name, patch }) {
  if (!project.value.meta) project.value.meta = {}
  const m = project.value.meta[name] || (project.value.meta[name] = {})
  Object.assign(m, patch)
  store.save()
}
function toggleStar() { const m = ensureMeta(); m.star = m.star ? 0 : 1; store.save() }
function togglePick() { const m = ensureMeta(); m.pick = m.pick ? null : 'keep'; store.save() }
function setColor(c) { const m = ensureMeta(); m.color = m.color === c ? null : c; store.save() }

const galleryFilter = ref('all') // all | star | pick | color
const galleryList = computed(() => {
  const imgs = project.value?.images || []
  if (galleryFilter.value === 'star') return imgs.filter(i => (metaOf(i.name)?.star || 0) > 0)
  if (galleryFilter.value === 'pick') return imgs.filter(i => metaOf(i.name)?.pick === 'keep')
  if (galleryFilter.value === 'color') return imgs.filter(i => metaOf(i.name)?.color)
  return imgs
})

/* ---------- 添加照片 ---------- */
const addPicker = ref(false)
async function onAdd({ dir, images }) {
  const base = dir.replace(/[\\/]+$/, '')
  const news = (images || []).filter(n => !project.value.images.some(i => i.name === n))
    .map(n => ({ name: n, path: base + '/' + n }))
  if (!news.length) { ElMessage.info('没有新照片'); return }
  project.value.images.push(...news)
  store.save()
  ElMessage.success(`已添加 ${news.length} 张`)
}

/* ---------- 导出 ---------- */
const exportShow = ref(false)
const exportFormat = ref('jpg')
const exportQuality = ref(92)
const exportScope = ref('current')
const exportBusy = ref(false)
const exportDone = ref(0)
const exportTotal = ref(0)

async function doExport() {
  const targets = exportScope.value === 'all'
    ? project.value.images.map((im, i) => i)
    : [curIdx.value]
  exportBusy.value = true
  exportDone.value = 0
  exportTotal.value = targets.length
  try {
    for (const idx of targets) {
      const im = project.value.images[idx]
      const nm = im.name
      const st = project.value.settings?.[nm]
      let url, name, reencoded = false
      if (st?.jobId) {
        url = API.resultUrl(st.jobId)
        name = nm.replace(/\.[^.]+$/, '') + '_pixlite.' + exportFormat.value
      } else {
        url = API.imageUrl(im.path)
        name = nm
      }
      let blob = await fetch(url).then(r => {
        if (!r.ok) throw new Error('结果已失效（服务重启），请重新精修')
        return r.blob()
      })
      // 仅在确需转格式或更低品质时重编码; 服务端已是 JPEG 时 jpg+q≥92 直接透传, 避免二次有损压缩
      if (exportFormat.value !== 'jpg' || exportQuality.value < 92) {
        blob = await reencode(blob, exportFormat.value, exportQuality.value)
        reencoded = true
      }
      if (reencoded) {
        // 重编码后扩展名必须与实际格式一致（未精修原图 .jpg→.png 等）
        name = name.replace(/\.[^.]+$/, '') + '.' + exportFormat.value
      }
      downloadBlob(blob, name)
      exportDone.value++
    }
    ElMessage.success('导出完成')
    exportShow.value = false
  } catch (e) {
    ElMessage.error(e.message || '导出失败')
  } finally {
    exportBusy.value = false
  }
}
async function reencode(blob, fmt, q) {
  const bmp = await createImageBitmap(blob)
  const c = document.createElement('canvas')
  c.width = bmp.width; c.height = bmp.height
  c.getContext('2d').drawImage(bmp, 0, 0)
  const mime = fmt === 'png' ? 'image/png' : 'image/jpeg'
  return new Promise(res => c.toBlob(res, mime, q / 100))
}
function downloadBlob(blob, name) {
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = name
  a.click()
  setTimeout(() => URL.revokeObjectURL(a.href), 5000)
}

/* 预设树：把 /api/presets 的列表挂到实机 9 类 */
const presetTreeList = computed(() => {
  const lib = presetLib.value || []
  const cats = PRESET_TREE.map(t => ({
    id: t.id, name: t.name,
    items: lib.filter(e => e.category === t.name).map(e => ({ id: e.name, name: e.name, ov: e.overrides, routing: e.routing })),
  }))
  // 防弱耦合静默丢类: 未命中任何 PRESET_TREE 分类的预设归入「其他」
  const known = new Set(PRESET_TREE.map(t => t.name))
  const others = lib.filter(e => !known.has(e.category)).map(e => ({ id: e.name, name: e.name, ov: e.overrides, routing: e.routing }))
  if (others.length) cats.push({ id: 'other', name: '其他', items: others })
  cats.push({ id: 'base', name: '基础', items: [
    { id: 'wedding_basic', name: '婚纱一键' },
    { id: 'facesurgery_headnarrow', name: '小头调整' },
  ] })
  return cats
})
const curRouting = ref(null)   // 当前预设五人群路由：服务端按脸自动选组，无需前端选
function applyLibPreset(item) {
  pushUndo(`预设 ${item.name || ''}`)
  lastLibItem = item
  curRouting.value = item.routing || null
  preset.value = 'wedding_basic'
  overrides.value = { ...defaultOverrides(), ...(item.ov || {}) }
  _lastSnap = JSON.stringify(overrides.value)
  onParamChange(true)
}

/* 人物属性（像素蛋糕五人群路由：女/男/儿童/长辈女/长辈男）
   与右侧面板 ParamPanel 的 __person 同步 */
const personGroup = ref('female')
const personGroups = [
  { id: 'female', label: '女' },
  { id: 'male', label: '男' },
  { id: 'child', label: '儿童' },
  { id: 'elder_f', label: '长辈女' },
  { id: 'elder_m', label: '长辈男' },
]
let lastLibItem = null
function setPersonGroup(g) {
  personGroup.value = g
  overrides.value = { ...overrides.value, __person: g }
  if (lastLibItem) applyLibPreset(lastLibItem)   // 重新套用该人群的路由参数
  else onParamChange()
}

const hasResult = computed(() => !!(project.value?.settings?.[imgName.value]?.jobId))

/* 工具条定义 — 实机 7 位：缩放(+合适)/抓手/祛边/更多工具/AI点修/重置效果/预设 */
const tools = [
  { id: 'zoom', icon: 'zoom-in', label: '缩放（滚轮）', act: () => canvas.value?.zoomIn?.() },
  { id: 'fit', icon: 'maximize', label: '合适大小', act: () => canvas.value?.fitScreen() },
  { id: 'hand', icon: 'hand', label: '抓手' },
  { id: 'defringe', icon: 'scissors', label: '祛边', wip: true },
  { id: 'more', icon: 'more-horizontal', label: '更多工具', wip: true },
  { id: 'spot', icon: 'wand-sparkles', label: 'AI点修', wip: true },
  { id: 'sep1' },
  { id: 'undo', icon: 'undo-2', label: '撤销（Ctrl+Z）', act: () => undo(), disabled: () => !undoStack.value.length },
  { id: 'redo', icon: 'redo-2', label: '重做（Ctrl+Shift+Z）', act: () => redo(), disabled: () => !redoStack.value.length },
  { id: 'reset', icon: 'rotate-ccw', label: '重置效果', act: resetAll },
  { id: 'preset', icon: 'library', label: '预设', act: () => (presetShow.value = !presetShow.value) },
]
const presetShow = ref(true)
function clickTool(t) {
  if (t.wip) { ElMessage.info(`「${t.label}」功能开发中`); return }
  if (t.disabled?.()) { return }
  if (t.act) { t.act(); return }
  tool.value = t.id
}
function resetAll() {
  pushUndo('重置效果')
  _lastSnap = JSON.stringify(defaultOverrides())
  overrides.value = defaultOverrides()
  resultJob.value = null
  saveSettings(null)
  runRetouch()
  ElMessage.success('已重置')
}
// 快捷键
function onKeydown(e) {
  if (!(e.ctrlKey || e.metaKey)) return
  const k = e.key.toLowerCase()
  if (k === 'z' && !e.shiftKey) { e.preventDefault(); undo() }
  else if (k === 'y' || (k === 'z' && e.shiftKey)) { e.preventDefault(); redo() }
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div class="ws" v-if="project">
    <!-- 顶栏 — 实机 11 位：首页/搜索/购买套餐/技术支持/个人中心/消息中心/传输列表/导出列表/促销/导出/菜单 -->
    <header class="ws-head">
      <div class="px-icon-btn" title="首页" @click="emit('back')">
        <PxIcon name="arrow-left"/>
      </div>
      <div class="ws-title">
        <span class="ws-name">{{ project.name }}</span>
        <span class="px-tag">{{ project.images.length }} 张</span>
      </div>
      <div class="ws-tabs">
        <div class="ws-tab" :class="{ on: tab === 'gallery' }" @click="tab = 'gallery'">
          <PxIcon name="image"/> 图库
        </div>
        <div class="ws-tab" :class="{ on: tab === 'retouch' }" @click="tab = 'retouch'; if(!resultJob) runRetouch()">
          <PxIcon name="sparkles"/> 精修
        </div>
      </div>
      <div style="flex:1"></div>
      <div class="px-icon-btn" title="搜索" @click="ElMessage.info('搜索开发中')"><PxIcon name="search"/></div>
      <div class="px-icon-btn" title="购买套餐（占位）" @click="ElMessage.info('购买套餐开发中')"><PxIcon name="shopping-cart"/></div>
      <div class="px-icon-btn" title="技术支持（占位）" @click="ElMessage.info('技术支持开发中')"><PxIcon name="headphones"/></div>
      <div class="px-icon-btn" title="个人中心" @click="ElMessage.info('个人中心开发中')"><PxIcon name="circle-user"/></div>
      <div class="px-icon-btn" title="消息中心" @click="ElMessage.info('消息中心开发中')"><PxIcon name="bell"/></div>
      <div class="px-icon-btn" title="传输列表" @click="ElMessage.info('传输列表开发中')"><PxIcon name="upload"/></div>
      <div class="px-icon-btn" title="导出列表"><PxIcon name="download"/></div>
      <div class="px-icon-btn" title="促销活动（占位）" @click="ElMessage.info('26年双11 敬请期待')"><PxIcon name="gift"/></div>
      <el-button class="px-btn-accent" @click="exportShow = true">
        <PxIcon name="download"/>导出
      </el-button>
      <el-dropdown trigger="click">
        <div class="px-icon-btn" title="菜单"><PxIcon name="menu"/></div>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item @click="presetShow = !presetShow">{{ presetShow ? '隐藏' : '显示' }}预设面板</el-dropdown-item>
            <el-dropdown-item divided @click="resetAll">重置全部效果</el-dropdown-item>
            <el-dropdown-item @click="addPicker = true">导入新图片</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </header>

    <div class="ws-body">
      <!-- 左工具条 -->
      <aside class="ws-tools" v-show="tab === 'retouch'">
        <template v-for="t in tools" :key="t.id">
          <div v-if="t.id.startsWith('sep')" class="ws-sep"></div>
          <div v-else class="px-icon-btn ws-tool" :class="{ active: tool === t.id, wipout: t.disabled?.() }"
               :title="t.label" @click="clickTool(t)">
            <PxIcon :name="t.icon" :size="16"/>
          </div>
        </template>
      </aside>

      <!-- 图库页 -->
      <div v-if="tab === 'gallery'" class="gal px-fade">
        <div class="gal-bar">
          <el-radio-group v-model="galleryFilter" size="small">
            <el-radio-button value="all">全部</el-radio-button>
            <el-radio-button value="star">星标</el-radio-button>
            <el-radio-button value="pick">旗标</el-radio-button>
            <el-radio-button value="color">色标</el-radio-button>
          </el-radio-group>
          <div style="flex:1"></div>
          <el-button size="small" text @click="addPicker = true">
            <PxIcon name="plus"/>&nbsp;导入新图片
          </el-button>
        </div>
        <div class="gal-grid">
          <div v-for="(im, i) in galleryList" :key="im.name" class="gal-cell"
               @dblclick="curIdx = project.images.indexOf(im); tab = 'retouch'">
            <img :src="API.imageUrl(im.path)" loading="lazy" />
            <!-- 标记行 -->
            <div class="gal-marks">
              <span class="gal-mark" @click.stop="curIdx = project.images.indexOf(im); toggleStar()">
                <PxIcon name="star" :style="{ color: project.meta?.[im.name]?.star ? '#f2c97d' : '' }"/>
              </span>
              <span class="gal-mark" @click.stop="curIdx = project.images.indexOf(im); togglePick()">
                <PxIcon name="flag" :style="{ color: project.meta?.[im.name]?.pick ? '#9ce7b4' : '' }"/>
              </span>
              <el-dropdown trigger="click" @command="c => { curIdx = project.images.indexOf(im); setColor(c) }">
                <span class="gal-mark"><PxIcon name="paintbrush"/></span>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="red">红色色标</el-dropdown-item>
                    <el-dropdown-item command="yellow">黄色色标</el-dropdown-item>
                    <el-dropdown-item command="green">绿色色标</el-dropdown-item>
                    <el-dropdown-item command="blue">蓝色色标</el-dropdown-item>
                    <el-dropdown-item command="purple">紫色色标</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
            <div v-if="project.meta?.[im.name]?.color" class="gal-colorband"
                 :style="{ background: ({red:'#e5534b',yellow:'#e0a63f',green:'#57ab5a',blue:'#539bf5',purple:'#b083f0'})[project.meta[im.name].color] }"></div>
            <div v-if="project.settings?.[im.name]?.jobId" class="gal-edited">已精修</div>
            <div class="gal-open" @click.stop="curIdx = i; tab = 'retouch'">
              <PxIcon name="sparkles"/> 进入精修
            </div>
          </div>
        </div>
        <el-empty v-if="!galleryList.length" description="无符合筛选的照片" :image-size="80" />
      </div>

      <!-- 精修页 -->
      <div v-else class="ret">
        <!-- 预设面板（实机 9 类树：推荐/我的） -->
        <aside v-show="presetShow" class="pr-panel">
          <div class="pr-tabs">
            <div class="pr-tab on">推荐</div>
            <div class="pr-tab">我的</div>
          </div>
          <div class="pr-person">
            <div class="pr-person-label">人物属性</div>
            <div class="pr-person-chips">
              <div v-for="g in personGroups" :key="g.id" class="pr-chip"
                   :class="{ on: personGroup === g.id }" @click="setPersonGroup(g.id)">{{ g.label }}</div>
            </div>
          </div>
          <el-scrollbar class="pr-scroll">
            <div v-for="cat in presetTreeList" :key="cat.id" class="pr-cat">
              <div class="pr-cat-name">{{ cat.name }}<span class="pr-cat-n">{{ cat.items.length }}</span></div>
              <div v-for="p in cat.items" :key="p.id" class="pr-item"
                   :class="{ on: preset === p.id && !p.ov }" @click="p.ov ? applyLibPreset(p) : (preset = p.id, onParamChange())">
                {{ p.name }}
              </div>
              <div v-if="!cat.items.length" class="pr-empty">—</div>
            </div>
          </el-scrollbar>
        </aside>
        <div class="ret-main">
          <div class="ret-canvas">
            <CompareCanvas ref="canvas" :src-url="img ? API.imageUrl(img.path) : ''"
                           :result-url="resultUrl" :processing="processing" />
          </div>
          <FilmStrip :images="project.images" :current="curIdx" :meta="project.meta || {}"
                     @select="curIdx = $event" @add="addPicker = true" @mark="onStripMark" />
        </div>
      </div>

      <!-- 右参数面板 -->
      <aside class="ws-panel" v-show="tab === 'retouch'">
        <ParamPanel v-model="overrides" :preset="preset" :presets="presets.length ? presets : ['wedding_basic']"
                    :result-url="resultUrl" :src-url="img ? API.imageUrl(img.path) : ''"
                    :exif="exifInfo"
                    @change="onParamChange" @update:preset="preset = $event; onParamChange()" />
      </aside>
    </div>

    <!-- 导出弹窗 -->
    <el-dialog v-model="exportShow" title="导出照片" width="460px">
      <el-form label-width="76px" label-position="left">
        <el-form-item label="范围">
          <el-radio-group v-model="exportScope">
            <el-radio value="current">当前照片</el-radio>
            <el-radio value="all">全部照片</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="格式">
          <el-radio-group v-model="exportFormat">
            <el-radio value="jpg">JPG</el-radio>
            <el-radio value="png">PNG</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="品质" v-if="exportFormat === 'jpg'">
          <el-slider v-model="exportQuality" :min="50" :max="100" style="width: 90%" />
          <span class="exp-q">{{ exportQuality }}</span>
        </el-form-item>
        <el-form-item label="说明">
          <div class="exp-note">未精修的照片将直接导出原图；已精修照片导出效果图。文件保存至浏览器默认下载目录。</div>
        </el-form-item>
        <el-form-item v-if="exportBusy" label="进度">
          <el-progress :percentage="exportTotal ? Math.round(exportDone / exportTotal * 100) : 0"
                       :stroke-width="8" />
          <span class="exp-q">{{ exportDone }}/{{ exportTotal }}</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="exportShow = false">取消</el-button>
        <el-button type="primary" class="px-btn-accent" :loading="exportBusy" @click="doExport">
          开始导出
        </el-button>
      </template>
    </el-dialog>

    <PathPicker v-model="addPicker" title="添加照片" @confirm="onAdd" />
  </div>
</template>

<script>
export default { name: 'Workspace' }
</script>

<style scoped>
.ws { display: flex; flex-direction: column; height: 100%; background: var(--px-bg-1); }

/* 顶栏 */
.ws-head { height: 48px; flex: none; display: flex; align-items: center; gap: 12px; padding: 0 14px;
  background: var(--px-bg-2); border-bottom: 1px solid var(--px-line); position: relative; z-index: 2;
  box-shadow: 0 1px 0 rgba(0,0,0,.25); }
.ws-title { display: flex; align-items: center; gap: 8px; margin-right: 12px; min-width: 0; }
.ws-name { font-size: 13px; font-weight: 600; max-width: 200px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
.ws-tabs { display: flex; gap: 4px; background: var(--px-bg-1); padding: 3px; border-radius: 8px;
  border: 1px solid var(--px-line); }
.ws-tab { display: flex; align-items: center; gap: 5px; padding: 5px 16px; border-radius: var(--px-radius-sm);
  font-size: 12px; color: var(--px-text-2); cursor: pointer;
  transition: all var(--px-dur) var(--px-ease); }
.ws-tab:hover { color: var(--px-text); }
.ws-tab.on { background: var(--px-bg-3); color: var(--px-accent-2); font-weight: 500;
  box-shadow: var(--px-shadow-1), inset 0 1px 0 rgba(255,255,255,.04); }

.ws-body { flex: 1; display: flex; min-height: 0; }

/* 左工具条 */
.ws-tools { width: 44px; flex: none; background: var(--px-bg-2); border-right: 1px solid var(--px-line);
  display: flex; flex-direction: column; align-items: center; padding: 10px 0; gap: 2px;
  box-shadow: 1px 0 0 rgba(0,0,0,.2); }
.ws-tool.wipout { opacity: .35; cursor: default; }
.ws-tool { width: 34px; height: 34px; }
.ws-sep { width: 22px; height: 1px; background: var(--px-line-2); margin: 6px 0; }

/* 图库 */
.gal { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.gal-bar { display: flex; align-items: center; gap: 12px; padding: 10px 16px; }
.gal-grid { flex: 1; overflow-y: auto; display: grid;
  grid-template-columns: repeat(auto-fill, minmax(168px, 1fr)); gap: 10px; padding: 4px 16px 20px;
  align-content: start; }
.gal-cell { position: relative; border-radius: var(--px-radius); overflow: hidden; cursor: pointer;
  background: var(--px-bg-2); border: 1px solid var(--px-line); aspect-ratio: 4/3;
  transition: all 180ms var(--px-ease); }
.gal-cell:hover { border-color: var(--px-accent); transform: translateY(-2px); box-shadow: var(--px-shadow-2); }
.gal-cell img { width: 100%; height: 100%; object-fit: cover; display: block; transition: transform .3s var(--px-ease); }
.gal-cell:hover img { transform: scale(1.03); }
.gal-marks { position: absolute; top: 6px; right: 6px; display: flex; gap: 2px; opacity: 0;
  transition: opacity .15s; }
.gal-cell:hover .gal-marks { opacity: 1; }
.gal-mark { width: 24px; height: 24px; border-radius: 6px; background: rgba(10,11,15,.72);
  display: flex; align-items: center; justify-content: center; color: var(--px-text-3); cursor: pointer; }
.gal-mark:hover { color: var(--px-text); }
.gal-mark .el-icon { font-size: 13px; }
.gal-colorband { position: absolute; left: 0; right: 0; bottom: 0; height: 3px; }
.gal-edited { position: absolute; left: 6px; top: 6px; font-size: 10px; color: #9ce7b4;
  background: rgba(10,11,15,.72); padding: 1px 7px; border-radius: 4px; }
.gal-open { position: absolute; inset: auto 0 0 0; padding: 9px 0; text-align: center; font-size: 12px;
  background: linear-gradient(transparent, rgba(8,9,12,.9)); color: #fff; display: flex; align-items: center;
  justify-content: center; gap: 5px; opacity: 0; transition: opacity var(--px-dur) var(--px-ease); }
.gal-cell:hover .gal-open { opacity: 1; }

/* 精修 */
.ret { flex: 1; display: flex; min-width: 0; }
.ret-main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.ret-canvas { flex: 1; position: relative; min-height: 0; }

/* 预设面板 */
.pr-panel { width: 168px; flex: none; background: var(--px-bg-2); border-right: 1px solid var(--px-line);
  display: flex; flex-direction: column; box-shadow: 1px 0 0 rgba(0,0,0,.2); }
.pr-tabs { display: flex; padding: 8px 10px 4px; gap: 10px; }
.pr-person { padding: 4px 12px 8px; border-bottom: 1px solid var(--px-line-1); }
.pr-person-label { font-size: 10px; color: var(--px-text-3); margin-bottom: 4px; }
.pr-person-chips { display: flex; flex-wrap: wrap; gap: 5px; }
.pr-chip { font-size: 11px; color: var(--px-text-2); background: var(--px-bg-3);
  border: 1px solid var(--px-line-2); border-radius: 10px; padding: 2px 9px; cursor: pointer; }
.pr-chip.on { color: #fff; background: var(--px-accent); border-color: var(--px-accent); }
.pr-tab { font-size: 12px; color: var(--px-text-3); cursor: pointer; padding-bottom: 4px;
  border-bottom: 2px solid transparent; }
.pr-tab.on { color: var(--px-accent-2); border-bottom-color: var(--px-accent); }
.pr-scroll { flex: 1; }
.pr-cat { padding: 4px 0; }
.pr-cat-name { font-size: 11px; color: var(--px-text-3); padding: 6px 12px 3px; display: flex;
  justify-content: space-between; }
.pr-cat-n { font-size: 10px; opacity: .7; }
.pr-item { font-size: 12px; color: var(--px-text-2); padding: 5px 12px 5px 20px; cursor: pointer;
  border-left: 2px solid transparent; transition: all 120ms var(--px-ease); }
.pr-item:hover { background: var(--px-bg-3); color: var(--px-text); }
.pr-item.on { color: var(--px-accent-2); background: linear-gradient(90deg, var(--px-accent-weak), transparent);
  border-left-color: var(--px-accent); font-weight: 500; }
.pr-empty { font-size: 11px; color: var(--px-text-3); opacity: .4; padding-left: 20px; }

/* 右面板 */
.ws-panel { width: clamp(320px, 26vw, 560px); flex: none; border-left: 1px solid var(--px-line); background: var(--px-bg-2); }

.exp-q { font-size: 12px; color: var(--px-text-2); margin-left: 8px; }
.exp-note { font-size: 11px; color: var(--px-text-3); line-height: 1.6; }
</style>
