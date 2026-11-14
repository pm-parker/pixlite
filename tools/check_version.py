#!/usr/bin/env python3
"""版本一致性检查 — 版本与 changelog 规范的自动化门禁。

唯一版本源是 `fnpack/manifest` 的 `version` 字段。本脚本校验三处必须一致:
  1. fnpack/manifest      version         (唯一源)
  2. readme.md            当前版本标记     `v{version}`
  3. RELEASE_NOTES.md     首行版本         `{version}`

任一处不一致即退出码 1 —— 版本漂移是最常见的发布事故来源 (曾出现 beta.2 缺 tag、
RELEASE_NOTES 停在旧版本), 交由 CI 在每次 push/PR 拦截。

用法:
    python tools/check_version.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "fnpack" / "manifest"
README = ROOT / "readme.md"
NOTES = ROOT / "RELEASE_NOTES.md"


def manifest_version() -> str:
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if line.split("=")[0].strip() == "version":
            return line.split("=", 1)[1].strip()
    raise SystemExit("fnpack/manifest 里找不到 version 字段")


def main() -> int:
    version = manifest_version()
    if not version:
        print("::error::fnpack/manifest 的 version 为空")
        return 1

    problems: list[str] = []

    readme = README.read_text(encoding="utf-8")
    if f"v{version}" not in readme:
        problems.append(f"readme.md 未标记当前版本 v{version}")

    notes = NOTES.read_text(encoding="utf-8") if NOTES.exists() else ""
    first_line = notes.splitlines()[0] if notes else ""
    if version not in first_line:
        problems.append(f"RELEASE_NOTES.md 首行未包含 {version} (当前首行: {first_line[:60]!r})")

    # changelog 规范: readme 的更新记录节里必须有当前版本条目
    if not re.search(r"^- \*\*" + re.escape(version) + r"\*\*", readme, re.M):
        problems.append(f"readme.md 更新记录里缺少 {version} 条目")

    if problems:
        print(f"::error::版本一致性检查失败 (manifest version = {version})")
        for p in problems:
            print(f"  - {p}")
        return 1

    print(f"版本一致性 OK: {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
