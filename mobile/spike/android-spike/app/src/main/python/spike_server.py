import os
import platform
import sys
import threading
import time
import traceback

_t_import = time.perf_counter()

import pydantic
import pydantic_core
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

print(
    "spike: imports done in %.0f ms" % ((time.perf_counter() - _t_import) * 1000),
    flush=True,
)


class Inner(BaseModel):
    n: int = Field(ge=0)

    @field_validator("n")
    @classmethod
    def must_be_even(cls, value: int) -> int:
        if value % 2:
            raise ValueError("odd")
        return value


class Health(BaseModel):
    ok: bool
    python: str
    platform: str
    machine: str
    pydantic: str
    pydantic_core: str
    core_module: str
    core_reject_odd: bool
    validate_20k_ms: float


app = FastAPI()


@app.get("/api/health")
def health() -> dict:
    rejected = False
    try:
        Inner(n=41)
    except Exception:
        rejected = True
    t = time.perf_counter()
    for i in range(20000):
        Inner(n=2)
    validate_ms = (time.perf_counter() - t) * 1000
    return Health(
        ok=True,
        python=sys.version.split()[0],
        platform=sys.platform,
        machine=platform.machine(),
        pydantic=pydantic.VERSION,
        pydantic_core=pydantic_core.__version__,
        core_module=pydantic_core._pydantic_core.__file__.rsplit("/", 1)[-1],
        core_reject_odd=rejected,
        validate_20k_ms=round(validate_ms, 1),
    ).model_dump()


@app.get("/api/chroma")
def chroma_probe() -> dict:
    import tempfile

    out: dict = {"ok": False}
    t_all = time.perf_counter()
    try:
        t = time.perf_counter()
        import chromadb_rust_bindings as rb

        out["import_ms"] = round((time.perf_counter() - t) * 1000, 1)
        out["binding_module"] = rb.__file__.rsplit("/", 1)[-1]

        root = os.path.join(tempfile.gettempdir(), "chroma-probe-%d" % int(time.time()))
        index_dir = os.path.join(root, "index")
        os.makedirs(index_dir, exist_ok=True)

        t = time.perf_counter()
        cfg = rb.SqliteDBConfig(
            hash_type=rb.MigrationHash.MD5,
            migration_mode=rb.MigrationMode.Apply,
            url=os.path.join(root, "sysdb.sqlite3"),
        )
        bindings = rb.Bindings(
            allow_reset=True,
            sqlite_db_config=cfg,
            hnsw_cache_size=16 * 1024 * 1024,
            persist_path=os.path.join(root, "index"),
        )
        out["init_ms"] = round((time.perf_counter() - t) * 1000, 1)

        t = time.perf_counter()
        collection = bindings.create_collection("probe_collection")
        out["create_ms"] = round((time.perf_counter() - t) * 1000, 1)

        t = time.perf_counter()
        bindings.add(
            ids=["a", "b", "c"],
            collection_id=str(collection.id),
            embeddings=[
                [0.10, 0.20, 0.30, 0.40],
                [0.20, 0.10, 0.30, 0.40],
                [0.90, 0.80, 0.70, 0.60],
            ],
            documents=["alpha", "beta", "gamma"],
        )
        out["add_ms"] = round((time.perf_counter() - t) * 1000, 1)

        t = time.perf_counter()
        result = bindings.query(
            collection_id=str(collection.id),
            ids=None,
            query_embeddings=[[0.10, 0.20, 0.30, 0.40]],
            n_results=2,
        )
        out["query_ms"] = round((time.perf_counter() - t) * 1000, 1)

        out["collection_id"] = str(collection.id)
        out["query_ids"] = result.ids
        out["query_distances"] = result.distances
        out["count"] = bindings.count(collection_id=str(collection.id))

        files = []
        for base, _, names in os.walk(root):
            for name in names:
                path = os.path.join(base, name)
                files.append((os.path.relpath(path, root), os.path.getsize(path)))
        out["files"] = sorted(files)
        out["data_dir"] = root
        out["ok"] = True
    except Exception:
        out["error"] = traceback.format_exc()
    out["total_ms"] = round((time.perf_counter() - t_all) * 1000, 1)
    return out


@app.get("/spike", response_class=HTMLResponse)
def index() -> str:
    return """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Demiurge Spike</title>
<style>body{font:15px/1.5 sans-serif;margin:24px;background:#111;color:#eee}
pre{background:#1d1d1f;padding:12px;border-radius:8px;white-space:pre-wrap}</style></head>
<body><h3>Python backend is running on the phone</h3>
<b>pydantic-core</b>
<pre id="out">loading…</pre>
<b>chromadb rust core</b>
<pre id="chroma">loading…</pre>
<script>
fetch('/api/health').then(r=>r.json()).then(d=>{
  document.getElementById('out').textContent =
    'OK\\n' + JSON.stringify(d, null, 2);
}).catch(e=>{
  document.getElementById('out').textContent = 'FETCH FAILED: ' + e;
});
fetch('/api/chroma').then(r=>r.json()).then(d=>{
  document.getElementById('chroma').textContent =
    (d.ok ? 'OK' : 'FAILED') + '\\n' + JSON.stringify(d, null, 2);
}).catch(e=>{
  document.getElementById('chroma').textContent = 'FETCH FAILED: ' + e;
});
</script></body></html>"""


_server_started = False
_frontend_dir = ""


def set_frontend_dir(path: str) -> None:
    """Mount the real frontend dist (extracted by MainActivity) at the site root."""
    global _frontend_dir
    index_html = os.path.join(path, "index.html")
    if not os.path.isfile(index_html):
        print("spike: frontend dir unusable: %s" % path, flush=True)
        return
    _frontend_dir = path
    app.mount("/", StaticFiles(directory=path, html=True), name="frontend")
    print("spike: frontend mounted from %s" % path, flush=True)


@app.get("/api/frontend")
def frontend_probe() -> dict:
    if not _frontend_dir:
        return {"ok": False, "reason": "not mounted"}
    files = 0
    total = 0
    for base, _, names in os.walk(_frontend_dir):
        for name in names:
            files += 1
            total += os.path.getsize(os.path.join(base, name))
    stamp = os.path.join(_frontend_dir, ".version")
    version = open(stamp, encoding="utf-8").read().strip() if os.path.isfile(stamp) else ""
    return {"ok": True, "dir": _frontend_dir, "files": files,
            "bytes": total, "version": version}


def start() -> None:
    global _server_started
    if not _frontend_dir:
        app.add_api_route("/", index, response_class=HTMLResponse)
        print("spike: no frontend dir, / falls back to diagnostics", flush=True)
    print(
        "spike: python=%s platform=%s machine=%s pydantic=%s core=%s uvicorn=%s"
        % (
            sys.version.split()[0],
            sys.platform,
            platform.machine(),
            pydantic.VERSION,
            pydantic_core.__version__,
            uvicorn.__version__,
        ),
        flush=True,
    )
    if _server_started:
        return
    _server_started = True
    config = uvicorn.Config(app, host="127.0.0.1", port=8010, log_level="info")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True, name="uvicorn").start()
    print("spike: uvicorn thread started on 127.0.0.1:8010", flush=True)
