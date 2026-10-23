//! 极小 HTTP/1.1 GET 客户端：只打本机回环、**不走任何代理**。
//!
//! 为什么不用 reqwest/ureq：那两者会读 `HTTP_PROXY` 等环境变量，本机代理端口是死的，
//! 探活会被代理拦成"服务没起来"（WPF 版是 `HttpClientHandler { UseProxy = false }`）。
//! 走裸 TCP 既能保证语义，又不引入 TLS/异步栈。
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::time::Duration;

pub struct Response {
    pub status: u16,
    pub body: String,
}

pub fn get(port: u16, path: &str, timeout: Duration) -> Option<Response> {
    let address: SocketAddr = ([127, 0, 0, 1], port).into();
    let mut stream = TcpStream::connect_timeout(&address, timeout).ok()?;
    stream.set_read_timeout(Some(timeout)).ok()?;
    stream.set_write_timeout(Some(timeout)).ok()?;
    let request = format!(
        "GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAccept: */*\r\nUser-Agent: Demiurge-Shell\r\nConnection: close\r\n\r\n"
    );
    stream.write_all(request.as_bytes()).ok()?;
    let mut raw = Vec::new();
    stream.read_to_end(&mut raw).ok()?;
    parse(&raw)
}

fn parse(raw: &[u8]) -> Option<Response> {
    let split = raw.windows(4).position(|w| w == b"\r\n\r\n")?;
    let head = String::from_utf8_lossy(&raw[..split]).to_string();
    let body_bytes = &raw[split + 4..];
    let status = head
        .lines()
        .next()?
        .split_whitespace()
        .nth(1)?
        .parse::<u16>()
        .ok()?;
    let lower = head.to_lowercase();
    let body = if lower.contains("transfer-encoding: chunked") {
        dechunk(body_bytes)
    } else {
        String::from_utf8_lossy(body_bytes).to_string()
    };
    Some(Response { status, body })
}

/// uvicorn 的静态文件与 JSON 都可能分块；不还原会读出 `1a2b\r\n{...}` 这种垃圾。
fn dechunk(mut data: &[u8]) -> String {
    let mut out = Vec::new();
    loop {
        let Some(line_end) = data.windows(2).position(|w| w == b"\r\n") else {
            break;
        };
        let size_text = String::from_utf8_lossy(&data[..line_end]).to_string();
        let size = usize::from_str_radix(size_text.trim().split(';').next().unwrap_or("0"), 16).unwrap_or(0);
        data = &data[line_end + 2..];
        if size == 0 || data.len() < size {
            break;
        }
        out.extend_from_slice(&data[..size]);
        data = &data[(size + 2).min(data.len())..];
    }
    String::from_utf8_lossy(&out).to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_plain_body() {
        let raw = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nhi";
        let response = parse(raw).expect("parse");
        assert_eq!(response.status, 200);
        assert_eq!(response.body, "hi");
    }

    #[test]
    fn parses_chunked_body() {
        let raw = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n";
        let response = parse(raw).expect("parse");
        assert_eq!(response.body, "hello");
    }
}
