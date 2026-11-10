import { reactive, watch } from 'vue'

/* ---------- API 封装 ---------- */
export const API = {
  presets: () => fetch('/api/presets', { headers: _h() }).then(r => r.json()),
  browse: (path = '') => fetch(`/api/browse?path=${encodeURIComponent(path)}`, { headers: _h() }).then(r => r.json()),
  upload: (file) => {
    const fd = new FormData(); fd.append('file', file)
    return fetch('/api/upload', { method: 'POST', body: fd, headers: _h() }).then(r => r.json())
  },
  imageUrl: (absPath) => `/api/image?path=${encodeURIComponent(absPath)}${apiKey() ? '&key=' + apiKey() : ''}`,
  async retouch(file, preset, overrides, routing, person) {
    const fd = new FormData()
    fd.append('file', file, file.name)
    fd.append('preset', preset)
    if (overrides && Object.keys(overrides).length) fd.append('overrides', JSON.stringify(overrides))
    if (routing && Object.keys(routing).length) fd.append('routing', JSON.stringify(routing))
    if (person) fd.append('person', person)
    const resp = await fetch('/api/retouch', { method: 'POST', body: fd, headers: _h() })
    const data = await resp.json()
    if (!resp.ok) throw new Error(data.error || `精修请求失败(${resp.status})`)
    return data.job
  },
  job: (id) => fetch(`/api/job/${id}`, { headers: _h() }).then(r => r.json()),
  gpu: () => fetch('/api/gpu', { headers: _h() }).then(r => r.json()),
  resultUrl: (id) => `/api/result/${id}${apiKey() ? '?key=' + apiKey() : ''}`,
  poll: (id, onProgress, interval = 400, timeoutMs = 300000) => new Promise((resolve, reject) => {
    const bail = (fn, arg) => { clearInterval(t); clearTimeout(guard); fn(arg) }
    const guard = setTimeout(() => bail(reject, new Error('精修超时（服务无响应 5 分钟）')), timeoutMs)
    const t = setInterval(async () => {
      try {
        const j = await API.job(id)
        onProgress && onProgress(j)
        if (j.status === 'done') bail(resolve, j)
        else if (j.status === 'error' || j.status === 'unknown') bail(reject, new Error(j.error || '任务失败'))
      } catch (e) { bail(reject, e) }
    }, interval)
  }),
  async batch(srcDir, dstDir, preset, recursive = true, workers = 2) {
    const fd = new FormData()
    fd.append('src_dir', srcDir); fd.append('preset', preset)
    if (dstDir) fd.append('dst_dir', dstDir)
    fd.append('recursive', recursive); fd.append('workers', workers)
    return fetch('/api/batch', { method: 'POST', body: fd }).then(r => r.json())
  },
  batchStatus: (id) => fetch(`/api/batch/${id}`, { headers: _h() }).then(r => r.json()),
}

/* ---------- 项目模型（localStorage 持久化） ----------
   project: { id, name, preset, dir, images: [{name, path}], createdAt }
   flag: { star: 0-5? -> boolean, pick, color: null|red|yellow|green|blue|purple }
   per-image settings: { preset, overrides, jobId (缓存最近一次结果) }
--------------------------------------------------- */
const LS_KEY = 'pixlite.projects.v1'

function load() {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw) return JSON.parse(raw)
  } catch (e) { /* ignore */ }
  return []
}

/* NAS 部署鉴权: key 由安装向导写入 localStorage, 所有 /api 请求自动携带 */
export const apiKey = () => localStorage.getItem('pixlite_api_key') || ''
export const _h = () => apiKey() ? { 'X-API-Key': apiKey() } : {}

/* ---------- 前端错误收集 (诊断转发用) ---------- */
const _errBuf = []
export function trackError(msg, ctx = '') {
  _errBuf.push({ ts: new Date().toISOString().slice(11, 19), msg: String(msg).slice(0, 300), ctx })
  if (_errBuf.length > 50) _errBuf.shift()
}
export function getErrors() { return [..._errBuf] }

if (typeof window !== 'undefined') {
  window.addEventListener('error', e => trackError(e.message, `${e.filename}:${e.lineno}`))
  window.addEventListener('unhandledrejection', e => trackError(e.reason?.message || String(e.reason), 'promise'))
}

