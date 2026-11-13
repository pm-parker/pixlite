# PixLite BUG 挖掘与修复报告

基线：`de1988e`（GitHub main）。环境：macOS arm64 / Python 3.13 / onnxruntime 1.31 / OpenCV 5.0，模型按 `tools/download_models.py` 的公开地址下载（beautygan 除外）。

## 结论

基线上原有 e2e 测试（`tests/run_e2e_c.py`）28 项中 **10 项输出与基线完全相同（功能失效）**；修复后 28/28 生效。
新增 `tests/test_regressions.py` 27 条回归用例：修复后全部通过，在原代码上 23 条失败（其余为新增防护用例）。

## BUG 清单（按严重度）

| # | 严重度 | 模块 | 问题 | 修复 |
|---|---|---|---|---|
| 1 | 高 | `engine/perception/landmarks.py` | 下载脚本提供的 478 点模型输入是 NCHW，代码喂 NHWC → 抛 INVALID_ARGUMENT，被 `attach_mesh` 静默吞掉。眼神光/笑容管理/露龈/牙齿排齐/高光阴影/双眼皮贴/整套妆全部失效（整套妆直接报错） | 按模型声明的输入形状自适应 NCHW/NHWC |
| 2 | 高 | `engine/color/__init__.py` `apply_lut` | 按像素 **坐标**（x→蓝, y→绿）而不是像素颜色查表，输出与图像内容无关；identity LUT 平均误差 63/255。92 个滤镜全部错误 | 按实测布局（tile=蓝，tile 内 x=红 y=绿）重建 3D LUT，三线性插值；identity 误差 0.44 |
| 3 | 高 | `engine/pipeline` `skin_tone` | OpenCV LAB a/b 是 0..255（128 中性），代码按 -128..127 处理并 clip 到 127 → 所有肤色像素被抹成中性灰；美白缩放中心错误 | 转为以 0 为中心的色度计算后再 +128 |
| 4 | 高 | `engine/pipeline` `_skin_mask` | `from .perception import mask_of`（模块不存在），异常被吞，永远返回 None → 美白/提亮作用到整张图，肤色统一从不执行 | 改为 `..perception` |
| 5 | 高 | `engine/effects/quality_enhance.py` | 模型是 fp16，代码喂 float32 → 画质修复整单失败；同时输入未转 RGB | 按模型输入 dtype 转换，BGR↔RGB |
| 6 | 高 | `engine/color` `white_balance` | 色温方向反了（+ 变冷）；负色温先乘后除完全抵消（无效）；色调 tint 方向反且负值三通道整体提亮 | 重写为 R/B、G/品红对称增益 |
| 7 | 高 | `engine/color` `hsl_adjust` | 8 个色带每带 22.5°，合计只覆盖 0–180°；蓝/紫/洋红颜色无论调哪个带都无效，"蓝色"带实际作用在绿色上 | 按红/橙/黄/绿/浅绿/蓝/紫/洋红真实色相中心做三角权重 |
| 8 | 中 | `engine/pipeline` | 预设缺 `face_parsing` 节点或通过 overrides 追加依赖 parsing 的节点时 `KeyError: 'parsing'` 整单失败 | 惰性计算 parsing（同时按当前图像尺寸校验） |
| 9 | 中 | `engine/pipeline` | `GLOBAL_OPS` 漏掉 `sky_replace/sky_enhance/quality_enhance`，无人脸的风景照上这些功能被静默跳过 | 补入全局节点列表 |
| 10 | 中 | `engine/effects/teeth.py` `teeth_fix` | 中值滤波按强度混合到 **整张图**（瑕疵掩码只用来判断是否执行）→ 牙齿祛瑕疵把全图糊掉 | 只在瑕疵点羽化替换 |
| 11 | 中 | `engine/color` `tone_adjust` | 前端有"亮度""祛雾"滑杆，引擎未实现，拖动无效 | 实现中间调 gamma 亮度与暗通道祛雾 |
| 12 | 中 | `server/api/main.py` | 无条件 mount `server/web`（.gitignore 的构建产物），新克隆/Docker 镜像（Dockerfile 不构建前端）import 即崩，测试脚本也跑不起来 | 目录存在才 mount，否则记录告警 |
| 13 | 中 | `server/api/main.py` `/api/retouch` | 先写文件并登记 running job，再校验 JSON 返回 400 → 留下永不结束的 job 和孤儿文件；preset 名直接拼路径（`../../x` 可读任意 .json），不存在的 preset 才在后台线程报错 | 先校验后落盘；preset 名白名单 + 存在性校验 |
| 14 | 中 | `server/api/main.py` `/api/batch` | 非法/非可执行 preset 返回 200 后在后台崩；batch 未经 `DictPipeline`；输出目录在源目录内时递归二次处理自己的输出 | 预校验 preset 返回 400；拒绝 dst 位于 src 内 |
| 15 | 中 | 前端多处 | `/api/batch`、`/api/filters`、`/api/makeup_suits`、`/api/ref`、`/api/exif`、`/api/preset_library`、滤镜缩略图未带 `X-API-Key` → 设置 `PIXLITE_TOKEN`（fpk 默认会设）后远程访问这些功能全部 401 | 统一带 `_h()` / `key` 参数 |
| 16 | 中 | `fnpack/build_pkg.py` | 先无条件删 `fnpack/etc` 再判断写死的作者 Windows LUT 路径 → 在其他机器运行一次就删掉仓库自带 92 个滤镜 | 源存在才替换；支持 `PIXLITE_LUT_SRC` |
| 17 | 低 | `server/api/main.py` | 空 `lut.filter`（前端"无滤镜"）整单报"LUT 滤镜不存在" | 空 filter 视为未选 |
| 18 | 低 | `/api/debug/report` | 前端事件 JSON 损坏时诊断接口本身 500 | 容错解析 |
| 19 | 低 | `/api/result` | job 为 done 但文件被 GC/删除时 FileResponse 抛 500 | 文件不存在返回 404 |
| 20 | 低 | `_gc_jobs` | 超限时可能淘汰运行中 job / 删除其源文件，前端轮询得到 unknown | 跳过 running job |
| 21 | 低 | `/api/exif` | 未做 `_path_ok` 校验；Pillow 返回 IFDRational，tuple 分支从不命中；`_rat` 引用未定义变量 | 加校验，统一数值/快门格式，删除死代码 |
| 22 | 低 | `perception.cached_parse` | 缓存 key 只用像素字节，同字节不同尺寸的图会命中错误尺寸的 parsing | key 加入 shape |
| 23 | 低 | `effects.skin_tone_hsl` / `hair_recolor` | 色相超出 0..179 后被 clip 而非环绕；最后一个肤色带漏掉 176–179 | 取模环绕，末带上界 180 |
| 24 | 低 | `effects/hair2.py` `hair_volume` | 左右分界固定用图像中线，人物不居中时左右判反 | 用发区质心 |
| 25 | 低 | `engine/warp` | 未使用的 `scipy` 导入，scipy 不在依赖中 | 删除 |
| 26 | 低 | `/api/retouch`、`/api/ref` | 上传文件扩展名未过滤；`cv2.imwrite` 在 Windows 中文路径静默失败；解码失败时报晦涩异常 | 扩展名白名单，`imencode().tofile()`，明确报错 |

