// 顶层 import 必须在 plugins 之前；`java` 在脚本里被 JavaPluginExtension 遮蔽，
// 全限定 java.util.zip.* 会解析成扩展的成员（AGP 8.7 + Kotlin DSL 实测 10 个错）。
import java.security.MessageDigest
import java.util.zip.ZipFile
import java.util.zip.ZipInputStream

// 前端解压缓存的键＝assets/frontend.zip 的**内容 sha256**（0.4.4 F3）。
// 手写常量（旧 `FRONTEND_VERSION = "0.3.5"`）漏改一次，升级后的手机就继续跑旧前端
// （`.version` 相同 ⇒ 跳过解压），而干净安装却是新的——典型「升级才复现」。
// 配置缓存未启用（gradle.properties 无 org.gradle.configuration-cache）⇒ 配置期读文件安全；
// 若将来启用，必须改成 Provider/ValueSource，否则 sha 会冻在缓存里。
fun sha256Hex(file: File): String {
    val digest = MessageDigest.getInstance("SHA-256")
    file.inputStream().use { input ->
        val buf = ByteArray(1 shl 16)
        while (true) {
            val n = input.read(buf)
            if (n < 0) break
            digest.update(buf, 0, n)
        }
    }
    return digest.digest().joinToString("") { (it.toInt() and 0xff).toString(16).padStart(2, '0') }
}

// 缺文件时不在这里炸：构建期自会因缺资产失败，此值只用于把错误显式化
val frontendSha = file("src/main/assets/frontend.zip")
    .takeIf { it.isFile }?.let(::sha256Hex) ?: "missing-frontend-zip"


plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

android {
    namespace = "dev.demiurge.spike"
    compileSdk = 35

    defaultConfig {
        applicationId = "dev.demiurge.spike"
        minSdk = 24
        targetSdk = 34
        versionCode = 15
        versionName = "0.30.0-beta"

        // 壳层据此判「离线前端要不要重解压」；由上面的 zip 实算，禁止手写
        buildConfigField("String", "FRONTEND_SHA", "\"$frontendSha\"")

        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }

    // AGP 8 默认关 BuildConfig；只为了把 FRONTEND_SHA 带进壳层常量池
    buildFeatures {
        buildConfig = true
    }

    buildTypes {
        release {
            // minify 必须保持 false：14 项 APK 静态闸靠 grep dex 常量池判定加固有没有编进去
            // （"history cleared, cookie flushed" / "demiurge_token"），R8 裁剪/内联会让闸失真。
            isMinifyEnabled = false
            // release 默认无签名 ⇒ 装不上。指到 debug 签名，瘦下来的包能在真机解锁后直接装，
            // 不必为「能装」再出一版。
            signingConfig = signingConfigs.getByName("debug")
        }
    }

    // frontend.zip 里全是已压缩的 webp/png/音频，zip 再压只浪费时间并可能变大
    androidResources {
        noCompress += "zip"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

chaquopy {
    defaultConfig {
        version = "3.13"
        buildPython("D:/tool/develop/Python/pythoncore-3.13-64/python.exe")
        pip {
            // 全部依赖已冻结进 android-lock.txt 并离线落盘到 wheelhouse，禁止再走网络解析
            options("--no-index")
            options("--find-links", rootProject.file("../wheelhouse").absolutePath)
            rootProject.file("../android-lock.txt").readLines()
                .map { it.trim() }
                .filter { it.isNotEmpty() && !it.startsWith("#") }
                .forEach { install(it) }
        }
    }
}

// 内嵌后端永远从唯一真源 backend/app 同步，避免 APK 里留一份会过期的副本
val syncBackendPython by tasks.registering(Sync::class) {
    from(rootProject.file("../../../backend/app")) {
        exclude("**/__pycache__/**", "**/*.pyc")
    }
    into(layout.projectDirectory.dir("src/main/python/app"))
}

tasks.named("preBuild") { dependsOn(syncBackendPython) }
// Chaquopy 直接把 src/main/python 当输入，必须显式声明生产者，否则 Gradle 视为隐式依赖
tasks.matching { it.name.endsWith("PythonSources") }.configureEach { dependsOn(syncBackendPython) }

// ---------------------------------------------------------------------------
// APK 隐私白名单闸（M4）：安装包绝不带用户数据。
// 判据是禁项扫描（不是「记得别拷」）：外层 APK + 内嵌 frontend.zip + Chaquopy 的
// app.imy 三层都扫——presets/、userdata/、backend/data/ 与 user_state.json（模型密钥）
// 只要出现任何一处就让构建失败。桌面 NSIS 安装器已有同类守卫（scripts/ 下），手机侧
// 必须有独立的一份，因为出包链路完全不同。
// ---------------------------------------------------------------------------
val verifyApkPrivacy by tasks.registering {
    group = "verification"
    description = "校验 APK 不含 presets/、userdata/、backend/data/ 与 user_state.json"
    val apkRoot = layout.buildDirectory.dir("outputs/apk")
    doLast {
        val apks = apkRoot.get().asFile.walkTopDown()
            .filter { it.isFile && it.name.endsWith(".apk") }
            .toList()
        if (apks.isEmpty()) throw GradleException("未找到 APK，隐私闸无法执行")

        fun violations(names: List<String>, where: String): List<String> =
            names.map { it.replace('\\', '/').lowercase() }.filter { name ->
                val segments = name.split('/')
                segments.any { it == "presets" || it == "userdata" } ||
                    segments.windowed(2).any { it == listOf("backend", "data") } ||
                    segments.last() == "user_state.json"
            }.map { "$where!$it" }

        apks.forEach { apk ->
            val bad = mutableListOf<String>()
            ZipFile(apk).use { zip ->
                bad += violations(zip.entries().asSequence().map { it.name }.toList(), apk.name)
                for (asset in listOf("assets/frontend.zip", "assets/chaquopy/app.imy")) {
                    val entry = zip.getEntry(asset) ?: continue
                    val names = ZipInputStream(zip.getInputStream(entry)).use { zin ->
                        generateSequence { zin.nextEntry }.map { it.name }.toList()
                    }
                    bad += violations(names, "$asset")
                }
            }
            if (bad.isNotEmpty()) {
                throw GradleException("APK 隐私闸失败，用户数据混入安装包：\n" + bad.joinToString("\n"))
            }
        }
        println("APK 隐私闸 PASS：" + apks.joinToString { it.name } + " 不含用户数据目录")
    }
}

tasks.matching { it.name == "assembleDebug" || it.name == "assembleRelease" }
    .configureEach { finalizedBy(verifyApkPrivacy) }
