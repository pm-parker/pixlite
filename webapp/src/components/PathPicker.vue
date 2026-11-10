<script setup>
/* NAS 目录浏览器：左侧目录树式列表（面包屑+子目录列表），右侧图片网格选择 */
import { ref, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { API } from '../lib.js'
import PxIcon from './PxIcon.vue'

const props = defineProps({
  modelValue: Boolean,
  title: { type: String, default: '选择图片目录' },
  needImages: { type: Boolean, default: true }, // 是否要求目录含图片
  selectMode: { type: String, default: 'dir' }, // dir: 选目录 | images: 选图
})
const emit = defineEmits(['update:modelValue', 'confirm'])

const visible = computed({
  get: () => props.modelValue,
  set: v => emit('update:modelValue', v),
})

const path = ref('')
const stack = ref([])      // 面包屑：[{name, path}]
const dirs = ref([])
const images = ref([])
const loading = ref(false)
const selected = ref([])   // selectMode==='images' 时选中的图片名

async function load(p) {
  loading.value = true
  try {
    const r = await API.browse(p)
    if (r.error) { ElMessage.error(r.error === 'permission' ? '无权限访问' : '路径不存在'); return }
    path.value = r.current || ''
    dirs.value = r.dirs || []
    images.value = r.images || []
    // 面包屑
    if (!path.value) stack.value = []
    else {
      const parts = path.value.replace(/\\/g, '/').split('/').filter(Boolean)
      stack.value = parts.map((part, i) => ({
        name: part,
        path: parts.slice(0, i + 1).join('/').startsWith('.')
          ? path.value : (part.includes(':') ? part + '/' : ''),
      }))
      // 修正盘符路径
      let acc = ''
      stack.value = parts.map(part => {
        acc = acc ? acc + '/' + part : (part.endsWith(':') ? part + '/' : part)
        return { name: part, path: acc }
      })
    }
  } finally {
    loading.value = false
  }
}

watch(() => props.modelValue, v => { if (v && !path.value) load('') })

function enter(d) { load(path.value ? path.value.replace(/[\\/]+$/, '') + '/' + d : '') }
function crumb(p) { load(p) }
function up() {
  if (!stack.value.length) return
  load(stack.value.length > 1 ? stack.value[stack.value.length - 2].path : '')
}

const ppTab = ref('nas')
const dragOver = ref(false)
const uploaded = ref([])
let fileInput = null

function pickLocal() {
  if (!fileInput) {
    fileInput = document.createElement('input')
    fileInput.type = 'file'
    fileInput.accept = 'image/*'
    fileInput.multiple = true
    fileInput.style.display = 'none'
    fileInput.addEventListener('change', () => { doUpload(fileInput.files); fileInput.value = '' })
    document.body.appendChild(fileInput)
  }
  fileInput.click()
}
function onDrop(e) { dragOver.value = false; doUpload(e.dataTransfer.files) }
async function doUpload(files) {
  if (!files || !files.length) return
  for (const f of files) {
    const fd = new FormData()
    fd.append('file', f)
    try {
      const j = await API.upload(f)
      if (j.error) ElMessage.error(`${f.name}: ${j.error}`)
      else uploaded.value.push(j)
    } catch (err) { ElMessage.error('上传失败: ' + err.message) }
  }
}
function confirmUpload() {
  emit('confirm', { uploaded: uploaded.value.map(u => ({ path: u.path, name: u.name })) })
  uploaded.value = []
  visible.value = false
}

const manual = ref('')
function goManual() { if (manual.value.trim()) load(manual.value.trim()) }

function toggleImg(name) {
  const i = selected.value.indexOf(name)
  if (i >= 0) selected.value.splice(i, 1)
  else selected.value.push(name)
}

function ok() {
  if (!path.value) { ElMessage.warning('请先进入一个目录'); return }
  if (props.selectMode === 'images') {
    if (!selected.value.length) { ElMessage.warning('请至少选择一张图片'); return }
    emit('confirm', { dir: path.value, names: [...selected.value] })
  } else {
    if (props.needImages && !images.value.length) { ElMessage.warning('该目录下没有图片'); return }
    emit('confirm', { dir: path.value, images: images.value })
  }
  visible.value = false
}
</script>

<template>
  <el-dialog v-model="visible" :title="title" width="760px" top="6vh" append-to-body
             :close-on-click-modal="false">
    <el-tabs v-model="ppTab" style="margin-bottom:4px">
      <el-tab-pane label="NAS 目录" name="nas" />
      <el-tab-pane label="上传本机图片" name="local" />
    </el-tabs>

    <!-- 本机上传 tab -->
    <div v-if="ppTab === 'local'" class="pp">
      <div class="pp-upload"
           @click="pickLocal"
           @dragover.prevent="dragOver = true"
           @dragleave="dragOver = false"
           @drop.prevent="onDrop"
           :class="{ over: dragOver }">
        <PxIcon name="upload" :size="34"/>
        <div style="margin-top:8px;font-size:14px">点击选择 或 拖拽本机图片到此处</div>
        <div style="margin-top:4px;font-size:12px;opacity:.55">支持 jpg / png / webp / RAW，可多选</div>
      </div>
      <div v-if="uploaded.length" class="pp-up-list">
        <div v-for="u in uploaded" :key="u.path" class="pp-up-item">
          <PxIcon name="image" :size="14"/> {{ u.name }}
          <span style="opacity:.5;font-size:12px">（{{ (u.size/1024).toFixed(0) }} KB）</span>
        </div>
      </div>
      <div style="margin-top:14px;text-align:right">
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" :disabled="!uploaded.length" @click="confirmUpload">
          使用上传的 {{ uploaded.length }} 张
        </el-button>
      </div>
    </div>

    <!-- NAS 目录 tab -->
    <div v-show="ppTab === 'nas'" class="pp">
      <!-- 路径栏 -->
      <div class="pp-bar">
        <div class="px-icon-btn" @click="up"><PxIcon name="arrow-up"/></div>
        <div class="pp-crumbs">
          <template v-if="!stack.length">
            <span class="pp-crumb-root"><PxIcon name="monitor"/> 此电脑</span>
          </template>
          <template v-else>
            <span class="pp-crumb" @click="load('')">此电脑</span>
            <span class="pp-sep">/</span>
            <span v-for="(c, i) in stack" :key="c.path">
              <span class="pp-crumb" :class="{ last: i === stack.length - 1 }" @click="crumb(c.path)">{{ c.name }}</span>
              <span v-if="i < stack.length - 1" class="pp-sep">/</span>
            </span>
          </template>
        </div>
        <el-input v-model="manual" size="small" placeholder="输入绝对路径，如 C:\photos" style="width:240px"
                  @keyup.enter="goManual">
          <template #append><el-button @click="goManual">跳转</el-button></template>
        </el-input>
      </div>

      <div v-loading="loading" class="pp-body">
        <!-- 目录列表 -->
        <div class="pp-dirs">
          <div v-if="!stack.length" class="pp-dir-hint">磁盘</div>
          <div v-for="d in dirs" :key="d" class="pp-dir" @dblclick="enter(d)" @click="enter(d)">
            <PxIcon name="folder"/>
            <span class="pp-dir-name" :title="d">{{ d }}</span>
            <PxIcon name="chevron-right"/>
          </div>
          <el-empty v-if="!dirs.length && stack.length" description="无子目录" :image-size="60" />
        </div>
        <!-- 图片网格 -->
        <div class="pp-imgs">
          <div class="pp-imgs-head">
            <span>图片 <b>{{ images.length }}</b> 张</span>
            <span v-if="selectMode === 'images'" class="pp-select-hint">点击选择（已选 {{ selected.length }}）</span>
          </div>
          <div class="pp-img-grid">
            <div v-for="img in images.slice(0, 200)" :key="img" class="pp-thumb"
                 :class="{ sel: selected.includes(img) }" @click="toggleImg(img)">
              <img :src="API.imageUrl(path + '/' + img)" loading="lazy" />
              <div class="pp-thumb-name" :title="img">{{ img }}</div>
              <div v-if="selected.includes(img)" class="pp-check"><PxIcon name="check"/></div>
            </div>
          </div>
        </div>
      </div>
    </div>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" class="px-btn-accent" @click="ok">确定</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.pp-upload { display:flex; flex-direction:column; align-items:center; justify-content:center;
  min-height:220px; border:1.5px dashed var(--el-border-color); border-radius:12px; cursor:pointer;
  transition: all .2s; color: var(--el-text-color-secondary); }