export async function buildDebugReport() {
  let env = null
  try {
    const r = await fetch('/api/debug/report?frontend_events=' + encodeURIComponent(JSON.stringify(getErrors())), { headers: _h() })
    env = await r.json()
  } catch (e) { trackError('诊断接口失败: ' + e.message, 'debug') }
  const lines = []
  lines.push('=== PixLite 诊断报告 ===')
  lines.push('时间: ' + (env?.generated_at || new Date().toLocaleString()))
  lines.push(`环境: Python ${env?.python || '?'} / ${env?.os || navigator.platform}`)
  lines.push(`GPU: ${env?.gpu?.vendor || '?'} → [${(env?.gpu?.providers || []).join(', ')}]`)
  lines.push(`LUT目录: ${env?.lut_dir_ok ? 'OK' : '缺失!'}  预设目录: ${env?.presets_dir_ok ? 'OK' : '缺失!'}  模型数: ${env?.models_found ?? '?'}`)
  if (env?.server_events?.length) {
    lines.push('', '--- 服务端最近事件 ---')
    for (const ev of env.server_events.slice(-8))
      lines.push(`[${ev.kind}] ${new Date(ev.ts * 1000).toLocaleTimeString()} ${ev.msg.split('\n').slice(0, 4).join(' | ')}`)
  }
  const errs = getErrors()
  if (errs.length) {
    lines.push('', '--- 前端最近报错 ---')
    for (const e of errs.slice(-10)) lines.push(`[${e.ts}]${e.ctx ? '(' + e.ctx + ')' : ''} ${e.msg}`)
  }
  return lines.join('\n')
}

export const store = reactive({
  projects: load(),
  save() {
    localStorage.setItem(LS_KEY, JSON.stringify(this.projects))
  },
  createProject(name, dir, images, preset) {
    const p = {
      id: 'p' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
      name, dir, preset, images,
      createdAt: Date.now(),
      meta: {},       // per image-name: {star,pick,color,exported}
      settings: {},   // per image-name: {preset, overrides, jobId}
    }
    this.projects.unshift(p)
    this.save()
    return p
  },
  getProject(id) { return this.projects.find(p => p.id === id) },
  removeProject(id) {
    this.projects = this.projects.filter(p => p.id !== id)
    this.save()
  },
})

/* ---------- 参数面板定义（像素蛋糕官方命名对标）
   key 映射引擎 op.params；未映射的引擎参数用 wip:true（显示但置灰，tooltip「引擎开发中」） */
const S = (key, label, def = 0, min = -100, max = 100, extra = {}) =>
  ({ key, label, min, max, step: 1, def, ...extra })

/* ---------- 人像美化：实机 11 Tab 结构（pixcake 10.0.5 UIA 实测名单） ----------
   tab -> sections -> params。带 engine 映射的生效；其余 __wip 置灰（引擎开发中）。
   左右对称参数为独立滑杆（瘦脸左/瘦脸右）。 */
