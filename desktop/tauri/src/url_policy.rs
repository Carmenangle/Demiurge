//! 导航与外链 URL 策略（壳内唯一属主，纯函数）。
//!
//! 两条判定刻意分开，不能互相替代：
//! - [`is_navigation_allowed`]：主窗口能不能自己导航过去——只允许本应用页面。
//! - [`is_external_http`]：能不能交给系统浏览器——只允许带主机的 http(s)。
//!
//! `mailto:` / `ms-*:` / `intent:` 等系统协议默认拒绝。产品确实需要时再加**显式**条目与测试，
//! 不得使用「不是 file: 就放行」这类反向规则。

use tauri::Url;

/// 导航白名单：tauri 协议页（boot.html 兜底页，Windows 上表现为
/// `http(s)://tauri.localhost`）、`about:blank` 与回环 http(s)
/// （后端送达的应用页、ComfyUI 等本机服务，端口由协商决定故不限）。
pub fn is_navigation_allowed(url: &Url) -> bool {
    match url.scheme() {
        "tauri" | "about" => true,
        "http" | "https" => {
            let host = url.host_str().unwrap_or("");
            host.eq_ignore_ascii_case("tauri.localhost") || is_loopback_host(host)
        }
        _ => false,
    }
}

/// 交系统浏览器的唯一白名单：只允许带非空主机的 http(s)。
/// `http://127.0.0.1@evil.com` 的 userinfo 不是主机，`host_str()` 是 evil.com，按外站处理。
pub fn is_external_http(url: &Url) -> bool {
    matches!(url.scheme(), "http" | "https")
        && url
            .host_str()
            .map(|host| !host.is_empty())
            .unwrap_or(false)
}

pub fn is_loopback_host(host: &str) -> bool {
    if host.eq_ignore_ascii_case("localhost") {
        return true;
    }
    // url crate 的 host_str() 对 IPv6 返回带方括号的 "[::1]"
    let bare = host.trim_start_matches('[').trim_end_matches(']');
    bare.parse::<std::net::IpAddr>()
        .map(|ip| ip.is_loopback())
        .unwrap_or(false)
}

/// 日志口径：只留 scheme + host，绝不打印 path/query（可能含 token）。
pub fn describe(url: &Url) -> String {
    match url.host_str() {
        Some(host) if !host.is_empty() => format!("{}://{}", url.scheme(), host),
        _ => format!("{}:", url.scheme()),
    }
}

#[cfg(test)]
mod tests {
    use super::{describe, is_external_http, is_loopback_host, is_navigation_allowed};

    fn url(raw: &str) -> tauri::Url {
        tauri::Url::parse(raw).unwrap()
    }

    /// 用户指定向量：导航放行 / 是否交系统浏览器，两者必须分开判定。
    #[test]
    fn 导航与外链判定向量() {
        let cases = [
            ("http://127.0.0.1:8010/", true, true),
            ("https://127.0.0.1:8010/", true, true),
            ("http://localhost:8021/index.html", true, true),
            ("http://[::1]:8010/", true, true),
            ("http://tauri.localhost/boot.html", true, true),
            ("tauri://localhost/boot.html", true, false),
            ("about:blank", true, false),
            ("https://example.com/x?q=1", false, true),
            ("http://127.0.0.1.evil.com/", false, true),
            ("http://127.0.0.1@evil.com/", false, true),
            ("file:///C:/Windows/System32/calc.exe", false, false),
            ("javascript:alert(1)", false, false),
            ("mailto:x@example.com", false, false),
            ("ms-settings:privacy", false, false),
            ("intent://scan/#Intent;scheme=zxing;end", false, false),
        ];
        for (raw, nav, external) in cases {
            let parsed = url(raw);
            assert_eq!(
                is_navigation_allowed(&parsed),
                nav,
                "导航判定不符：{raw}"
            );
            assert_eq!(is_external_http(&parsed), external, "外链判定不符：{raw}");
        }
    }

    #[test]
    fn loopback判定() {
        assert!(is_loopback_host("127.0.0.1"));
        assert!(is_loopback_host("127.8.8.8"));
        assert!(is_loopback_host("LOCALHOST"));
        assert!(is_loopback_host("::1"));
        assert!(!is_loopback_host("192.168.1.5"));
        assert!(!is_loopback_host("localhost.attacker.com"));
        assert!(!is_loopback_host(""));
    }

    #[test]
    fn 日志只留scheme与host() {
        assert_eq!(
            describe(&url("https://example.com/path?token=secret")),
            "https://example.com"
        );
        assert_eq!(describe(&url("mailto:x@example.com")), "mailto:");
        assert_eq!(describe(&url("about:blank")), "about:");
    }

    /// 接线锁：explorer 只能出现在 open_in_system_browser 的唯一出口，且出口内必须再校验。
    #[test]
    fn 接线锁_系统浏览器出口唯一且自校验() {
        let source = include_str!("main.rs");
        assert_eq!(
            source.matches("Command::new(\"explorer\")").count(),
            1,
            "explorer 只允许出现在 open_in_system_browser 的唯一出口"
        );
        let opener = source
            .split("fn open_in_system_browser")
            .nth(1)
            .expect("main.rs 必须保留 open_in_system_browser 门面");
        assert!(
            opener.contains("is_external_http"),
            "open_in_system_browser 内部必须再校验一次协议白名单"
        );

        let navigation = source
            .split(".on_navigation")
            .nth(1)
            .expect("main.rs 必须保留导航钩子");
        assert!(
            navigation.contains("is_navigation_allowed"),
            "导航钩子必须走 url_policy::is_navigation_allowed"
        );
        assert!(
            navigation.contains("open_in_system_browser"),
            "被拒导航必须交给唯一 opener 门面"
        );

        let new_window = source
            .split(".on_new_window")
            .nth(1)
            .expect("main.rs 必须保留新窗口钩子")
            .split("});")
            .next()
            .expect("新窗口钩子必须有闭合");
        assert!(
            new_window.contains("open_in_system_browser"),
            "新窗口必须交给唯一 opener 门面"
        );
        assert!(
            !new_window.contains("Command::new"),
            "新窗口钩子不得自己起进程"
        );
    }
}
