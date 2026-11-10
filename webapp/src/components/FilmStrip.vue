<script setup>
/* 底部缩略图条 — 实机对齐：未筛选下拉 + 文件名排序 + 留用/排除旗标 + 星级1-5 + 色标 + 已选N张(共M张) */
import { ref, watch, nextTick, computed } from 'vue'
import { API } from '../lib.js'
import PxIcon from './PxIcon.vue'

const props = defineProps({
  images: Array,          // [{name, path}]
  current: Number,
  meta: Object,           // name -> {star(0-5),pick:'keep'|'reject'|null,color}
})
const emit = defineEmits(['select', 'add', 'mark'])

const strip = ref(null)

/* 筛选/排序 */
const filterMode = ref('all')   // all | keep | reject | star | color
const sortMode = ref('name')    // name | seq
const filterLabel = { all: '未筛选', keep: '留用', reject: '排除', star: '有星级', color: '有色标' }

const shown = computed(() => {
  let list = props.images.map((im, i) => ({ ...im, _i: i }))
  const m = n => props.meta?.[n] || {}
  if (filterMode.value === 'keep') list = list.filter(im => m(im.name).pick === 'keep')
  else if (filterMode.value === 'reject') list = list.filter(im => m(im.name).pick === 'reject')
  else if (filterMode.value === 'star') list = list.filter(im => (m(im.name).star || 0) > 0)
  else if (filterMode.value === 'color') list = list.filter(im => m(im.name).color)
  list.sort((a, b) => sortMode.value === 'name' ? a.name.localeCompare(b.name, 'zh') : a._i - b._i)
  return list
})

const selectedCount = computed(() => {
  const ms = props.meta || {}
  return props.images.filter(im => ms[im.name]?.pick === 'keep').length
})

function sel(i) { emit('select', i) }

watch(() => props.current, async () => {
  await nextTick()
  const el = strip.value?.querySelector('.fs-thumb.cur')
  el?.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' })
})

/* 标记操作 */
function mark(name, patch) { emit('mark', { name, patch }) }
function setPick(im, v) { mark(im.name, { pick: (props.meta?.[im.name]?.pick === v) ? null : v }) }
function setStar(im, n) { mark(im.name, { star: (props.meta?.[im.name]?.star === n) ? 0 : n }) }
const COLORS = [
  { id: 'red', c: '#e5534b' }, { id: 'yellow', c: '#e0a63f' }, { id: 'green', c: '#57ab5a' },
  { id: 'blue', c: '#539bf5' }, { id: 'purple', c: '#b083f0' },
]
function setColor(im, id) { mark(im.name, { color: (props.meta?.[im.name]?.color === id) ? null : id }) }
</script>

<template>
  <div class="fs">
    <!-- 工具行：筛选/排序/旗标/星级/色标/统计 -->
    <div class="fs-bar">
      <el-dropdown trigger="click" @command="v => filterMode = v">
        <span class="fs-opt">{{ filterLabel[filterMode] }} <PxIcon name="chevron-down"/></span>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item v-for="(l, k) in filterLabel" :key="k" :command="k">{{ l }}</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <el-dropdown trigger="click" @command="v => sortMode = v">
        <span class="fs-opt">排序：{{ sortMode === 'name' ? '文件名' : '导入顺序' }} <PxIcon name="chevron-down"/></span>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="name">文件名排序</el-dropdown-item>
            <el-dropdown-item command="seq">导入顺序</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <span class="fs-sep"></span>
      <span class="fs-opt" title="留用当前图" @click="images[current] && setPick(images[current], 'keep')">
        <PxIcon name="flag"/> 留用
      </span>
      <span class="fs-opt reject" title="排除当前图" @click="images[current] && setPick(images[current], 'reject')">
        <PxIcon name="x"/> 排除
      </span>
      <span class="fs-stars" title="为当前图打星">
        <span v-for="n in 5" :key="n" class="fs-star"
              :style="{ color: (meta?.[images[current]?.name]?.star || 0) >= n ? 'var(--px-star)' : '' }"
              @click="images[current] && setStar(images[current], n)"><PxIcon name="star" :size="13"/></span>
      </span>
      <span class="fs-sep"></span>
      <span class="fs-colors" title="当前图色标">
        <span v-for="c in COLORS" :key="c.id" class="fs-cdot"
              :class="{ on: meta?.[images[current]?.name]?.color === c.id }"
              :style="{ background: c.c }"
              @click="images[current] && setColor(images[current], c.id)"></span>
      </span>
      <div style="flex:1"></div>
      <span class="fs-count">已选 {{ selectedCount }} 张（共 {{ images.length }} 张）</span>
      <div class="px-icon-btn fs-add" title="添加图片" @click="emit('add')">
        <PxIcon name="plus"/>
      </div>
    </div>
    <!-- 缩略图 -->
    <div ref="strip" class="fs-strip">
      <div v-for="im in shown" :key="im.name"
           class="fs-thumb" :class="{ cur: im._i === current, reject: meta[im.name]?.pick === 'reject' }"
           @click="sel(im._i)">
        <img :src="API.imageUrl(im.path)" loading="lazy" draggable="false" />
        <div v-if="meta[im.name]?.pick === 'keep'" class="fs-badge keep"><PxIcon name="flag"/></div>
        <div v-if="meta[im.name]?.pick === 'reject'" class="fs-badge reject"><PxIcon name="x"/></div>
        <div v-if="(meta[im.name]?.star || 0) > 0" class="fs-star">{{ meta[im.name].star }}<PxIcon name="star"/></div>
        <div v-if="meta[im.name]?.color" class="fs-dot"
             :style="{ background: (COLORS.find(c => c.id === meta[im.name].color) || {}).c }"></div>
        <div class="fs-name" :title="im.name">{{ im.name }}</div>
      </div>
      <el-empty v-if="!shown.length" description="无符合筛选的照片" :image-size="40" style="margin:auto" />
    </div>
  </div>