export const BEAUTY_TABS = [
  {
    id: 'bt1', name: '祛除瑕疵', sections: [
      { name: '基础祛瑕', params: [
        S('blemish.degree', '祛斑祛痘', 0, 0, 100, { engine: 'blemish.degree', scale: 0.01 }),
        S('face_enhance.degree', '深度磨皮', 0, 0, 100, { engine: 'face_enhance.degree', scale: 0.01 }),
        S('quality_enhance.degree', '画质修复(超清)', 0, 0, 100, { engine: 'quality_enhance.degree', scale: 0.01 }),
        S('blemish_mole.degree', '祛痣', 0, 0, 100, { engine: 'blemish_mole.degree', scale: 0.01 }),
        S('oil_control.degree', '祛油光（脸部）', 0, 0, 100, { engine: 'oil_control.degree', scale: 0.01 }),
        S('__wip_sweat_face', '祛汗珠（脸部）', 0),
        S('dark_circle.degree', '祛黑眼圈', 0, 0, 100, { engine: 'dark_circle.degree', scale: 0.01 }),
        S('eye_bag.degree', '祛眼袋', 0, 0, 100, { engine: 'eye_bag.degree', scale: 0.01 }),
        S('__wip_nostril', '祛鼻孔瑕疵', 0),
        S('lip_polish.degree', '唇纹修整', 0, 0, 100, { engine: 'lip_polish.degree', scale: 0.01 }),
        S('double_chin.degree', '祛双下巴', 0, 0, 100, { engine: 'double_chin.degree', scale: 0.01 }),
        S('__wip_jawline', '下颌线增强', 0),
        S('__wip_beard', '祛胡须', 0),
        S('wrinkle_neck.degree', '祛颈纹', 0, 0, 100, { engine: 'wrinkle_neck.degree', scale: 0.01 }),
      ]},
      { name: '保护开关', toggles: ['胡型保护', '妆容保护'] },
      { name: '面部祛纹', params: [
        S('wrinkle_forehead.degree', '祛抬头纹', 0, 0, 100, { engine: 'wrinkle_forehead.degree', scale: 0.01 }),
        S('wrinkle_sichuan.degree', '川字纹', 0, 0, 100, { engine: 'wrinkle_sichuan.degree', scale: 0.01 }),
        S('wrinkle_eye.degree', '眼周纹', 0, 0, 100, { engine: 'wrinkle_eye.degree', scale: 0.01 }),
        S('wrinkle_nose.degree', '鼻背纹', 0, 0, 100, { engine: 'wrinkle_nose.degree', scale: 0.01 }),
        S('wrinkle_nasolabial_l.degree', '法令纹左', 0, 0, 100, { engine: 'wrinkle_nasolabial_l.degree', scale: 0.01 }),
        S('wrinkle_nasolabial_r.degree', '法令纹右', 0, 0, 100, { engine: 'wrinkle_nasolabial_r.degree', scale: 0.01 }),
        S('wrinkle_cheek.degree', '脸颊纹', 0, 0, 100, { engine: 'wrinkle_cheek.degree', scale: 0.01 }),
        S('wrinkle_puppet.degree', '木偶纹', 0, 0, 100, { engine: 'wrinkle_puppet.degree', scale: 0.01 }),
        S('wrinkle_mouth.degree', '嘴周纹', 0, 0, 100, { engine: 'wrinkle_mouth.degree', scale: 0.01 }),
      ]},
      { name: '身体', params: [
        S('blemish_body.degree', '身体祛瑕疵', 0, 0, 100, { engine: 'blemish_body.degree', scale: 0.01 }),
        S('axilla.degree', '祛副乳', 0, 0, 100, { engine: 'axilla.degree', scale: 0.01 }),
        S('striae.degree', '祛妊娠纹', 0, 0, 100, { engine: 'striae.degree', scale: 0.01 }),
      ]},
      { name: 'AI 祛除', params: [S('__wip_tattoo', 'AI祛纹身', 0), S('__wip_bodyhair', 'AI祛体毛', 0)] },
    ],
  },
  {
    id: 'bt2', name: '皮肤调整', sections: [
      { name: '磨皮质感', method: [
        { id: 'neutral_flat', label: '中性灰平整', param: S('skin_retouch.strength', '程度', 0, -100, 100, { engine: 'skin_retouch.strength', scale: 0.01, base: 0.6 }) },
        { id: 'neutral_3d', label: '中性灰立体', param: S('__wip_ms_n3d', '程度', 0) },
        { id: 'texture', label: '质感磨皮', param: S('__wip_ms_tex', '程度', 0) },
        { id: 'hf_face', label: '高低频磨皮脸', param: S('__wip_ms_hff', '程度', 0) },
        { id: 'hf_body', label: '高低频磨皮身体', param: S('__wip_ms_hfb', '程度', 0) },
        { id: 'aqua_face', label: '水润磨皮脸', param: S('__wip_ms_aqf', '程度', 0) },
        { id: 'aqua_body', label: '水润磨皮身体', param: S('__wip_ms_aqb', '程度', 0) },
      ]},
      { name: '轮廓与丰盈', params: [
        S('__wip_collarbone', '锁骨增强', 0), S('__wip_face_full', '全脸丰盈', 0),
        S('__wip_part_full', '面部丰盈', 0), S('__wip_local_adj', '局部调整', 0),
      ]},
      { name: '皮肤质感', params: [
        S('__wip_gloss', '光泽度', 0),
        S('skin_retouch.texture_keep', '纹理度', 0, -100, 100, { engine: 'skin_retouch.texture_keep', scale: 0.0035, base: 0.35 }),
        S('__wip_skin_sharp', '清晰度', 0),
      ]},
      { name: '皮肤颜色', params: [
        S('skin_tone.l', '肤色透亮', 0, -100, 100, { engine: 'skin_tone.hsl2', idx: 2, scale: 0.08 }),
        S('__wip_skin_whiten', '肤色美白', 0),
        S('skin_tone.s', '皮肤红润', 0, -100, 100, { engine: 'skin_tone.hsl2', idx: 1, scale: 0.08 }),
      ]},
      { name: 'AI全身肤色统一', params: [
        S('__wip_unify_multi', 'AI多人肤色统一', 0), S('__wip_unify_face', 'AI肤色统一脸', 0),
        S('__wip_unify_body', 'AI肤色统一身体', 0), S('__wip_unify_body2', '肤色统一身体', 0),
      ]},
      { name: '肤色选择', params: [
        S('__wip_skin_temp', '肤色色温', 0), S('__wip_skin_tone', '肤色色调', 0),
      ]},
      { name: 'AI补光', params: [S('__wip_relight', 'AI补光', 0)] },
    ],
  },
  {
    id: 'bt3', name: '面部重塑', sections: [
      { name: '脸型', params: [
        S('facesurgery.degree', '瘦脸左', 0, -100, 100, { engine: 'facesurgery.degree', scale: 0.4, base: 40 }),
        S('__wip_thin_r', '瘦脸右', 0),
        S('__wip_temple_l', '太阳穴左', 0), S('__wip_temple_r', '太阳穴右', 0),
        S('__wip_cheekbone_l', '颧骨左', 0), S('__wip_cheekbone_r', '颧骨右', 0),
        S('__wip_jaw_l', '下颌左', 0), S('__wip_jaw_r', '下颌右', 0),
        S('__wip_ai_smoothface', 'AI面部流畅', 0), S('__wip_jaw_correct', '颌面矫正', 0),
        S('__wip_smallface', '小脸', 0),
        S('__wip_vface_l', 'V脸左', 0), S('__wip_vface_r', 'V脸右', 0),
        S('__wip_shortface', '短脸', 0),
        S('__wip_facewidth_l', '脸宽左', 0), S('__wip_facewidth_r', '脸宽右', 0),
        S('__wip_hairline', '发际线', 0),
        S('__wip_forehead_w_l', '额头宽度左', 0), S('__wip_forehead_w_r', '额头宽度右', 0),
        S('__wip_forehead_h_l', '额头高度左', 0), S('__wip_forehead_h_r', '额头高度右', 0),
        S('__wip_philtrum', '人中', 0), S('__wip_midface', '中庭', 0), S('__wip_lowface', '下庭', 0),
        S('__wip_chin_len', '下巴长短', 0),
        S('__wip_pointchin_l', '尖下巴左', 0), S('__wip_pointchin_r', '尖下巴右', 0),
        S('__wip_tuckchin', '收下巴', 0), S('__wip_facelift', '全脸提拉', 0),
      ]},
    ],
  },
  {
    id: 'bt4', name: '表情管理', sections: [
      { name: '表情', params: [
        S('smile.degree', '微笑嘴角', 0, 0, 100, { engine: 'smile.degree', scale: 1 }),
        S('smile_manage.level', '笑容管理', 0, 0, 100, { engine: 'smile_manage.level', scale: 0.05 })] },
    ],
  },
  {
    id: 'bt5', name: '牙齿美白', sections: [
      { name: '露龈调整', params: [S('gum_fix_l.degree', '露龈调整左', 0, 0, 100, { engine: 'gum_fix_l.degree', scale: 0.01 }), S('gum_fix_r.degree', '露龈调整右', 0, 0, 100, { engine: 'gum_fix_r.degree', scale: 0.01 })] },
      { name: '牙齿', params: [
        S('teeth_fix.degree', '牙齿祛瑕疵', 0, 0, 100, { engine: 'teeth_fix.degree', scale: 0.01 }),
        S('teeth_whiten.degree', '牙齿美白', 0, -100, 100, { engine: 'teeth_whiten.degree', scale: 0.005, base: 0.5 }),
        S('teeth_align.level', '牙齿排齐', 0, 0, 100, { engine: 'teeth_align.level', scale: 0.05 }),
      ]},
    ],
  },
  {
    id: 'bt6', name: '眼睛增强', sections: [
      { name: '亮眼', params: [
        S('eye_brighten.degree', '亮眼左', 0, -100, 100, { engine: 'eye_brighten.degree', scale: 0.004, base: 0.4 }),
        S('eye_brighten_r.degree', '亮眼右', 0, -100, 100, { engine: 'eye_brighten_r.degree', scale: 0.004, base: 0.4 }),
      ]},
      { name: '眼周净化', params: [
        S('red_vein.degree', '祛红血丝', 0, 0, 100, { engine: 'red_vein.degree', scale: 0.01 }),
        S('eye_white.degree', '眼白净化', 0, 0, 100, { engine: 'eye_white.degree', scale: 0.01 }),
        S('lid_patch_l.degree', '祛双眼皮贴左', 0, 0, 100, { engine: 'lid_patch_l.degree', scale: 0.01 }),
        S('lid_patch_r.degree', '祛双眼皮贴右', 0, 0, 100, { engine: 'lid_patch_r.degree', scale: 0.01 }),
        S('antiglare.degree', '眼镜祛反光', 0, 0, 100, { engine: 'antiglare.degree', scale: 0.01 }),
      ]},
      { name: '眼神与双眼皮', params: [
        S('catchlight.degree', '眼神光', 0, 0, 100, { engine: 'catchlight.degree', scale: 0.01 }),
        S('__wip_doublelid', '双眼皮', 0),
      ]},
    ],
  },
  {
    id: 'bt7', name: '妆容调整', sections: [
      { name: 'AI 妆容迁移', params: [
        S('makeup_transfer.degree', '妆强度', 0, 0, 100, { engine: 'makeup_transfer.degree', scale: 0.01 }),
        S('makeup_transfer.level', '妆感档位(1淡/2舞台/3浓)', 2, 1, 3, 1, { engine: 'makeup_transfer.level' }),
      ]},
      { name: '原厂整套妆', select: 'makeup_suit.suit_id', params: [
        S('makeup_suit.degree', '套妆强度', 0, 0, 100, { engine: 'makeup_suit.degree', scale: 0.01 }),
      ]},
      { name: '立体', params: [
        S('makeup_highlight.degree', '高光立体', 0, 0, 100, { engine: 'makeup_highlight.degree', scale: 0.01 }),
        S('makeup_shadow.degree', '阴影立体', 0, 0, 100, { engine: 'makeup_shadow.degree', scale: 0.01 })] },
      { name: '五官妆容', params: [
        S('brow_enh_l.degree', '眉毛增强左', 0, 0, 100, { engine: 'brow_enh_l.degree', scale: 0.01 }),
        S('brow_enh_r.degree', '眉毛增强右', 0, 0, 100, { engine: 'brow_enh_r.degree', scale: 0.01 }),
        S('eye_makeup_l.degree', '眼妆增强左', 0, 0, 100, { engine: 'eye_makeup_l.degree', scale: 0.01 }),
        S('eye_makeup_r.degree', '眼妆增强右', 0, 0, 100, { engine: 'eye_makeup_r.degree', scale: 0.01 }),
        S('lip_makeup.degree', '唇妆增强', 0, 0, 100, { engine: 'lip_makeup.degree', scale: 0.01 }),
      ]},
    ],
  },
  {
    id: 'bt8', name: '头发调整', sections: [
      { name: '头发', params: [
        S('flyaway.degree', '祛碎发', 0, 0, 100, { engine: 'flyaway.degree', scale: 0.01 }),
        S('hair_smooth.degree', '头发柔顺', 0, 0, 100, { engine: 'hair_smooth.degree', scale: 0.01 }),
        S('hair_gap.degree', '发缝填充', 0, 0, 100, { engine: 'hair_gap.degree', scale: 0.01 }),
        S('crown.degree', '颅顶调整', 0, 0, 100, { engine: 'crown.degree', scale: 0.01 }),
        S('__wip_crown_top', '颅顶调整（头顶）', 0),
        S('hair_volume_l.degree', '发量调整左', 0, 0, 100, { engine: 'hair_volume_l.degree', scale: 0.01 }),
        S('hair_volume_r.degree', '发量调整右', 0, 0, 100, { engine: 'hair_volume_r.degree', scale: 0.01 }),
        S('hair_recolor.light', '头发光泽', 0, -100, 100, { engine: 'hair_recolor.light', scale: 1 }),
        S('white_hair.degree', '祛白发', 0, 0, 100, { engine: 'white_hair.degree', scale: 0.01 }),
        S('hair_unify.degree', '发色统一', 0, 0, 100, { engine: 'hair_unify.degree', scale: 0.01 }),
        S('hair_recolor.hue', '发色调整（色相）', 0, -100, 100, { engine: 'hair_recolor.hue', scale: 1 }),
        S('hair_recolor.sat', '发色调整（饱和）', 0, -100, 100, { engine: 'hair_recolor.sat', scale: 1 }),
      ]},
    ],
  },
  {
    id: 'bt9', name: '手部美化', sections: [
      { name: '手部', params: [
        S('__wip_hand_gray', '手部中性灰', 0), S('__wip_hand_vein', '祛青筋', 0),
        S('__wip_hand_shape', '手部美型', 0),
      ]},
    ],
  },
  {
    id: 'bt10', name: '脚部美化', sections: [
      { name: '脚部', params: [
        S('__wip_foot_gray', '脚部中性灰', 0), S('__wip_foot_vein', '祛青筋（脚）', 0),
        S('__wip_foot_shape', '脚部美型', 0),
      ]},
    ],
  },
  {
    id: 'bt11', name: '全身美型', sections: [
      { name: '全身', params: [
        S('__wip_body_ai', 'AI塑形', 0), S('__wip_smallhead', '小头', 0),
        S('__wip_neck_len', '脖子长度', 0),
        S('__wip_neck_thin_l', '瘦脖子左', 0), S('__wip_neck_thin_r', '瘦脖子右', 0),
        S('__wip_square_shoulder', '直角肩', 0), S('__wip_shoulder_w', '肩膀宽度', 0),
        S('__wip_arm_thin_l', 'AI瘦手臂左', 0), S('__wip_arm_thin_r', 'AI瘦手臂右', 0),
        S('body_waist.degree', '瘦腰', 0, 0, 100, { engine: 'body_waist.degree', scale: 0.01 }),
        S('body_leg_thin.degree', '瘦腿', 0, 0, 100, { engine: 'body_leg_thin.degree', scale: 0.01 }),
        S('body_leg_long.degree', '长腿', 0, 0, 100, { engine: 'body_leg_long.degree', scale: 0.01 }),
        S('__wip_hip', '丰臀', 0),
      ]},
    ],
  },
]
/* 兼容旧引用 */
export const PORTRAIT_GROUPS = BEAUTY_TABS.map(t => ({ id: t.id, name: t.name, children: t.sections.map((s, i) => ({ id: t.id + 's' + i, name: s.name, params: (s.params || []).concat((s.method || []).map(m => m.param)) })) }))

