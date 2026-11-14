# ADR-001: 商业化路线下的插件化与计费模块边界

**Status:** Proposed
**Date:** 2026-02-27
**Deciders:** pm-parker（属主）；架构侧（本 ADR 作者）

## Context

PixLite 走向付费版/授权路线（fpk 付费分发、影楼单机授权），同时保持开源核心。当前现实：

- 引擎按效果域拆分良好（`engine/effects/*` 一域一文件、`engine/pipeline` 为 DAG 执行器、
  `perception/color/warp` 为能力层），**但 op 分发是 72 个 `if/elif op ==` 硬编码分支**
  （`engine/pipeline/__init__.py` L145-300+），新增 op 必须改 pipeline 源码——闭源插件
  无法不改核心代码而注册新效果。
- API 层是单文件 `server/api/main.py`（1025 行），鉴权（Issue #1 fail-closed + fnOS 网关
  免密）、jobs、presets、batch、filesystem 白名单、LUT 全在一个模块里互相穿插。
- 计费/授权为零起步：没有任何 entitlement（权利项）抽象，付费功能的"开/关"今天无处安放。

商业化提出的硬要求：① 闭源付费效果包可独立分发；② 免费/付费边界可声明、可校验、可撤销；
③ 核心开源仓库升级时，付费包不重编译也能继续工作（接口稳定）。

## Decision

**1. effect/op 注册表模式（plugin registry），不引入插件框架。**

在 `engine` 与付费包之间立一个最小契约：

```python
# engine/registry.py (开源核心提供)
@dataclass(frozen=True)
class OpSpec:
    op: str                      # preset JSON 里的 op 名, 全局唯一
    fn: Callable[[np.ndarray, dict, Ctx], np.ndarray]   # (bgr, params, ctx) -> bgr
    global_op: bool = False      # 无人脸是否仍执行 (并入 GLOBAL_OPS)
    entitlement: str | None = None   # None=免费; "pro_portrait_pack" 等为付费权利项
    version: int = 1             # 契约版本, 付费包声明兼容范围

registry.register(spec)         # 核心效果在 effects/__init__ 里注册
```

- pipeline 的 72 分支保留为"核心内置 op 的实现"，但分发入口改为**先查 registry、查不到
  再走内置分支**——存量 preset JSON 与行为零变化（渐进迁移，不做大爆炸重构）。
- 付费包 = 一个 Python 包（fpk 内独立目录 + entry point），安装时 `register()`，
  携带自己的模型文件与 `entitlement` 标记。**不做动态加载/热插拔**（NAS 场景无必要，
  风险大）。
- 未持有 entitlement 时 op 不是消失，而是**降级**：预设照常执行、该节点跳过并在
  warnings 里注明 "requires pro pack"——保证免费用户打开影楼预设不至于照片崩坏。

**2. server/api 按资源域拆模块，鉴权/计费走中间件层。**

```
server/api/
  main.py          # 仅组装: app = FastAPI(); include_router(...)
  deps.py          # 鉴权 (现有 _auth_decision + 网关中间件原样迁入)
  routes/jobs.py   # /api/retouch, /api/job/*, /api/result/*
  routes/presets.py# /api/presets, /api/preset_library, /api/makeup_suits
  routes/batch.py  # /api/batch*, 取消/状态
  routes/fs.py     # /api/image, /api/upload, /api/browse (文件系统白名单 Issue #1 原样保留)
  routes/system.py # /api/health, /api/auth/info, /api/models/*, /api/gpu, /api/debug/*
  licensing.py     # 授权模块: license 文件加载/验签 → entitlement 集合, 只读注入 registry
```

边界规则：routes 不得 import 彼此；全部经 `deps.py` 取鉴权后的 request 对象；
`licensing.py` 是唯一 entitlement 来源，pipeline/registry 只读查询，**引擎永不反向
依赖 server**（方向：server → engine 单向）。