</template>

<style scoped>
.fs { height: 110px; display: flex; flex-direction: column; background: var(--px-bg-2);
  border-top: 1px solid var(--px-line); box-shadow: 0 -1px 0 rgba(0,0,0,.2); }
.fs-bar { display: flex; align-items: center; gap: 8px; padding: 3px 12px; border-bottom: 1px solid var(--px-line); }
.fs-opt { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; color: var(--px-text-2);
  cursor: pointer; padding: 2px 6px; border-radius: 4px; white-space: nowrap;
  transition: all 120ms var(--px-ease); }
.fs-opt:hover { background: var(--px-bg-3); color: var(--px-text); }
.fs-opt .el-icon { font-size: 10px; }
.fs-opt.reject:hover { color: #e5534b; }
.fs-sep { width: 1px; height: 12px; background: var(--px-line-2); }
.fs-stars { display: inline-flex; align-items: center; gap: 1px; cursor: pointer; }
.fs-star { display: inline-flex; align-items: center; color: var(--px-text-3); cursor: pointer; }
.fs-star:hover { color: var(--px-star); }
.fs-colors { display: inline-flex; align-items: center; gap: 4px; }
.fs-cdot { width: 10px; height: 10px; border-radius: 50%; cursor: pointer; opacity: .55;
  border: 2px solid transparent; }
.fs-cdot:hover { opacity: 1; }
.fs-cdot.on { opacity: 1; border-color: #fff; }
.fs-count { font-size: 11px; color: var(--px-text-3); white-space: nowrap; }
.fs-add { width: 24px; height: 24px; border: 1px dashed var(--px-line-2); }

.fs-strip { flex: 1; display: flex; gap: 8px; padding: 6px 12px; overflow-x: auto; align-items: center; }
.fs-thumb { position: relative; flex: none; width: 68px; height: 56px; border-radius: var(--px-radius-sm); overflow: hidden;
  cursor: pointer; border: 2px solid transparent; background: var(--px-bg-3);
  transition: all 160ms var(--px-ease); }
.fs-thumb:hover { transform: translateY(-2px); box-shadow: var(--px-shadow-2); }
.fs-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; opacity: .82; transition: opacity .15s; }
.fs-thumb:hover img { opacity: 1; }
.fs-thumb.cur { border-color: var(--px-accent); box-shadow: 0 0 0 2px var(--px-accent-weak); }
.fs-thumb.cur img { opacity: 1; }
.fs-thumb.reject img { opacity: .35; filter: grayscale(.8); }
.fs-badge { position: absolute; top: 2px; width: 15px; height: 15px; display: flex; align-items: center;
  justify-content: center; font-size: 10px; }
.fs-badge.keep { left: 2px; color: #9ce7b4; }
.fs-badge.reject { left: 2px; color: #e5534b; }
.fs-star { position: absolute; top: 1px; right: 3px; display: inline-flex; align-items: center; gap: 1px;
  font-size: 9px; color: #f2c97d; text-shadow: 0 0 3px rgba(0,0,0,.8); }
.fs-dot { position: absolute; bottom: 14px; left: 50%; transform: translateX(-50%); width: 7px; height: 7px;
  border-radius: 50%; box-shadow: 0 0 3px rgba(0,0,0,.6); }
.fs-name { position: absolute; inset: auto 0 0 0; font-size: 8px; color: #d5d6db; text-align: center;
  background: linear-gradient(transparent, rgba(0,0,0,.75)); padding: 6px 0 1px;
  overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
</style>
