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
        versionCode = 7
        versionName = "0.3.2"

        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
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
            options("--index-url", "https://pypi.tuna.tsinghua.edu.cn/simple")
            options("--find-links", "D:/tool/Demiurge/mobile/spike/wheelhouse")
            install("fastapi==0.140.13")
            install("uvicorn==0.51.0")
            install("pydantic==2.13.4")
            install("chromadb-rust-bindings==0.1.0")
        }
    }
}