/* 右侧功能面板板块（实机折叠列表顺序，含联机/自定义/帮助/反馈） */
export const RIGHT_PANEL_SECTIONS = [
  { id: 'beauty', name: '人像美化' },
  { id: 'color', name: 'AI色彩' }, { id: 'adjust', name: '调色' },
  { id: 'local', name: '局部' }, { id: 'portrait', name: '人像' },
  { id: 'image', name: '图像' }, { id: 'clothes', name: '衣物' },
  { id: 'pet', name: '宠物' }, { id: 'postcolor', name: '后调色' },
  { id: 'crop', name: '裁剪' }, { id: 'history', name: '历史' },
  { id: 'tether', name: '联机' }, { id: 'custom', name: '自定义' },
  { id: 'help', name: '帮助' }, { id: 'feedback', name: '反馈' },
]

/* 色彩面板 17 子 tab（实机顺序，纵向排列） */
export const COLOR_SUBTABS = [
  { id: 'ai_color', name: 'AI色彩' }, { id: 'profile', name: '配置文件' },
  { id: 'filter', name: '滤镜' }, { id: 'wb', name: '白平衡' },
  { id: 'tone', name: '影调' }, { id: 'levels', name: '色阶' },
  { id: 'curve', name: '曲线' }, { id: 'hsl', name: 'HSL' },
  { id: 'color_editor', name: '色彩编辑器' }, { id: 'grading', name: '颜色分级' },
  { id: 'selective', name: '可选颜色' }, { id: 'detail', name: '细节' },
  { id: 'grain', name: '颗粒' }, { id: 'lens', name: '镜头调整' },
  { id: 'perspective', name: '透视矫正' }, { id: 'calibrate', name: '校准' },
  { id: 'creative', name: '创意色彩' },
]