`fnpack/target/`（fpk 打包暂存目录）已同步为修复后的 engine 与 server 代码。

## 验证

| 项 | 结果 |
|---|---|
| `python -m pytest tests/test_regressions.py` | 27 passed（原代码 23 failed） |
| `python tests/run_e2e_c.py` | 修复前 fails 10 项 → 修复后 `fails: none` |
| `python tests/run_out_c.py` | `fails: none` |
| `cd webapp && npm ci && npm run build` | 构建成功 |
| uvicorn 真实服务（含 `PIXLITE_TOKEN`）| health/首页 200；精修（白平衡+HSL+祛雾）、LUT 滤镜 FT00000005、3 张递归批处理均 done，输出为有效 JPEG；非法 preset 400 |

## 未覆盖 / 待确认

- `beautygan.onnx`（妆容迁移）未下载验证；RAW（rawpy）未测试。
- 浏览器端 UI 未做点击级回归，前端改动仅验证构建通过与接口鉴权逻辑。
- LUT 色彩效果按标准 512×512 布局实现，与像素蛋糕原厂渲染的逐像素一致性未比对。
- 未提交、未推送、未创建 PR/Issue。

## Issue 修复（#1–#12，v0.1.11）

逐条在 `11e6202` 上复现或按代码核对后处理。回归测试见 `tests/test_issues.py`（`python -m pytest`）。

