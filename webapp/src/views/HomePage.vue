<script setup>
/* 项目主页：左侧导航 + 项目卡片网格 + 新建向导 + 推荐预设 + 批量任务 */
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { API, store, PRESET_LABELS } from '../lib.js'
import PathPicker from '../components/PathPicker.vue'
import PxIcon from '../components/PxIcon.vue'

const emit = defineEmits(['open'])

const presets = ref([])
onMounted(async () => {
  try { presets.value = await API.presets() } catch (e) { presets.value = [] }
})

const hasApiKey = computed(() => !!localStorage.getItem('pixlite_api_key'))
function editApiKey() {
  ElMessageBox.prompt('远程访问 NAS 服务时需要填写连接密钥（本机访问无需填写）', '连接密钥', {
    inputValue: localStorage.getItem('pixlite_api_key') || '',
    inputPlaceholder: '留空 = 不使用密钥',
    confirmButtonText: '保存',
    cancelButtonText: '取消',
  }).then(({ value }) => {
    if (value) localStorage.setItem('pixlite_api_key', value.trim())
    else localStorage.removeItem('pixlite_api_key')
    hasApiKey.value = !!value
    ElMessage.success(value ? '密钥已保存' : '密钥已清除')
  }).catch(() => {})
}

import DiagDialog from '../components/DiagDialog.vue'
const diagShow = ref(false)
const gpuInfo = ref(null)
const gpuLabel = computed(() => {
  const g = gpuInfo.value
  if (!g) return '本地引擎 · 检测中'
  const ep = g.providers?.[0] || ''
  if (ep.includes('CUDA')) return `GPU 加速 · NVIDIA CUDA`
  if (ep.includes('ROCm')) return `GPU 加速 · AMD ROCm`
  if (ep.includes('Dml')) return `GPU 加速 · DirectML`
  if (ep.includes('OpenVINO')) return `GPU 加速 · Intel`
  return '本地引擎 · CPU'
})
async function loadGpu() {
  try { gpuInfo.value = await API.gpu() } catch (e) { gpuInfo.value = null }
}
function showGpuInfo() {
  const g = gpuInfo.value || {}
  const epName = { 'CUDAExecutionProvider': 'NVIDIA CUDA', 'ROCmExecutionProvider': 'AMD ROCm',
                   'DmlExecutionProvider': 'DirectML', 'OpenVINOExecutionProvider': 'Intel OpenVINO',
                   'CPUExecutionProvider': 'CPU' }
  let html = `<p>显卡厂商: <b>${{nvidia:'NVIDIA',amd:'AMD',intel:'Intel'}[g.vendor] || '未识别'}</b><br>当前推理设备: <b>${epName[(g.providers||[])[0]] || g.providers?.join(', ') || '—'}</b></p>`
  if (g.accel_ready) {
    html += `<p style="color:var(--px-keep)">✓ GPU 加速已生效</p>`
  } else if (g.install_steps) {
    html += `<p style="color:var(--px-star)">当前 CPU 运行，可加速 ${g.suggest_ep.toUpperCase()}：</p><ol style="text-align:left;padding-left:18px">`
    for (const s of g.install_steps) html += `<li>${s.action}</li>`
    html += `</ol>`
  } else if (g.guide) {
    html += `<p style="color:var(--px-text-3)">${g.guide}</p>`
  }
  ElMessageBox.alert(html, 'GPU 加速状态', { dangerouslyUseHTMLString: true })
}
loadGpu()

const nav = ref('home') // home | local | batch
const search = ref('')

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  return store.projects.filter(p => !q || p.name.toLowerCase().includes(q))
})

function timeAgo(ts) {
  const m = Math.floor((Date.now() - ts) / 60000)
  if (m < 1) return '刚刚'
  if (m < 60) return m + ' 分钟前'
  const h = Math.floor(m / 60)
  if (h < 24) return h + ' 小时前'
  const d = Math.floor(h / 24)
  if (d < 30) return d + ' 天前'
  return new Date(ts).toLocaleDateString('zh-CN')
}

