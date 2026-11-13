#!/usr/bin/env python3
"""定时依赖漏洞审计：四个生态共用一张豁免表（单属主）。

用法（仓库根执行）：
    python scripts/security_audit.py python   [--requirements backend/requirements.txt]
    python scripts/security_audit.py npm      [--cwd frontend]
    python scripts/security_audit.py cargo    [--cwd desktop/tauri]
    python scripts/security_audit.py dotnet   [--project desktop/Demiurge.Desktop/Demiurge.Desktop.csproj]
通用参数：--allowlist PATH / --today YYYY-MM-DD / --from-json PATH

判绿口径：
- 未豁免的公告 ⇒ 退出码 1；
- 豁免表里 expires 早于今天的条目 ⇒ 退出码 1（到期即失效，命中与否都要人工复核）；
- 豁免表结构错误 ⇒ 退出码 1；
- 本次未命中的有效豁免 ⇒ 只警告（提示删条目，避免豁免表静默腐烂）。

「扫描通过」只代表扫描当日公开漏洞库没有命中；漏洞库随上游更新会变化，
报告必须写清扫描日期与覆盖范围，不能当成长期安全结论。
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ALLOWLIST = Path(__file__).resolve().with_name("security-audit-allowlist.json")
TOOLS = ("pip-audit", "npm", "cargo", "dotnet")
_GHSA_RE = re.compile(r"GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}", re.IGNORECASE)


@dataclasses.dataclass(frozen=True)
class Finding:
    tool: str
    id: str
    package: str
    version: str = ""
    detail: str = ""


@dataclasses.dataclass(frozen=True)
class AllowEntry:
    tool: str
    id: str
    package: str
    reason: str
    expires: dt.date


@dataclasses.dataclass
class Decision:
    blocking: list[Finding]
    allowed: list[tuple[Finding, AllowEntry]]
    stale: list[AllowEntry]
    expired: list[AllowEntry]


def parse_allowlist(raw: object) -> tuple[list[AllowEntry], list[str]]:
    """解析豁免表；合法条目照收，非法条目逐条报错（不静默丢弃）。"""
    if not isinstance(raw, dict) or not isinstance(raw.get("entries"), list):
        return [], ["豁免表根节点必须是对象，且含 entries 数组"]
    entries: list[AllowEntry] = []
    errors: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for index, item in enumerate(raw["entries"], start=1):
        if not isinstance(item, dict):
            errors.append(f"entries[{index}] 不是对象")
            continue
        tool = item.get("tool")
        advisory_id = item.get("id")
        package = item.get("package")
        reason = item.get("reason")
        expires_raw = item.get("expires")
        problems: list[str] = []
        if tool not in TOOLS:
            problems.append(f"tool 必须是 {', '.join(TOOLS)} 之一（收到 {tool!r}）")
        for name, value in (("id", advisory_id), ("package", package), ("reason", reason)):
            if not isinstance(value, str) or not value.strip():
                problems.append(f"缺少非空字段 {name}")
        expires: dt.date | None = None
        if not isinstance(expires_raw, str):
            problems.append("expires 必须是 YYYY-MM-DD 字符串")
        else:
            try:
                expires = dt.date.fromisoformat(expires_raw)
            except ValueError:
                problems.append(f"expires 不是合法日期：{expires_raw!r}")
        if problems:
            errors.append(f"entries[{index}]：{'；'.join(problems)}")
            continue
        key = (str(tool), str(advisory_id), str(package))
        if key in seen:
            errors.append(f"entries[{index}]：重复条目（tool/id/package = {key[0]}/{key[1]}/{key[2]}）")
            continue
        seen.add(key)
        entries.append(
            AllowEntry(
                tool=key[0],
                id=key[1],
                package=key[2],
                reason=str(reason).strip(),
                expires=expires,  # type: ignore[arg-type]
            )
        )
    return entries, errors


def evaluate(
    findings: list[Finding],
    entries: list[AllowEntry],
    *,
    today: dt.date,
    tool: str | None = None,
) -> Decision:
    """按「工具 + 公告号 + 包名」匹配豁免；expires 当天仍有效，次日起失效。

    `tool` 给定时，「本次未命中」只统计同一工具的条目——否则跑 npm/cargo/dotnet
    会把 Python 的豁免全部误报成「可清理」。
    """
    expired = [entry for entry in entries if entry.expires < today]
    active = [entry for entry in entries if entry.expires >= today]
    blocking: list[Finding] = []
    allowed: list[tuple[Finding, AllowEntry]] = []
    matched: set[AllowEntry] = set()
    for finding in findings:
        hit = next(
            (
                entry
                for entry in active
                if entry.tool == finding.tool
                and entry.id == finding.id
                and entry.package == finding.package
            ),
            None,
        )
        if hit is None:
            blocking.append(finding)
        else:
            allowed.append((finding, hit))
            matched.add(hit)
    stale = [
        entry
        for entry in active
        if entry not in matched and (tool is None or entry.tool == tool)
    ]
    return Decision(blocking=blocking, allowed=allowed, stale=stale, expired=expired)


def _ghsa_from_url(url: str) -> str | None:
    match = _GHSA_RE.search(url)
    return match.group(0) if match else None


def parse_pip_audit_output(raw: object) -> list[Finding]:
    if not isinstance(raw, dict) or not isinstance(raw.get("dependencies"), list):
        raise TypeError("pip-audit 输出缺少 dependencies 数组")
    findings: list[Finding] = []
    seen: set[tuple[str, str, str]] = set()
    for dependency in raw["dependencies"]:
        if not isinstance(dependency, dict):
            continue
        package = str(dependency.get("name") or "")
        version = str(dependency.get("version") or "")
        for vuln in dependency.get("vulns") or []:
            if not isinstance(vuln, dict):
                continue
            advisory_id = str(vuln.get("id") or "")
            if not advisory_id:
                continue
            # 同一包同时是直接依赖与传递依赖时，pip-audit 会把同一公告报两遍。
            key = (advisory_id, package, version)
            if key in seen:
                continue
            seen.add(key)
            aliases = [str(alias) for alias in vuln.get("aliases") or []]
            fixes = [str(fix) for fix in vuln.get("fix_versions") or []]
            detail_bits = []
            if aliases:
                detail_bits.append(", ".join(aliases))
            detail_bits.append("修复版本 " + ", ".join(fixes) if fixes else "无已知修复版本")
            findings.append(
                Finding("pip-audit", advisory_id, package, version, "；".join(detail_bits))
            )
    return findings


def parse_npm_audit_output(raw: object) -> list[Finding]:
    if not isinstance(raw, dict) or not isinstance(raw.get("vulnerabilities"), dict):
        raise TypeError("npm audit 输出缺少 vulnerabilities 对象")
    findings: list[Finding] = []
    for package_name, record in raw["vulnerabilities"].items():
        if not isinstance(record, dict):
            continue
        package = str(record.get("name") or package_name)
        for via in record.get("via") or []:
            # `via` 里的字符串是「经由哪个包传入」的摘要，不是公告本身，跳过。
            if not isinstance(via, dict):
                continue
            advisory_id = _ghsa_from_url(str(via.get("url") or ""))
            if advisory_id is None and via.get("source") is not None:
                advisory_id = f"npm-{via['source']}"
            if advisory_id is None:
                continue
            findings.append(
                Finding("npm", advisory_id, package, "", str(via.get("title") or ""))
            )
    return findings


def parse_cargo_audit_output(raw: object) -> list[Finding]:
    if not isinstance(raw, dict):
        raise TypeError("cargo audit 输出不是对象")
    vulnerabilities = raw.get("vulnerabilities")
    if not isinstance(vulnerabilities, dict):
        raise TypeError("cargo audit 输出缺少 vulnerabilities 对象")
    findings: list[Finding] = []
    for item in vulnerabilities.get("list") or []:
        if not isinstance(item, dict):
            continue
        advisory = item.get("advisory") if isinstance(item.get("advisory"), dict) else {}
        package = item.get("package") if isinstance(item.get("package"), dict) else {}
        advisory_id = str(advisory.get("id") or "")
        if not advisory_id:
            continue
        findings.append(
            Finding(
                "cargo",
                advisory_id,
                str(package.get("name") or ""),
                str(package.get("version") or ""),
                str(advisory.get("title") or ""),
            )
        )
    return findings


def parse_dotnet_output(raw: object) -> list[Finding]:
    if not isinstance(raw, dict) or not isinstance(raw.get("projects"), list):
        raise TypeError("dotnet list package 输出缺少 projects 数组")
    findings: list[Finding] = []
    for project in raw["projects"]:
        if not isinstance(project, dict):
            continue
        for framework in project.get("frameworks") or []:
            if not isinstance(framework, dict):
                continue
            for group in ("topLevelPackages", "transitivePackages"):
                for package in framework.get(group) or []:
                    if not isinstance(package, dict):
                        continue
                    package_id = str(package.get("id") or "")
                    version = str(package.get("resolvedVersion") or package.get("requestedVersion") or "")
                    for vuln in package.get("vulnerabilities") or []:
                        if not isinstance(vuln, dict):
                            continue
                        advisory_url = str(vuln.get("advisoryurl") or "")
                        advisory_id = _ghsa_from_url(advisory_url) or advisory_url
                        if not advisory_id:
                            continue
                        findings.append(
                            Finding(
                                "dotnet",
                                advisory_id,
                                package_id,
                                version,
                                str(vuln.get("severity") or ""),
                            )
                        )
    return findings


PARSERS = {
    "pip-audit": parse_pip_audit_output,
    "npm": parse_npm_audit_output,
    "cargo": parse_cargo_audit_output,
    "dotnet": parse_dotnet_output,
}


def _resolve_executable(name: str) -> str:
    resolved = shutil.which(name)
    if resolved is None:
        raise RuntimeError(
            f"找不到命令 {name}；请先在当前环境安装它（pip-audit / npm / cargo-audit / dotnet SDK）"
        )
    return resolved


def _run_json(command: list[str], *, cwd: Path) -> object:
    """跑扫描器并解析 JSON。扫描器命中漏洞时退出码本来就是 1，不能当失败。"""
    executable = _resolve_executable(command[0])
    proc = subprocess.run(
        [executable, *command[1:]],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    stdout = (proc.stdout or "").strip()
    if not stdout:
        # 取 stderr 尾部：前面多是 traceback 噪声，真正的失败原因在最后几行。
        stderr = (proc.stderr or "").strip()[-1200:]
        raise RuntimeError(f"{Path(executable).name} 无 JSON 输出（exit {proc.returncode}）：{stderr}")
    start = stdout.find("{")
    if start < 0:
        raise RuntimeError(f"{Path(executable).name} 输出不是 JSON：{stdout[:400]}")
    try:
        return json.loads(stdout[start:])
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{Path(executable).name} 输出 JSON 解析失败：{error}") from error


def run_tool(tool: str, args: argparse.Namespace) -> tuple[list[Finding], str]:
    """跑真实扫描器，返回（公告列表, 覆盖范围描述）。"""
    if tool == "pip-audit":
        requirements = Path(args.requirements or REPO_ROOT / "backend" / "requirements.txt").resolve()
        raw = _run_json(
            [
                sys.executable,
                "-m",
                "pip_audit",
                "-r",
                str(requirements),
                "--format",
                "json",
                "--progress-spinner",
                "off",
                # 默认 15s 在慢网络下会把「没扫完」误报成扫描失败；放宽到 60s。
                "--timeout",
                "60",
            ],
            cwd=REPO_ROOT,
        )
        return parse_pip_audit_output(raw), f"{requirements}（含传递依赖，由 pip-audit 解析）"
    if tool == "npm":
        cwd = Path(args.cwd or REPO_ROOT / "frontend").resolve()
        raw = _run_json(["npm", "audit", "--omit=dev", "--json"], cwd=cwd)
        return parse_npm_audit_output(raw), f"{cwd / 'package-lock.json'}（--omit=dev，仅生产依赖）"
    if tool == "cargo":
        cwd = Path(args.cwd or REPO_ROOT / "desktop" / "tauri").resolve()
        raw = _run_json(["cargo", "audit", "--json"], cwd=cwd)
        return parse_cargo_audit_output(raw), f"{cwd / 'Cargo.lock'}（含传递依赖）"
    if tool == "dotnet":
        project = Path(
            args.project or REPO_ROOT / "desktop" / "Demiurge.Desktop" / "Demiurge.Desktop.csproj"
        ).resolve()
        raw = _run_json(
            [
                "dotnet",
                "list",
                str(project),
                "package",
                "--vulnerable",
                "--include-transitive",
                "--format",
                "json",
            ],
            cwd=REPO_ROOT,
        )
        return parse_dotnet_output(raw), f"{project}（--include-transitive）"
    raise ValueError(tool)


def _format_finding(finding: Finding) -> str:
    version = f" {finding.version}" if finding.version else ""
    detail = f" | {finding.detail}" if finding.detail else ""
    return f"{finding.package}{version} {finding.id}{detail}"


def render_report(
    *,
    tool: str,
    scope: str,
    today: dt.date,
    entries: list[AllowEntry],
    errors: list[str],
    decision: Decision,
    findings: list[Finding],
    scan_error: str | None = None,
) -> str:
    earliest = min((entry.expires for entry in entries), default=None)
    lines = [
        "# 依赖漏洞定时审计",
        "",
        f"- 工具：{tool}",
        f"- 扫描日期（UTC）：{today.isoformat()}",
        f"- 覆盖范围：{scope}",
        f"- 豁免表：{DEFAULT_ALLOWLIST.name}（{len(entries)} 条"
        + (f"，最早到期 {earliest.isoformat()}" if earliest else "")
        + "）",
        "- 口径：只代表扫描当日公开漏洞库的命中；「通过」不等于安全，漏洞库随上游更新会变化。",
        "",
        "## 结果",
        f"- 命中公告：{len(findings)} 条",
        f"- 已豁免：{len(decision.allowed)} 条",
        f"- 阻断：{len(decision.blocking)} 条",
        "",
    ]
    if errors:
        lines.append("### 豁免表错误（必须修）")
        lines.extend(f"- {error}" for error in errors)
        lines.append("")
    if scan_error:
        # 扫描器没跑完时 findings 必然为空；不单独标红就会把「没扫成」当成「没问题」。
        lines.append("### 扫描器执行失败（必须修，本次结果无效）")
        lines.append(f"- {scan_error}")
        lines.append("")
    if decision.expired:
        lines.append("### 豁免已到期（到期即失效，必须复核）")
        lines.extend(
            f"- {entry.tool} / {entry.package} / {entry.id}（expires {entry.expires.isoformat()}）"
            for entry in decision.expired
        )
        lines.append("")
    if decision.blocking:
        lines.append("### 阻断（未豁免公告）")
        lines.extend(f"- {_format_finding(finding)}" for finding in decision.blocking)
        lines.append("")
    if decision.allowed:
        lines.append("### 已豁免（到期日前有效）")
        lines.extend(
            f"- [豁免至 {entry.expires.isoformat()}] {_format_finding(finding)} | {entry.reason}"
            for finding, entry in decision.allowed
        )
        lines.append("")
    # 扫描没跑成时，「未命中」与「可清理」都不成立——它们会让失败看起来像通过/无事可做。
    if decision.stale and not scan_error:
        lines.append("### 可清理（本次扫描未命中）")
        lines.extend(
            f"- {entry.tool} / {entry.package} / {entry.id}（expires {entry.expires.isoformat()}）"
            for entry in decision.stale
        )
        lines.append("")
    if not findings and not scan_error:
        lines.append("未发现漏洞库命中的公告。")
        lines.append("")
    return "\n".join(lines)


def _write_summary(text: str) -> None:
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    try:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(text)
            handle.write("\n")
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="依赖漏洞定时审计（四个生态共用一张豁免表）")
    parser.add_argument("tool", choices=["pip", "npm", "cargo", "dotnet"])
    parser.add_argument("--allowlist", default=str(DEFAULT_ALLOWLIST))
    parser.add_argument("--today", help="覆盖扫描日期（YYYY-MM-DD，默认 UTC 今天）")
    parser.add_argument("--from-json", help="用已抓取的扫描器 JSON 代替现场执行（离线复现）")
    parser.add_argument("--requirements", help="Python 依赖清单（默认 backend/requirements.txt）")
    parser.add_argument("--cwd", help="npm / cargo 的工作目录")
    parser.add_argument("--project", help="dotnet 项目路径")
    args = parser.parse_args(argv)
    tool = "pip-audit" if args.tool == "pip" else args.tool
    today = dt.date.fromisoformat(args.today) if args.today else dt.datetime.now(dt.timezone.utc).date()

    allowlist_path = Path(args.allowlist).resolve()
    try:
        entries, errors = parse_allowlist(
            json.loads(allowlist_path.read_text(encoding="utf-8"))
        )
    except OSError as error:
        entries, errors = [], [f"豁免表读取失败：{allowlist_path}（{error}）"]
    except json.JSONDecodeError as error:
        entries, errors = [], [f"豁免表不是合法 JSON：{allowlist_path}（{error}）"]

    scan_error: str | None = None
    try:
        if args.from_json:
            findings = PARSERS[tool](json.loads(Path(args.from_json).read_text(encoding="utf-8")))
            scope = f"离线回放 {Path(args.from_json).resolve()}（不代表当前依赖现状）"
        else:
            findings, scope = run_tool(tool, args)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        scan_error = str(error)
        scope = f"扫描器执行失败：{error}"
        findings = []

    decision = evaluate(findings, entries, today=today, tool=tool)
    report = render_report(
        tool=tool,
        scope=scope,
        today=today,
        entries=entries,
        errors=errors,
        decision=decision,
        findings=findings,
        scan_error=scan_error,
    )
    print(report)
    _write_summary(report)
    failed = bool(errors or scan_error or decision.blocking or decision.expired)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
