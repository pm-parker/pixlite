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