| Issue | 核实结论 | 处理 |
|---|---|---|
| #1 未配置 token 时放行 + POSIX 无路径白名单 | 属实 | 未配置 `PIXLITE_TOKEN` 时非本机请求一律 401（`PIXLITE_ALLOW_NO_TOKEN=1` 显式放开）；fpk 首启自动生成 32 字节随机 token；所有客户端路径（browse/image/exif/batch/追色参考图）必须 realpath 后位于 `PIXLITE_ALLOWED_ROOTS` 内，防 `../` 和符号链接逃逸；URL `?key=` 只在 image/result/filter-thumb 三个资源路由接受 |
| #2 上传/批量资源无上限 | 属实 | 单文件字节上限（流式，超限删半成品）、解码前像素上限、单图并发、单批文件数、批量并发上限；批量取消接口；work 目录 72h TTL 清理；均可用环境变量调整 |
| #3 干净检出无法构建 | 部分属实（启动崩溃已在 11e6202 修复） | Dockerfile 改为多阶段：构建前端、拷贝 LUT、可选构建期下载并校验模型、启动自检；`build_pkg.py` 自动构建前端、移除机器相关路径、`--with-models` 先校验模型、产物结构校验失败非零退出 |
| #4 批量请求不带 API key | 已在 11e6202 修复 | 本次补充 `resp.ok` 错误处理 |
| #5 路径选择器丢失 POSIX 前导 `/` | 属实 | 面包屑保留 `/`；根列表条目按绝对路径直接进入 |
| #6 肤色效果回退到整图 | 部分属实（导入错误已在 11e6202 修复） | 掩码不可用时 fail closed：跳过局部肤色调整并记录原因，不再整图调整 |
| #7 牙齿祛瑕疵作用于整图 | 已在 11e6202 修复 | 已有回归测试 `test_teeth_fix_is_local` |
| #8 背景颜色 degree=0 变黑 | 属实（复现：角落背景 → 纯黑） | 原背景与目标色按 degree 插值后再按 alpha 合成；degree=0 原样返回 |
| #9 多人照片只识别第一张脸 | 属实（复现：女+长辈合影都被判为 female） | 逐脸识别写入各自 `face['person']`；混合人群不再套用单一全图人群参数 |
| #10 模型无校验、版本未固定 | 属实 | 新增 `models_manifest.py`：全部模型固定到不可变 revision + size + sha256；下载后校验通过才落盘；加载前校验大小；`requirements.txt` 全部 `==` 固定 |
| #11 画质修复 degree=0 仍跑模型 | 属实（复现：degree=0 仍推理 4.5s） | degree≤0 直接返回原图，不加载模型 |
| #12 图像回归脚本不返回非零退出码 | 属实 | 两个旧脚本失败时 `exit 1`；新增 pytest 用例覆盖鉴权、路径边界、上传上限、缺失/截断模型、效果边界值 |

## 预设效果修复（v0.1.12-beta.5）

现象：套用场景预设后照片过亮 / 发灰、脸部色块、偏紫、变黑白、眼睛发灰、背景或身体被拉扯。用 8 张测试人像 × 54 个预设（预设库 52 个 + 婚纱一键 + 深度修复）按前端真实参数链路出图，逐节点测量差异定位到下列原因。

