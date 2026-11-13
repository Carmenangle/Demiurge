//! 运行时按需交付（安装版第三步）：清单校验 → 下载/复用分件 → 合并校验 → 解压临时目录 → 原子落位。
//!
//! 合同：
//! - 只认 `release/runtime-delivery.json` 这一份 pin（构建时打进壳里）；哈希与大小以
//!   `Demiurge_update_<平台>_<tag>.json` 清单为唯一真源，pin 不重复存哈希（否则 CI 要先有清单才能写 pin）。
//! - 下载会先把**每个分件**校验一遍，再合并出归档并校验归档；任何一步不符就删掉坏文件重来，
//!   绝不把「看起来下完了」的东西解压进运行时目录。
//! - 落位是内容寻址：`runtime/base/<base_id>`、`runtime/apps/<application_id>`、`runtime/rag/<rag_id>`；
//!   先解压到 `runtime/.staging-*` 再改名，`current.json` 用临时文件 + 改名替换。
//! - 失败只清理自己写的临时目录与坏下载，不碰用户数据（`userdata/`、预设、日志）。
//!   唯一的例外是 `userdata/delivery.json`（P3-b 的后台补齐进度账本）：它只有字节数与阶段名，
//!   不含任何用户内容，登记在 `backend/app/data_layout.py` 的**缓存档**，卸载随缓存一起删。
//! - 离线入口：`<安装目录>\runtime-pack\` 与 `%LOCALAPPDATA%\Demiurge\downloads\`；有完整清单与
//!   分件时完全不联网。
//!
//! 提交合同（P2，2026-11-02）——「翻车不等于永久打不开」的四条：
//! - **提交闸**：`current.json` 只在候选运行时被探活证明能起之后才改写。只在「已提交过、
//!   且那一份现在还能起」时跑（首装没有可回退的东西，闸只会白加一次冷启动）。
//! - **前态**：提交时三层 id 有变化才写 `previous.json`，记录的是「上一个被提交的 commit」，
//!   不是「上一次尝试」。旧层是内容寻址，换版本＝换 id，所以旧层目录本来就不会被删；
//!   回收策略是「新提交成功启动过之后再删孤儿代际」（见 `prune_old_layers`）。
//! - **拒绝提交**：闸不过 ⇒ 放回同 id 旧目录、只删本次新建的层目录、旧 `current.json` 逐字
//!   不动、拒因既写日志也进错误串；`downloads/` 的分件缓存保留 ⇒ 重试只做本地重合并，不花流量。
//! - **临时项**：`.staging-*` / `.work-*` 在装配前扫一次（`sweep_temp`），删除失败不再被 `let _ =` 吞掉。
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

/// 构建期嵌入的交付 pin。CI 在构建壳之前按 release tag 重写 `release/runtime-delivery.json`。
pub const PIN_JSON: &str = include_str!("../../../release/runtime-delivery.json");

/// 必装层：装上壳就得能用起来的下限（对话、作品、检索的向量库都在这里）。
/// `rag` **不在**这一档：它 ~2 GiB，装的是 torch / sentence-transformers 这类本地推理栈，
/// 缺席时应用照跑，只是本地嵌入档与 Reranker 不可选（2026-11-03 P3 定案）。
const REQUIRED_LAYERS: [&str; 2] = ["base", "application"];
const OPTIONAL_LAYERS: [&str; 1] = ["rag"];
const DOWNLOAD_CHUNK: usize = 256 * 1024;
/// 解压体积估计：归档多为 PyInstaller/前端压缩产物，留 3 倍余量。
const EXTRACT_FACTOR: u64 = 3;
const SPACE_MARGIN: u64 = 256 * 1024 * 1024;

/// 一次交付管哪几层。
///
/// `Required` ＝首启/升级的正常路径（只碰必装层，rag 缺席也不报错）。
/// `Optional` ＝ P3-b 的后台补齐：只下「pin 说是可选、清单里真有、盘上还缺」的那几层。
/// 两档走**同一条主体**（同一套校验、同一个提交闸、同一次落位），只是层的集合不同——
/// 可选层绝不能因为「只是可选」就跳过探活：torch 的 DLL 与 base 层 ABI 绑死，
/// 坏的一代的失败形状是进程起不来，不是少个功能。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LayerScope {
    Required,
    Optional,
}

#[derive(Debug)]
pub struct DeliveryError(pub String);

impl std::fmt::Display for DeliveryError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for DeliveryError {}

impl From<std::io::Error> for DeliveryError {
    fn from(err: std::io::Error) -> Self {
        Self(format!("文件操作失败：{err}"))
    }
}

impl From<zip::result::ZipError> for DeliveryError {
    fn from(err: zip::result::ZipError) -> Self {
        Self(format!("压缩包处理失败：{err}"))
    }
}

