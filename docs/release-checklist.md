# PixLite 发布前 Checklist

> 复制这份到发布 issue 里逐项打勾。判据来源见 `docs/engineering-baseline.md`。
> 未打满勾的项, 要么补做, 要么在 RELEASE_NOTES 里如实写明"未验证"。

版本号: `______`（必须等于 `fnpack/manifest` 的 `version`）　　Tag: `v______`

## A. 自动化门禁（CI 必须全绿）

- [ ] `lint` — ruff 全仓零告警
- [ ] `typecheck` — pyright 零 error
- [ ] `version-consistency` — manifest / readme / RELEASE_NOTES 三处一致
- [ ] `webapp-build` — 前端构建通过
- [ ] `test` — 单测 + API 测试全绿（模型相关项允许 skip, 需说明跳过数）
- [ ] `preset-quality` — 预设出图回归通过, `preset_quality.json` 与上版对比无劣化

## B. 产物

- [ ] 精简包 `fnnas.pixlite_<version>.fpk` 构建成功
- [ ] full 包 `fnnas.pixlite_<version>_full.fpk` 构建成功（tag / 手动勾选时）
- [ ] 包内文件清单校验通过（package job 已自动核对 manifest / app.tgz 关键路径）
- [ ] `.sha256` 校验文件随包产出

## C. 真机冒烟（rc 与正式版硬判据）

- [ ] 飞牛 NAS（或 VM）安装 fpk 成功
- [ ] 桌面图标可打开, 网关路由正常（`/app/fnnas-pixlite`）
- [ ] 建项目 → 选预设 → 出 1 张图 → 导出成功
- [ ] 批量目录模式跑通（含取消）
- [ ] 旧版本覆盖升级后模型保留、预设可读

## D. 文档与版本

- [ ] `RELEASE_NOTES.md` 重写为本次版本内容（首行含版本号）
- [ ] `readme.md` 更新记录新增一行, 版本号与 manifest 一致
- [ ] `fnpack/manifest` 的 `changelog` 字段已更新
- [ ] tag 名 = `v` + manifest version, 无遗漏（历史上 beta.2 曾缺 tag）
- [ ] beta/rc 后缀 → 自动 prerelease（CI 已实现, 无需手工设置）

## E. 收尾

- [ ] 本版引入的 P0/P1 Issue 已关闭或列入下版
- [ ] 付费相关变更同步 ADR-001 的 entitlement 清单（若有）

## 回滚

若发布后发现阻断级问题: 不删 tag（会破坏已安装用户的校验链）, 而是
发一个新的 hotfix tag（`v<version>-hotfix.1`）并在 RELEASE_NOTES 顶部标注问题与修复。
