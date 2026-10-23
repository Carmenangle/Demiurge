namespace Demiurge.Desktop;

internal sealed class SingleInstance : IDisposable
{
    private readonly Mutex _mutex;
    private readonly EventWaitHandle _activate;
    private readonly EventWaitHandle _stop = new(false, EventResetMode.ManualReset);
    private readonly Thread? _listener;
    private bool _ownsMutex;
    private bool _disposed;

    public bool IsFirstInstance { get; }
    public event Action? ActivationRequested;

    public SingleInstance()
    {
        _mutex = new Mutex(initiallyOwned: true, ShellPaths.SingleInstanceMutexName, out var createdNew);
        IsFirstInstance = createdNew;
        _ownsMutex = createdNew;
        _activate = new EventWaitHandle(false, EventResetMode.AutoReset, ShellPaths.ActivateEventName);
        if (!createdNew)
        {
            return;
        }
        _listener = new Thread(Listen) { IsBackground = true, Name = "demiurge-activate-listener" };
        _listener.Start();
    }

    public void SignalFirstInstance()
    {
        try
        {
            _activate.Set();
        }
        catch (Exception ex)
        {
            ShellLog.Warn("通知已有实例失败", ex);
        }
    }

    private void Listen()
    {
        var handles = new WaitHandle[] { _activate, _stop };
        while (true)
        {
            int index;
            try
            {
                index = WaitHandle.WaitAny(handles);
            }
            catch (Exception ex)
            {
                ShellLog.Warn("单实例监听退出", ex);
                return;
            }
            if (index != 0)
            {
                return;
            }
            try
            {
                ActivationRequested?.Invoke();
            }
            catch (Exception ex)
            {
                ShellLog.Warn("激活主窗口失败", ex);
            }
        }
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }
        _disposed = true;
        _stop.Set();
        _listener?.Join(TimeSpan.FromSeconds(1));
        if (_ownsMutex)
        {
            try
            {
                _mutex.ReleaseMutex();
            }
            catch
            {
                // 进程退出时内核会回收。
            }
            _ownsMutex = false;
        }
        _mutex.Dispose();
        _activate.Dispose();
        _stop.Dispose();
    }
}
