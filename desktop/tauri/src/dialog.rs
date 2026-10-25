//! 关窗询问与壳级提示：直接走 Win32 `MessageBoxW`，不引入 Tauri dialog 插件。
//! 按钮文案由 Windows 本地化（中文系统显示「是 / 否 / 取消」），正文把三者的后果写清楚。
use windows_sys::Win32::UI::WindowsAndMessaging::{
    MessageBoxW, IDNO, IDYES, MB_ICONERROR, MB_ICONINFORMATION, MB_ICONQUESTION, MB_ICONWARNING,
    MB_OK, MB_SETFOREGROUND, MB_TOPMOST, MB_YESNO, MB_YESNOCANCEL,
};

pub enum CloseAction {
    HideToTray,
    Quit,
    Cancel,
}

fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain(std::iter::once(0)).collect()
}

fn message_box(text: &str, caption: &str, flags: u32) -> i32 {
    let text = wide(text);
    let caption = wide(caption);
    unsafe { MessageBoxW(std::ptr::null_mut(), text.as_ptr(), caption.as_ptr(), flags) }
}

pub fn ask_close_action() -> CloseAction {
    let result = message_box(
        "要隐藏到托盘，还是彻底关闭 Demiurge？\n\n\
         是：隐藏到托盘，后端继续运行（手机端仍可连接）\n\
         否：彻底关闭，同时停止后端并关闭前端窗口\n\
         取消：留在当前窗口",
        "关闭 Demiurge",
        MB_YESNOCANCEL | MB_ICONQUESTION | MB_SETFOREGROUND | MB_TOPMOST,
    );
    match result {
        IDYES => CloseAction::HideToTray,
        IDNO => CloseAction::Quit,
        _ => CloseAction::Cancel,
    }
}

/// 是/否确认框：更新安装等不可逆动作先问一句。
pub fn ask_confirm(caption: &str, text: &str) -> bool {
    let result = message_box(
        text,
        caption,
        MB_YESNO | MB_ICONQUESTION | MB_SETFOREGROUND | MB_TOPMOST,
    );
    result == IDYES
}

pub fn show_info(caption: &str, text: &str) {
    message_box(
        text,
        caption,
        MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND | MB_TOPMOST,
    );
}

pub fn show_warning(caption: &str, text: &str) {
    message_box(
        text,
        caption,
        MB_OK | MB_ICONWARNING | MB_SETFOREGROUND | MB_TOPMOST,
    );
}

pub fn show_error(caption: &str, text: &str) {
    message_box(
        text,
        caption,
        MB_OK | MB_ICONERROR | MB_SETFOREGROUND | MB_TOPMOST,
    );
}
