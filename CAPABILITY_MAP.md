# CAPABILITY MAP — 滑杆 → 引擎能力映射（C/D 组补齐：478 点 + 妆容 + 头发 + 身体）

约定：UI 滑杆 ±100，引擎 degree 类参数 0–1（`toEngineOverrides` 内 `v*0.01`），
smile/hair_recolor 为 0–100 / ±100 直传，smile_manage/teeth_align 为 5 档（UI 0–100 → level 0–5）。
所有 op 纯本地推理：OpenCV + SegFormer ONNX 解析 + YuNet 人脸 + MediaPipe
face_landmarks ONNX 478 点（含 irises 468–477，onnxruntime 直接运行，无 mediapipe
Python 包、无云端）。样张与 diff 数值见 `pm-parker/pixlite-assets` 仓库 `tests-qa/tests/out_c/`（diffs.json、e2e_diffs.json，
全链路全开 diff=10.081）。

## 478 点 landmark（P0 已解锁）

- 模型：`engine/perception/models/face_landmarks.onnx`（Google MediaPipe
  face_landmarks_detector.tflite 的 ONNX 转换版，HF senty-au/face_landmarks_detector-ONNX）
- 接口：`perception/landmarks.py get_landmarks(img, box) -> 478 pts + iris_l/iris_r`，
  pipeline 在 face_detect 后自动 `attach_mesh` 到每张脸
- 精度验证：mesh 眼/唇关键点与 SegFormer eye/lip mask bbox 吻合（±2px 级）
- 左右语义统一为「图像左/右」，运行时按质心解析（SegFormer l_/r_ 为主体左右）

## 真实生效（已进引擎 + 前端解灰）

