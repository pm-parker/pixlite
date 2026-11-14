# PixLite 工程与发布基线

> 状态: 生效中（`chore/engineering-baseline` 引入）。目标是把"独立开发者靠热情推进"变成
> "按判据推进"——每一步是否合格, 由 CI 和 checklist 说了算, 不靠记忆和自觉。

## 1. CI 门禁矩阵

| 检查 | Workflow / Job | 何时跑 | 失败后果 |
|---|---|---|---|
| ruff 全仓 | `ci-quality.yml / lint` | 每个 push/PR | PR 变红, 不得合并 |
| pyright 类型检查 | `ci-quality.yml / typecheck` | 每个 push/PR | PR 变红（零 error 判据） |
| 版本一致性 | `ci-quality.yml / version-consistency` | 每个 push/PR | PR 变红 |
| webapp 构建 | `ci-quality.yml / webapp-build` | 每个 push/PR | PR 变红 |
| 单测 + API 测试 | `build-fpk.yml / test` | push main / PR（`**/*.md` 除外） | package/release 全部跳过 |
| 预设出图回归 | `build-fpk.yml / preset-quality` | 同上 | release 被阻断 |
| fpk 打包 | `build-fpk.yml / package` | 同上 | release 被阻断 |

**发布链已存在**：`build-fpk.yml` L217 `release needs [package, preset-quality]`——出图回归
不过, tag 也发不出版。本基线确认并保持这条链不变。

**新增 `ci-quality.yml` 的理由**：`build-fpk.yml` 的 `paths-ignore: ['**.md','docs/**']`
意味着只改文档的 push 根本不触发任何检查；且类型检查此前完全缺席（`pyrightconfig.json`
存在但没有任何 job 使用它——配置躺着不用等于没有）。新 workflow 补上这两块, 并与
`build-fpk.yml` 解耦, 避免改动打包/发布链。

**类型判据**：零 error（本次 PR 已修掉存量 8 个 `reportOptionalMemberAccess` /
`reportOptionalSubscript`，全部集中在 `tests/test_beta4.py` 的 `cv2.imread` 返回值未判空）。
pyright 版本钉死 `1.1.414`（版本漂移会让 error 数忽上忽下）。**不许用 `# type: ignore`
把新错误压下去**——新错误就是新错误，修掉。

**合并纪律**：main 当前无分支保护（GitHub API 返回 404 "Branch not protected"）。
需属主在 GitHub → Settings → Branches 为 main 开启 "Require status checks"：
`lint`、`typecheck`、`version-consistency`、`webapp-build`、`test`。这一步是仓库设置、
非代码变更, 只能由属主操作一次。

## 2. 版本与 Changelog 规范

- **唯一版本源**: `fnpack/manifest` 的 `version` 字段（当前 `0.1.12-beta.5`）。
  `readme.md` 更新日志、`RELEASE_NOTES.md`、`fnpack/manifest` 的 `changelog` 字段与之同步。
- **自动门禁**: `tools/check_version.py` 校验三处一致（manifest / readme 标记 / RELEASE_NOTES
  首行）+ readme 更新记录含当前版本条目, 不一致即 CI 失败。历史上出现过 beta.2 缺 tag、
  RELEASE_NOTES 停在旧版本, 这类漂移以后由 CI 拦, 不靠记性。
- **版本语义**: `0.1.12-beta.5` = "0.1.12 的第 5 个 beta"。rc 阶段升 `0.1.12-rc.1`;
  正式版去掉后缀为 `0.1.12`。
- **tag 规则**: tag 名 = `v` + manifest version, 打 tag 前必须核对一致; 每个版本都要有
  tag（beta.2 的教训）。带 `-beta`/`-rc` 后缀的 tag 自动发为 prerelease
  （`build-fpk.yml` release job 已实现）。
- **CHANGELOG**: `readme.md` 的「更新记录」节为史册, 一行一个版本, 最新在上。

## 3. 发布前 Checklist（beta→rc→正式版判据）

tag 前逐项打勾, 全绿才允许 push tag:

- [ ] CI 全绿（lint / typecheck / version-consistency / webapp-build / test / preset-quality）
- [ ] manifest version 与即将打的 tag 一致；readme 更新日志、RELEASE_NOTES 已同步
- [ ] 精简包与 full 包都在干净环境构建成功（package job 已验证 manifest 完整性）
- [ ] **真机冒烟**: 飞牛 NAS/VM 安装 fpk → 建项目 → 出 1 张图 → 导出成功
- [ ] 出图回归基线未劣化（`preset-quality` artifact 的 `preset_quality.json` 与上版对比）
- [ ] 无 P0/P1 级未关闭 Issue 被本版引入

**阶段判据**：
- **beta**: CI 全绿 + 真机冒烟可选。允许带已知问题发布, 但 RELEASE_NOTES 必须如实列出。
- **rc**: beta 判据 + 真机冒烟必做 + 连续 7 天无新增 P0 回归 + 出图回归指标不劣化。
- **正式版**: rc 判据 + rc 跑满 14 天 + 至少一轮真实影楼样本（高分辨率/多人/逆光）
  出图回归通过。正式版发布后新功能进下一版本线, 不再动已发布的 tag。

## 4. 迭代节奏（双周迭代建议）

- **还债期（迭代 1，2 周）**：类型错误清零（已完成）、仓库瘦身合入（与并行重构 PR 对齐）、
  effects 模块单测从 0 起步（每模块 ≥1 个黄金用例）、真机冒烟脚本化。
  交付物：基线清零的 PR + 首份真机冒烟记录。
- **功能期（迭代 2-3，4 周）**：按用户反馈排优先级的功能迭代, 每个功能 PR 必须带测试;
  出图回归样本扩容（真实影楼样本入库 fixtures）。
  交付物：两个 beta 版本 + 扩容后的回归集。
- **付费版准备期（迭代 4，2 周）**：ADR-001 插件边界落地（op 注册表 + entitlement 开关）、
  授权校验模块（Ed25519 离线验签 + NAS 指纹绑定）、fpk 付费分发对接。
  交付物：rc.1 + 授权链路可演示。

## 5. 与其他工作的边界

- 本基线不碰 `.gitignore`、测试图资产、`TODO_LEDGER.md`——仓库瘦身由并行 PR 负责,
  避免冲突。
- `build-fpk.yml` 保持不动（判据已达标）, 新门禁独立放 `ci-quality.yml`。
- 商业化架构决策见 `docs/adr/ADR-001-plugin-and-billing-boundary.md`。