/* 预设分类树（实机 9 类） */
export const PRESET_TREE = [
  { id: 'none', name: '无效果', match: p => /none|origin/.test(p) },
  { id: 'skin', name: '肤质肤色', match: p => /skin|face/.test(p) },
  { id: 'wedding', name: '婚纱', match: p => /wedding/.test(p) },
  { id: 'wedding_photo', name: '婚礼跟拍', match: p => /event/.test(p) },
  { id: 'film', name: '胶片', match: p => /film/.test(p) },
  { id: 'child', name: '儿童', match: p => /child|kid/.test(p) },
  { id: 'newborn', name: '新生儿', match: p => /baby|newborn/.test(p) },
  { id: 'pregnant', name: '孕妇', match: p => /pregnan|matern/.test(p) },
  { id: 'family', name: '全家福', match: p => /family/.test(p) },
]
export const HSL_BANDS = ['红色', '橙色', '黄色', '绿色', '浅绿', '蓝色', '紫色', '洋红']
export const WB_PRESETS = [
  { label: 'AI校准', t: 0, g: 0, ai: 'white_balance' }, { label: '原照设置', t: 0, g: 0 },
  { label: '自动', t: 4, g: -2 }, { label: '阴天', t: 15, g: 5 }, { label: '日光', t: 8, g: 0 },
  { label: '荧光', t: -5, g: 10 }, { label: '闪光', t: 5, g: -5 },
]