**3. 多租户：明确不做（现阶段）。** NAS 单机授权 = 一个 license 一个 entitlement 集合，
无用户体系、无配额。多租户 SaaS 是另一个产品形态，触发条件见"后果"。

## Options Considered

### Option A: 注册表 + 目录式付费包（本决策）
| Dimension | Assessment |
|-----------|------------|
| Complexity | 低——一个 dataclass + dict, 无框架 |
| Cost | 迁移 72 分支可渐进, 每次迁一批 |
| 扩展性 | 付费包任意增加, 核心零改动 |
| 团队熟悉度 | 单人可维护, 无新概念 |

**Pros:** 存量 preset 零破坏；免费/付费边界落在一处（`entitlement` 字段）；闭源包
与开源核心解耦于一个稳定契约。**Cons:** 契约本身成为长期 API, 版本化要认真
（`version` 字段）；迁移期间 registry 与 elif 并存, 需防重名。

### Option B: 全量微插件框架（pluggy/hook 体系, 前端也插件化）
**Pros:** 前后端效果一致可插拔, 理论最灵活。**Cons:** 复杂度对一个单人 NAS 项目严重
超配；前端 Vue 滑杆面板插件化需要一整套 UI schema, 工作量数倍于收益；框架本身成为
新的维护负担。**否决。**

### Option C: 不插件化, 付费功能直接编译进 fpk, 用编译开关/仓库分叉管理
**Pros:** 最省事, 零新概念。**Cons:** 开源核心与付费版两套代码线, 每次核心升级都要
合并到付费线, 长期分叉不可维护；且"付费版 fpk"与"免费 fpk"的差异无法用同一管线
构建, CI 复杂化。**否决。**

## Trade-off Analysis

- **注册表 vs 框架**：商业化的真实需求只有"闭源包 + 付费开关"，注册表 100 行解决；
  框架解决的是我们不存在的问题。
- **降级 vs 硬墙**：付费 op 缺授权时选择降级执行（跳过+警告）而非报错，因为影楼场景
  预设是核心体验, 硬报错会把免费用户挡在门外——转化漏斗优先。
- **拆模块的时机**：现在拆是趁 main.py 还只有 1025 行; 等计费代码进来再拆, 缠绕翻倍。

## Consequences

- **变容易**：新增效果（无论免费付费）不再碰 pipeline 源码；付费边界可审计
  （grep entitlement 即得全量清单）；fpk 付费版与免费版同一构建管线, 仅打包清单不同。
- **变难**：`OpSpec` 契约一旦有外部付费包依赖就进入冻结期, 改动需走版本号；
  registry 重名/重复注册需要 CI 检查（并入 ci-quality）。
- **前置条件（引入计费/鉴权前必须完成）**：
  1. `engine/registry.py` 契约 + 至少 3 个存量 op 迁移示范（含 1 个 entitlement 示例）；
  2. `licensing.py` 验签（Ed25519, license 文件含 NAS 指纹 + entitlement 列表 + 期效）；
  3. CI 增加"registry 无重名、entitlement 集合与 license 声明一致"检查；
  4. 出图回归扩容（真实影楼样本）先行——付费用户付的钱买的是效果稳定, 回归集不扩,
     商业化只是放大翻车面。
- **需重新审视**：若未来出现多租户需求（一台 NAS 多影楼账号、按量计费），本 ADR 的
  licensing 单机假设作废, 届时新开 ADR。

## Action Items

1. [ ] 迭代 4（付费准备期）: 落地 `engine/registry.py` + 3 个 op 迁移（还债期之后）
2. [ ] `licensing.py` Ed25519 验签 + NAS 指纹绑定, 离线可验（影楼 NAS 不保证联网）
3. [ ] ci-quality 增加 registry 重名检查
4. [ ] 前端 ParamPanel 按 entitlement 显隐/降级提示（读 `/api/auth/info` 扩展字段）