| # | 模块 | 问题 | 修复 |
|---|---|---|---|
| 1 | `engine/pipeline` `color_match` | 无参考图时回退 `auto_exposure`（L 通道线性 ×0.7~1.4），而基础预设的参考图恒为空 → 每个预设都被整图提亮或压暗，单节点 dL +25 / -42 | 无参考图不处理 |
| 2 | `webapp/src/lib.js` + `presets/preset_library.json` | 预设库存引擎尺度 (0..1)，前端当滑杆值再 ×0.01 → 祛斑 1→0.01 等约 20 项等于关闭；带 base 的滑杆反向（磨皮 0.2→0.602）；默认值也下发，覆盖基础预设（深度修复的 GFPGAN 0.8 被置 0） | 预设库迁移为滑杆尺度；默认值不下发；base 滑杆按引擎范围限幅；`skin_retouch.degree`→`strength`、`rosy`→`rouge` |
| 3 | `lib.js` 妆感档位 | `S()` 多传一个参数，engine 映射被丢弃，默认 level=2 透传 → 每个预设追加 0.7 强度妆容迁移 | 修正定义；未开妆强度时不下发档位 |
| 4 | `preset_library.json` 影调 | 7 个预设的 pf3000–3021 按 ×550 导入（tint 49.5/66/99、vibrance 57.8 等）→ 整图偏紫 | 按 `palette_pf_map.json` 重算 |
| 5 | `engine/pipeline` `skin_tone` | brighten 不限幅（预设 10 → L×1.6）；遮罩不含鼻子、最近邻硬边 → 脸部成块 | 0..1 限幅 + 肩部曲线；遮罩含鼻子、向内羽化 |
| 6 | `engine/perception` `SkinParser` | 模型输出 128×128，argmax 后最近邻放大 → 1024 图上 8×8 方块锯齿 | logits 双线性放大后 argmax |
| 7 | `engine/pipeline` 解析 | 眯眼 / 小脸时整只眼睛被判成皮肤，被祛斑、肤色处理磨平 | 用 478 点关键点补回眼睛标签 |
| 8 | `engine/effects/blemish.py` | 固定阈值 4 ≈ 皮肤纹理 / 噪声水平 → 改动 25%~72% 的皮肤（整脸磨平） | 平滑细节层 + 自适应阈值，远离遮罩边缘；改动面积降到 8%~17% |
| 9 | `engine/pipeline` `_auto_liquify` | 太阳穴径向外推，半径按整图尺寸 → 发际线处圆环 | 按脸宽取半径，水平向中线收 |
| 10 | `engine/effects/body.py` | `body_slim` 从内侧采样，实际加宽（100px→108px）；无身体时整图当身体；`axilla_fix` 固定取整图 25%~50% 高度，头像照里提亮眼眉 | 方向修正，按每张脸定位身体带，头像照不处理；祛副乳限下巴以下、软权重 |
| 11 | `server/api/main.py` `build_preset` | 基础预设没有的操作全部追加到末尾，在追色 / LUT / 颗粒之后执行；亮眼双眼与单眼节点重复 | 按 修复→修饰→肤色妆容→形变→背景→调色→颗粒 排序；亮眼合并为左右各一次 |
| 12 | `engine/effects` `grain` | `size` 被忽略，默认密度偏高 | size 控制颗粒粗细，默认密度减半 |
| 13 | `presets/preset_library.json` | 「户外草坪」用的 FT00000008「森林」是饱和度 0 的黑白 LUT | 改用 FT00000087「森系婚」 |
| 14 | `engine/color` `tone_adjust` | 曝光直接乘 sRGB 编码值，同 EV 明暗变化约为正常 2 倍 | 线性光中计算 |

验证：8 图 × 54 预设共 432 张出图零失败。|dL|>15 的出图 359→32，高光溢出 >5% 104→2，SSIM<0.7 104→0（中位数 0.758→0.918）。新增 `tests/test_presets.py`（17 条）和 `tests/test_preset_quality.py`（出图回归，需要模型）：后者在修复前代码上 162 项中 93 项超阈值，修复后全部通过；CI 新增 `preset-quality` 作业。

未处理：FT00000007「日系」把中性灰映射为偏暗偏蓝（「胶片·日系」「清新户外」整图暗 25~41），疑似素材问题，回归检查暂不检查这两个预设的亮度；21 个滤镜 PNG 的 IDAT 数据损坏（OpenCV / Pillow 均无法解码），选中会导致精修失败；全部改动未在飞牛真机验证。