/* ---------- 新建项目向导 ---------- */
const wiz = ref(false)
const wizStep = ref(0)
const wizMode = ref('dir') // dir | files
const pickerShow = ref(false)
const wizName = ref('')
const wizPreset = ref('wedding_basic')
const wizDir = ref('')
const wizCount = ref(0)

function startWiz() { wizStep.value = 0; wizMode.value = 'dir'; wizName.value = ''; wizDir.value=''; wizCount.value=0; wiz.value = true }

function chooseDir() { wizMode.value = 'dir'; pickerShow.value = true }
function chooseFiles() { wizMode.value = 'files'; pickerShow.value = true }

function onPickerConfirm({ dir, images, names, uploaded }) {
  if (uploaded && uploaded.length) {
    // 本机上传分支: 直接用上传后的 NAS 绝对路径建项目
    wizMode.value = 'uploaded'
    wizDir.value = uploaded[0].path.replace(/[\\/][^\\/]+$/, '')
    wizCount.value = uploaded.length
    wizPreviewImages.value = uploaded.map(u => u.name)
    wizNames = uploaded.map(u => u.name)
    wizUploadedPaths = uploaded.reduce((m, u) => (m[u.name] = u.path, m), {})
    if (!wizName.value) wizName.value = '本机上传 ' + new Date().toLocaleDateString()
    wizStep.value = 1
    return
  }
  if (wizMode.value === 'dir') {
    wizDir.value = dir
    wizCount.value = images.length
    wizPreviewImages.value = images    // 立即保存图片清单，供创建与预览
  } else {
    wizDir.value = dir
    wizCount.value = names.length
    wizPreviewImages.value = names
    wizNames = names
  }
  if (!wizName.value) wizName.value = dir.split(/[\\/]/).filter(Boolean).pop() || '未命名项目'
  wizStep.value = 1
}
let wizNames = null
let wizUploadedPaths = null

function create() {
  if (!wizDir.value) { ElMessage.warning('请先选择图片'); return }
  const name = wizName.value.trim() || '未命名项目'
  let images
  if (wizMode.value === 'uploaded' && wizUploadedPaths) {
    images = wizNames.map(n => ({ name: n, path: wizUploadedPaths[n] }))
  } else if (wizMode.value === 'dir') {
    images = wizPreviewImages.value.map(n => ({ name: n, path: wizDir.value.replace(/[\\/]+$/, '') + '/' + n }))
  } else {
    images = wizNames.map(n => ({ name: n, path: wizDir.value.replace(/[\\/]+$/, '') + '/' + n }))
  }
  if (!images.length) { ElMessage.error('所选目录没有图片，请换个目录'); return }
  const p = store.createProject(name, wizDir.value, images, wizPreset.value)
  wiz.value = false
  ElMessage.success('项目已创建')
  emit('open', p.id)
}

const wizPreviewLoading = ref(false)
const wizPreviewImages = ref([])
const wizPreviewPicked = ref([])
async function afterStep1() {
  if (wizMode.value !== 'dir') return
  wizPreviewLoading.value = true
  try {
    const r = await API.browse(wizDir.value)
    wizPreviewImages.value = r.images || []
  } finally { wizPreviewLoading.value = false }
}

function removeProject(p) {
  ElMessageBox.confirm(`确定删除项目「${p.name}」？此操作仅移除项目记录，不删除源文件。`, '删除项目', {
    type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
  }).then(() => {
    store.removeProject(p.id)
    ElMessage.success('已删除')
  }).catch(() => {})
}

/* ---------- 批量精修 ---------- */
const batchShow = ref(false)
const batchDir = ref('')
const batchPreset = ref('wedding_basic')
const batchRecursive = ref(true)
const batchWorkers = ref(2)
const batchRunning = ref(false)
const batchStat = ref(null)
const batchPickerShow = ref(false)

function pickBatchDir() { batchPickerShow.value = true }
function onBatchDir({ dir }) { batchDir.value = dir }

