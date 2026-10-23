using System.Windows;
using Application = System.Windows.Application;
using MessageBox = System.Windows.MessageBox;

namespace Demiurge.Desktop;

public partial class App : Application
{
    private SingleInstance? _singleInstance;
    private RuntimeHost? _runtime;
    private MainWindow? _window;

    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        if (e.Args.Any(arg => string.Equals(arg, "--self-test", StringComparison.OrdinalIgnoreCase)))
        {
            int code;
            try
            {
                code = SelfTest.Run();
            }
            catch (Exception ex)
            {
                ShellLog.Error("壳层自检崩溃", ex);
                try
                {
                    Console.WriteLine($"self-test: crashed — {ex}");
                }
                catch
                {
                    // 无控制台时忽略。
                }
                code = 1;
            }
            Shutdown(code);
            return;
        }

        _singleInstance = new SingleInstance();
        if (!_singleInstance.IsFirstInstance)
        {
            _singleInstance.SignalFirstInstance();
            Shutdown(0);
            return;
        }

        var settings = ShellSettings.Load();
        int port;
        try
        {
            port = RuntimeHost.ResolvePort(settings);
        }
        catch (RuntimeStartupException ex)
        {
            MessageBox.Show(ex.Message, "Demiurge", MessageBoxButton.OK, MessageBoxImage.Error);
            Shutdown(1);
            return;
        }
        if (settings.Port != port)
        {
            settings.Port = port;
            try
            {
                settings.Save();
            }
            catch (Exception ex)
            {
                ShellLog.Warn("保存端口设置失败", ex);
            }
        }

        _runtime = new RuntimeHost(port);
        _window = new MainWindow(_runtime, settings);
        _singleInstance.ActivationRequested += () =>
            Dispatcher.BeginInvoke(new Action(() => _window?.ActivateFromShell()));
        MainWindow = _window;
        _window.Show();
    }

    protected override void OnExit(ExitEventArgs e)
    {
        try
        {
            _runtime?.Dispose();
        }
        catch (Exception ex)
        {
            ShellLog.Warn("释放运行时失败", ex);
        }
        _singleInstance?.Dispose();
        base.OnExit(e);
    }
}
