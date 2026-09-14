"""固化流程 01/02/03/04 阅读版（合订单文档）：打包 + 可选上传到公开仓库（2026-09-14）。

把应用内新手指引「固化流程」四条链路的 GitHub 阅读版打包成可直接落入公开仓库的
目录结构；上传需要代理，联网动作由用户在有代理的环境自行执行。

打包内容（来源 = 本仓库工作树，唯一真源）：
  docs/guide/curing-flows.md                 （01 批量生图 / 02 小说转合集卡 /
                                                03 ST 卡转合集卡 / 04 自定义环节 合订）
  docs/assets/guide/curing-process-1..9.png  （配图，从 md 内引用关系自动收集）
  docs/tutorials/README.md                   （图解引导表：含固化流程行）

刻意不含 docs/guide/create-curing-process.md——「自定义环节」2026-09-14 已并入
curing-flows.md 的 04 节（配图 curing-process-6..9.png），分篇不再单独对外，
与前端 newcomerGuide.ts 的分组保持一致。

用法：
  python scripts/publish_guide_pack.py                 # 打包到 _tmp/guide-pack/ 并校验
  python scripts/publish_guide_pack.py --zip           # 同上，另出 _tmp/guide-pack.zip
  python scripts/publish_guide_pack.py --repo <path>   # 打包 + 校验 + 拷入公开仓库 clone
                                                       # 并 git add/commit/push（需代理，
                                                       # 由用户自行执行）

校验（公开文档红线，2026-09-02 定案）：
  1. md 内引用的相对图片必须真实存在（防死链）；
  2. md 内禁止指向内部文档的链接（tech-manual / memory / onboarding）；
  3. md 内禁止应用内阅读语法 doc: / guide:（只允许应用内用，不进远端文件）；
  4. 疑似密钥 pattern 一票否决。
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACK_DIR = PROJECT_ROOT / "_tmp" / "guide-pack"

GUIDE_DOCS = [
    "docs/guide/curing-flows.md",
]
README_DOC = "docs/tutorials/README.md"

# 公开文档红线：这些路径/语法不允许出现在对外 md 里
FORBIDDEN_LINK_PATTERNS = (
    re.compile(r"\((?:\.\./)+tech-manual/"),
    re.compile(r"\((?:\.\./)+memory/"),
    re.compile(r"\((?:\.\./)+onboarding/"),
    re.compile(r"\]\((?:doc|guide):"),  # 应用内阅读语法
)
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9_]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
IMAGE_REF = re.compile(r"\(\.{0,2}/(assets/guide/[A-Za-z0-9_\-./]+\.png)\)")


def _fail(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def _collect_images() -> list[str]:
    """从三篇 md 的图片引用关系自动收集（不手工枚举，防漏防多）。"""
    images: list[str] = []
    for doc in GUIDE_DOCS:
        text = (PROJECT_ROOT / doc).read_text(encoding="utf-8")
        for ref in IMAGE_REF.findall(text):
            rel = f"docs/{ref}"
            if rel not in images:
                images.append(rel)
    return images


def _validate() -> list[str]:
    """公开文档红线校验；返回待打包文件清单（md + 图 + README）。"""
    files = [*GUIDE_DOCS, *_collect_images(), README_DOC]
    for rel in files:
        if not (PROJECT_ROOT / rel).is_file():
            _fail(f"待打包文件不存在：{rel}")

    for rel in [*GUIDE_DOCS, README_DOC]:
        text = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
        for pat in FORBIDDEN_LINK_PATTERNS:
            if pat.search(text):
                _fail(f"{rel} 命中公开文档红线（内部链接/应用内语法）：{pat.pattern}")
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                _fail(f"{rel} 命中疑似密钥 pattern：{pat.pattern}")
    return files


def build_pack() -> list[str]:
    files = _validate()
    if PACK_DIR.exists():
        shutil.rmtree(PACK_DIR)
    for rel in files:
        dest = PACK_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT / rel, dest)
    print("打包完成 →", PACK_DIR)
    for rel in files:
        print("  +", rel)
    return files


def make_zip() -> Path:
    zip_path = PACK_DIR.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(PACK_DIR.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(PACK_DIR))
    print("zip 完成 →", zip_path)
    return zip_path


def push_to_repo(repo: str) -> None:
    """拷入公开仓库 clone 并 commit + push。代理由执行环境自行提供。"""
    target = Path(repo).resolve()
    if not (target / ".git").is_dir():
        _fail(f"--repo 不是 git 仓库：{target}")
    for rel in sorted(PACK_DIR.rglob("*")):
        if rel.is_file():
            dest = target / rel.relative_to(PACK_DIR)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(rel, dest)
    def _git(*args: str) -> str:
        r = subprocess.run(["git", "-C", str(target), *args],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            _fail(f"git {' '.join(args[:2])} 失败：{r.stderr.strip()[:300]}")
        return r.stdout.strip()

    if not _git("status", "--porcelain"):
        print("公开仓库无变更，跳过 commit/push。")
        return
    _git("add", "docs")
    _git("commit", "-m", "docs(guide): 固化流程 01/02/03/04 图解引导 + README 索引")
    _git("push")
    print("已推送到公开仓库。")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--zip", action="store_true", help="打包后额外生成 zip")
    ap.add_argument("--repo", default="", help="公开仓库 clone 路径；给定则拷入并 commit+push")
    args = ap.parse_args()

    build_pack()
    if args.zip:
        make_zip()
    if args.repo:
        push_to_repo(args.repo)


if __name__ == "__main__":
    main()