async function runBatch() {
  if (!batchDir.value) { ElMessage.warning('请选择源目录'); return }
  batchRunning.value = true
  batchStat.value = { done: 0, total: 0 }
  try {
    const r = await API.batch(batchDir.value, '', batchPreset.value, batchRecursive.value, batchWorkers.value)
    if (r.error) throw new Error(r.error)
    batchStat.value = { done: 0, total: r.total }
    let fails = 0
    const t = setInterval(async () => {
      try {
        const s = await API.batchStatus(r.job)
        fails = 0
        if (s.status === 'done' || s.status === 'error') {
          clearInterval(t); batchRunning.value = false
          if (s.status === 'done') ElMessage.success(`批量完成：${s.done}/${s.total} 已输出至 ${r.dst}`)
          else ElMessage.error(s.error || '批量失败')
        } else {
          batchStat.value = s
        }
      } catch (e) {
        if (++fails >= 10) {  // 轮询连续失败 10 次(≈6s) 判定服务异常, 防无限轮询
          clearInterval(t); batchRunning.value = false
          ElMessage.error('批量状态获取失败，请检查服务后重试')
        }
      }
    }, 600)
  } catch (e) {
    batchRunning.value = false
    ElMessage.error(e.message || '批量启动失败')
  }
}

const libCats = [
  { id: 'skin', label: '肤质肤色', desc: '自然/质感/水润/奶油肌 4 款', grad: 'g0' },
  { id: 'wedding', label: '婚纱', desc: '纯欲/韩系/复古/油画 4 款', grad: 'g1' },
  { id: 'wedding_photo', label: '婚礼跟拍', desc: '纪实/黄昏/户外/晚宴 4 款', grad: 'g2' },
  { id: 'film', label: '胶片', desc: '8 款电影感滤镜调色', grad: 'g0' },
  { id: 'child', label: '儿童', desc: '透亮/软萌/活力 3 款', grad: 'g1' },
  { id: 'newborn', label: '新生儿', desc: '柔光/暖浴 2 款', grad: 'g2' },
  { id: 'pregnant', label: '孕妇', desc: '温柔/清新 2 款', grad: 'g0' },
  { id: 'family', label: '全家福', desc: '暖调/影棚 2 款', grad: 'g1' },
  { id: 'other', label: '创意', desc: '黑金/赛博/莫兰迪/通透', grad: 'g2' },
]
</script>