.pp-upload.over { border-color: var(--px-brand, #3187FF); background: rgba(49,135,255,.06); }
.pp-up-list { margin-top:12px; max-height:180px; overflow:auto; }
.pp-up-item { padding:6px 10px; border-radius:8px; background: var(--el-fill-color-light);
  margin-bottom:6px; font-size:13px; display:flex; align-items:center; gap:6px; }

.pp { display: flex; flex-direction: column; gap: 10px; }
.pp-bar { display: flex; align-items: center; gap: 8px; }
.pp-crumbs { flex: 1; display: flex; align-items: center; gap: 2px; overflow: hidden;
  background: var(--px-bg-1); border: 1px solid var(--px-line); border-radius: 6px;
  padding: 5px 10px; font-size: 12px; white-space: nowrap; }
.pp-crumb-root { color: var(--px-text-2); display: inline-flex; align-items: center; gap: 4px; }
.pp-crumb { color: var(--px-text-2); cursor: pointer; padding: 0 2px; }
.pp-crumb:hover { color: var(--px-accent-2); }
.pp-crumb.last { color: var(--px-text); font-weight: 500; }
.pp-sep { color: var(--px-text-3); }
.pp-body { display: flex; gap: 10px; height: 56vh; min-height: 360px; }
.pp-dirs { width: 240px; overflow-y: auto; background: var(--px-bg-1); border: 1px solid var(--px-line);
  border-radius: 6px; padding: 6px; }
.pp-dir-hint { font-size: 11px; color: var(--px-text-3); padding: 4px 8px; }
.pp-dir { display: flex; align-items: center; gap: 8px; padding: 7px 10px; border-radius: var(--px-radius-sm);
  cursor: pointer; color: var(--px-text-2); transition: all 120ms var(--px-ease); }
.pp-dir:hover { background: var(--px-bg-3); color: var(--px-text); }
.pp-dir .pxi { color: var(--px-accent-2); flex: none; }
.pp-dir-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pp-dir-arrow { color: var(--px-text-3); font-size: 12px; }
.pp-imgs { flex: 1; display: flex; flex-direction: column; background: var(--px-bg-1);
  border: 1px solid var(--px-line); border-radius: 6px; overflow: hidden; }
.pp-imgs-head { padding: 8px 12px; font-size: 12px; color: var(--px-text-2);
  border-bottom: 1px solid var(--px-line); display: flex; justify-content: space-between; }
.pp-select-hint { color: var(--px-text-3); }
.pp-img-grid { flex: 1; overflow-y: auto; display: grid; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr));
  gap: 8px; padding: 10px; align-content: start; }
.pp-thumb { position: relative; border-radius: 6px; overflow: hidden; cursor: pointer;
  border: 2px solid transparent; background: var(--px-bg-2); }
.pp-thumb img { width: 100%; aspect-ratio: 1; object-fit: cover; display: block; }
.pp-thumb.sel { border-color: var(--px-accent); box-shadow: 0 0 0 2px var(--px-accent-weak); }
.pp-thumb-name { position: absolute; left: 0; right: 0; bottom: 0; font-size: 10px;
  background: rgba(0,0,0,.6); color: #ddd; padding: 2px 4px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pp-check { position: absolute; top: 4px; right: 4px; width: 18px; height: 18px; border-radius: 50%;
  background: var(--px-accent); color: #fff; display: flex; align-items: center; justify-content: center; }
</style>
