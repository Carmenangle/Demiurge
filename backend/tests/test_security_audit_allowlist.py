"""定时依赖审计的豁免表合同（scripts/security_audit.py）。

三条硬口径：
- 豁免必须带到期日；到期当天起即使公告仍命中也不放行（否则豁免表会变成永久白名单）。
- 豁免按「工具 + 公告号 + 包名」三者匹配：同一条公告出现在别的生态/别的包上不得借用。
- 本次没命中的豁免要显式列为「可清理」，否则漏洞消失后豁免会静默腐烂。

另钉住四个工具的输出解析：扫描器升级/换字段时必须在测试里先红，而不是在 CI 里静默零命中。
"""
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "security_audit.py"
ALLOWLIST = REPO / "scripts" / "security-audit-allowlist.json"


def _load():
    spec = importlib.util.spec_from_file_location("security_audit", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["security_audit"] = module
    spec.loader.exec_module(module)
    return module


audit = _load()


def _entry(**overrides):
    base = {
        "tool": "pip-audit",
        "id": "PYSEC-2026-311",
        "package": "chromadb",
        "reason": "上游尚无修复版本",
        "expires": date(2027, 1, 31),
    }
    base.update(overrides)
    return audit.AllowEntry(**base)


def _finding(**overrides):
    base = {
        "tool": "pip-audit",
        "id": "PYSEC-2026-311",
        "package": "chromadb",
        "version": "1.5.9",
        "detail": "",
    }
    base.update(overrides)
    return audit.Finding(**base)


def test_过期豁免即使命中也不放行():
    expired = _entry(expires=date(2026, 11, 6))
    finding = _finding()
    decision = audit.evaluate([finding], [expired], today=date(2026, 11, 7))
    assert decision.blocking == [finding]
    assert decision.allowed == []
    assert decision.expired == [expired]


def test_有效豁免只放行同工具同公告号同包():
    valid = _entry()
    matched = _finding()
    other_tool = _finding(tool="npm")
    other_package = _finding(package="langchain-chroma")
    decision = audit.evaluate(
        [matched, other_tool, other_package], [valid], today=date(2026, 11, 7)
    )
    assert decision.allowed == [(matched, valid)]
    assert decision.blocking == [other_tool, other_package]


def test_本次未命中的豁免列为可清理():
    valid = _entry()
    decision = audit.evaluate([], [valid], today=date(2026, 11, 7))
    assert decision.blocking == []
    assert decision.stale == [valid]


def test_其他工具的豁免不算可清理():
    # 四个生态共用一张豁免表：跑 npm 时不能把 Python 的生效条目误报成「可清理」。
    valid = _entry()
    decision = audit.evaluate([], [valid], today=date(2026, 11, 7), tool="npm")
    assert decision.stale == []


def test_允许列表缺字段或日期非法必须报错():
    errors = audit.parse_allowlist(
        {
            "entries": [
                {"tool": "pip-audit", "id": "PYSEC-1", "package": "x", "expires": "2027-01-31"},
                {"tool": "unknown-scanner", "id": "X", "package": "x", "reason": "r", "expires": "2027-01-31"},
                {"tool": "npm", "id": "GHSA-1", "package": "x", "reason": "r", "expires": "not-a-date"},
                {
                    "tool": "npm",
                    "id": "GHSA-2",
                    "package": "x",
                    "reason": "r",
                    "expires": "2027-01-31",
                },
                {
                    "tool": "npm",
                    "id": "GHSA-2",
                    "package": "x",
                    "reason": "重复条目",
                    "expires": "2027-02-01",
                },
            ]
        }
    )[1]
    assert any("reason" in error for error in errors)
    assert any("unknown-scanner" in error for error in errors)
    assert any("not-a-date" in error for error in errors)
    assert any("重复" in error for error in errors)


def test_仓库里的真实允许列表可解析且字段合法():
    entries, errors = audit.parse_allowlist(json.loads(ALLOWLIST.read_text(encoding="utf-8")))
    assert errors == []
    assert entries, "当前应保留 chromadb 的无修复版本豁免（修好后请删条目而不是留空壳）"


def test_扫描器执行失败必须红(monkeypatch, capsys):
    # 反例：扫描器崩溃时 findings 为空，若只看「未命中」就会误判成通过。
    def boom(tool, args):
        raise RuntimeError("模拟扫描器崩溃")

    monkeypatch.setattr(audit, "run_tool", boom)
    code = audit.main(["npm"])
    output = capsys.readouterr().out
    assert code == 1
    assert "扫描器执行失败" in output
    # 扫描失败不能附带「未命中」「可清理」这类绿色措辞。
    assert "未发现漏洞库命中的公告" not in output
    assert "可清理" not in output


def test_扫描失败时不输出可清理与未发现():
    entry = _entry()
    decision = audit.Decision(blocking=[], allowed=[], stale=[entry], expired=[])
    report = audit.render_report(
        tool="pip-audit",
        scope="模拟失败",
        today=date(2026, 11, 7),
        entries=[entry],
        errors=[],
        decision=decision,
        findings=[],
        scan_error="模拟扫描器崩溃",
    )
    assert "扫描器执行失败" in report
    assert "可清理" not in report
    assert "未发现漏洞库命中的公告" not in report


def test_pip_audit重复上报同一公告只算一次():
    # 实测 pip-audit 2.10.1 会把 chromadb 的 PYSEC-2026-311 上报两次
    # （requirements.txt 里 chromadb 既是直接依赖、又被 langchain-chroma 传递依赖）。
    duplicate = {
        "name": "chromadb",
        "version": "1.5.9",
        "vulns": [
            {"id": "PYSEC-2026-311", "fix_versions": [], "aliases": ["CVE-2026-45829"]},
            {"id": "PYSEC-2026-311", "fix_versions": [], "aliases": ["CVE-2026-45829"]},
        ],
    }
    findings = audit.parse_pip_audit_output({"dependencies": [duplicate]})
    assert len(findings) == 1
    assert findings[0].id == "PYSEC-2026-311"


def test_pip_audit输出解析():
    findings = audit.parse_pip_audit_output(
        {
            "dependencies": [
                {
                    "name": "chromadb",
                    "version": "1.5.9",
                    "vulns": [
                        {
                            "id": "PYSEC-2026-311",
                            "fix_versions": [],
                            "aliases": ["CVE-2026-45829"],
                        }
                    ],
                },
                {"name": "fastapi", "version": "0.115.0", "vulns": []},
            ]
        }
    )
    assert findings == [
        audit.Finding(
            tool="pip-audit",
            id="PYSEC-2026-311",
            package="chromadb",
            version="1.5.9",
            detail="CVE-2026-45829；无已知修复版本",
        )
    ]


def test_npm_audit输出解析取GHSA编号():
    findings = audit.parse_npm_audit_output(
        {
            "vulnerabilities": {
                "lodash": {
                    "name": "lodash",
                    "severity": "high",
                    "via": [
                        {
                            "source": 1234567,
                            "title": "Prototype Pollution",
                            "url": "https://github.com/advisories/GHSA-jf85-cpcp-j695",
                        },
                        "some-transitive-package",
                    ],
                }
            }
        }
    )
    assert findings == [
        audit.Finding(
            tool="npm",
            id="GHSA-jf85-cpcp-j695",
            package="lodash",
            version="",
            detail="Prototype Pollution",
        )
    ]


def test_cargo_audit输出解析():
    findings = audit.parse_cargo_audit_output(
        {
            "vulnerabilities": {
                "found": True,
                "count": 1,
                "list": [
                    {
                        "advisory": {"id": "RUSTSEC-2024-0001", "title": "unsound"},
                        "package": {"name": "foo", "version": "0.1.0"},
                    }
                ],
            }
        }
    )
    assert findings == [
        audit.Finding(
            tool="cargo",
            id="RUSTSEC-2024-0001",
            package="foo",
            version="0.1.0",
            detail="unsound",
        )
    ]


def test_dotnet_list_package输出解析含传递依赖():
    findings = audit.parse_dotnet_output(
        {
            "version": 1,
            "projects": [
                {
                    "path": "Demiurge.Desktop.csproj",
                    "frameworks": [
                        {
                            "framework": "net8.0-windows7.0",
                            "topLevelPackages": [
                                {
                                    "id": "Microsoft.Web.WebView2",
                                    "resolvedVersion": "1.0.2592.51",
                                    "vulnerabilities": [],
                                }
                            ],
                            "transitivePackages": [
                                {
                                    "id": "Foo.Transitive",
                                    "resolvedVersion": "1.2.3",
                                    "vulnerabilities": [
                                        {
                                            "severity": "High",
                                            "advisoryurl": "https://github.com/advisories/GHSA-aaaa-bbbb-cccc",
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )
    assert findings == [
        audit.Finding(
            tool="dotnet",
            id="GHSA-aaaa-bbbb-cccc",
            package="Foo.Transitive",
            version="1.2.3",
            detail="High",
        )
    ]