<template>
  <div class="home">
    <!-- 侧边导航 -->
    <aside class="side">
      <div class="side-logo">
        <div class="logo-mark"><PxIcon name="sparkles" :size="18"/></div>
        <div class="logo-text">PixLite<span class="logo-sub">像素蛋糕</span></div>
      </div>
      <div class="side-nav">
        <div class="side-item" :class="{ on: nav === 'home' }" @click="nav = 'home'">
          <PxIcon name="home"/> 主页
        </div>
        <div class="side-item" :class="{ on: nav === 'local' }" @click="nav = 'local'">
          <PxIcon name="folder"/> 本地项目
          <span class="side-count">{{ store.projects.length }}</span>
        </div>
        <div class="side-item" :class="{ on: nav === 'batch' }" @click="nav = 'batch'">
          <PxIcon name="list"/> 批量精修
        </div>
      </div>
      <div class="side-foot">
        <div class="px-tag" @click="showGpuInfo" style="cursor:pointer">
          <PxIcon :name="gpuInfo?.providers?.[0]?.includes('CPU') ? 'cpu' : 'zap'"/>
          {{ gpuLabel }}
        </div>
        <div class="px-tag" @click="diagShow = true" style="cursor:pointer;margin-top:4px">
          <PxIcon name="bug"/> 诊断报告
        </div>
        <DiagDialog v-model="diagShow"/>
      </div>
    </aside>

    <!-- 主区 -->
    <main class="main">
      <header class="main-head">
        <div class="main-title">{{ nav === 'batch' ? '批量精修' : nav === 'local' ? '本地项目' : '主页' }}</div>
        <el-input v-model="search" placeholder="搜索项目" size="small" clearable style="width: 240px"
                  v-if="nav !== 'batch'">
          <template #prefix><PxIcon name="search" :size="14" style="color:var(--px-text-3)"/></template>
        </el-input>
        <el-tooltip content="连接密钥（远程访问 NAS 时填写）" placement="bottom">
          <el-button size="small" text @click="editApiKey" style="margin-left:8px">
            <PxIcon name="key-round" :size="14" :style="{ color: hasApiKey ? 'var(--px-accent)' : 'var(--px-text-3)' }"/>
          </el-button>
        </el-tooltip>
        <div style="flex:1"></div>
        <el-button class="px-btn-accent" @click="startWiz">
          <PxIcon name="plus"/>导入照片 / 新建项目
        </el-button>
      </header>

      <!-- 批量页 -->
      <div v-if="nav === 'batch'" class="page px-fade">
        <div class="batch-card">
          <h3>目录批量精修</h3>
          <p class="batch-desc">选择 NAS/本地目录，引擎将按预设批量输出至 <code>源目录_pixlite</code>，支持断点续跑。</p>
          <div class="batch-form">
            <div class="batch-row">
              <div class="pm-label2">源目录</div>
              <el-input v-model="batchDir" placeholder="点击右侧浏览选择目录" readonly size="default"
                        @click="pickBatchDir">
                <template #append>
                  <el-button @click.stop="pickBatchDir">浏览</el-button>
                </template>
              </el-input>
            </div>
            <div class="batch-row">
              <div class="pm-label2">预设</div>
              <el-select v-model="batchPreset" style="width: 100%">
                <el-option v-for="p in presetList" :key="p.id" :value="p.id" :label="p.label" />
              </el-select>
            </div>
            <div class="batch-row">
              <div class="pm-label2">选项</div>
              <el-checkbox v-model="batchRecursive">包含子目录</el-checkbox>
              <el-select v-model="batchWorkers" style="width: 110px; margin-left: 12px">
                <el-option :value="1" label="1 线程" /><el-option :value="2" label="2 线程" />
                <el-option :value="4" label="4 线程" /><el-option :value="8" label="8 线程" />
              </el-select>
            </div>
            <div class="batch-actions">
              <el-button class="px-btn-accent" size="large" :loading="batchRunning" @click="runBatch">
                {{ batchRunning ? '运行中…' : '开始批量精修' }}
              </el-button>
            </div>
            <div v-if="batchStat" class="batch-stat">
              <el-progress :percentage="batchStat.total ? Math.round(batchStat.done / batchStat.total * 100) : 0"
                           :stroke-width="8" striped striped-flow />
              <div class="batch-stat-text">{{ batchStat.done || 0 }} / {{ batchStat.total || 0 }} 张</div>
            </div>
          </div>
        </div>
      </div>

      <!-- 项目列表页 -->
      <div v-else class="page px-fade">
        <div class="sec-head">
          <span class="sec-title">推荐预设</span>
        </div>
        <div class="preset-row">
          <div v-for="p in libCats" :key="p.id" class="preset-card" @click="startWiz">
            <div class="preset-grad" :class="p.grad">
              <PxIcon name="sparkles" :size="22"/>
            </div>
            <div class="preset-name">{{ p.label }}</div>
            <div class="preset-desc">{{ p.desc }}</div>
          </div>
        </div>

        <div class="sec-head" style="margin-top: 22px">
          <span class="sec-title">我的项目</span>
          <span class="sec-sub" v-if="store.projects.length">{{ store.projects.length }} 个项目</span>
        </div>

        <div class="proj-grid">
          <!-- 新建卡片 -->
          <div class="proj-card new px-dashed" @click="startWiz">
            <PxIcon name="plus" :size="34" style="color:"#5c6272"/>
            <div class="new-t1">导入照片</div>
            <div class="new-t2">选择目录或单张照片创建项目</div>
          </div>
          <div v-for="p in filtered" :key="p.id" class="proj-card" @click="emit('open', p.id)">
            <div class="proj-cover">
              <img v-if="p.images.length" :src="API.imageUrl(p.images[0].path)" loading="lazy" />
              <div v-else class="proj-cover-empty"><PxIcon name="image"/></div>
              <div class="proj-count"><PxIcon name="image"/> {{ p.images.length }}</div>
            </div>
            <div class="proj-info">
              <div class="proj-name" :title="p.name">{{ p.name }}</div>
              <div class="proj-meta">
                <span>{{ PRESET_LABELS[p.preset] || p.preset }}</span>
                <span>·</span>
                <span>{{ timeAgo(p.createdAt) }}</span>
              </div>
            </div>
            <div class="proj-del" @click.stop="removeProject(p)" title="删除项目">
              <PxIcon name="trash-2"/>
            </div>
          </div>
        </div>
        <el-empty v-if="!filtered.length && !store.projects.length" description="还没有项目，点击「导入照片」开始"
                  :image-size="90" style="grid-column: 1 / -1" />
      </div>
    </main>

    <!-- 新建向导 -->
    <el-dialog v-model="wiz" title="新建项目" width="640px" :close-on-click-modal="false">
      <el-steps :active="wizStep" align-center finish-status="success" simple style="margin-bottom: 18px">
        <el-step title="选择照片" /><el-step title="确认创建" />
      </el-steps>

      <template v-if="wizStep === 0">
        <div class="wiz-choose">
          <div class="wiz-opt" @click="chooseDir">
            <PxIcon name="folder-open" :size="26" style="color:"#9b8dff"/>
            <div class="wiz-opt-t">从文件夹导入</div>
            <div class="wiz-opt-d">选择 NAS / 本地目录，导入其中全部图片</div>
          </div>
          <div class="wiz-opt" @click="chooseFiles">
            <PxIcon name="images" :size="26" style="color:"#9b8dff"/>
            <div class="wiz-opt-t">挑选照片</div>
            <div class="wiz-opt-d">浏览目录缩略图，勾选需要的照片</div>
          </div>
        </div>
      </template>

      <template v-else>
        <el-form label-width="84px" label-position="left">
          <el-form-item label="项目名称">
            <el-input v-model="wizName" placeholder="项目名称" />
          </el-form-item>
          <el-form-item label="照片目录">
            <el-input :model-value="wizDir" readonly />
          </el-form-item>
          <el-form-item label="照片数量">
            <span class="wiz-count">{{ wizCount }} 张</span>
          </el-form-item>
          <el-form-item label="精修预设">
            <el-select v-model="wizPreset" style="width: 100%">
              <el-option v-for="p in presetList" :key="p.id" :value="p.id" :label="p.label" />
            </el-select>
          </el-form-item>
          <el-form-item label="预览确认">
            <div v-loading="wizPreviewLoading" class="wiz-preview">
              <div v-for="img in wizPreviewImages.slice(0, 12)" :key="img" class="wiz-thumb">
                <img :src="API.imageUrl(wizDir + '/' + img)" loading="lazy" />
              </div>
              <div v-if="wizPreviewImages.length > 12" class="wiz-more">+{{ wizPreviewImages.length - 12 }}</div>
            </div>
          </el-form-item>
        </el-form>
      </template>

      <template #footer>
        <el-button v-if="wizStep === 1" @click="wizStep = 0">上一步</el-button>
        <el-button v-if="wizStep === 1" type="primary" class="px-btn-accent" @click="create">创建项目</el-button>
      </template>
    </el-dialog>

    <PathPicker v-model="pickerShow" @confirm="onPickerConfirm"
                :selectMode="wizMode" :title="wizMode === 'dir' ? '选择图片目录' : '挑选照片'" />
    <PathPicker v-model="batchPickerShow" :selectMode="'dir'" title="选择批量源目录" @confirm="onBatchDir" />
  </div>