export const PRESET_LABELS = {
  wedding_basic: '婚纱一键',
  facesurgery_headnarrow: '小头调整',
  lut_names: '滤镜彩蛋',
}

export function defaultOverrides() {
  const o = {}
  const walk = (groups) => {
    for (const g of groups) {
      if (g.children) for (const c of g.children) for (const p of c.params) o[p.key] = p.def
      // color defaults
    }
  }
  walk(PORTRAIT_GROUPS)
  // color panel defaults
  o['color_match.strength'] = 0
  o['color_match.tone'] = 0
  o['color_match.color'] = 0
  o['lut.filter'] = ''
  o['lut.degree'] = 70
  o['white_balance.temperature'] = 0
  o['white_balance.tint'] = 0
  for (const k of ['exposure', 'contrast', 'brightness', 'highlight', 'shadow', 'whites', 'blacks',
                   'texture', 'clarity', 'dehaze', 'vibrance', 'saturation'])
    o['tone.' + k] = 0
  for (let i = 0; i < 8; i++) {
    o[`hsl.${i}.h`] = 0; o[`hsl.${i}.s`] = 0; o[`hsl.${i}.l`] = 0
  }
  o['grain.density'] = 0   // UI 0-100，引擎基线 0.04
  o['grain.size'] = 30
  // F6 色阶/曲线/颜色分级/可选颜色/色彩编辑器
  o['levels.in_black'] = 0; o['levels.in_white'] = 255; o['levels.gamma'] = 1
  o['levels.out_black'] = 0; o['levels.out_white'] = 255
  for (const ch of ['rgb', 'r', 'g', 'b']) o['curves.' + ch] = ''
  for (const z of ['shadow', 'mid', 'high'])
    o[`color_grading.${z}.h`] = 0, o[`color_grading.${z}.s`] = 0, o[`color_grading.${z}.l`] = 0
  o['color_editor.global_hue'] = 0
  for (let i = 0; i < 8; i++) {
    o[`color_editor.bands.${i}.h`] = 0; o[`color_editor.bands.${i}.s`] = 0; o[`color_editor.bands.${i}.l`] = 0
  }
  for (const c of ['red', 'yellow', 'green', 'cyan', 'blue', 'magenta', 'white', 'neutral', 'black'])
    for (const f of ['c', 'm', 'y', 'k', 'absolute']) o[`selective_color.${c}.${f}`] = f === 'absolute' ? false : 0
  // 人物属性
  o.__person = 'female'
  o.__scope = 'single'
  return o
}

