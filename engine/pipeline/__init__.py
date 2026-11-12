"""DAG pipeline executor — aligned with PixCake CraftFormula + effect.json semantics.
Executes preset JSON (see presets/wedding_basic.json) with population factors."""
import numpy as np, cv2, json, os
from .. import perception as P
from .. import effects as E
from .. import warp as W
from ..warp import facesurgery as FS
from .. import color as C
from ..color import advanced as CA
from ..color import masks as CM
from ..perception import landmarks as LM

def _skin_mask(ctx, bgr):
    """Soft skin mask at bgr resolution (None if unavailable). Uses perception
    mask_of (handles label-map parsing + resize)."""
    try:
        # 原为 `from .perception import mask_of` (engine.pipeline.perception 不存在),
        # ImportError 被下方 except 吞掉 → 永远返回 None → skin_tone 美白/提亮作用到
        # 整张图 (背景/衣服一起变色), 肤色统一 (unify) 永远不执行。
        from ..perception import mask_of
        m = mask_of(_parsing(ctx, bgr), 'skin', dilate=2)
        if m.shape[:2] != bgr.shape[:2]:
            m = cv2.resize(m, (bgr.shape[1], bgr.shape[0]), interpolation=cv2.INTER_NEAREST)
        return m
    except Exception:
        return None

def _parsing(ctx, bgr):
    """Lazy face-parsing: 预设缺 face_parsing 节点、或通过 overrides 追加了依赖
    parsing 的新节点时, 原代码直接 ctx['parsing'] KeyError 导致整单失败。"""
    par = ctx.get('parsing')
    if par is None or par.shape[:2] != bgr.shape[:2]:
        par = ctx['parsing'] = P.get_skin_parser()(bgr)
    return par


class _LazyCtx(dict):
    """ctx['parsing'] 缺失时按当前图像惰性计算 (见 _parsing)。"""
    def __init__(self):
        super().__init__()
        self.cur = None

    def __missing__(self, key):
        if key == 'parsing' and self.cur is not None:
            return _parsing(self, self.cur)
        if key == 'faces':
            return []
        raise KeyError(key)


# 不依赖人脸的全局节点: 无人脸时仍执行 (原列表漏了天空/画质类, 纯风景照
# 上天空替换/增强、画质修复全部被静默跳过)
GLOBAL_OPS = {'tone', 'hsl', 'lut', 'white_balance', 'grain', 'curves',
              'levels', 'color_grading', 'selective_color', 'color_editor',
              'ai_calibrate', 'color_match', 'raw_develop', 'face_detect',
              'face_parsing', 'sky_replace', 'sky_enhance', 'quality_enhance'}


