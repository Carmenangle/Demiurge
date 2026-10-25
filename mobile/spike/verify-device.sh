#!/usr/bin/env bash
# M2 真机验证一键脚本：安装最新 spike APK → 两轮启动（首轮含 82MB 依赖+103MB 前端解压，
# 第二轮量真实冷启动）→ 从 logcat（TAG=DemiurgeSpike）抽取计时与自检结果 → 判定。
#
# 用法（Git Bash）：
#   ./verify-device.sh                # 用 dist/ 下最新的 demiurge-spike-*-debug.apk
#   ./verify-device.sh dist/demiurge-spike-0.4.0-debug.apk
#
# 判定依据（全部来自应用自己的打点，不经 PC 侧回环 HTTP——沙箱 shell 的 127.0.0.1
# 可能被 harness 端口映射接管，logcat 才是可信证据通道）：
#   失败标记：PYTHON ERROR / FRONTEND EXTRACT ERROR / "server did not answer" /
#            "import app.main failed" / "uvicorn crashed"
#   成功标记：HEALTH OK + SELFTEST 含 5 步 "ok":true
# 计时标记：[+X ms] app started → python started → mobile_server.start() returned in Y ms
#          → HEALTH OK (Z ms after python start)；mobile_server 另报 import/handoff ms。
set -uo pipefail

SPIKE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG="dev.demiurge.spike"
ACTIVITY="$PKG/.MainActivity"
ADB="${ADB:-}"
if [[ -z "$ADB" ]]; then
    for cand in "D:/tool/android-sdk/platform-tools/adb.exe" "adb"; do
        if command -v "$cand" >/dev/null 2>&1; then ADB="$cand"; break; fi
    done
fi
if [[ -z "$ADB" ]]; then echo "未找到 adb（设 ADB=/path/to/adb.exe）"; exit 2; fi

APK="${1:-$(ls -t "$SPIKE_ROOT"/dist/demiurge-spike-*-debug.apk 2>/dev/null | head -1)}"
if [[ -z "$APK" || ! -f "$APK" ]]; then echo "未找到 APK：$APK"; exit 2; fi
LOG_OUT="$SPIKE_ROOT/device-verify-$(date +%Y%m%d-%H%M%S).log"

echo "== APK: $APK"
echo "== 日志: $LOG_OUT"

# adb devices = 表头 + 每个设备一行 + 末尾空行；不能按固定行号取（踩过：从第 3 行取把设备行漏掉）
if [[ -z "$("$ADB" devices | awk 'NR>1 && $2=="device" {print $1}')" ]]; then
    echo "无设备连接（先插 USB 并开 USB 调试；无线调试需先 adb connect）"
    "$ADB" devices
    exit 2
fi
"$ADB" wait-for-device
echo "== 设备: $("$ADB" shell getprop ro.product.model 2>/dev/null) Android $("$ADB" shell getprop ro.build.version.release 2>/dev/null)"

# 应用的 print() 走 Chaquopy 的 python.stdout/python.stderr，不在 DemiurgeSpike 标签下；
# 失败标记（import app.main failed / uvicorn crashed）只有带上这两个标签才看得到
dump_logs() { "$ADB" logcat -d -s DemiurgeSpike:I python.stdout:I python.stderr:I 2>/dev/null; }

echo "== 安装（-r 覆盖，保留数据目录）"
"$ADB" install -r "$APK" || { echo "安装失败"; exit 1; }

run_boot() {  # $1=轮次标签 $2=超时秒
    local label="$1" timeout_s="$2" waited=0
        "$ADB" logcat -c 2>/dev/null
        "$ADB" shell am force-stop "$PKG" 2>/dev/null
        sleep 1
        echo "== [$label] 启动 $ACTIVITY"
        "$ADB" shell am start -n "$ACTIVITY" >/dev/null || { echo "am start 失败"; return 1; }
        while (( waited < timeout_s )); do
            sleep 5; waited=$((waited + 5))
            dump_logs > "$LOG_OUT.body"
        if grep -qE "PYTHON ERROR|FRONTEND EXTRACT ERROR|server did not answer|import app\.main failed|uvicorn crashed" "$LOG_OUT.body"; then
            echo "== [$label] 失败标记命中（${waited}s）"; cat "$LOG_OUT.body" >> "$LOG_OUT"; return 1
        fi
        if grep -q "SELFTEST:" "$LOG_OUT.body"; then
            cat "$LOG_OUT.body" >> "$LOG_OUT"
            echo "== [$label] 自检完成（${waited}s 内），关键行："
            grep -E "app started|python started|mobile_server|HEALTH OK|SELFTEST|extracted" "$LOG_OUT.body" | head -12
            return 0
        fi
    done
    cat "$LOG_OUT.body" >> "$LOG_OUT" 2>/dev/null
    echo "== [$label] ${timeout_s}s 内未等到 SELFTEST"; return 1
}

verdict() {  # $1=轮次标签；COLD_MS 供最终冷启动判据使用
    local body="$LOG_OUT.body"
    echo "-- [$1] 计时（应用内 [+X ms] 口径）："
    grep -oE "\[\+[0-9]+ ms\][^:]*" "$body" 2>/dev/null | head -6
    grep -oE "mobile_server\.start\(\) returned in [0-9]+ ms" "$body" 2>/dev/null
    grep -oE "import [0-9.]+ ms, handoff [0-9.]+ ms" "$body" 2>/dev/null
    grep -oE "HEALTH OK \([0-9]+ ms after python start\)" "$body" 2>/dev/null
    COLD_MS=$(grep -oE "\[\+[0-9]+ ms\] HEALTH OK" "$body" 2>/dev/null | grep -oE '[0-9]+' | head -1)
    echo "-- [$1] 点图标→API 可服务：${COLD_MS:-未取到} ms（阈值 8000）"
    echo "-- [$1] 自检五步："
    local ok_n
    ok_n=$(grep -o '"ok": *true' "$body" 2>/dev/null | wc -l)
    local bad_n
    bad_n=$(grep -o '"ok": *false' "$body" 2>/dev/null | wc -l)
    echo "   ok=true ${ok_n} 步 / ok=false ${bad_n} 步（期望 5/0）"
    if (( ok_n != 5 || bad_n != 0 )); then FAIL=1; fi
    grep -oE '"(chromadb_rust_bindings|chroma_roundtrip|langchain_chroma|tiktoken|grpc)": *\{[^}]*"error[^}]*' "$body" 2>/dev/null | head -5
}

FAIL=0
COLD_MS=""
if run_boot "第一轮-首启（视是否已解压）" 480; then verdict "第一轮-首启（视是否已解压）"; else FAIL=1; fi
if run_boot "第二轮-真实冷启动" 240; then verdict "第二轮-真实冷启动"; else FAIL=1; fi

if (( FAIL )); then
    echo "== 拉取 boot-error.txt（若存在）"
    "$ADB" shell run-as "$PKG" cat files/boot-error.txt 2>/dev/null | head -40 || true
    echo "== 判定：失败（完整日志 $LOG_OUT）"; exit 1
fi
if [[ -z "${COLD_MS:-}" ]]; then
    echo "== 判定：自检全绿但未取到冷启动时间戳（完整日志 $LOG_OUT）"; exit 1
fi
if (( COLD_MS > 8000 )); then
    echo "== 判定：冷启动 ${COLD_MS} ms 超阈值 8000 ms（完整日志 $LOG_OUT）"; exit 1
fi
echo "== 判定：PASS——自检 5/5 绿，点图标→API 可服务 ${COLD_MS} ms ≤ 8000 ms（完整日志 $LOG_OUT）"