| 前端滑杆 | 引擎 op（参数） | 引擎文件 | 算法 |
|---|---|---|---|
| 祛斑祛痘 / 祛痣 / 身体祛瑕疵 | `blemish(.mole/.body).degree` | effects/blemish.py | skin mask 高频离群→中值替换 |
| 祛油光 | `oil_control.degree` | effects/region_fix.py | 镜面高光低频压缩 |
| 祛黑眼圈 / 祛眼袋 | `dark_circle/.eye_bag.degree` | effects/region_fix.py | 眼下 ROI L 亏损提升 / 袋褶高频抑制 |
| 眼白净化 / 祛红血丝 | `eye_white/.red_vein.degree` | effects/region_fix.py | 巩膜提亮去饱和 / 红系去饱和 |
| 唇纹修整 | `lip_polish.degree` | effects/region_fix.py | 唇 mask 保边双边滤波 |
| 祛双下巴 | `double_chin.degree` | effects/wrinkle.py | 下巴-颈阴影带提亮 |
| 面部祛纹 9 项 + 祛颈纹 | `wrinkle_*.degree` | effects/wrinkle.py | 程序化线带 ROI 方向性低通 |
| 微笑嘴角 | `smile.degree` | warp/smile.py | 嘴角锚点径向上提 |
| 亮眼左 | `eye_brighten.degree` | effects/__init__.py | 眼 mask L 提升 |
| **亮眼右** | `eye_brighten_r.degree` | effects/eyes.py | 同上，图像右眼单侧 mask |
| **祛双眼皮贴左/右** | `lid_patch_l/r.degree` | effects/eyes.py | 478 点眼皮带：亮贴片高频压缩+中值回填 |
| **眼镜祛反光** | `antiglare.degree` | effects/eyes.py | eye_g mask（无眼镜时眼周回退）镜面高光压缩 |
| **眼神光** | `catchlight.degree` | effects/eyes.py | iris 中心 10 点钟软高光贴点 |
| **笑容管理（5档）** | `smile_manage.level` | effects/teeth.py | 嘴角 61/291 上提 + 脸颊 50/280 上外推 |
| **露龈调整左/右** | `gum_fix_l/r.degree` | effects/teeth.py | 半嘴上唇下压形变（forward warp） |
| **牙齿排齐（5档）** | `teeth_align.level` | effects/teeth.py | 牙 mask 三次形变收拢 + 中值混合 |
| **牙齿祛瑕疵** | `teeth_fix.degree` | effects/teeth.py | 牙 mask L 离群点中值替换 |
| 牙齿美白 | `teeth_whiten.degree` | effects/__init__.py | mouth 区去饱和提亮 |
| **高光立体** | `makeup_highlight.degree` | effects/makeup.py | 478 点 T 区/额头/颧骨 LAB L 加亮羽化 |
| **阴影立体** | `makeup_shadow.degree` | effects/makeup.py | 鼻侧/颊窝/下颌侧 L 压暗羽化 |
| **眉毛增强左/右** | `brow_enh_l/r.degree` | effects/makeup.py | brow mask 加饱和+压明度 |
| **眼妆增强左/右** | `eye_makeup_l/r.degree` | effects/makeup.py | 眼周 ring mask 加饱和 |
| **唇妆增强** | `lip_makeup.degree` | effects/makeup.py | 唇 mask 加饱和+微压明度 |
| 祛碎发 / 头发光泽 / 祛白发 / 发色调整 | `flyaway/.hair_recolor/.white_hair` | effects/hair.py | 同 B 组 |
| **头发柔顺** | `hair_smooth.degree` | effects/hair2.py | hair mask 双边滤波保边平滑 |
| **发缝填充** | `hair_gap.degree` | effects/hair2.py | 发内暗带中值填充 |
| **颅顶调整** | `crown.degree` | effects/hair2.py | 头顶发区径向上提（限 hair mask 内） |
| **发量调整左/右** | `hair_volume_l/r.degree` | effects/hair2.py | 发缘单侧径向外扩 |
| **发色统一** | `hair_unify.degree` | effects/hair2.py | 发色向主色（圆均值 H + 中值 S）角向聚合 |
| 瘦脸 | `facesurgery.degree` | warp/facesurgery.py | 24 点轮廓内推 |
| 磨皮/肤色/颗粒/调色/LUT | `skin_retouch/skin_tone/grain/tone/hsl/lut` | effects/color | 同基线 |
| **瘦腰** | `body_waist.degree` | effects/body.py | cloth/neck 代理带水平内推液化 |
| **瘦腿** | `body_leg_thin.degree` | effects/body.py | 下身带水平内推 |
| **长腿** | `body_leg_long.degree` | effects/body.py | 下 38% 区域纵向拉伸（过渡带防缝） |
| **祛副乳** | `axilla.degree` | effects/body.py | 胸侧暗部 L 亏损提亮 |
| **祛妊娠纹** | `striae.degree` | effects/body.py | 腹部纹路高频抑制（wrinkle 思路复用） |

## 置灰（仅剩云端/大模型类 + 无本地分割依据项）

- 基础祛瑕：祛汗珠（脸部）、祛鼻孔瑕疵、下颌线增强、祛胡须
- AI 祛除：AI祛纹身、AI祛体毛（需大模型）
- 双眼皮（贴片祛除已生效，生成类双眼皮暂缓）
- 颅顶调整（头顶）——与颅顶调整同引擎，合并前暂灰
- 手部/脚部：手部中性灰、祛青筋、手/脚部美型（无手部分割模型，维持）
- 全身其余：AI塑形、小头、脖子长度/瘦脖子、直角肩、肩膀宽度、AI瘦手臂左/右、丰臀（需身体关键点/分割）
- 皮肤调整 tab 各项按原状（磨皮变体/AI 类）

## 端到端验证（tests/test_face.jpg）

- 28 个新 op 单项 diff 全部 > 0（0.001–4.85，diffs.json）
- 全链路 slider-drag 模拟：`build_preset('wedding_basic', overrides)`，
  每个新 op overrides 开 vs 基线 diff 全 > 0（e2e_diffs.json）；
  28 op 全开 composite diff=10.081（e2e_cgroup_full.jpg / e2e_cgroup_baseline.jpg）
- 已知精度限制：teeth_align/gum_fix 在闭嘴/无齿图上走 mask 回退路径（效果温和）；
  身体类 op 用 cloth/neck 解析类作身体代理，头特写图上效果限于背景带
