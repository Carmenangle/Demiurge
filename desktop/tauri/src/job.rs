//! 把运行时子进程绑到 Job 对象（KILL_ON_JOB_CLOSE）：
//! 壳被强杀时由内核回收整棵进程树，避免留下孤儿服务占着 8010。
//! 与 WPF 的 `ChildProcessJob.cs` 同一套 Win32 调用。
use std::ffi::c_void;
use std::mem::size_of;
use windows_sys::Win32::Foundation::{CloseHandle, FALSE};
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows_sys::Win32::System::Threading::{OpenProcess, PROCESS_SET_QUOTA, PROCESS_TERMINATE};

pub struct ChildProcessJob(isize);

impl ChildProcessJob {
    pub fn create() -> Option<Self> {
        unsafe {
            let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if handle.is_null() {
                return None;
            }
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let ok = SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                &info as *const _ as *const c_void,
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            );
            if ok == 0 {
                CloseHandle(handle);
                return None;
            }
            Some(Self(handle as isize))
        }
    }

    /// 按 pid 接管：std::process::Child 不暴露句柄，这里用 OpenProcess 拿同权限句柄。
    pub fn assign_pid(&self, pid: u32) -> bool {
        unsafe {
            let process = OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, FALSE, pid);
            if process.is_null() {
                return false;
            }
            let ok = AssignProcessToJobObject(self.0 as *mut c_void, process);
            CloseHandle(process);
            ok != 0
        }
    }

    pub fn is_valid(&self) -> bool {
        self.0 != 0
    }
}

impl Drop for ChildProcessJob {
    fn drop(&mut self) {
        if self.0 != 0 {
            unsafe { CloseHandle(self.0 as *mut c_void) };
            self.0 = 0;
        }
    }
}
