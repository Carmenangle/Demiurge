#!/usr/bin/env bash
# M4 PC 等价冒烟：真实 app.main + LAF_LOCAL_TOKEN。
#
# 三段判据：
#   A. 带令牌实例：无凭证 401；头/cookie 均放行；/__auth 种 HttpOnly+SameSite=Strict
#      cookie 并 303 到站内；local-view 媒体用 cookie 可取；后端自取图（image_store）
#      带令牌成功、不带令牌被自己的闸拒绝。
#   B. 不带令牌实例：/api/health 免头 200、/__auth 404 —— 桌面默认行为零变化。
#
# 隔离：独立数据目录 + 端口，不读不写 backend/data、不碰 8010。用后台 Python 进程
# 起真实 uvicorn，curl 从外部发请求（不经 TestClient）。
set -u

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="$ROOT/backend/.venv/Scripts/python.exe"
PORT="${SMOKE_PORT:-18077}"
PORT2=$((PORT + 1))
TOKEN="$("$PY" -c 'import secrets; print(secrets.token_urlsafe(32))')"
HEADER="X-Demiurge-Token"
WORK="$ROOT/backend/_tmp/m4-pc-smoke-$$"
WIN_WORK="$(cygpath -m "$WORK" 2>/dev/null || printf '%s' "$WORK")"
WIN_ROOT="$(cygpath -m "$ROOT" 2>/dev/null || printf '%s' "$ROOT")"
DATA="$WIN_WORK/data"
FRONTEND="$WIN_ROOT/mobile/spike/frontend-mobile-dist"
FAIL=0
PID=""

mkdir -p "$DATA/assets" "$WIN_WORK/tmp"

cleanup() {
    if [[ -n "$PID" ]]; then
        kill "$PID" 2>/dev/null
        wait "$PID" 2>/dev/null
    fi
    "$PY" -c 'import shutil,sys; shutil.rmtree(sys.argv[1], ignore_errors=True)' "$WORK"
}
trap cleanup EXIT

pass() { echo "PASS $*"; }
fail() { echo "FAIL $*"; FAIL=1; }

start_server() {  # $1=port  $2=token(可空)  $3=日志
    pushd "$ROOT/backend" >/dev/null || exit 1
    env \
        LAF_DATA_DIR="$DATA" \
        LAF_RUNTIME_PORT="$1" \
        LAF_FRONTEND_DIST="$FRONTEND" \
        LAF_COMFY_AUTOSTART=0 \
        LAF_COMFY_EXT_DIR="$WIN_WORK/comfyui-ext" \
        TIKTOKEN_CACHE_DIR="$WIN_WORK/tiktoken-cache" \
        TMPDIR="$WIN_WORK/tmp" \
        PYTHONUTF8=1 \
        ${2:+LAF_LOCAL_TOKEN="$2"} \
        "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$1" >"$3" 2>&1 &
    PID=$!
    popd >/dev/null || exit 1
}

wait_health() {  # $1=port  $2=token(可空)  $3=日志
    for _ in $(seq 1 120); do
        local code
        code=$(curl -s -o /dev/null -w '%{http_code}' ${2:+-H "$HEADER: $2"} \
            "http://127.0.0.1:$1/api/health")
        [[ "$code" == "200" ]] && return 0
        sleep 0.5
    done
    echo "--- server.log ---"
    tail -40 "$3"
    return 1
}

expect_code() {  # $1=desc $2=want $3=port $4...=curl extra args
    local desc="$1" want="$2" port="$3"
    shift 3
    local got
    got=$(curl -s -o /dev/null -w '%{http_code}' "$@" "http://127.0.0.1:$port$URL_PATH")
    [[ "$got" == "$want" ]] && pass "$desc" || fail "$desc（want $want, got $got）"
}

kill_server() {
    kill "$PID" 2>/dev/null
    wait "$PID" 2>/dev/null
    PID=""
}

# ---------------------------------------------------------------------------
# A. 带令牌实例
# ---------------------------------------------------------------------------
start_server "$PORT" "$TOKEN" "$WIN_WORK/server-token.log"
if ! wait_health "$PORT" "$TOKEN" "$WIN_WORK/server-token.log"; then
    echo "FAIL 带令牌实例未就绪"; exit 1
fi

URL_PATH="/api/health"
expect_code "无凭证 /api/health → 401" 401 "$PORT"
expect_code "错令牌 /api/health → 401" 401 "$PORT" -H "$HEADER: wrong-token"
expect_code "令牌头 /api/health → 200" 200 "$PORT" -H "$HEADER: $TOKEN"