impl From<serde_json::Error> for DeliveryError {
    fn from(err: serde_json::Error) -> Self {
        Self(format!("JSON 解析失败：{err}"))
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct DeliveryPin {
    pub schema_version: u32,
    pub tag: String,
    pub version: String,
    pub target: String,
    pub release_base: String,
    pub manifest: String,
    #[serde(default)]
    pub required_layers: Vec<String>,
    #[serde(default)]
    pub optional_layers: Vec<String>,
    #[serde(default)]
    pub mirrors: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
struct Manifest {
    schema_version: u32,
    app_version: String,
    target: String,
    #[serde(default)]
    edition: String,
    base_id: String,
    application_id: String,
    #[serde(default)]
    rag_id: String,
    layers: BTreeMap<String, Layer>,
}

impl Manifest {
    fn layer_id(&self, name: &str) -> &str {
        match name {
            "base" => &self.base_id,
            "application" => &self.application_id,
            "rag" => &self.rag_id,
            _ => "",
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
struct Layer {
    id: String,
    archive: String,
    size: u64,
    sha256: String,
    #[serde(default)]
    assets: Vec<Asset>,
}

#[derive(Debug, Clone, Deserialize)]
struct Asset {
    name: String,
    size: u64,
    sha256: String,
}

#[derive(Debug, Clone)]
pub struct DeliveryReport {
    pub base_id: String,
    pub application_id: String,
    pub rag_id: String,
    pub downloaded_layers: Vec<String>,
    pub reused: bool,
}

/// 一次提交涉及的三层 id。`rag_id` 允许为空（RAG 后补时它还不存在）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LayerIds {
    pub base_id: String,
    pub application_id: String,
    pub rag_id: String,
}

/// 一份状态档（`current.json` / `previous.json` / 闸用的临时档）的读取结果。
#[derive(Debug, Clone)]
pub struct StateDoc {
    pub ids: LayerIds,
    pub app_version: String,
    pub target: String,
    pub edition: String,
    pub schema_version: u64,
}

/// 提交闸要探的那个候选运行时怎么起：路径全部由交付侧给，**绝不指向真实数据**。
#[derive(Debug, Clone)]
pub struct GateRequest {
    /// `home/runtime`：层目录的根，探活要在这里起 exe。
    pub runtime_root: PathBuf,
    /// 安装目录：只为把壳自带的 MinGit / ffmpeg 前置进 PATH，与真实启动同一份合同。
    pub install_dir: PathBuf,
    /// `runtime/.gate-<token>/current.json`：写的是候选三层 id。
    pub state_path: PathBuf,
    /// `runtime/.gate-<token>/userdata`：候选层的 `LAF_DATA_DIR`，空的，用完即删。
    pub data_dir: PathBuf,
    /// 候选 base 层 id：用来定位 `base/<id>/Demiurge-Runtime.exe`。
    pub base_id: String,
}

/// 提交闸。`None` = 不过闸（自检与首装）；`Some` = 提交前必须过的探活，返回 `Err(拒因)`。
pub(crate) type GateFn = fn(&GateRequest) -> Result<(), String>;

/// 维护入口（`--install-runtime`）用来念「这次装哪几层」的文案真源，免得再出现
/// 一句写死三层、和契约对不上的横幅。
pub fn required_layers_text() -> String {
    REQUIRED_LAYERS.join(" + ")
}

pub fn embedded_pin() -> Result<DeliveryPin, DeliveryError> {
    let pin: DeliveryPin = serde_json::from_str(PIN_JSON)
        .map_err(|err| DeliveryError(format!("内置运行时交付 pin 无法解析：{err}")))?;
    validate_pin(&pin).map_err(DeliveryError)?;
    Ok(pin)
}

/// 安装版首次启动（或 `--install-runtime`）调用：把运行时部署到 `home()/runtime`。
/// 便携包自带运行时，`--install-runtime` 在包根没有意义。
pub fn is_portable_layout(exe_dir: &Path) -> bool {
    crate::paths::resolve_package(exe_dir).is_some()
}

/// 安装版首次启动（或 `--install-runtime`、托盘「检查更新」里的运行时漂移分支）调用：
/// 把运行时部署到 `home()/runtime`。**生产入口一律带提交闸**。
pub fn ensure_installed_runtime(
    install_dir: &Path,
    progress: &mut dyn FnMut(&str),
) -> Result<DeliveryReport, DeliveryError> {
    let pin = embedded_pin()?;
    deliver_with_gate(
        &pin,
        &crate::paths::home(),
        install_dir,
        None,
        Some(crate::runtime_host::probe_candidate),
        progress,
    )
}

/// 自检与单测入口：注入 pin / home / 可用磁盘空间，**不带提交闸**。
/// 自检夹具里的 `Demiurge-Runtime.exe` 是假字节（起不来），所以闸本身由第 19 项用
/// 脚本化闸单独验证，不能让第 17 项去跑真探活。
pub(crate) fn deliver(
    pin: &DeliveryPin,
    home: &Path,
    install_dir: &Path,
    free_bytes_override: Option<u64>,
    progress: &mut dyn FnMut(&str),
) -> Result<DeliveryReport, DeliveryError> {
    deliver_with_gate(pin, home, install_dir, free_bytes_override, None, progress)
}

/// 必装层交付（首启 / 升级 / 维护入口）。
pub(crate) fn deliver_with_gate(
    pin: &DeliveryPin,
    home: &Path,
    install_dir: &Path,
    free_bytes_override: Option<u64>,
    gate: Option<GateFn>,
    progress: &mut dyn FnMut(&str),
) -> Result<DeliveryReport, DeliveryError> {
    deliver_with_scope(
        pin,
        home,
        install_dir,
        free_bytes_override,
        gate,
        LayerScope::Required,
        &mut None,
        progress,
    )
}

/// 后台补齐（P3-b）：只交付可选层里「清单真提供、盘上还缺」的那几层，并把进度落到
/// `userdata/delivery.json`。走的是必装层同一条主体（同一套校验、同一个提交闸），
/// 差别只有层的集合，以及这次多一本对外可见的账。
pub(crate) fn deliver_optional(
    pin: &DeliveryPin,
    home: &Path,
    install_dir: &Path,
    free_bytes_override: Option<u64>,
    gate: Option<GateFn>,
    progress: &mut dyn FnMut(&str),
) -> Result<DeliveryReport, DeliveryError> {
    let mut ledger = Some(DeliveryLedger::new(home, &pin.optional_layers));
    let outcome = deliver_with_scope(
        pin,
        home,
        install_dir,
        free_bytes_override,
        gate,
        LayerScope::Optional,
        &mut ledger,
        progress,
    );
    if let Some(ledger) = ledger.as_mut() {
        match &outcome {
            // 这个版本没有可选组件可下（清单不含，或早已挂着且指向也翻好了）⇒ 不留账本：
            // 面板不该显示一条没发生过的补齐。
            Ok(report) if report.downloaded_layers.is_empty() && report.rag_id.is_empty() => {
                ledger.discard()
            }
            Ok(_) => ledger.finish_ok(),
            Err(err) => ledger.finish_err(&err.0),
        }
    }
    outcome
}

/// 生产入口：托盘「补齐 RAG」与进应用后的那一次询问都走这里，**带提交闸**。
pub fn backfill_optional_runtime(
    install_dir: &Path,
    progress: &mut dyn FnMut(&str),
) -> Result<DeliveryReport, DeliveryError> {
    let pin = embedded_pin()?;
    deliver_optional(
        &pin,
        &crate::paths::home(),
        install_dir,
        None,
        Some(crate::runtime_host::probe_candidate),
        progress,
    )
}

/// 可选层还没挂上吗。
///
/// 判据是 `current.json` 的 `rag_id` 是否为空——P3-a 定过这条诚实合同（状态档只写盘上真挂着的
/// 那代），所以「空」既覆盖「还没下」也覆盖「下坏了被闸拒掉」，不会谎报。
/// 读不到 `current.json` 返回 `false`：那是连必装层都还没提交的布局（开发态、或首启交付链还在跑），
/// 该由首启那条路径处理，后台补齐不抢跑。
pub fn optional_layer_missing(home: &Path) -> bool {
    match read_layer_ids(&home.join("runtime").join("current.json")) {
        Some(ids) => ids.rag_id.trim().is_empty(),
        None => false,
    }
}

/// 交付主体。`gate = Some` 时 `current.json` 只在候选运行时被探活证明能起之后才改写；
/// `ledger = Some` 时每一秒的字节 tick 额外落一份 `userdata/delivery.json`（见 `DeliveryLedger`）。
fn deliver_with_scope(
    pin: &DeliveryPin,
    home: &Path,
    install_dir: &Path,
    free_bytes_override: Option<u64>,
    gate: Option<GateFn>,
    scope: LayerScope,
    ledger: &mut Option<DeliveryLedger>,
    progress: &mut dyn FnMut(&str),
) -> Result<DeliveryReport, DeliveryError> {
    validate_pin(pin).map_err(DeliveryError)?;
    fs::create_dir_all(home)?;
    let runtime_root = home.join("runtime");
    let downloads = home.join("downloads");
    fs::create_dir_all(&runtime_root)?;
    fs::create_dir_all(&downloads)?;
    let local_dirs = vec![install_dir.join("runtime-pack"), downloads.clone()];

    // 装配前扫一次无主残留：同一进程内交付是单飞的（托盘与启动互斥），所以不设年龄护栏。
    let orphans = sweep_temp(&runtime_root, &downloads, None);
    for failure in &orphans {
        crate::shell_log::warn(&format!("清理上次残留的临时目录失败：{failure}"));
    }

    progress("读取运行时清单…");
    let manifest = match find_offline_manifest(pin, &local_dirs)? {
        Some(manifest) => manifest,
        None => fetch_manifest(pin, &downloads, progress)?,
    };
    validate_manifest(pin, &manifest).map_err(DeliveryError)?;

    // 这次管哪几层：必装档里缺层是硬错误；可选档只认「清单真的提供」的那几条——
    // 不提供不是失败，只是这个版本没有可选组件（`windows-x64` 无 rag 的那一类发行）。
    let declared: &Vec<String> = match scope {
        LayerScope::Required => &pin.required_layers,
        LayerScope::Optional => &pin.optional_layers,
    };
    let mut offered: Vec<&str> = Vec::new();
    for name in declared {
        if !manifest.layers.contains_key(name.as_str()) {
            if scope == LayerScope::Required {
                return Err(DeliveryError(format!("运行时清单缺少 {name} 层")));
            }
            continue;
        }
        offered.push(name.as_str());
    }
    let mut needed: Vec<&str> = Vec::new();
    for name in &offered {
        if !layer_installed(name, &runtime_root, manifest.layer_id(name)) {
            needed.push(*name);
        }
    }

    let rag_on_disk = installed_rag_id(&runtime_root, &manifest);
    // 「这次提交之后 rag 会不会真在盘上」：已经在，或这次就把它下下来。
    // 否则留空——盘上残留的旧一代绝不能接着挂（torch 的 DLL 与 base 层 ABI 绑死）。
    let takes_optional_rag = needed.iter().any(|name| *name == "rag") && !manifest.rag_id.trim().is_empty();
    let candidate = LayerIds {
        base_id: manifest.base_id.clone(),
        application_id: manifest.application_id.clone(),
        rag_id: if !rag_on_disk.is_empty() || takes_optional_rag {
            manifest.rag_id.clone()
        } else {
            String::new()
        },
    };
    // 提交闸只在「已提交过、那一份现在还能起、且这次要换指向」时跑。
    // 首装没有可回退的东西，闸只会给每台机器白加一次冷启动（torch 导入是分钟级）。
    let guarded: Option<LayerIds> = match read_layer_ids(&runtime_root.join("current.json")) {
        Some(committed)
            if committed != candidate && bootable_installed(&runtime_root, &committed) =>
        {
            Some(committed)
        }
        _ => None,
    };

    if needed.is_empty() {
        if offered.is_empty() {
            progress("这个版本没有提供可选组件，无需补齐");
            return Ok(DeliveryReport {
                base_id: manifest.base_id.clone(),
                application_id: manifest.application_id.clone(),
                rag_id: candidate.rag_id.clone(),
                downloaded_layers: Vec::new(),
                reused: true,
            });
        }
        // 层文件都在盘上不等于 commit 不用变：候选层已解压好但 `current.json` 还指着旧一代时，
        // 这条路径就是「只翻指向」的升级，同样必须过闸。
        run_gate(
            gate,
            guarded.as_ref(),
            &runtime_root,
            install_dir,
            &candidate,
            &manifest,
            progress,
        )?;
        let rag_after = installed_rag_id(&runtime_root, &manifest);
        commit(&runtime_root, &manifest, &rag_after)?;
        progress("运行时已就绪，无需重复部署");
        return Ok(DeliveryReport {
            base_id: manifest.base_id.clone(),
            application_id: manifest.application_id.clone(),
            rag_id: rag_after,
            downloaded_layers: Vec::new(),
            reused: true,
        });
    }

    let mut required_bytes = SPACE_MARGIN;
    let mut asset_bytes: u64 = 0;
    for name in &needed {
        let layer = &manifest.layers[*name];
        let all_local = layer
            .assets
            .iter()
            .all(|asset| local_dirs.iter().any(|dir| dir.join(&asset.name).is_file()));
        required_bytes = required_bytes
            .saturating_add(if all_local { 0 } else { layer.size })
            .saturating_add(layer.size.saturating_mul(EXTRACT_FACTOR));
        for asset in &layer.assets {
            asset_bytes = asset_bytes.saturating_add(asset.size);
        }
    }
    ensure_space(home, required_bytes, free_bytes_override)?;
    if let Some(ledger) = ledger.as_mut() {
        ledger.set_total(asset_bytes);
    }

    let token = unique_token();
    let staging = runtime_root.join(format!(".staging-{token}"));
    let work = downloads.join(format!(".work-{token}"));
    fs::create_dir_all(&staging)?;
    fs::create_dir_all(&work)?;

    let outcome = (|| -> Result<DeliveryReport, DeliveryError> {
        let fetcher = Fetcher::new();
        for name in &needed {
            let layer = &manifest.layers[*name];
            let label = layer_label(name);
            progress(&format!("准备{label}…"));
            let archive = acquire_layer(
                &fetcher,
                pin,
                name,
                layer,
                &local_dirs,
                &downloads,
                &work,
                &mut *ledger,
                progress,
            )?;
            progress(&format!("解压{label}…"));
            extract_zip_stripped(&archive, &staging.join(name))
                .map_err(|err| DeliveryError(format!("{label}解压失败：{err}")))?;
        }
        if let Some(ledger) = ledger.as_mut() {
            ledger.set_stage("解压落位");
        }

        let mut moved: Vec<(PathBuf, PathBuf)> = Vec::new();
        // 本次「凭空新建」的层目录：闸不过时只删这些，旧提交指向的目录绝不碰。
        let mut created: Vec<PathBuf> = Vec::new();
        let deployed = (|| -> Result<(), DeliveryError> {
            for name in &needed {
                let target = layer_dir(&runtime_root, name, manifest.layer_id(name));
                let stale = staging.join("stale").join(name);
                let pre_existing = target.exists();
                if pre_existing {
                    if let Some(parent) = stale.parent() {
                        fs::create_dir_all(parent)?;
                    }
                    fs::rename(&target, &stale)?;
                    moved.push((target.clone(), stale));
                }
                if let Some(parent) = target.parent() {
                    fs::create_dir_all(parent)?;
                }
                fs::rename(staging.join(name), &target)?;
                if !pre_existing {
                    created.push(target);
                }
            }
            Ok(())
        })();
        if let Err(err) = deployed {
            restore_moved_layers(&moved);
            return Err(err);
        }

        if let Some(ledger) = ledger.as_mut() {
            ledger.set_stage("启动自检");
        }
        if let Err(err) = run_gate(
            gate,
            guarded.as_ref(),
            &runtime_root,
            install_dir,
            &candidate,
            &manifest,
            progress,
        ) {
            restore_moved_layers(&moved);
            let leaked = discard_layers(&created);
            return Err(DeliveryError(format!(
                "{}{}{}",
                err.0,
                cleanup_note(&leaked),
                CACHE_KEPT_NOTE
            )));
        }
        if let Some(ledger) = ledger.as_mut() {
            ledger.set_stage("提交");
        }

        // 提交时按**盘上现状**重写 rag_id：过了闸也可能出现标记文件不合格的情况，那种时候
        // 宁可留空也不写没挂着的代际（P3-a 的诚实合同）。
        let rag_after = installed_rag_id(&runtime_root, &manifest);
        commit(&runtime_root, &manifest, &rag_after)?;
        progress("运行时部署完成");
        Ok(DeliveryReport {
            base_id: manifest.base_id.clone(),
            application_id: manifest.application_id.clone(),
            rag_id: rag_after,
            downloaded_layers: needed.iter().map(|name| name.to_string()).collect(),
            reused: false,
        })
    })();

    // 临时目录删除失败不再吞掉：它意味着几个 GB 留在盘上，必须能在日志里看到路径。
    for path in [&staging, &work] {
        if let Err(err) = fs::remove_dir_all(path) {
            if path.exists() {
                crate::shell_log::warn(&format!("清理临时目录失败：{}（{err}）", path.display()));
            }
        }
    }
    outcome
}

/// 提交闸：`Some(gate)` 且存在「能起的前一代」时，用候选层起一个隔离进程探活。
///
/// 探活跑在 `runtime/.gate-<token>/` 里：状态档写候选三层 id，`LAF_DATA_DIR` 指到同目录的
/// 空 `userdata`，用完即删。⚠ 有意的取舍：这样只能证明「这一层能起」，证明不了「带着用户的
/// 数据能起」——后者由启动侧的回退（`runtime_host` 的 `commit_previous`）兜第二道网。
fn run_gate(
    gate: Option<GateFn>,
    guarded: Option<&LayerIds>,
    runtime_root: &Path,
    install_dir: &Path,
    candidate: &LayerIds,
    manifest: &Manifest,
    progress: &mut dyn FnMut(&str),
) -> Result<(), DeliveryError> {
    let Some(gate) = gate else {
        return Ok(());
    };
    let Some(previous) = guarded else {
        crate::shell_log::info("提交自检跳过：没有可回退的已提交版本（首次部署或旧提交已不可启动）");
        return Ok(());
    };
    crate::shell_log::info(&format!(
        "提交自检开始：候选 base={} app={} rag={}，前一代 base={} app={} rag={}",
        candidate.base_id, candidate.application_id, candidate.rag_id,
        previous.base_id, previous.application_id, previous.rag_id
    ));
    progress("新运行时就位，正在做启动自检（可能要几分钟）…");

    let gate_dir = runtime_root.join(format!(".gate-{}", unique_token()));
    let state_path = gate_dir.join("current.json");
    let data_dir = gate_dir.join("userdata");
    fs::create_dir_all(&data_dir)?;
    let state = serde_json::json!({
        "schema_version": manifest.schema_version,
        "app_version": manifest.app_version,
        "target": manifest.target,
        "edition": manifest.edition,
        "base_id": candidate.base_id,
        "application_id": candidate.application_id,
        "rag_id": candidate.rag_id,
    });
    write_state_file(&state_path, &state)?;

    let request = GateRequest {
        runtime_root: runtime_root.to_path_buf(),
        install_dir: install_dir.to_path_buf(),
        state_path,
        data_dir,
        base_id: candidate.base_id.clone(),
    };
    let outcome = gate(&request);

    let leaked = match fs::remove_dir_all(&gate_dir) {
        Ok(()) => Vec::new(),
        Err(err) if gate_dir.exists() => vec![format!("{}（{err}）", gate_dir.display())],
        Err(_) => Vec::new(),
    };
    match outcome {
        Ok(()) => {
            crate::shell_log::info("提交自检通过：候选运行时健康检查与前端页面都就绪");
            progress("启动自检通过，正在提交新版本…");
            Ok(())
        }
        Err(reason) => {
            crate::shell_log::warn(&format!("提交自检未通过：{reason}"));
            Err(DeliveryError(format!(
                "新运行时未通过启动自检，已保持原版本不提交：{reason}{}{}",
                cleanup_note(&leaked),
                CACHE_KEPT_NOTE
            )))
        }
    }
}

/// 拒绝提交时删掉本次新建的层目录；失败路径逐条回报，不再 `let _ =` 吞掉。
fn discard_layers(created: &[PathBuf]) -> Vec<String> {
    created
        .iter()
        .filter(|dir| dir.exists())
        .filter_map(|dir| match fs::remove_dir_all(dir) {
            Ok(()) => {
                crate::shell_log::info(&format!("已删除未通过自检的新层：{}", dir.display()));
                None
            }
            Err(err) => Some(format!("{}（{err}）", dir.display())),
        })
        .collect()
}

fn cleanup_note(leaked: &[String]) -> String {
    if leaked.is_empty() {
        return String::new();
    }
    format!(
        "\n\n以下临时内容清理失败，需要手动删除：\n{}",
        leaked.join("\n")
    )
}

/// 拒绝提交后的固定收尾说明：分件缓存留在 `downloads/`，重试只做本地重合并与重解压。
const CACHE_KEPT_NOTE: &str = "\n\n已下载的分件保留在本地缓存里，重试不会重复下载。";

// ── 进度账本（P3-b 后台补齐）──────────────────────────────────────────────────

/// 账本文件名：`userdata/delivery.json`。读侧＝`backend/app/services/shell_delivery.py`
/// （再由 `activity_feed` 投影进「后台活动」面板），名字同时登记在
/// `backend/app/data_layout.py` 的**缓存**档 ⇒ 卸载随缓存一起删。
const LEDGER_NAME: &str = "delivery.json";

/// 后台补齐的进度账本。壳是唯一写入方，另一个进程只读。
///
/// 🔴 这是壳与后端之间**第一条跨进程通道**：此前两侧只有启动时单向注入的 5 个环境变量
/// （`LAF_RUNTIME_ROOT` / `LAF_RUNTIME_STATE` / `LAF_DATA_DIR` / `LAF_RUNTIME_PORT` /
/// `LAF_NO_BROWSER`），壳的进度只写启动页文案。所以文件位置、字段名和陈旧口径都是跨层合同，
/// 改一侧必须同步另一侧（`backend/tests/test_shell_delivery.py` 盯读侧，第 17/19 项自检盯写侧）。
///
/// 落盘节奏跟着分件下载的 1 秒 tick 走。壳中途崩掉时没人把 `running` 改成终态，所以
/// **陈旧判据放在读侧**（按 `updated_at` 年龄），这边不留需要谁去清的僵尸状态。
/// 写入用「临时文件 + rename」：读侧任何一刻拿到的都是完整的旧档或完整的新档。
struct DeliveryLedger {
    path: PathBuf,
    layer: String,
    label: String,
    state: &'static str,
    stage: &'static str,
    done_bytes: u64,
    total_bytes: u64,
    speed_bps: u64,
    started_at: f64,
    updated_at: f64,
    last_tick_at: f64,
    last_tick_done: u64,
    /// 本次任务里已经拿完的分件字节数：`done = asset_base + 当前分件已写字节`。
    asset_base: u64,
    error: String,
}

impl DeliveryLedger {
    fn new(home: &Path, layers: &[String]) -> Self {
        let started = now_epoch();
        let names: Vec<&str> = layers.iter().map(String::as_str).collect();
        Self {
            path: home.join("userdata").join(LEDGER_NAME),
            layer: names.join("+"),
            label: names.iter().map(|name| layer_label(name)).collect::<Vec<_>>().join("、"),
            state: "running",
            stage: "准备清单",
            done_bytes: 0,
            total_bytes: 0,
            speed_bps: 0,
            started_at: started,
            updated_at: started,
            last_tick_at: started,
            last_tick_done: 0,
            asset_base: 0,
            error: String::new(),
        }
    }

    /// 记下这次的总字节（＝待交付那几层的分件字节数之和），分件下载进度按它算百分比。
    fn set_total(&mut self, bytes: u64) {
        self.total_bytes = bytes;
        self.stage = "下载分件";
        self.flush();
    }

    fn set_stage(&mut self, stage: &'static str) {
        self.stage = stage;
        self.flush();
    }

    /// 当前分件已写 `written` 字节：一秒一次，顺带算出这次的瞬时速率（读侧不再自己攒状态）。
    fn tick(&mut self, written: u64) {
        self.done_bytes = self.asset_base.saturating_add(written).min(self.total_bytes);
        let now = now_epoch();
        let elapsed = now - self.last_tick_at;
        if elapsed < 0.5 {
            return;
        }
        let delta = self.done_bytes.saturating_sub(self.last_tick_done);
        self.speed_bps = (delta as f64 / elapsed).max(0.0) as u64;
        self.last_tick_at = now;
        self.last_tick_done = self.done_bytes;
        self.flush();
    }

    /// 一个分件到手（本地复用与真下载都算）：字节账推进，速率起点跟着走。
    fn end_asset(&mut self, size: u64) {
        self.asset_base = self.asset_base.saturating_add(size);
        self.done_bytes = self.asset_base.min(self.total_bytes);
        self.last_tick_done = self.done_bytes;
        self.last_tick_at = now_epoch();
        self.flush();
    }

    fn finish_ok(&mut self) {
        self.state = "done";
        self.stage = "已提交";
        self.done_bytes = self.total_bytes;
        self.speed_bps = 0;
        self.flush();
    }

    fn finish_err(&mut self, reason: &str) {
        self.state = "failed";
        self.speed_bps = 0;
        self.error = reason.to_string();
        self.flush();
    }

    /// 无事可做时删掉账本：面板不该显示一条没发生过的补齐。删不掉也不报错——
    /// 下次交付会整体覆盖，未登记进任何删除面之外的路径都由缓存档兜着。
    fn discard(&mut self) {
        if let Err(err) = fs::remove_file(&self.path) {
            if self.path.exists() {
                crate::shell_log::warn(&format!("清理补齐进度账本失败：{}（{err}）", self.path.display()));
            }
        }
    }

    fn flush(&mut self) {
        self.updated_at = now_epoch();
        let value = serde_json::json!({
            "schema_version": 1,
            "layer": self.layer,
            "label": self.label,
            "state": self.state,
            "stage": self.stage,
            "done_bytes": self.done_bytes,
            "total_bytes": self.total_bytes,
            "speed_bps": self.speed_bps,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "shell_pid": std::process::id(),
            "error": self.error,
        });
        if let Some(parent) = self.path.parent() {
            if let Err(err) = fs::create_dir_all(parent) {
                crate::shell_log::warn(&format!("创建进度账本目录失败：{err}"));
                return;
            }
        }
        // 账本只是可观测性，写坏它不能把正在进行的交付带崩。
        if let Err(err) = write_state_file(&self.path, &value) {
            crate::shell_log::warn(&format!("写入补齐进度账本失败：{err}"));
        }
    }
}

fn now_epoch() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|elapsed| elapsed.as_secs_f64())
        .unwrap_or(0.0)
}

// ── pin / 清单校验 ──────────────────────────────────────────────────────────

pub(crate) fn normalize_version(raw: &str) -> Option<String> {
    let trimmed = raw.trim();
    let core = trimmed.strip_prefix('v').unwrap_or(trimmed);
    let parts: Vec<&str> = core.split('.').collect();
    if !(2..=3).contains(&parts.len()) {
        return None;
    }
    let mut numbers = Vec::new();
    for part in parts {
        if part.is_empty() || part.len() > 6 || !part.chars().all(|c| c.is_ascii_digit()) {
            return None;
        }
        numbers.push(part.parse::<u64>().ok()?);
    }
    if numbers.len() == 2 {
        numbers.push(0);
    }
    Some(format!("{}.{}.{}", numbers[0], numbers[1], numbers[2]))
}

fn valid_sha(value: &str) -> bool {
    value.len() == 64 && value.chars().all(|c| c.is_ascii_hexdigit())
}

fn safe_asset_name(name: &str) -> bool {
    !name.is_empty()
        && !name.contains(['/', '\\', ':'])
        && name != "."
        && name != ".."
}

fn safe_base_url(url: &str) -> bool {
    url.starts_with("https://") || url.starts_with("file://")
}

/// 目标 ID 的平台段：`windows-x64-full-rag` ⇒ `windows`。
fn platform_of(target: &str) -> &str {
    target.split('-').next().unwrap_or("")
}

/// 归档基名（去掉 .zip/.tar.gz/.7z）；分卷名由它派生。
fn archive_stem(name: &str) -> &str {
    for suffix in [".tar.gz", ".zip", ".7z"] {
        if let Some(stem) = name.strip_suffix(suffix) {
            return stem;
        }
    }
    name
}

fn validate_pin(pin: &DeliveryPin) -> Result<(), String> {
    if pin.schema_version != 1 {
        return Err(format!("交付 pin schema_version 必须是 1，实际 {}", pin.schema_version));
    }
    let version = normalize_version(&pin.tag)
        .ok_or_else(|| format!("交付 pin 的 tag 不是合法版本号：{}", pin.tag))?;
    if version != pin.version {
        return Err(format!(
            "交付 pin 的 version 与 tag 不一致：tag={} ⇒ {version}，version={}",
            pin.tag, pin.version
        ));
    }
    if pin.target.trim().is_empty() || pin.target.contains(['/', '\\', ':']) {
        return Err(format!("交付 pin 的 target 不合法：{}", pin.target));
    }
    let expected_manifest = format!("Demiurge_update_{}_{}.json", platform_of(&pin.target), pin.tag);
    if pin.manifest != expected_manifest {
        return Err(format!(
            "交付 pin 的清单名不是 {expected_manifest}：{}",
            pin.manifest
        ));
    }
    if !safe_base_url(&pin.release_base) {
        return Err(format!(
            "交付 pin 的 release_base 必须是 https://（自检允许 file://）：{}",
            pin.release_base
        ));
    }
    for mirror in &pin.mirrors {
        if !safe_base_url(mirror) {
            return Err(format!("交付 pin 的镜像地址不合法：{mirror}"));
        }
    }
    let required: BTreeSet<&str> = pin.required_layers.iter().map(String::as_str).collect();
    let expected_required: BTreeSet<&str> = REQUIRED_LAYERS.into_iter().collect();
    if required != expected_required || pin.required_layers.len() != REQUIRED_LAYERS.len() {
        return Err(format!(
            "交付 pin 的 required_layers 必须恰好是 {:?}：{:?}",
            REQUIRED_LAYERS, pin.required_layers
        ));
    }
    let optional: BTreeSet<&str> = pin.optional_layers.iter().map(String::as_str).collect();
    let allowed_optional: BTreeSet<&str> = OPTIONAL_LAYERS.into_iter().collect();
    if !optional.is_subset(&allowed_optional) {
        return Err(format!("交付 pin 的 optional_layers 只能包含 rag：{:?}", pin.optional_layers));
    }
    if !required.is_disjoint(&optional) {
        return Err("交付 pin 的 required_layers 与 optional_layers 不能重叠".to_string());
    }
    Ok(())
}

fn validate_layer(name: &str, layer: &Layer) -> Result<(), String> {
    if layer.id.trim().is_empty() || layer.id.contains(['/', '\\', ':']) {
        return Err(format!("{name} 层的 id 不合法：{}", layer.id));
    }
    if !safe_asset_name(&layer.archive) {
        return Err(format!("{name} 层的归档名不合法：{}", layer.archive));
    }
    if layer.assets.is_empty() {
        return Err(format!("{name} 层没有任何分件"));
    }
    if !valid_sha(&layer.sha256) {
        return Err(format!("{name} 层的归档哈希格式不正确"));
    }
    let mut names = BTreeSet::new();
    let mut total = 0u64;
    for asset in &layer.assets {
        if !safe_asset_name(&asset.name) {
            return Err(format!("{name} 层的分件名不合法：{}", asset.name));
        }
        if !names.insert(asset.name.as_str()) {
            return Err(format!("{name} 层的分件名重复：{}", asset.name));
        }
        if !valid_sha(&asset.sha256) {
            return Err(format!("{name} 层分件哈希格式不正确：{}", asset.name));
        }
        total = total.saturating_add(asset.size);
    }
    if layer.assets.len() == 1 {
        if layer.assets[0].name != layer.archive {
            return Err(format!(
                "{name} 层只有一个分件时，分件名必须等于归档名：{} != {}",
                layer.assets[0].name, layer.archive
            ));
        }
    } else {
        let prefix = format!("{}.part", archive_stem(&layer.archive));
        for asset in &layer.assets {
            if !asset.name.starts_with(&prefix) {
                return Err(format!(
                    "{name} 层的分卷必须以 {prefix} 开头：{}",
                    asset.name
                ));
            }
        }
    }
    if total != layer.size {
        return Err(format!(
            "{name} 层分件大小之和与归档大小不符：{total} != {}",
            layer.size
        ));
    }
    Ok(())
}

fn validate_manifest(pin: &DeliveryPin, manifest: &Manifest) -> Result<(), String> {
    if manifest.schema_version != 2 {
        return Err(format!(
            "运行时清单 schema_version 必须是 2，实际 {}",
            manifest.schema_version
        ));
    }
    if manifest.target != pin.target {
        return Err(format!(
            "运行时清单 target 与 pin 不一致：{} != {}",
            manifest.target, pin.target
        ));
    }
    if normalize_version(&manifest.app_version).as_deref() != Some(pin.version.as_str()) {
        return Err(format!(
            "运行时清单版本与 pin 不一致：{} != {}",
            manifest.app_version, pin.version
        ));
    }
    if manifest.base_id.trim().is_empty() || manifest.application_id.trim().is_empty() {
        return Err("运行时清单缺少 base_id / application_id".to_string());
    }
    for name in &pin.required_layers {
        if !manifest.layers.contains_key(name) {
            return Err(format!("运行时清单缺少必需层：{name}"));
        }
    }
    if manifest.layers.contains_key("rag") && manifest.rag_id.trim().is_empty() {
        return Err("运行时清单有 rag 层但缺少 rag_id".to_string());
    }
    let mut all_names = BTreeSet::new();
    for (name, layer) in &manifest.layers {
        if !REQUIRED_LAYERS.contains(&name.as_str()) && !OPTIONAL_LAYERS.contains(&name.as_str()) {
            return Err(format!("运行时清单包含未知层：{name}"));
        }
        validate_layer(name, layer)?;
        for asset in &layer.assets {
            if !all_names.insert(asset.name.as_str()) {
                return Err(format!("运行时清单跨层重复使用分件：{}", asset.name));
            }
        }
    }
    Ok(())
}

// ── 清单获取 ────────────────────────────────────────────────────────────────

fn manifest_matches(pin: &DeliveryPin, manifest: &Manifest) -> bool {
    manifest.schema_version == 2
        && manifest.target == pin.target
        && normalize_version(&manifest.app_version).as_deref() == Some(pin.version.as_str())
}

fn find_offline_manifest(pin: &DeliveryPin, dirs: &[PathBuf]) -> Result<Option<Manifest>, DeliveryError> {
    for dir in dirs {
        let exact = dir.join(&pin.manifest);
        if exact.is_file() {
            if let Ok(manifest) = read_manifest(&exact) {
                if manifest_matches(pin, &manifest) {
                    return Ok(Some(manifest));
                }
            }
        }
        let Ok(entries) = fs::read_dir(dir) else {
            continue;
        };
        // 新命名不带 target（`Demiurge_update_windows_v0.30.json`），平台与版本由
        // `manifest_matches` 逐项裁决；这里只做粗筛，避免误读无关 JSON。
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if !name.starts_with("Demiurge_update_") || !name.ends_with(".json") {
                continue;
            }
            let path = entry.path();
            if path == exact || !path.is_file() {
                continue;
            }
            if let Ok(manifest) = read_manifest(&path) {
                if manifest_matches(pin, &manifest) {
                    return Ok(Some(manifest));
                }
            }
        }
    }
    Ok(None)
}

fn read_manifest(path: &Path) -> Result<Manifest, DeliveryError> {
    let bytes = fs::read(path)?;
    serde_json::from_slice(&bytes)
        .map_err(|err| DeliveryError(format!("清单解析失败（{}）：{err}", path.display())))
}

fn remote_sources(pin: &DeliveryPin) -> Vec<String> {
    let mut sources = Vec::new();
    for candidate in std::iter::once(&pin.release_base).chain(pin.mirrors.iter()) {
        let trimmed = candidate.trim_end_matches('/').to_string();
        if !trimmed.is_empty() && !sources.contains(&trimmed) {
            sources.push(trimmed);
        }
    }
    sources
}

fn fetch_manifest(
    pin: &DeliveryPin,
    downloads: &Path,
    progress: &mut dyn FnMut(&str),
) -> Result<Manifest, DeliveryError> {
    let dest = downloads.join(&pin.manifest);
    let fetcher = Fetcher::new();
    let mut errors: Vec<String> = Vec::new();
    for source in remote_sources(pin) {
        let url = url_join(&source, &pin.manifest);
        for attempt in 0..2 {
            if attempt > 0 {
                let _ = fs::remove_file(&dest);
            }
            progress(&format!("下载运行时清单（{source}）…"));
            if let Err(err) = fetcher.fetch(&url, &dest, progress, "运行时清单", &mut None) {
                errors.push(format!("{url}: {err}"));
                break;
            }
            match fs::read(&dest)
                .map_err(DeliveryError::from)
                .and_then(|bytes| {
                    serde_json::from_slice::<Manifest>(&bytes)
                        .map_err(|err| DeliveryError(format!("清单解析失败：{err}")))
                }) {
                Ok(manifest) if manifest_matches(pin, &manifest) => return Ok(manifest),
                Ok(_) => errors.push(format!("{url}: 清单与交付 pin 不一致")),
                Err(err) => errors.push(format!("{url}: {err}")),
            }
        }
    }
    Err(DeliveryError(format!(
        "无法获取运行时清单 {}：{}",
        pin.manifest,
        errors.join("；")
    )))
}

// ── 下载与校验 ──────────────────────────────────────────────────────────────

struct Fetcher {
    agent: ureq::Agent,
}

impl Fetcher {
    fn new() -> Self {
        let mut builder = ureq::AgentBuilder::new()
            .timeout_connect(Duration::from_secs(30))
            .timeout_read(Duration::from_secs(60));
        // ureq 的 `native-tls` 只是可选依赖，不会成为默认后端；不显式挂上时 https
        // 在建立连接前就返回 UnknownScheme（首次启动下载运行时会全量失败）。
        match native_tls::TlsConnector::new() {
            Ok(connector) => builder = builder.tls_connector(std::sync::Arc::new(connector)),
            Err(err) => eprintln!("[runtime] 初始化系统 TLS 失败：{err}"),
        }
        Self { agent: builder.build() }
    }

    fn fetch(
        &self,
        url: &str,
        dest: &Path,
        progress: &mut dyn FnMut(&str),
        label: &str,
        ledger: &mut Option<DeliveryLedger>,
    ) -> Result<(), DeliveryError> {
        if url.starts_with("file://") {
            self.copy_file(url, dest)
        } else if url.starts_with("https://") || url.starts_with("http://") {
            self.http(url, dest, progress, label, ledger)
        } else {
            Err(DeliveryError(format!("不支持的下载地址：{url}")))
        }
    }

    /// 本地源（自检的 file:// 或用户自己放的包）：按剩余字节续传，语义与 HTTP 保持一致。
    fn copy_file(&self, url: &str, dest: &Path) -> Result<(), DeliveryError> {
        let source = file_url_to_path(url)?;
        if !source.is_file() {
            return Err(DeliveryError(format!("本地源文件不存在：{}", source.display())));
        }
        let total = source.metadata()?.len();
        let existing = dest.metadata().map(|meta| meta.len()).unwrap_or(0);
        let mut input = File::open(&source)?;
        let mut output = if existing > 0 && existing <= total {
            input.seek(SeekFrom::Start(existing))?;
            OpenOptions::new().append(true).open(dest)?
        } else {
            File::create(dest)?
        };
        std::io::copy(&mut input, &mut output)?;
        output.flush()?;
        Ok(())
    }

    fn http(
        &self,
        url: &str,
        dest: &Path,
        progress: &mut dyn FnMut(&str),
        label: &str,
        ledger: &mut Option<DeliveryLedger>,
    ) -> Result<(), DeliveryError> {
        let existing = dest.metadata().map(|meta| meta.len()).unwrap_or(0);
        let mut request = self.agent.get(url);
        if existing > 0 {
            request = request.set("Range", &format!("bytes={existing}-"));
        }
        let response = match request.call() {
            Ok(response) => response,
            Err(ureq::Error::Status(416, _)) if existing > 0 => return Ok(()),
            Err(ureq::Error::Status(code, _)) => {
                return Err(DeliveryError(format!("HTTP {code}")));
            }
            Err(err) => return Err(DeliveryError(format!("网络错误：{err}"))),
        };
        let status = response.status();
        let mut reader = response.into_reader();
        let mut output = if status == 206 && existing > 0 {
            OpenOptions::new().append(true).open(dest)?
        } else {
            File::create(dest)?
        };
        let mut written = if status == 206 { existing } else { 0 };
        let mut buffer = vec![0u8; DOWNLOAD_CHUNK];
        let mut last_report = Instant::now();
        loop {
            let read = reader
                .read(&mut buffer)
                .map_err(|err| DeliveryError(format!("读取响应失败：{err}")))?;
            if read == 0 {
                break;
            }
            output.write_all(&buffer[..read])?;
            written += read as u64;
            if last_report.elapsed() >= Duration::from_secs(1) {
                last_report = Instant::now();
                progress(&format!("{label}：已下载 {}", human_bytes(written)));
                // `written` 含续传已有的字节（206 时起点就是 `existing`），所以账本这边
                // 直接按「这个分件现在手上有几字节」推进，续传不会重复计数。
                if let Some(ledger) = ledger.as_mut() {
                    ledger.tick(written);
                }
            }
        }
        output.flush()?;
        Ok(())
    }
}

fn acquire_layer(
    fetcher: &Fetcher,
    pin: &DeliveryPin,
    name: &str,
    layer: &Layer,
    local_dirs: &[PathBuf],
    downloads: &Path,
    work: &Path,
    ledger: &mut Option<DeliveryLedger>,
    progress: &mut dyn FnMut(&str),
) -> Result<PathBuf, DeliveryError> {
    let label = layer_label(name);
    let sources = remote_sources(pin);
    let mut asset_paths: Vec<PathBuf> = Vec::new();
    for (index, asset) in layer.assets.iter().enumerate() {
        if let Some(found) = find_verified(local_dirs, asset) {
            asset_paths.push(found);
            if let Some(ledger) = ledger.as_mut() {
                ledger.end_asset(asset.size);
            }
            continue;
        }
        let dest = downloads.join(&asset.name);
        let position = if layer.assets.len() > 1 {
            format!("（{}/{}）", index + 1, layer.assets.len())
        } else {
            String::new()
        };
        progress(&format!(
            "下载{label}{position} {}（{}）…",
            asset.name,
            human_bytes(asset.size)
        ));
        download_asset(fetcher, &sources, asset, &dest, &label, &mut *ledger, progress)?;
        if let Some(ledger) = ledger.as_mut() {
            ledger.end_asset(asset.size);
        }
        asset_paths.push(dest);
    }

    let archive = if asset_paths.len() == 1
        && layer.assets.len() == 1
        && asset_paths[0].file_name().and_then(|value| value.to_str()) == Some(layer.archive.as_str())
    {
        asset_paths[0].clone()
    } else {
        let target = work.join(&layer.archive);
        assemble(&asset_paths, &target)?;
        target
    };
    verify_file(&archive, layer.size, &layer.sha256)
        .map_err(|err| DeliveryError(format!("{label}归档校验失败：{err}")))?;
    Ok(archive)
}

fn find_verified(dirs: &[PathBuf], asset: &Asset) -> Option<PathBuf> {
    for dir in dirs {
        let candidate = dir.join(&asset.name);
        if candidate.is_file() && verify_file(&candidate, asset.size, &asset.sha256).is_ok() {
            return Some(candidate);
        }
    }
    None
}

fn download_asset(
    fetcher: &Fetcher,
    sources: &[String],
    asset: &Asset,
    dest: &Path,
    label: &str,
    ledger: &mut Option<DeliveryLedger>,
    progress: &mut dyn FnMut(&str),
) -> Result<(), DeliveryError> {
    if dest.is_file() {
        let length = fs::metadata(dest).map(|meta| meta.len()).unwrap_or(0);
        if length == asset.size {
            if verify_file(dest, asset.size, &asset.sha256).is_ok() {
                return Ok(());
            }
            // 完整但内容坏 ⇒ 删掉重下；只靠 Range 续传会一直撞 416。
            let _ = fs::remove_file(dest);
        } else if length > asset.size {
            let _ = fs::remove_file(dest);
        }
    }
    let mut errors: Vec<String> = Vec::new();
    for source in sources {
        let url = url_join(source, &asset.name);
        for attempt in 0..2 {
            match fetcher.fetch(&url, dest, progress, label, &mut *ledger) {
                Ok(()) => match verify_file(dest, asset.size, &asset.sha256) {
                    Ok(()) => return Ok(()),
                    Err(err) => {
                        errors.push(format!("{url}: {err}"));
                        let _ = fs::remove_file(dest);
                        if attempt == 1 {
                            break;
                        }
                    }
                },
                Err(err) => {
                    errors.push(format!("{url}: {err}"));
                    break;
                }
            }
        }
    }
    Err(DeliveryError(format!(
        "{label}分件下载失败 {}：{}",
        asset.name,
        errors.join("；")
    )))
}

fn assemble(parts: &[PathBuf], target: &Path) -> Result<(), DeliveryError> {
    if target.exists() {
        let _ = fs::remove_file(target);
    }
    let mut output = File::create(target)?;
    for part in parts {
        let mut input = File::open(part)
            .map_err(|err| DeliveryError(format!("缺少分件 {}：{err}", part.display())))?;
        std::io::copy(&mut input, &mut output)?;
    }
    output.flush()?;
    Ok(())
}

fn verify_file(path: &Path, size: u64, sha256: &str) -> Result<(), String> {
    let metadata = fs::metadata(path).map_err(|err| format!("无法读取 {}：{err}", path.display()))?;
    if metadata.len() != size {
        return Err(format!(
            "大小不符：{}（期望 {size}，实际 {}）",
            path.file_name().unwrap_or_default().to_string_lossy(),
            metadata.len()
        ));
    }
    let actual = sha256_file(path).map_err(|err| err.to_string())?;
    if !actual.eq_ignore_ascii_case(sha256) {
        return Err(format!(
            "哈希不符：{}（期望 {sha256}，实际 {actual}）",
            path.file_name().unwrap_or_default().to_string_lossy()
        ));
    }
    Ok(())
}

fn sha256_file(path: &Path) -> Result<String, DeliveryError> {
    let mut file = File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0u8; 1024 * 1024];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

// ── 解压与落位 ──────────────────────────────────────────────────────────────

fn extract_zip_stripped(archive: &Path, dest: &Path) -> Result<(), DeliveryError> {
    let file = File::open(archive)?;
    let mut zip = zip::ZipArchive::new(file)?;
    let mut roots: BTreeSet<String> = BTreeSet::new();
    for index in 0..zip.len() {
        let entry = zip.by_index(index)?;
        let normalized = entry.name().replace('\\', "/");
        if normalized.starts_with('/') || normalized.contains(':') {
            return Err(DeliveryError(format!("压缩包包含绝对路径：{normalized}")));
        }
        let components: Vec<String> = normalized
            .split('/')
            .filter(|part| !part.is_empty() && *part != ".")
            .map(str::to_string)
            .collect();
        if components.iter().any(|part| part == "..") {
            return Err(DeliveryError(format!("压缩包包含越界路径：{normalized}")));
        }
        if components.is_empty() {
            continue;
        }
        roots.insert(components[0].clone());
    }
    if roots.len() != 1 {
        return Err(DeliveryError(format!(
            "运行时归档必须只有一个根目录，实际 {} 个",
            roots.len()
        )));
    }

    fs::create_dir_all(dest)?;
    for index in 0..zip.len() {
        let mut entry = zip.by_index(index)?;
        let normalized = entry.name().replace('\\', "/");
        let components: Vec<String> = normalized
            .split('/')
            .filter(|part| !part.is_empty() && *part != ".")
            .map(str::to_string)
            .collect();
        if components.len() <= 1 {
            continue;
        }
        let relative: PathBuf = components[1..].iter().collect();
        let target = dest.join(relative);
        if entry.is_dir() {
            fs::create_dir_all(&target)?;
            continue;
        }
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut output = File::create(&target)?;
        std::io::copy(&mut entry, &mut output)?;
        output.flush()?;
    }
    Ok(())
}

fn layer_dir(runtime_root: &Path, name: &str, id: &str) -> PathBuf {
    match name {
        "base" => runtime_root.join("base").join(id),
        "application" => runtime_root.join("apps").join(id),
        "rag" => runtime_root.join("rag").join(id),
        _ => runtime_root.join(name).join(id),
    }
}

fn layer_installed(name: &str, runtime_root: &Path, id: &str) -> bool {
    if id.trim().is_empty() {
        return false;
    }
    let dir = layer_dir(runtime_root, name, id);
    match name {
        "base" => {
            dir.join("Demiurge-Runtime.exe").is_file()
                && marker_id(&dir, "base-manifest.json", "base_id").as_deref() == Some(id)
        }
        "application" => {
            dir.join("frontend").join("index.html").is_file()
                && marker_id(&dir, "application-manifest.json", "application_id").as_deref()
                    == Some(id)
        }
        "rag" => {
            marker_id(&dir, "rag-manifest.json", "rag_id").as_deref() == Some(id)
        }
        _ => false,
    }
}

fn marker_id(dir: &Path, file: &str, key: &str) -> Option<String> {
    let text = fs::read_to_string(dir.join(file)).ok()?;
    let value: serde_json::Value = serde_json::from_str(&text).ok()?;
    value.get(key)?.as_str().map(str::to_string)
}

/// 清单广告的这代 rag 层**真的在盘上**才写进状态档。
///
/// rag 从 P3 起是可选层：没交付时 `current.json` 的 `rag_id` 必须留空，否则启动入口会去挂
/// 一个不存在的 `site-packages`。id 对不上也不行——留在盘上的旧一代 rag 不能接着挂，
/// torch 的 DLL 与 base 层 ABI 相互绑定（见 `runtime-layered.spec` 里 msvcp140 那段），
/// 错配的代价是进程起不来，而不是「少个功能」。
fn installed_rag_id(runtime_root: &Path, manifest: &Manifest) -> String {
    if !manifest.layers.contains_key("rag") || manifest.rag_id.trim().is_empty() {
        return String::new();
    }
    if layer_installed("rag", runtime_root, &manifest.rag_id) {
        manifest.rag_id.clone()
    } else {
        String::new()
    }
}

/// 提交：先记前态，再翻 `current.json`。
///
/// `previous.json` 只在三层 id 真的变化时写，内容是**上一个被提交的 commit**（不是上一次尝试），
/// 所以「探活失败」「解压失败」都不会污染回退点。
fn commit(runtime_root: &Path, manifest: &Manifest, rag_id: &str) -> Result<(), DeliveryError> {
    let candidate = LayerIds {
        base_id: manifest.base_id.clone(),
        application_id: manifest.application_id.clone(),
        rag_id: rag_id.to_string(),
    };
    if let Some(previous) = read_state(&runtime_root.join("current.json")) {
        if previous.ids != candidate {
            crate::shell_log::info(&format!(
                "记录前一代提交：base {}→{}、app {}→{}、rag {}→{}",
                previous.ids.base_id, candidate.base_id,
                previous.ids.application_id, candidate.application_id,
                previous.ids.rag_id, candidate.rag_id
            ));
            write_state_file(&runtime_root.join("previous.json"), &state_value(&previous))?;
        }
    }
    let state = serde_json::json!({
        "schema_version": manifest.schema_version,
        "app_version": manifest.app_version,
        "target": manifest.target,
        "edition": manifest.edition,
        "base_id": manifest.base_id,
        "application_id": manifest.application_id,
        "rag_id": rag_id,
    });
    write_state_file(&runtime_root.join("current.json"), &state)
}

fn state_value(doc: &StateDoc) -> serde_json::Value {
    serde_json::json!({
        "schema_version": doc.schema_version,
        "app_version": doc.app_version,
        "target": doc.target,
        "edition": doc.edition,
        "base_id": doc.ids.base_id,
        "application_id": doc.ids.application_id,
        "rag_id": doc.ids.rag_id,
    })
}

/// 状态档写入都是临时文件 + rename：读侧永远只会看到「旧的完整档」或「新的完整档」。
fn write_state_file(path: &Path, state: &serde_json::Value) -> Result<(), DeliveryError> {
    let payload = serde_json::to_vec_pretty(state)?;
    let file_name = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("current.json");
    let tmp = path.with_file_name(format!(".{file_name}-{}", unique_token()));
    fs::write(&tmp, payload)?;
    if let Err(err) = fs::rename(&tmp, path) {
        let _ = fs::remove_file(&tmp);
        return Err(DeliveryError(format!(
            "写入 {} 失败：{err}",
            path.display()
        )));
    }
    Ok(())
}

fn read_state(path: &Path) -> Option<StateDoc> {
    let text = fs::read_to_string(path).ok()?;
    let value: serde_json::Value = serde_json::from_str(&text).ok()?;
    let read_id = |key: &str| -> String {
        value
            .get(key)
            .and_then(|item| item.as_str())
            .unwrap_or_default()
            .to_string()
    };
    let ids = LayerIds {
        base_id: read_id("base_id"),
        application_id: read_id("application_id"),
        rag_id: read_id("rag_id"),
    };
    if ids.base_id.trim().is_empty() || ids.application_id.trim().is_empty() {
        return None;
    }
    Some(StateDoc {
        ids,
        app_version: read_id("app_version"),
        target: read_id("target"),
        edition: read_id("edition"),
        schema_version: value
            .get("schema_version")
            .and_then(|item| item.as_u64())
            .unwrap_or(0),
    })
}

pub fn read_layer_ids(path: &Path) -> Option<LayerIds> {
    read_state(path).map(|doc| doc.ids)
}

/// 「这一代现在起得来吗」的静态判据：base + application 的标记文件都合格。
/// RAG 不参与判定——它缺了只是功能降级，不该因此放弃回退点（P3 还要把它改成可后补）。
fn bootable_installed(runtime_root: &Path, ids: &LayerIds) -> bool {
    layer_installed("base", runtime_root, &ids.base_id)
        && layer_installed("application", runtime_root, &ids.application_id)
}

/// 能不能回退：只判据、不写盘。返回（坏的当前代，可用的前一代）。
pub fn rollback_target(runtime_root: &Path) -> Result<(LayerIds, LayerIds), String> {
    let current = read_layer_ids(&runtime_root.join("current.json"))
        .ok_or_else(|| "current.json 缺失或没有可用的层 id".to_string())?;
    let previous = read_layer_ids(&runtime_root.join("previous.json"))
        .ok_or_else(|| "没有前一代提交（previous.json 缺失或内容不完整）".to_string())?;
    if previous == current {
        return Err("前一代与当前提交相同，回退没有意义".to_string());
    }
    if !bootable_installed(runtime_root, &previous) {
        return Err("前一代的层文件不完整，无法回退".to_string());
    }
    Ok((current, previous))
}

/// 回退：`current.json` 改指前一代，`previous.json` 改成那份坏的（这样它不会被回收，
/// 也不会被下一轮回退当成可用点——两档互换后 `rollback_target` 会因「相同」直接拒绝）。
///
/// 两次 rename 之间崩溃的后果：`current` 仍指坏代、`previous` 也变成坏代 ⇒ 下次启动
/// 判定「相同」不再回退，行为与不回退一致，不会来回翻档。
pub fn commit_previous(runtime_root: &Path) -> Result<(LayerIds, LayerIds), String> {
    let (broken, used) = rollback_target(runtime_root)?;
    let broken_doc = read_state(&runtime_root.join("current.json"))
        .ok_or_else(|| "current.json 读不出来".to_string())?;
    let used_doc = read_state(&runtime_root.join("previous.json"))
        .ok_or_else(|| "previous.json 读不出来".to_string())?;
    write_state_file(&runtime_root.join("previous.json"), &state_value(&broken_doc))
        .map_err(|err| err.to_string())?;
    write_state_file(&runtime_root.join("current.json"), &state_value(&used_doc))
        .map_err(|err| err.to_string())?;
    crate::shell_log::warn(&format!(
        "已回退到前一代运行时：base {}→{}、app {}→{}",
        broken.base_id, used.base_id, broken.application_id, used.application_id
    ));
    Ok((broken, used))
}

/// 已装运行时的 `app_version` 与本壳 pin 不一致 ⇒ 层需要升级。纯本地读档，零流量。
pub fn runtime_drift(home: &Path, pin: &DeliveryPin) -> Option<(String, String)> {
    let doc = read_state(&home.join("runtime").join("current.json"))?;
    let installed = normalize_version(&doc.app_version)?;
    if installed == pin.version {
        return None;
    }
    Some((doc.app_version, pin.version.clone()))
}

/// 回收上一次崩掉的临时目录：`runtime/.staging-*` 与 `downloads/.work-*`。
/// `min_age` 是给「壳启动路径」留的护栏（并行的 `--install-runtime` 可能正在写）；
/// 交付自己调用时传 `None`（同进程单飞）。返回删除失败的路径清单，不再 `let _ =` 吞掉。
pub fn sweep_temp(
    runtime_root: &Path,
    downloads: &Path,
    min_age: Option<Duration>,
) -> Vec<String> {
    let mut failures = Vec::new();
    for (dir, prefix) in [
        (runtime_root, ".staging-"),
        (downloads, ".work-"),
        (runtime_root, ".gate-"),
    ] {
        let Ok(entries) = fs::read_dir(dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let name = entry.file_name();
            let name = name.to_string_lossy().to_string();
            if !name.starts_with(prefix) {
                continue;
            }
            if let Some(limit) = min_age {
                let old = entry
                    .metadata()
                    .and_then(|meta| meta.modified())
                    .ok()
                    .map(|modified| modified.elapsed().unwrap_or(Duration::from_secs(u64::MAX)))
                    .unwrap_or(Duration::from_secs(u64::MAX));
                if old < limit {
                    continue;
                }
            }
            let path = entry.path();
            if let Err(err) = fs::remove_dir_all(&path) {
                if path.exists() {
                    failures.push(format!("{}（{err}）", path.display()));
                }
            }
        }
    }
    failures
}

/// 旧代回收：删掉既不被 `current.json` 也不被 `previous.json` 引用的层目录。
///
/// 两档引用的目录**绝不进名单**（那是回退点）；再加 `min_age` 护栏，防误杀刚改名进来、
/// 还没提交的候选层（装配中途的目录只有分钟级年龄）。代际上限＝2 ⇒ 磁盘代价上限写进手册。
pub fn prune_old_layers(runtime_root: &Path, min_age: Duration) -> (Vec<String>, Vec<String>) {
    let keep = |name: &str| -> Vec<String> {
        read_layer_ids(&runtime_root.join(name))
            .map(|ids| vec![ids.base_id, ids.application_id, ids.rag_id])
            .unwrap_or_default()
    };
    let mut referenced = keep("current.json");
    referenced.extend(keep("previous.json"));
    let mut removed = Vec::new();
    let mut failures = Vec::new();
    for layer in ["base", "apps", "rag"] {
        let Ok(entries) = fs::read_dir(runtime_root.join(layer)) else {
            continue;
        };
        for entry in entries.flatten() {
            let name = entry.file_name();
            let Some(name) = name.to_str().map(str::to_string) else {
                continue;
            };
            if referenced.iter().any(|id| *id == name) {
                continue;
            }
            let old = entry
                .metadata()
                .and_then(|meta| meta.modified())
                .ok()
                .map(|modified| modified.elapsed().unwrap_or(Duration::from_secs(u64::MAX)))
                .unwrap_or(Duration::from_secs(u64::MAX));
            if old < min_age {
                continue;
            }
            let path = entry.path();
            match fs::remove_dir_all(&path) {
                Ok(()) => removed.push(format!("{layer}/{name}")),
                Err(err) => failures.push(format!("{}（{err}）", path.display())),
            }
        }
    }
    (removed, failures)
}

/// 安全模式的「只清运行时」：精确删三层与两档状态，`userdata/`、`downloads/`、`logs/` 一律不碰。
/// 返回失败清单（拒绝对宽泛目录动手，也不假装删干净了）。
pub fn wipe_runtime_layers(runtime_root: &Path) -> Vec<String> {
    let mut targets: Vec<PathBuf> = vec![
        runtime_root.join("base"),
        runtime_root.join("apps"),
        runtime_root.join("rag"),
        runtime_root.join("current.json"),
        runtime_root.join("previous.json"),
    ];
    targets.extend(
        fs::read_dir(runtime_root)
            .map(|entries| {
                entries
                    .flatten()
                    .map(|entry| entry.path())
                    .filter(|path| {
                        path.file_name()
                            .and_then(|name| name.to_str())
                            .map(|name| name.starts_with('.'))
                            .unwrap_or(false)
                    })
                    .collect::<Vec<PathBuf>>()
            })
            .unwrap_or_default(),
    );
    let mut failures = Vec::new();
    for path in targets {
        if !path.exists() {
            continue;
        }
        let outcome = if path.is_dir() {
            fs::remove_dir_all(&path)
        } else {
            fs::remove_file(&path)
        };
        if let Err(err) = outcome {
            failures.push(format!("{}（{err}）", path.display()));
        }
    }
    crate::shell_log::warn(&format!(
        "安全模式：已清除运行时目录 {}（失败 {} 项）；作品数据与下载缓存保留",
        runtime_root.display(),
        failures.len()
    ));
    failures
}

pub fn downloads_dir(home: &Path) -> PathBuf {
    home.join("downloads")
}

/// 落位失败时把先前挪走的同 id 旧目录放回原位；失败逐条写日志（不静默）。
fn restore_moved_layers(moved: &[(PathBuf, PathBuf)]) {
    for (target, stale) in moved.iter().rev() {
        if !target.exists() && stale.exists() {
            if let Some(parent) = target.parent() {
                let _ = fs::create_dir_all(parent);
            }
            if let Err(err) = fs::rename(stale, target) {
                crate::shell_log::warn(&format!(
                    "放回旧层失败：{} → {}（{err}）",
                    stale.display(),
                    target.display()
                ));
            }
        }
    }
}

// ── 空间与工具 ──────────────────────────────────────────────────────────────

fn ensure_space(home: &Path, required: u64, override_free: Option<u64>) -> Result<(), DeliveryError> {
    let free = match override_free {
        Some(value) => Some(value),
        None => available_bytes(home),
    };
    if let Some(free) = free {
        if free < required {
            return Err(DeliveryError(format!(
                "磁盘空间不足：需要约 {}，可用 {}（{}）。\n\n\
                 请清理磁盘后重试；也可以把发布资产放到安装目录的 runtime-pack 子目录，\
                 或 %LOCALAPPDATA%\\Demiurge\\downloads 离线安装。",
                human_bytes(required),
                human_bytes(free),
                home.display()
            )));
        }
    }
    Ok(())
}

#[cfg(windows)]
fn available_bytes(path: &Path) -> Option<u64> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::GetDiskFreeSpaceExW;
    let wide: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
    let mut free = 0u64;
    let ok = unsafe {
        GetDiskFreeSpaceExW(wide.as_ptr(), &mut free, std::ptr::null_mut(), std::ptr::null_mut())
    };
    (ok != 0).then_some(free)
}

#[cfg(not(windows))]
fn available_bytes(_path: &Path) -> Option<u64> {
    None
}

pub(crate) fn url_join(base: &str, name: &str) -> String {
    format!("{}/{}", base.trim_end_matches('/'), name)
}

fn file_url_to_path(url: &str) -> Result<PathBuf, DeliveryError> {
    let raw = url
        .strip_prefix("file://")
        .ok_or_else(|| DeliveryError(format!("不是 file:// 地址：{url}")))?;
    let decoded = percent_decode(raw);
    #[cfg(windows)]
    {
        let trimmed = decoded.trim_start_matches('/');
        if trimmed.is_empty() {
            return Err(DeliveryError(format!("file:// 地址缺少路径：{url}")));
        }
        Ok(PathBuf::from(trimmed))
    }
    #[cfg(not(windows))]
    {
        Ok(PathBuf::from(decoded))
    }
}

fn percent_decode(value: &str) -> String {
    let bytes = value.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'%' && index + 2 < bytes.len() {
            let high = (bytes[index + 1] as char).to_digit(16);
            let low = (bytes[index + 2] as char).to_digit(16);
            if let (Some(high), Some(low)) = (high, low) {
                out.push((high * 16 + low) as u8);
                index += 3;
                continue;
            }
        }
        out.push(bytes[index]);
        index += 1;
    }
    String::from_utf8_lossy(&out).to_string()
}

