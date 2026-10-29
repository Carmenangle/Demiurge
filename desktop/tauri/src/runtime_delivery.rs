//! 运行时按需交付（安装版第三步）：清单校验 → 下载/复用分件 → 合并校验 → 解压临时目录 → 原子落位。
//!
//! 合同：
//! - 只认 `release/runtime-delivery.json` 这一份 pin（构建时打进壳里）；哈希与大小以
//!   `Demiurge-update-*.json` 清单为唯一真源，pin 不重复存哈希（否则 CI 要先有清单才能写 pin）。
//! - 下载会先把**每个分件**校验一遍，再合并出归档并校验归档；任何一步不符就删掉坏文件重来，
//!   绝不把「看起来下完了」的东西解压进运行时目录。
//! - 落位是内容寻址：`runtime/base/<base_id>`、`runtime/apps/<application_id>`、`runtime/rag/<rag_id>`；
//!   先解压到 `runtime/.staging-*` 再改名，`current.json` 用临时文件 + 改名替换。
//! - 失败只清理自己写的临时目录与坏下载，不碰用户数据（`userdata/`、预设、日志）。
//! - 离线入口：`<安装目录>\runtime-pack\` 与 `%LOCALAPPDATA%\Demiurge\downloads\`；有完整清单与
//!   分件时完全不联网。
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

/// 构建期嵌入的交付 pin。CI 在构建壳之前按 release tag 重写 `release/runtime-delivery.json`。
pub const PIN_JSON: &str = include_str!("../../../release/runtime-delivery.json");

const REQUIRED_LAYERS: [&str; 2] = ["base", "application"];
const OPTIONAL_LAYERS: [&str; 1] = ["rag"];
const DOWNLOAD_CHUNK: usize = 256 * 1024;
/// 解压体积估计：归档多为 PyInstaller/前端压缩产物，留 3 倍余量。
const EXTRACT_FACTOR: u64 = 3;
const SPACE_MARGIN: u64 = 256 * 1024 * 1024;

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
    /// 实际落地的 RAG 层 id；空串表示未安装。
    pub rag_id: String,
    /// 清单里存在可选 RAG 层（用于给出「可用 --with-rag 追加」的提示）。
    pub rag_available: bool,
    pub downloaded_layers: Vec<String>,
    pub reused: bool,
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

pub fn ensure_installed_runtime(
    install_dir: &Path,
    include_rag: bool,
    progress: &mut dyn FnMut(&str),
) -> Result<DeliveryReport, DeliveryError> {
    let pin = embedded_pin()?;
    deliver(&pin, &crate::paths::home(), install_dir, include_rag, None, progress)
}

/// 自检与单测入口：允许注入 pin / home / 可用磁盘空间。
pub(crate) fn deliver(
    pin: &DeliveryPin,
    home: &Path,
    install_dir: &Path,
    include_rag: bool,
    free_bytes_override: Option<u64>,
    progress: &mut dyn FnMut(&str),
) -> Result<DeliveryReport, DeliveryError> {
    validate_pin(pin).map_err(DeliveryError)?;
    fs::create_dir_all(home)?;
    let runtime_root = home.join("runtime");
    let downloads = home.join("downloads");
    fs::create_dir_all(&runtime_root)?;
    fs::create_dir_all(&downloads)?;
    let local_dirs = vec![install_dir.join("runtime-pack"), downloads.clone()];

    progress("读取运行时清单…");
    let manifest = match find_offline_manifest(pin, &local_dirs)? {
        Some(manifest) => manifest,
        None => fetch_manifest(pin, &downloads, progress)?,
    };
    validate_manifest(pin, &manifest, include_rag).map_err(DeliveryError)?;

    let mut needed: Vec<&str> = Vec::new();
    for name in REQUIRED_LAYERS {
        if !manifest.layers.contains_key(name) {
            return Err(DeliveryError(format!("运行时清单缺少 {name} 层")));
        }
        if !layer_installed(name, &runtime_root, manifest.layer_id(name)) {
            needed.push(name);
        }
    }
    if include_rag && manifest.layers.contains_key("rag") && !layer_installed("rag", &runtime_root, &manifest.rag_id) {
        needed.push("rag");
    }
    let rag_installed = !manifest.rag_id.trim().is_empty()
        && layer_installed("rag", &runtime_root, &manifest.rag_id);

    if needed.is_empty() {
        let rag_id = if rag_installed { manifest.rag_id.clone() } else { String::new() };
        write_current(&runtime_root, &manifest, &rag_id)?;
        progress("运行时已就绪，无需重复部署");
        return Ok(DeliveryReport {
            base_id: manifest.base_id.clone(),
            application_id: manifest.application_id.clone(),
            rag_id,
            rag_available: manifest.layers.contains_key("rag"),
            downloaded_layers: Vec::new(),
            reused: true,
        });
    }

    let mut required_bytes = SPACE_MARGIN;
    for name in &needed {
        let layer = &manifest.layers[*name];
        let all_local = layer
            .assets
            .iter()
            .all(|asset| local_dirs.iter().any(|dir| dir.join(&asset.name).is_file()));
        required_bytes = required_bytes
            .saturating_add(if all_local { 0 } else { layer.size })
            .saturating_add(layer.size.saturating_mul(EXTRACT_FACTOR));
    }
    ensure_space(home, required_bytes, free_bytes_override)?;

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
            let archive = acquire_layer(&fetcher, pin, name, layer, &local_dirs, &downloads, &work, progress)?;
            progress(&format!("解压{label}…"));
            extract_zip_stripped(&archive, &staging.join(name))
                .map_err(|err| DeliveryError(format!("{label}解压失败：{err}")))?;
        }

        let mut moved: Vec<(PathBuf, PathBuf)> = Vec::new();
        let deployed = (|| -> Result<(), DeliveryError> {
            for name in &needed {
                let target = layer_dir(&runtime_root, name, manifest.layer_id(name));
                let stale = staging.join("stale").join(name);
                if target.exists() {
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
            }
            Ok(())
        })();
        if let Err(err) = deployed {
            rollback(&moved);
            return Err(err);
        }

        let rag_id = if !manifest.rag_id.trim().is_empty()
            && layer_installed("rag", &runtime_root, &manifest.rag_id)
        {
            manifest.rag_id.clone()
        } else {
            String::new()
        };
        write_current(&runtime_root, &manifest, &rag_id)?;
        progress("运行时部署完成");
        Ok(DeliveryReport {
            base_id: manifest.base_id.clone(),
            application_id: manifest.application_id.clone(),
            rag_id,
            rag_available: manifest.layers.contains_key("rag"),
            downloaded_layers: needed.iter().map(|name| name.to_string()).collect(),
            reused: false,
        })
    })();

    let _ = fs::remove_dir_all(&staging);
    let _ = fs::remove_dir_all(&work);
    outcome
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
    let expected_manifest = format!("Demiurge-update-{}-{}.json", pin.tag, pin.target);
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
        let prefix = format!("{}.part", layer.archive);
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