class Pipeline:
    def __init__(self, preset_path):
        self.preset = json.load(open(preset_path, encoding='utf-8'))

    def detect_population(self, faces):
        """Aligned: Male/Female/Child/Older degree factors. Heuristic via face size ratio."""
        return 'adult'

    

    def run(self, bgr, progress_cb=None):
        preset = self.preset
        ctx = _LazyCtx()
        nodes = preset.get('nodes') or []
        factors = preset.get('population_factors', {})
        pop = 'adult'
        total = len(nodes)
        for i, node in enumerate(nodes):
            op = node['op']; pr = dict(node.get('params', {}))
            rt = preset.get('routing')
            if rt and op != 'face_detect':
                g = rt.get(pop)
                if g:
                    for k2, v2 in g.items():
                        base_, _, sub_ = k2.partition('.')
                        if base_ == op and sub_:
                            pr[sub_] = max(pr.get(sub_, 0), v2)
            self._apply_factors(op, pr, factors, pop)
            ctx.cur = bgr
            if ctx.get('no_face') and op not in GLOBAL_OPS:
                continue
            if node.get('mask'):   # F5 局部调色: op restricted to mask region
                mk = CM.build_mask(_parsing(ctx, bgr), node['mask'],
                                   feather=node.get('feather', 25))
                def _run_one(img, _n={'op': op, 'params': pr}):
                    p2 = Pipeline.__new__(Pipeline)
                    p2.preset = {'nodes': [_n]}
                    return p2.run(img)
                bgr = CM.apply_masked(bgr, _run_one, mk)
                if progress_cb:
                    progress_cb((i + 1) / total)
                continue
            if op == 'face_detect':
                det = P.get_face_detector()
                ctx['faces'] = det(bgr)
                if not ctx['faces']:
                    ctx['no_face'] = True  # portrait ops skip; global color ops continue
                try:
                    LM.attach_mesh(bgr, ctx['faces'])
                except Exception:
                    pass  # mesh optional; ops fall back to no-op
                # 自动人群识别 (insightface genderage, 96x96 RGB)
                if ctx['faces'] and not pr.get('person'):
                    from ..perception.agegender import classify
                    f0 = ctx['faces'][0]
                    info = classify(bgr, f0.get('box', (0, 0, bgr.shape[1], bgr.shape[0])))
                    if info:
                        pop = info['group']
                        ctx['person'] = info
                        for f_ in ctx['faces']:
                            f_['person'] = info
                if pr.get('person'):
                    pop = pr['person']  # manual override: 女/男/child/elder_f/elder_m
                pop = self.detect_population(ctx['faces']) if not (ctx['faces'] and pop != 'adult') else pop
            elif op == 'sky_replace':
                from ..effects.sky_fx import replace_sky
                bgr = replace_sky(bgr, mode=pr.get('mode', 'sunset'), degree=float(pr.get('degree', 1.0)))
            elif op == 'sky_enhance':
                from ..effects.sky_fx import enhance_sky
                bgr = enhance_sky(bgr, degree=float(pr.get('degree', 0.6)))
            elif op == 'blur_background':
                from ..effects.background_fx import blur_bg
                bgr = blur_bg(bgr, strength=float(pr.get('degree', 0.7)))
            elif op == 'bg_color':
                from ..effects.background_fx import replace_bg_color
                bgr = replace_bg_color(bgr, tuple(pr.get('color', (18,18,18))), degree=float(pr.get('degree', 1.0)))
            elif op == 'quality_enhance':
                # Real-ESRGAN 超分细节重建（对标像素蛋糕"画质修复"）
                from ..effects.quality_enhance import enhance_quality
                bgr = enhance_quality(bgr, degree=float(pr.get('degree', 0.6)))
            elif op == 'face_enhance':
                # GFPGAN 深度磨皮（开源效果层，对标像素蛋糕磨皮质感）
                from ..effects.gfp_enhance import enhance_face
                bgr = enhance_face(bgr, degree=float(pr.get('degree', 0.7)))
            elif op == 'face_parsing':
                parser = P.get_skin_parser()
                ctx['parsing'] = parser(bgr)
            elif op == 'blemish':
                bgr = E.blemish.blemish_remove(bgr, ctx['parsing'], ctx.get('faces'),
                                               degree=pr.get('degree', .5),
                                               mode=pr.get('mode', 'spot'),
                                               region=pr.get('region', 'face'))
            elif op == 'blemish_mole':
                bgr = E.blemish.blemish_remove(bgr, ctx['parsing'], ctx.get('faces'),
                                               degree=pr.get('degree', .5), mode='mole')
            elif op == 'blemish_body':
                bgr = E.blemish.blemish_remove(bgr, ctx['parsing'], ctx.get('faces'),
                                               degree=pr.get('degree', .5), region='body')
            elif op == 'oil_control':
                bgr = E.region_fix.oil_control(bgr, ctx['parsing'], pr.get('degree', .5))
            elif op == 'dark_circle':
                bgr = E.region_fix.dark_circle(bgr, ctx['parsing'], pr.get('degree', .5))
            elif op == 'eye_bag':
                bgr = E.region_fix.eye_bag(bgr, ctx['parsing'], pr.get('degree', .5))
            elif op == 'eye_white':
                bgr = E.region_fix.eye_white(bgr, ctx['parsing'], pr.get('degree', .5))
            elif op == 'red_vein':
                bgr = E.region_fix.red_vein(bgr, ctx['parsing'], pr.get('degree', .5))
            elif op == 'lip_polish':
                bgr = E.region_fix.lip_polish(bgr, ctx['parsing'], pr.get('degree', .5))
            elif op == 'wrinkle':
                bgr = E.wrinkle.wrinkle_smooth(bgr, ctx['parsing'], ctx.get('faces'),
                                               region=pr.get('region', 'cheek'),
                                               degree=pr.get('degree', .5))
            elif op.startswith('wrinkle_'):
                bgr = E.wrinkle.wrinkle_smooth(bgr, ctx['parsing'], ctx.get('faces'),
                                               region=op[len('wrinkle_'):],
                                               degree=pr.get('degree', .5))
            elif op == 'double_chin':
                bgr = E.wrinkle.double_chin(bgr, ctx['parsing'], ctx.get('faces'),
                                            degree=pr.get('degree', .5))
            elif op == 'smile':
                bgr = W.smile.smile_lift(bgr, ctx.get('faces', []), degree=pr.get('degree', 50))
            elif op == 'hair_recolor':
                bgr = E.hair.hair_recolor(bgr, ctx['parsing'], pr.get('hue', 0),
                                          pr.get('sat', 0), pr.get('light', 0))
            elif op == 'flyaway':
                bgr = E.hair.flyaway_remove(bgr, ctx['parsing'], pr.get('degree', .5))
            elif op == 'white_hair':
                bgr = E.hair.white_hair_remove(bgr, ctx['parsing'], pr.get('degree', .5))
            elif op == 'eye_brighten_l':
                bgr = E.eyes.eye_brighten_side(bgr, ctx['parsing'], pr.get('degree', .4), 'l')
            elif op == 'eye_brighten_r':
                bgr = E.eyes.eye_brighten_side(bgr, ctx['parsing'], pr.get('degree', .4), 'r')
            elif op == 'lid_patch_l':
                bgr = E.eyes.lid_patch_remove(bgr, ctx['parsing'], ctx.get('faces'), pr.get('degree', .5), 'l')
            elif op == 'lid_patch_r':
                bgr = E.eyes.lid_patch_remove(bgr, ctx['parsing'], ctx.get('faces'), pr.get('degree', .5), 'r')
            elif op == 'antiglare':
                bgr = E.eyes.glasses_antiglare(bgr, ctx['parsing'], pr.get('degree', .5))
            elif op == 'catchlight':
                bgr = E.eyes.catchlight(bgr, ctx['parsing'], ctx.get('faces'), max(pr.get('degree', 0) or 0, pr.get('degree_l', 0) or 0, pr.get('degree_r', 0) or 0) or .5)
            elif op == 'smile_manage':
                bgr = E.teeth.smile_manage(bgr, ctx.get('faces'), int(pr.get('level', 3)))
            elif op == 'gum_fix_l':
                bgr = E.teeth.gum_fix(bgr, ctx['parsing'], ctx.get('faces'), pr.get('degree', .5), 'l')
            elif op == 'gum_fix_r':
                bgr = E.teeth.gum_fix(bgr, ctx['parsing'], ctx.get('faces'), pr.get('degree', .5), 'r')
            elif op == 'teeth_align':
                bgr = E.teeth.teeth_align(bgr, ctx['parsing'], ctx.get('faces'), int(pr.get('level', 3)))
            elif op == 'teeth_fix':
                bgr = E.teeth.teeth_fix(bgr, ctx['parsing'], ctx.get('faces'), pr.get('degree', .5))
            elif op == 'makeup_highlight':
                bgr = E.makeup.makeup_highlight(bgr, ctx['parsing'], ctx.get('faces'), pr.get('degree', .5))
            elif op == 'makeup_shadow':
                bgr = E.makeup.makeup_shadow(bgr, ctx['parsing'], ctx.get('faces'), pr.get('degree', .5))
            elif op == 'brow_enh_l':
                bgr = E.makeup.brow_enhance(bgr, ctx['parsing'], pr.get('degree', .5), 'l')
            elif op == 'brow_enh_r':
                bgr = E.makeup.brow_enhance(bgr, ctx['parsing'], pr.get('degree', .5), 'r')
            elif op == 'eye_makeup_l':
                bgr = E.makeup.eye_makeup_enhance(bgr, ctx['parsing'], pr.get('degree', .5), 'l')
            elif op == 'eye_makeup_r':
                bgr = E.makeup.eye_makeup_enhance(bgr, ctx['parsing'], pr.get('degree', .5), 'r')
            elif op == 'lip_makeup':
                bgr = E.makeup.lip_makeup_enhance(bgr, ctx['parsing'], pr.get('degree', .5))
            elif op == 'makeup_transfer':
                from ..effects.makeup_transfer import transfer_makeup
                bgr = transfer_makeup(bgr, ctx.get('faces'),
                                      degree=pr.get('degree', .7),
                                      style=pr.get('style'),
                                      level=pr.get('level', 2))
            elif op == 'makeup_suit':
                from ..effects.makeup_suit import apply_suit
                bgr = apply_suit(bgr, ctx.get('faces'),
                                 suit_id=pr.get('suit_id', 'MU00000001'),
                                 degree=pr.get('degree', .7))
            elif op == 'hair_smooth':
                bgr = E.hair2.hair_smooth(bgr, ctx['parsing'], pr.get('degree', .5))
            elif op == 'hair_gap':
                bgr = E.hair2.hair_gap_fill(bgr, ctx['parsing'], pr.get('degree', .5))
            elif op == 'crown':
                bgr = E.hair2.crown_lift(bgr, ctx['parsing'], ctx.get('faces'), pr.get('degree', .5))
            elif op == 'hair_volume_l':
                bgr = E.hair2.hair_volume(bgr, ctx['parsing'], pr.get('degree', .5), 'l')
            elif op == 'hair_volume_r':
                bgr = E.hair2.hair_volume(bgr, ctx['parsing'], pr.get('degree', .5), 'r')
            elif op == 'hair_unify':
                bgr = E.hair2.hair_unify(bgr, ctx['parsing'], pr.get('degree', .5))
            elif op == 'body_waist':
                bgr = E.body.body_slim(bgr, ctx['parsing'], ctx.get('faces'), 'waist', pr.get('degree', .5))
            elif op == 'body_leg_thin':
                bgr = E.body.body_slim(bgr, ctx['parsing'], ctx.get('faces'), 'legs', pr.get('degree', .5))
            elif op == 'body_leg_long':
                bgr = E.body.leg_lengthen(bgr, ctx['parsing'], pr.get('degree', .5))
            elif op == 'axilla':
                bgr = E.body.axilla_fix(bgr, ctx['parsing'], ctx.get('faces'), pr.get('degree', .5))
            elif op == 'striae':
                bgr = E.body.stretch_marks(bgr, ctx['parsing'], ctx.get('faces'), pr.get('degree', .5))
            elif op == 'skin_retouch':
                bgr = E.skin_retouch(bgr, ctx['parsing'], pr.get('strength', .6), pr.get('texture_keep', .35))
            elif op == 'skin_tone':
                # cloud preset semantic params (pf 90004 透亮/90005 美白/90007 红润/
                # 90085-86 肤色统一) — aggregated, skin-masked
                bright = float(pr.get('brighten') or pr.get('degree') or 0)
                whiten = float(pr.get('whiten') or 0)
                rouge  = float(pr.get('rouge') or 0)
                unify  = max(float(pr.get('unify_face') or 0), float(pr.get('unify_body') or 0))
                if bright or whiten or rouge or unify:
                    # BUGFIX: OpenCV 8-bit LAB 的 a/b 通道是 0..255 (128=中性), 旧代码按
                    # 有符号 -128..127 处理: 末尾 clip 把 a/b>127 (所有偏暖/偏红像素,
                    # 即全部肤色) 截成中性灰, 美白的 *= 也以 0 而非 128 为中心缩放。
                    # 这里转成以 0 为中心的有符号色度再计算。
                    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
                    lab[..., 1:] -= 128.0
                    skin = _skin_mask(ctx, bgr)
                    L = lab[..., 0]
                    if bright:
                        L = L * (1 + 0.06 * bright)
                    if whiten:  # 美白: 降 a/b 黄红 + 提亮
                        L = L * (1 + 0.04 * whiten)
                        lab[..., 1] *= (1 - 0.15 * whiten)
                        lab[..., 2] *= (1 - 0.10 * whiten)
                    if rouge:   # 红润: a+ 
                        lab[..., 1] = np.clip(lab[..., 1] + 12 * rouge, -128, 127)
                    if unify and skin is not None:   # 肤色统一: 向皮肤均值收缩 a/b
                        sel = skin > 128
                        if sel.any():
                            ma = float(np.mean(lab[..., 1][sel]))
                            mb = float(np.mean(lab[..., 2][sel]))
                            w = 0.25 * unify * (skin.astype(np.float32) / 255.0)
                            lab[..., 1] = lab[..., 1] * (1 - w) + ma * w
                            lab[..., 2] = lab[..., 2] * (1 - w) + mb * w
                    lab[..., 0] = np.clip(L, 0, 255)
                    lab[..., 1] = np.clip(lab[..., 1] + 128.0, 0, 255)
                    lab[..., 2] = np.clip(lab[..., 2] + 128.0, 0, 255)
                    adj = cv2.cvtColor(np.round(lab).astype(np.uint8), cv2.COLOR_LAB2BGR)
                    if skin is not None:
                        m = (skin.astype(np.float32) / 255.0)[..., None]
                        bgr = (adj * m + bgr * (1 - m)).astype(np.uint8)
                    else:
                        bgr = adj
                if pr.get('hsl'):
                    bgr = E.skin_tone_hsl(bgr, ctx['parsing'], pr['hsl'])
            elif op == 'teeth_whiten':
                bgr = E.teeth_whiten(bgr, ctx['parsing'], pr.get('degree', .5))
            elif op == 'eye_brighten':
                bgr = E.eye_brighten(bgr, ctx['parsing'], pr.get('degree', .4))
            elif op == 'grain':
                bgr = E.grain(bgr, pr.get('density', .1), pr.get('luminance', .29))
            elif op == 'liquify_auto':
                bgr = self._auto_liquify(bgr, ctx.get('faces') or [], pr.get('profile', 'light'))
            elif op == 'facesurgery':
                bgr = FS.apply_head_narrow(bgr, ctx.get('faces', []),
                                           degree=pr.get('degree', 50),
                                           profile=pr.get('profile'),
                                           yaw=pr.get('yaw', 'normal'))
            elif op == 'color_match':
                ref = pr.get('ref_image')
                ref_img = None
                if ref and os.path.isfile(ref):   # np.fromfile: Windows 中文路径安全
                    ref_img = cv2.imdecode(np.fromfile(ref, dtype=np.uint8), cv2.IMREAD_COLOR)
                mask = None
                if dict.get(ctx, 'parsing') is not None:
                    from ..perception import mask_of
                    mask = mask_of(ctx['parsing'], 'skin')
                if ref_img is not None:
                    bgr = C.color_match(bgr, ref_img, protect_skin_mask=mask,
                                        strength=pr.get('strength', 1.0))
                else:
                    bgr = C.auto_exposure(bgr)
            elif op == 'lut':
                lut_path = pr.get('path')
                if lut_path and os.path.exists(lut_path):
                    lut = cv2.imread(lut_path)
                    bgr = C.apply_lut(bgr, lut, degree=pr.get('degree', 70))
            if op == 'raw_develop':
                pass  # raw handled at ingest
            elif op == 'tone':
                bgr = C.tone_adjust(bgr, pr)
            elif op == 'white_balance':
                bgr = C.white_balance(bgr, pr.get('temperature', 0), pr.get('tint', 0))
            elif op == 'hsl':
                for band, vals in pr.items():
                    if isinstance(vals, dict):
                        bgr = C.hsl_adjust(bgr, int(band), vals.get('h', 0),
                                           vals.get('s', 0), vals.get('l', 0))
            elif op == 'ai_calibrate':
                if pr.get('white_balance'):
                    bgr = C.auto_white_balance(bgr)
                if pr.get('exposure'):
                    bgr = C.auto_exposure(bgr)
            elif op == 'curves':   # F6 曲线: {rgb|rg|gb: [[x,y],..]} control points 0..255
                bgr = CA.curves(bgr, rgb=pr.get('rgb') or pr.get('rg'),
                                r=pr.get('r'), g=pr.get('g'), b=pr.get('b'))
            elif op == 'levels':   # F6 色阶
                bgr = CA.levels(bgr, in_black=pr.get('in_black', 0),
                                in_white=pr.get('in_white', 255),
                                gamma=pr.get('gamma', 1.0),
                                out_black=pr.get('out_black', 0),
                                out_white=pr.get('out_white', 255))
            elif op == 'color_grading':   # F6 颜色分级: shadow/mid/high {h,s,l}
                gp = {k: v for k, v in pr.items()}
                for zone in ('shadow', 'mid', 'high'):
                    flat = gp.pop(zone, None)
                    if isinstance(flat, dict):
                        gp[zone] = flat
                for k in [k for k in gp if k.startswith(('shadow.', 'mid.', 'high.'))]:
                    z, f = k.split('.', 1)
                    gp.setdefault(z, {})[f] = gp.pop(k)
                bgr = CA.color_grading(bgr, gp.get('shadow'), gp.get('mid'), gp.get('high'))
            elif op == 'selective_color':   # F6 可选颜色: {color: {c,m,y,k,absolute}}
                adj = {}
                for k, v in pr.items():
                    if k == 'adjustments' and isinstance(v, dict):
                        adj.update(v); continue
                    if '.' in k:
                        c, f = k.split('.', 1)
                        adj.setdefault(c, {})[f] = v
                bgr = CA.selective_color(bgr, adj)
            elif op == 'color_editor':   # F6 色彩编辑器: {0..7:{h,s,l}} + global_hue
                bands = pr.get('bands')
                if not isinstance(bands, dict):
                    bands = {}
                    for k, v in pr.items():
                        if k.startswith('bands.'):
                            b_, f = k[len('bands.'):].split('.', 1)
                            bands.setdefault(b_, {})[f] = v
                bgr = CA.color_editor(bgr, bands, pr.get('global_hue', 0))
            if progress_cb:
                progress_cb((i + 1) / total)
        return bgr

    def _auto_liquify(self, bgr, faces, profile):
        """Aligned: AIReshape/AutoLiquify — face-box driven subtle slimming (YuNet 5pt).
        Full landmark-driven version pending 478-pt model on py3.14."""
        out = bgr
        k = {'light': 0.015, 'medium': 0.03, 'strong': 0.05}.get(profile, 0.02)
        for f in faces:
            if 'box' not in f:
                continue
            x, y, fw, fh = f['box']
            # temples at upper corners of face box
            for cx in (x + fw * 0.08, x + fw * 0.92):
                out = W.liquify_push(out, (float(cx), float(y + fh * 0.35)),
                                     radius=min(bgr.shape[:2]) * 0.10,
                                     strength=min(bgr.shape[:2]) * k, direction=None)
        return out

    def _apply_factors(self, op, params, factors, pop):
        """Aligned: MaleDegreeFactor/ChildDegreeFactor/OlderDegreeFactor multiplication."""
        f = factors.get(pop, {})
        for key, val in f.items():
            base, _, sub = key.partition('.')
            if base == op and sub in params:
                params[sub] *= val
