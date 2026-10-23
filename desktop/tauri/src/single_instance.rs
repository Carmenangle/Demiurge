//! 单实例 + 「激活既有窗口」。
//!
//! 与 WPF 壳同一套 IPC 真源（名字不改，两套壳能互相唤得动）：
//! - 互斥量 `Local\Demiurge.Desktop.SingleInstance`：判「是否已有实例」
//! - 事件   `Local\Demiurge.Desktop.Activate`：第二个实例 SetEvent ⇒ 第一个把窗口拉出来
//!
//! 事件用**自动重置**（manual reset = false）：一次 SetEvent 只唤醒一个等待者，唤完自动复位，
//! 不会出现「上一轮的激活信号把下一轮也顶起来」。
use std::ffi::c_void;

use windows_sys::Win32::Foundation::{CloseHandle, GetLastError, ERROR_ALREADY_EXISTS};
use windows_sys::Win32::System::Threading::{CreateEventW, CreateMutexW, OpenEventW, SetEvent, WaitForSingleObject};

const MUTEX_NAME: &str = "Local\\Demiurge.Desktop.SingleInstance";
const ACTIVATE_EVENT: &str = "Local\\Demiurge.Desktop.Activate";
const EVENT_MODIFY_STATE: u32 = 0x0002;
const SYNCHRONIZE: u32 = 0x0010_0000;
const INFINITE: u32 = 0xFFFF_FFFF;
const WAIT_OBJECT_0: u32 = 0;

pub struct InstanceGuard {
    mutex: isize,
}

impl Drop for InstanceGuard {
    fn drop(&mut self) {
        if self.mutex != 0 {
            unsafe { CloseHandle(self.mutex as *mut c_void) };
            self.mutex = 0;
        }
    }
}

/// `Some(guard)`＝本次是唯一实例；`None`＝已经有一个实例在跑（调用方应当激活它后退出）。
pub fn acquire() -> Option<InstanceGuard> {
    let name = wide(MUTEX_NAME);
    unsafe {
        let handle = CreateMutexW(std::ptr::null(), 0, name.as_ptr());
        if handle.is_null() {
            return None;
        }
        if GetLastError() == ERROR_ALREADY_EXISTS {
            CloseHandle(handle);
            return None;
        }
        Some(InstanceGuard {
            mutex: handle as isize,
        })
    }
}

/// 第二个实例的动作：给已有实例发一次激活信号。事件不存在（老版本/已退出）时返回 false。
pub fn signal_existing() -> bool {
    let name = wide(ACTIVATE_EVENT);
    unsafe {
        let event = OpenEventW(EVENT_MODIFY_STATE, 0, name.as_ptr());
        if event.is_null() {
            return false;
        }
        let ok = SetEvent(event) != 0;
        CloseHandle(event);
        ok
    }
}

/// 第一个实例：开一个监听线程，每收到一次信号就回调（回调里把窗口 show + 置前）。
pub fn spawn_activation_listener<F>(on_activate: F)
where
    F: Fn() + Send + 'static,
{
    std::thread::spawn(move || {
        let name = wide(ACTIVATE_EVENT);
        unsafe {
            // 交给库创建的句柄，进程退出时由内核回收，不必显式 Close。
            let event = CreateEventW(std::ptr::null(), 0, 0, name.as_ptr());
            if event.is_null() {
                crate::shell_log::warn("创建激活事件失败：第二次启动将无法唤起本窗口");
                return;
            }
            loop {
                let code = WaitForSingleObject(event, INFINITE);
                if code != WAIT_OBJECT_0 {
                    crate::shell_log::warn(&format!("等待激活事件返回 {code}，监听线程退出"));
                    return;
                }
                on_activate();
            }
        }
    });
}

#[allow(dead_code)]
const _SYNCHRONIZE: u32 = SYNCHRONIZE;

fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain(std::iter::once(0)).collect()
}