URL_PATH="/"
expect_code "无 cookie 根页面 → 401" 401 "$PORT"

# 引导端点：303 + 严格同站 HttpOnly cookie
curl -s -D "$WORK/auth.h" -o /dev/null -c "$WORK/jar" \
    "http://127.0.0.1:$PORT/__auth?token=$TOKEN&next=/"
head -1 "$WORK/auth.h" | grep -q " 303" && pass "/__auth → 303" || fail "/__auth 未 303"
grep -qi "^set-cookie:.*httponly" "$WORK/auth.h" \
    && pass "cookie HttpOnly" || fail "cookie 缺 HttpOnly"
grep -qi "^set-cookie:.*samesite=strict" "$WORK/auth.h" \
    && pass "cookie SameSite=Strict" || fail "cookie 缺 SameSite=Strict"

expect_code "cookie 根页面 → 200" 200 "$PORT" -b "$WORK/jar"
expect_code "cookie /api/health → 200" 200 "$PORT" -b "$WORK/jar"

# next 只允许站内绝对路径
curl -s -D "$WORK/next.h" -o /dev/null \
    "http://127.0.0.1:$PORT/__auth?token=$TOKEN&next=//evil.example/x"
grep -qi "^location: /$" "$WORK/next.h" \
    && pass "next=//evil 收敛到 /" || fail "next 未收敛（可被重定向到外站）"

# 媒体（<img>/<video> 无法自定义头，只能靠 cookie）
printf '\x89PNG\r\n\x1a\nsmoke' >"$WORK/data/assets/smoke.png"
ENC="$("$PY" -c 'import sys,urllib.parse; print(urllib.parse.quote(sys.argv[1]))' \
    "$DATA/assets/smoke.png")"
URL_PATH="/api/comfyui/local-view?path=$ENC"
expect_code "cookie 取 local-view 图 → 200" 200 "$PORT" -b "$WORK/jar"
if curl -s -b "$WORK/jar" "http://127.0.0.1:$PORT$URL_PATH" | cmp -s - \
    "$WORK/data/assets/smoke.png"; then
    pass "local-view 字节一致"
else
    fail "local-view 字节不一致"
fi

# 后端自取图：带令牌成功、不带令牌失败（image_store 是 4 处自取点的代表）
URL_FULL="http://127.0.0.1:$PORT$URL_PATH"
if (cd "$ROOT/backend" && LAF_DATA_DIR="$DATA" LAF_RUNTIME_PORT="$PORT" \
    LAF_LOCAL_TOKEN="$TOKEN" PYTHONUTF8=1 "$PY" - "$URL_FULL" \
    "$WORK/data/assets/smoke.png" <<'PY'
import sys
from app.services import image_store
data, _ext = image_store._from_src(sys.argv[1])
assert data == open(sys.argv[2], "rb").read(), "self-fetch 字节不一致"
PY
); then
    pass "自取图带令牌成功"
else
    fail "自取图带令牌失败"
fi

if (cd "$ROOT/backend" && env -u LAF_LOCAL_TOKEN LAF_DATA_DIR="$DATA" \
    LAF_RUNTIME_PORT="$PORT" PYTHONUTF8=1 "$PY" - "$URL_FULL" <<'PY'
import sys
from app.services import image_store
try:
    image_store._from_src(sys.argv[1])
except Exception:
    raise SystemExit(0)
raise SystemExit("无令牌自取图竟然成功，令牌头没有生效")
PY
); then
    pass "自取图无令牌被拒"
else
    fail "自取图无令牌未被拒"
fi

kill_server

# ---------------------------------------------------------------------------
# B. 不带令牌实例（桌面默认路径）
# ---------------------------------------------------------------------------
start_server "$PORT2" "" "$WIN_WORK/server-plain.log"
if ! wait_health "$PORT2" "" "$WIN_WORK/server-plain.log"; then
    echo "FAIL 无令牌实例未就绪"; exit 1
fi

URL_PATH="/api/health"
expect_code "未设令牌 /api/health 免头 → 200" 200 "$PORT2"
URL_PATH="/__auth"
expect_code "未设令牌 /__auth → 404" 404 "$PORT2"
URL_PATH="/"
expect_code "未设令牌根页面免凭证 → 200" 200 "$PORT2"
kill_server

if [[ "$FAIL" -eq 0 ]]; then
    echo "M4 PC SMOKE PASS"
else
    echo "M4 PC SMOKE FAIL"
    exit 1
fi