</template>

<script>
export default { name: 'HomePage' }
</script>

<style scoped>
.home { display: flex; height: 100%; background: var(--px-bg-1); }

/* 侧边栏 */
.side { width: 200px; flex: none; background: var(--px-bg-2); border-right: 1px solid var(--px-line);
  display: flex; flex-direction: column; position: relative; z-index: 1;
  box-shadow: 1px 0 0 rgba(0,0,0,.2); }
.side-logo { display: flex; align-items: center; gap: 10px; padding: 16px 16px 14px; }
.logo-mark { width: 32px; height: 32px; border-radius: 8px; display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg, #4a97ff, #1d529c); color: #fff; box-shadow: 0 2px 10px rgba(124,108,255,.35); }
.logo-text { font-size: 15px; font-weight: 700; letter-spacing: 1px; }
.logo-sub { display: block; font-size: 10px; color: var(--px-text-3); font-weight: 400; letter-spacing: 3px; }
.side-nav { padding: 8px 10px; display: flex; flex-direction: column; gap: 2px; }
.side-item { display: flex; align-items: center; gap: 10px; min-height: 44px; padding: 8px 12px; border-radius: var(--px-radius-sm);
  color: var(--px-text-2); cursor: pointer; font-size: 13px;
  transition: all var(--px-dur) var(--px-ease); }
.side-item:hover { background: var(--px-bg-3); color: var(--px-text); }
.side-item.on { background: var(--px-accent-weak); color: var(--px-accent-2); font-weight: 500;
  box-shadow: inset 2px 0 0 var(--px-accent); }
.side-count { margin-left: auto; font-size: 11px; color: var(--px-text-3); }
.side-foot { margin-top: auto; padding: 14px 16px; }

/* 主区 */
.main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.main-head { display: flex; align-items: center; gap: 14px; padding: 14px 22px;
  border-bottom: 1px solid var(--px-line); }
.main-title { font-size: 16px; font-weight: 600; }
.page { flex: 1; overflow-y: auto; padding: 0 22px 30px; }

/* 预设区 */
.sec-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 12px; }
.sec-title { font-size: 13px; font-weight: 600; color: var(--px-text); }
.sec-sub { font-size: 11px; color: var(--px-text-3); }
.preset-row { display: flex; gap: 12px; flex-wrap: wrap; }
.preset-card { width: 224px; min-height: 118px; background: var(--px-bg-3); border: 1px solid var(--px-line-2); border-radius: var(--px-radius-lg);
  padding: 14px; cursor: pointer; transition: all 200ms var(--px-ease); }
