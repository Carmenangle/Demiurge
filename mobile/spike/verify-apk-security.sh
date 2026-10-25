#!/usr/bin/env bash
# M4 APK 静态安全闸：对已产出的 APK 复核「清单属性 + 网络安全配置 + 包内容白名单」。
# 只看安装包本身，不跑 Gradle；可在任意机器上对交付物复验。
# 用法：bash mobile/spike/verify-apk-security.sh [apk]
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APK="${1:-$(ls -t "$ROOT"/mobile/spike/dist/*.apk 2>/dev/null | head -1)}"
FAILS=0
pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; FAILS=$((FAILS + 1)); }
check() { if [ "$1" = "0" ]; then pass "$2"; else fail "$2"; fi; }

[ -n "$APK" ] && [ -f "$APK" ] || { echo "找不到 APK：${APK:-<空>}"; exit 1; }
echo "APK: $APK ($(wc -c < "$APK") bytes)"

SDK="${ANDROID_SDK_ROOT:-${ANDROID_HOME:-}}"
if [ -z "$SDK" ]; then
    PROPS="$ROOT/mobile/spike/android-spike/local.properties"
    if [ -f "$PROPS" ]; then
        # Java properties 转义：\\ -> \，\: -> :，\= -> =
        SDK="$(sed -n 's/^sdk\.dir=//p' "$PROPS" | head -1)"
        SDK="${SDK//\\\\/\\}"
        SDK="${SDK//\\:/:}"
        SDK="${SDK//\\=/\=}"
    fi
fi
if [ -n "$SDK" ] && command -v cygpath >/dev/null 2>&1; then
    SDK="$(cygpath -u "$SDK")"
fi
AAPT2="${AAPT2:-$(ls -d "$SDK"/build-tools/*/aapt2.exe "$SDK"/build-tools/*/aapt2 2>/dev/null | sort -V | tail -1)}"
[ -n "$AAPT2" ] && [ -x "$AAPT2" ] || { echo "找不到 aapt2（设 AAPT2=... 或 ANDROID_HOME）"; exit 1; }
UNZIP="${UNZIP:-unzip}"
command -v "$UNZIP" >/dev/null 2>&1 || { echo "找不到 unzip"; exit 1; }

BADGING="$("$AAPT2" dump badging "$APK" 2>/dev/null)"
MANIFEST="$("$AAPT2" dump xmltree --file AndroidManifest.xml "$APK" 2>/dev/null)"
NETCFG="$("$AAPT2" dump xmltree --file res/xml/network_security_config.xml "$APK" 2>/dev/null)"

echo "$BADGING" | grep -q "package: name='dev.demiurge.spike'" ; check $? "包名 dev.demiurge.spike"
MINSDK="$(echo "$BADGING" | sed -n "s/^minSdkVersion:'\([0-9]*\)'$/\1/p")"
[ "${MINSDK:-0}" -ge 24 ] 2>/dev/null; check $? "minSdk >= 24（实测 ${MINSDK:-?}）"

echo "$MANIFEST" | grep -qE "allowBackup(\(0x[0-9a-f]+\))?=false"; check $? "allowBackup=false（密钥不进云备份）"
echo "$MANIFEST" | grep -qE "networkSecurityConfig(\(0x[0-9a-f]+\))?="; check $? "清单挂载 networkSecurityConfig"
if echo "$MANIFEST" | grep -qE "usesCleartextTraffic(\(0x[0-9a-f]+\))?=true"; then
    fail "usesCleartextTraffic 未被禁用"
else
    pass "无全局明文开关（usesCleartextTraffic 不存在的放行）"
fi

[ -n "$NETCFG" ]; check $? "网络安全配置已打进包"
echo "$NETCFG" | grep -qE "cleartextTrafficPermitted(\(0x[0-9a-f]+\))?=false"; check $? "base-config 禁明文"
[ "$(echo "$NETCFG" | grep -cE "cleartextTrafficPermitted(\(0x[0-9a-f]+\))?=true")" = "1" ]; check $? "仅一处域级放行"
echo "$NETCFG" | grep -q "127.0.0.1"; check $? "明文放行含 127.0.0.1"
echo "$NETCFG" | grep -q "localhost"; check $? "明文放行含 localhost"

ENTRIES="$("$UNZIP" -Z1 "$APK" | tr '\\' '/' | tr 'A-Z' 'a-z')"
echo "$ENTRIES" | grep -q "^assets/chaquopy/app.imy$"; check $? "Chaquopy 后端源码入包（app.imy）"
if echo "$ENTRIES" | grep -qE "(^|/)(presets|userdata)/|(^|/)backend/data/|(^|/)user_state\.json$"; then
    fail "包内混入用户数据（presets/userdata/backend/data/user_state.json）"
else
    pass "包内无用户数据目录"
fi

# 壳层令牌面：这两条字符串在 dex 常量池里，能证明「引导落地清历史」与「二次启动用
# 持久化 cookie 直连」确实编译进了交付包，而不是只存在于源码。
DEX_TMP="$(mktemp -d)"
"$UNZIP" -q -o "$APK" 'classes*.dex' -d "$DEX_TMP" 2>/dev/null
if grep -aq "history cleared, cookie flushed" "$DEX_TMP"/classes*.dex; then
    pass "dex 含引导落地清历史 + cookie 落盘（令牌不留在 WebView 历史）"
else
    fail "dex 缺引导落地清历史逻辑"
fi
if grep -aq "demiurge_token" "$DEX_TMP"/classes*.dex; then
    pass "dex 含 demiurge_token（二次启动直连判据在服务端 cookie 名上一致）"
else
    fail "dex 缺 demiurge_token，二次启动无法直连"
fi
rm -rf "$DEX_TMP"

echo "---"
if [ "$FAILS" = "0" ]; then
    echo "M4 APK SECURITY PASS"
    exit 0
fi
echo "M4 APK SECURITY FAIL：$FAILS 项"
exit 1