/* UI 值(±100) -> 引擎真实值。
   - 带 engine 映射的滑杆: base + v*scale, clamp
   - skin_tone.hsl2 idx -> hsl 数组 delta
   - __wip* 参数不提交（引擎开发中）
   - color_match 有参考图才生效 */
export function toEngineOverrides(values) {
  const out = {}
  const scale = {}
  // collect engine mappings from definitions
  const collect = (groups) => {
    for (const g of groups) if (g.children)
      for (const c of g.children) for (const p of c.params) if (p.engine) scale[p.key] = p
  }
  collect(PORTRAIT_GROUPS)
  let skinHsl = null
  const vals = { ...values }
  for (const [k, v] of Object.entries(vals)) {
    if (k.startsWith('__')) continue
    if (k === 'grain.density') {
      // UI 0..100 → 引擎密度 0..0.3 (0=引擎默认0.04); 高于100透传引擎原始值
      if (v) out[k] = v <= 100 ? Math.round((v / 100 * 0.3) * 1000) / 1000 : v
      continue
    }
    const m = scale[k]
    if (m) {
      if (m.engine === 'skin_tone.hsl2') {
        skinHsl = skinHsl || [0, 0, 0]
        skinHsl[m.idx] = (v || 0) * m.scale
        continue
      }
      const base = m.base !== undefined ? m.base : 0
      let ev = base + (v || 0) * (m.scale || 1)
      if (m.base !== undefined && m.min !== undefined && m.max !== undefined) ev = Math.min(Math.max(m.min, ev), m.max)
      out[m.engine] = m.engine === 'makeup_suit.suit_id' ? ev : (Math.round(ev * 1000) / 1000)
    } else if (k.startsWith('rt.')) {
      // routing passthrough: value already in engine scale (0..1), keep as-is
      out[k.slice(3)] = v
    } else if (k.startsWith('tone.') || k.startsWith('white_balance.') ||
               k.startsWith('hsl.') || k.startsWith('lut.') ||
               k.startsWith('mask.') || k.startsWith('ai_calibrate.') || k.startsWith('antiglare.') || k.startsWith('axilla.') || k.startsWith('blemish.') || k.startsWith('blemish_body.') || k.startsWith('blemish_mole.') || k.startsWith('body_leg_long.') || k.startsWith('body_leg_thin.') || k.startsWith('body_waist.') || k.startsWith('brow_enh_l.') || k.startsWith('brow_enh_r.') || k.startsWith('catchlight.') || k.startsWith('crown.') || k.startsWith('dark_circle.') || k.startsWith('double_chin.') || k.startsWith('eye_bag.') || k.startsWith('eye_brighten.') || k.startsWith('eye_brighten_l.') || k.startsWith('eye_brighten_r.') || k.startsWith('eye_makeup_l.') || k.startsWith('eye_makeup_r.') || k.startsWith('eye_white.') || k.startsWith('facesurgery.') || k.startsWith('flyaway.') || k.startsWith('gum_fix_l.') || k.startsWith('gum_fix_r.') || k.startsWith('hair_gap.') || k.startsWith('hair_recolor.') || k.startsWith('hair_smooth.') || k.startsWith('hair_unify.') || k.startsWith('hair_volume_l.') || k.startsWith('hair_volume_r.') || k.startsWith('lid_patch_l.') || k.startsWith('lid_patch_r.') || k.startsWith('lip_makeup.') || k.startsWith('lip_polish.') || k.startsWith('liquify_auto.') || k.startsWith('makeup_highlight.') || k.startsWith('makeup_shadow.') || k.startsWith('oil_control.') || k.startsWith('red_vein.') || k.startsWith('skin_retouch.') || k.startsWith('skin_tone.') || k.startsWith('smile.') || k.startsWith('smile_manage.') || k.startsWith('striae.') || k.startsWith('teeth_align.') || k.startsWith('teeth_fix.') || k.startsWith('teeth_whiten.') || k.startsWith('white_hair.') || k.startsWith('wrinkle.') ||
               k.startsWith('levels.') || k.startsWith('curves.') ||
               k.startsWith('color_grading.') || k.startsWith('selective_color.') ||
               k.startsWith('color_editor.')) {
      if (k === 'lut.filter') { if (v) out[k] = v; continue }
      if (k.startsWith('hsl.')) { if (v) out[k] = v; continue }
      if (k.startsWith('white_balance.')) { if (v) out[k] = v; continue }
      if (k.startsWith('tone.')) { if (v) out[k] = v; continue }
      if (k.startsWith('levels.')) {
        // in_white/out_white 默认值不算变化
        if (v !== undefined && ((k === 'levels.in_black' && v) || (k === 'levels.out_black' && v) ||
            (k === 'levels.in_white' && v !== 255) || (k === 'levels.out_white' && v !== 255) ||
            (k === 'levels.gamma' && v !== 1))) out[k] = v
        continue
      }
      if (k.startsWith('curves.')) { if (Array.isArray(v) && v.length >= 2) out[k] = v; continue }
      if (k.startsWith('mask.')) { if (v !== 0 && v !== '' && !(Array.isArray(v))) out[k] = v; else if (Array.isArray(v) && v.length >= 2) out[k] = v; continue }
      // color_grading / selective_color / color_editor: skip zero/false
      if (k.startsWith('selective_color.')) { if (v && v !== false) out[k] = v; continue }
      if (v) out[k] = v
      continue
    } else if (k === 'color_match.strength' || k === 'color_match.tone' || k === 'color_match.color') {
      continue // handled below with ref
    } else if (!k.startsWith('__')) {
      if (v !== undefined && v !== null && v !== 0) out[k] = v
    }
  }
  if (skinHsl && (skinHsl[0] || skinHsl[1] || skinHsl[2])) {
    // 引擎期望 24 元素: [h0..h7, s0..s7, l0..l7]。肤色跨橙红波段(0-25), delta 填全 8 波段保证覆盖
    const hd = Math.round(skinHsl[0]), sd = Math.round(skinHsl[1]), ld = Math.round(skinHsl[2])
    out['skin_tone.hsl'] = [
      ...Array(8).fill(hd), ...Array(8).fill(sd), ...Array(8).fill(ld),
    ]
  }
  // AI 追色：有参考图才发
  const st = values['color_match.strength'] || 0
  const refPath = values['color_match.ref_path']
  if (refPath) {
    out['color_match.ref_image'] = refPath
    out['color_match.strength'] = st > 0 ? st / 100 : 0.8
  }
  if (values['ai_calibrate.white_balance']) out['ai_calibrate.white_balance'] = true
  if (values['ai_calibrate.exposure']) out['ai_calibrate.exposure'] = true
  if (values['grain.size'] && values['grain.size'] !== 30) {
    // size 无独立引擎参数，微调 density（近似）
  }
  return out
}