.preset-card:hover { border-color: var(--px-accent); transform: translateY(-3px);
  box-shadow: var(--px-shadow-3), 0 0 0 1px var(--px-accent-weak); }
.preset-grad { width: 42px; height: 42px; border-radius: 10px; display: flex; align-items: center; justify-content: center;
  color: #fff; margin-bottom: 10px; box-shadow: inset 0 1px 0 rgba(255,255,255,.2), 0 2px 8px rgba(0,0,0,.3); }
.preset-grad.g0 { background: linear-gradient(135deg, #3d7fd4, #1d529c); }
.preset-grad.g1 { background: linear-gradient(135deg, #2e6ab8, #163d72); }
.preset-grad.g2 { background: linear-gradient(135deg, #4a97ff, #276ccc); }
.preset-name { font-size: 13px; font-weight: 600; }
.preset-desc { font-size: 11px; color: var(--px-text-3); margin-top: 4px; }

/* 项目网格 */
.proj-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; }
.proj-card { background: var(--px-bg-2); border: 1px solid var(--px-line); border-radius: var(--px-radius-lg); overflow: hidden;
  cursor: pointer; position: relative; transition: all 200ms var(--px-ease); }
.proj-card:hover { border-color: var(--px-accent); transform: translateY(-3px);
  box-shadow: var(--px-shadow-3), 0 0 0 1px var(--px-accent-weak); }
.proj-card.new { display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 8px; min-height: 200px; background: transparent; }
.proj-card.new:hover { background: var(--px-bg-2); border-color: var(--px-accent);
  box-shadow: 0 0 0 3px var(--px-accent-weak); }
.proj-card.new:hover .pxi { color: var(--px-accent-2); }
.proj-card.new .pxi { transition: color var(--px-dur); }
.new-t1 { font-size: 13px; color: var(--px-text-2); font-weight: 500; }
.new-t2 { font-size: 11px; color: var(--px-text-3); }
.proj-cover { position: relative; height: 130px; background: var(--px-bg-3); }
.proj-cover img { width: 100%; height: 100%; object-fit: cover; display: block;
  transition: transform .35s var(--px-ease); }
.proj-card:hover .proj-cover img { transform: scale(1.04); }
.proj-cover-empty { height: 100%; display: flex; align-items: center; justify-content: center;
  color: var(--px-text-3); font-size: 26px; }
.proj-count { position: absolute; right: 8px; top: 8px; display: inline-flex; align-items: center; gap: 4px;
  font-size: 11px; background: rgba(0,0,0,.55); color: #eee; border-radius: 4px; padding: 2px 7px; backdrop-filter: blur(3px); }
.proj-info { padding: 10px 12px; }
.proj-name { font-size: 13px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.proj-meta { font-size: 11px; color: var(--px-text-3); margin-top: 3px; display: flex; gap: 6px; }
.proj-del { position: absolute; right: 8px; bottom: 8px; width: 26px; height: 26px; border-radius: 6px;
  display: none; align-items: center; justify-content: center; background: rgba(0,0,0,.55); color: #bbb; }
.proj-card:hover .proj-del { display: flex; }
.proj-del:hover { color: #fff; background: var(--px-danger); }

/* 向导 */
.wiz-choose { display: flex; gap: 14px; }
.wiz-opt { flex: 1; padding: 26px 18px; border: 1px solid var(--px-line); border-radius: var(--px-radius-lg); text-align: center;
  cursor: pointer; transition: all var(--px-dur) var(--px-ease); background: var(--px-bg-1); }
.wiz-opt:hover { border-color: var(--px-accent); background: var(--px-accent-weak); transform: translateY(-2px);
  box-shadow: 0 6px 20px rgba(0,0,0,.3); }
.wiz-opt-t { font-size: 13px; font-weight: 600; margin-top: 10px; }
.wiz-opt-d { font-size: 11px; color: var(--px-text-3); margin-top: 4px; }
.wiz-count { color: var(--px-accent-2); font-weight: 600; }
.wiz-preview { display: flex; flex-wrap: wrap; gap: 6px; }
.wiz-thumb { width: 56px; height: 56px; border-radius: 6px; overflow: hidden; background: var(--px-bg-3); }
.wiz-thumb img { width: 100%; height: 100%; object-fit: cover; }
.wiz-more { font-size: 12px; color: var(--px-text-3); align-self: center; }

/* 批量页 */
.batch-card { max-width: 560px; background: var(--px-bg-2); border: 1px solid var(--px-line);
  border-radius: 12px; padding: 22px 24px; margin-top: 10px; }
.batch-card h3 { margin: 0 0 6px; font-size: 15px; }
.batch-desc { font-size: 12px; color: var(--px-text-3); margin: 0 0 18px; }
.batch-desc code { color: var(--px-accent-2); background: var(--px-accent-weak); padding: 1px 5px; border-radius: 4px; }
.batch-row { margin-bottom: 16px; }
.pm-label2 { font-size: 12px; color: var(--px-text-2); margin-bottom: 6px; }
.batch-actions { margin-top: 6px; }
.batch-stat { margin-top: 18px; }
.batch-stat-text { font-size: 12px; color: var(--px-text-2); margin-top: 6px; }

/* el-steps 深色对比度适配 (设计走查 P2) */
:deep(.el-steps--simple) { background: var(--px-bg-3); border-radius: var(--px-radius); padding: 8px 16px; }
:deep(.el-step__title) { color: var(--px-text-2); font-size: 12px; }
</style>
