package dev.demiurge.spike;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;

/**
 * M4 运行时：生成任务在途期间的前台服务（PLAN §7「长任务」）。
 *
 * 生命周期由 MainActivity 的健康监控线程驱动：running_jobs > 0 起服（常驻通知
 * 「正在生成 · N 个任务」防 Doze 杀进程），回到 0 停服。任务数变化时再次 start
 * 即更新通知。targetSdk 34 要求 Manifest 声明 foregroundServiceType=specialUse
 * （已带 PROPERTY_SPECIAL_USE_FGS_SUBTYPE），否则 startForeground 直接抛异常。
 */
public class GenerationForegroundService extends Service {
    private static final String CHANNEL_ID = "generation";
    private static final int NOTIFICATION_ID = 1001;

    @Override
    public void onCreate() {
        super.onCreate();
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            if (nm.getNotificationChannel(CHANNEL_ID) == null) {
                NotificationChannel channel = new NotificationChannel(
                        CHANNEL_ID, "生成任务", NotificationManager.IMPORTANCE_LOW);
                channel.setDescription("生成任务进行中，保持后端运行");
                nm.createNotificationChannel(channel);
            }
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        int running = 1;
        if (intent != null && intent.hasExtra("running")) {
            running = Math.max(1, intent.getIntExtra("running", 1));
        }
        startForeground(NOTIFICATION_ID, buildNotification(running));
        // 任务态由壳层健康监控驱动，服务被杀后自行重启没有意义（监控会重新起服）
        return START_NOT_STICKY;
    }

    private Notification buildNotification(int running) {
        String title = "正在生成 · " + running + " 个任务";
        Notification.Builder builder = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(this, CHANNEL_ID)
                : new Notification.Builder(this);
        return builder
                .setSmallIcon(android.R.drawable.stat_sys_download)
                .setContentTitle(title)
                .setContentText("Demiurge 后端保持运行")
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .build();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