fn layer_label(name: &str) -> &'static str {
    match name {
        "base" => "运行时基础层",
        "application" => "应用层",
        "rag" => "RAG 增强层",
        _ => "运行时层",
    }
}

fn human_bytes(bytes: u64) -> String {
    const UNITS: [&str; 4] = ["B", "KiB", "MiB", "GiB"];
    let mut value = bytes as f64;
    let mut unit = 0;
    while value >= 1024.0 && unit + 1 < UNITS.len() {
        value /= 1024.0;
        unit += 1;
    }
    if unit == 0 {
        format!("{bytes} {}", UNITS[unit])
    } else {
        format!("{value:.1} {}", UNITS[unit])
    }
}

pub(crate) fn unique_token() -> String {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or(0);
    format!("{nanos:x}-{}", std::process::id())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 回归：https 必须真的走到 TLS 后端。
    ///
    /// ureq 在没挂 tls_connector 时会返回 UnknownScheme「no TLS backend」，
    /// 安装版首次启动下载运行时会全量失败（v0.30 / v0.30.1 的安装器就是这么坏的）。
    /// 要点是**先让 TCP 接住**：端口关着时 TCP 先失败，根本走不到 TLS，测不出差别。
    #[test]
    fn https_requests_reach_a_tls_backend() {
        use std::io::Read;

        let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("绑定临时端口");
        let port = listener.local_addr().expect("取临时端口").port();
        let server = std::thread::spawn(move || {
            if let Ok((mut stream, _)) = listener.accept() {
                let mut head = [0u8; 32];
                let _ = stream.read(&mut head);
            }
        });

        let dir = std::env::temp_dir().join(format!("laf-tls-probe-{}", unique_token()));
        std::fs::create_dir_all(&dir).expect("建临时目录");
        let dest = dir.join("probe.bin");
        let mut progress = |_: &str| {};
        let mut no_ledger: Option<DeliveryLedger> = None;
        let err = Fetcher::new()
            .fetch(
                &format!("https://127.0.0.1:{port}/"),
                &dest,
                &mut progress,
                "测试",
                &mut no_ledger,
            )
            .expect_err("对端立即断开，TLS 握手必须失败");
        let _ = server.join();
        let _ = std::fs::remove_dir_all(&dir);

        assert!(
            !err.0.contains("no TLS backend"),
            "https 请求没有可用 TLS 后端：{}",
            err.0
        );
    }
}
