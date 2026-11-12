# PixLite — 开源版"像素蛋糕"引擎（内部实验）

对齐目标: 像素蛋糕 10.0.5 全功能。参照物: ../pixcake-reverse/ (shader/参数/DAG 已全量提取)

架构 (对齐 PixCake 分层):
- engine/perception  = TSAI 检测层: MediaPipe(face mesh 478点+pose) + face-parsing(BiSeNet) + MODNet matting
- engine/effects     = MagicEngine 效果层: GLSL 移植 (源码: pixcake-reverse/pixcook_extracted/glsl2)
- engine/warp        = MLS 变形 (对齐 FaceSurgery/PoseRefine, 参数表: all_effects.json)
- engine/color       = TSRE 色彩层: rawpy 16bit + LUT + Reinhard 追色
- engine/pipeline    = CraftFormula DAG 执行器 + 一键模板 (婚纱/儿童/证件照/写真)
- server             = FastAPI + Web UI (Canvas/GLSL); Docker 打包; 飞牛 app 壳

## 模型下载

模型文件不入库（~750MB 公共开源权重），首次使用前下载：

```bash
python tools/download_models.py            # 直连
python tools/download_models.py --mirror   # 国内走 hf-mirror.com
```

## 飞牛 fnOS 安装包（自动构建）

`.github/workflows/build-fpk.yml` 用官方 [fnpack](https://developer.fnnas.com/docs/cli/fnpack/) 自动打包：

- push 到 `main` / PR：跑测试并打精简包，在 Actions 运行页的 Artifacts 下载 `.fpk`
- 推送 tag `vX.Y.Z`：版本号取 tag，同时构建精简包和内置模型的 `_full.fpk`，发布到 GitHub Release
- 手动运行（Actions → Build fnOS package → Run workflow）可勾选同时构建 full 包

本地打包：`python fnpack/build_pkg.py --fnpack /path/to/fnpack [--with-models] [--version X.Y.Z]`，产物在 `dist/`。