fn validate_manifest(pin: &DeliveryPin, manifest: &Manifest, include_rag: bool) -> Result<(), String> {
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
    if include_rag && !manifest.layers.contains_key("rag") {
        return Err("清单里没有 rag 层，无法安装可选的 RAG 增强层".to_string());
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
        let suffix = format!("-{}.json", pin.target);
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if !name.starts_with("Demiurge-update-") || !name.ends_with(&suffix) {
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
            if let Err(err) = fetcher.fetch(&url, &dest, progress, "运行时清单") {
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
    ) -> Result<(), DeliveryError> {
        if url.starts_with("file://") {
            self.copy_file(url, dest)
        } else if url.starts_with("https://") || url.starts_with("http://") {
            self.http(url, dest, progress, label)
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
    progress: &mut dyn FnMut(&str),
) -> Result<PathBuf, DeliveryError> {
    let label = layer_label(name);
    let sources = remote_sources(pin);
    let mut asset_paths: Vec<PathBuf> = Vec::new();
    for (index, asset) in layer.assets.iter().enumerate() {
        if let Some(found) = find_verified(local_dirs, asset) {
            asset_paths.push(found);
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
        download_asset(fetcher, &sources, asset, &dest, &label, progress)?;
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
            match fetcher.fetch(&url, dest, progress, label) {
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

fn write_current(runtime_root: &Path, manifest: &Manifest, rag_id: &str) -> Result<(), DeliveryError> {
    let state = serde_json::json!({
        "schema_version": manifest.schema_version,
        "app_version": manifest.app_version,
        "target": manifest.target,
        "edition": manifest.edition,
        "base_id": manifest.base_id,
        "application_id": manifest.application_id,
        "rag_id": rag_id,
    });
    let payload = serde_json::to_vec_pretty(&state)?;
    let tmp = runtime_root.join(format!(".current-{}.json", unique_token()));
    fs::write(&tmp, payload)?;
    if let Err(err) = fs::rename(&tmp, runtime_root.join("current.json")) {
        let _ = fs::remove_file(&tmp);
        return Err(DeliveryError(format!("写入 current.json 失败：{err}")));
    }
    Ok(())
}

fn rollback(moved: &[(PathBuf, PathBuf)]) {
    for (target, stale) in moved.iter().rev() {
        if !target.exists() && stale.exists() {
            if let Some(parent) = target.parent() {
                let _ = fs::create_dir_all(parent);
            }
            let _ = fs::rename(stale, target);
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

fn unique_token() -> String {
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
        let err = Fetcher::new()
            .fetch(&format!("https://127.0.0.1:{port}/"), &dest, &mut progress, "测试")
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
