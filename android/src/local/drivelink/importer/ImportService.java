package local.drivelink.importer;

import android.app.*;
import android.content.*;
import android.net.Uri;
import android.os.*;
import org.json.*;
import java.io.*;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import java.util.regex.*;

public final class ImportService extends Service {
    private static volatile ImportService active;
    private static JSONObject state = new JSONObject();
    private final AtomicBoolean cancelled = new AtomicBoolean();
    private ExecutorService worker;
    private ExecutorService copies;
    private PowerManager.WakeLock wake;
    private long started;
    private int totalFiles;
    private int totalUnits;
    private final AtomicInteger copied = new AtomicInteger();
    private final AtomicInteger completed = new AtomicInteger();
    private final List<Node> nodes = new ArrayList<>();
    private String importName;

    private static final class Node {
        final JSONObject info;
        final String name;
        final Node parent;
        String destination;
        Node(JSONObject info, String name, Node parent) { this.info = info; this.name = name; this.parent = parent; }
        boolean folder() { return DriveClient.FOLDER.equals(info.optString("mimeType")); }
    }

    static boolean isBusy() { return active != null; }

    static synchronized JSONObject snapshot(Context context) throws Exception {
        if (state.length() == 0) {
            String saved = context.getSharedPreferences("status", 0).getString("last", "{}");
            state = new JSONObject(saved);
            if (state.optBoolean("busy")) {
                state.put("busy", false).put("phase", "error").put("message", "Import interrupted. Check My Drive for files already copied.");
            }
        }
        return new JSONObject(state.toString());
    }

    static void cancelCurrent() {
        ImportService service = active;
        if (service != null) {
            service.cancelled.set(true);
            service.publish("cancelling", "Cancelling…", null, null);
            if (service.copies != null) service.copies.shutdownNow();
            if (service.worker != null) service.worker.shutdownNow();
        }
    }

    void checkCancelled() throws InterruptedException {
        if (cancelled.get() || Thread.currentThread().isInterrupted()) throw new InterruptedException();
    }

    void retrying() { publish("copying", "Google Drive is busy. Retrying…", null, null); }

    private synchronized void publish(String phase, String message, Integer percent, String error) {
        try {
            JSONObject value = new JSONObject();
            boolean busy = !Arrays.asList("done", "error", "cancelled").contains(phase);
            value.put("busy", busy).put("phase", phase).put("message", message);
            if (percent != null) value.put("percent", percent);
            if (started > 0) {
                long seconds = (System.currentTimeMillis() - started) / 1000;
                value.put("metrics", copied.get() + " copied · " + totalFiles + " files · " + seconds / 60 + "m " + seconds % 60 + "s");
            }
            if (error != null) value.put("error", error);
            if ("done".equals(phase)) value.put("finishedAt", Long.toString(System.currentTimeMillis()));
            synchronized (ImportService.class) { state = value; }
            getSharedPreferences("status", 0).edit().putString("last", value.toString()).apply();
            ((NotificationManager) getSystemService(NOTIFICATION_SERVICE)).notify(8, notification(message, percent, busy));
        } catch (Exception ignored) {}
    }

    private Notification notification(String message, Integer percent, boolean busy) {
        PendingIntent open = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification.Builder builder = new Notification.Builder(this, "imports")
            .setSmallIcon(android.R.drawable.stat_sys_download).setContentTitle("Drive Link Importer")
            .setContentText(message).setContentIntent(open).setOngoing(busy).setOnlyAlertOnce(true);
        if (busy) builder.setProgress(100, percent == null ? 0 : percent, percent == null);
        return builder.build();
    }

    @Override public int onStartCommand(Intent intent, int flags, int id) {
        if (active != null || intent == null) { if (intent == null) stopSelf(); return START_NOT_STICKY; }
        active = this;
        NotificationManager manager = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        manager.createNotificationChannel(new NotificationChannel("imports", "Drive imports", NotificationManager.IMPORTANCE_LOW));
        startForeground(8, notification("Checking the Drive link…", null, true));
        started = System.currentTimeMillis();
        wake = ((PowerManager) getSystemService(POWER_SERVICE)).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "drivelink:import");
        wake.acquire(3600000);
        worker = Executors.newSingleThreadExecutor();
        worker.submit(() -> {
            try {
                runImport(intent.getStringExtra("link"), intent.getStringExtra("name"));
                publish("done", "Saved in Drive Link Imports › " + importName, 100, null);
            } catch (Exception error) {
                if (cancelled.get() || error instanceof InterruptedException) {
                    publish("cancelled", "Import cancelled. Files already copied remain in My Drive.", null, null);
                } else {
                    publish("error", "Import couldn't finish. Check the error details.", null,
                            error.getMessage() == null ? "Check your connection and Google Drive access." : error.getMessage());
                }
            } finally {
                if (copies != null) copies.shutdownNow();
                worker.shutdown();
                if (wake != null && wake.isHeld()) wake.release();
                stopForeground(STOP_FOREGROUND_DETACH);
                active = null;
                stopSelf();
            }
        });
        return START_NOT_STICKY;
    }

    private void runImport(String link, String customName) throws Exception {
        Uri uri = Uri.parse(link.trim());
        if (!"https".equals(uri.getScheme()) || !Arrays.asList("drive.google.com", "docs.google.com").contains(uri.getHost())) {
            throw new IOException("Paste an HTTPS Google Drive file or folder link.");
        }
        Matcher match = Pattern.compile("/(?:folders|d)/([A-Za-z0-9_-]+)(?:/|$)").matcher(uri.getPath());
        String sourceId = match.find() ? match.group(1) : uri.getQueryParameter("id");
        if (sourceId == null || !sourceId.matches("[A-Za-z0-9_-]+")) throw new IOException("That link doesn't contain a Drive file ID.");
        String key = uri.getQueryParameter("resourcekey");
        publish("scanning", "Reading the original Drive name…", null, null);
        DriveClient drive = new DriveClient(new AuthStore(this), this);
        JSONObject original = drive.metadata(sourceId, key);
        JSONObject root = drive.resolve(original);
        importName = customName == null || customName.trim().isEmpty() ? original.getString("name") : customName.trim();
        if (!DriveClient.FOLDER.equals(root.getString("mimeType")) && !root.getString("mimeType").startsWith("application/vnd.google-apps.")) {
            String filename = root.getString("name");
            int dot = filename.lastIndexOf('.');
            if (dot > 0 && !importName.contains(".")) importName += filename.substring(dot);
        }
        Node rootNode = new Node(root, importName, null);
        scan(drive, rootNode, new HashSet<String>());
        totalUnits = nodes.size();
        publish("copying", "Copying " + importName + "…", 0, null);
        String parent = drive.importsFolder();
        for (Node node : nodes) {
            checkCancelled();
            if (node.folder()) {
                node.destination = drive.createFolder(node.name, node.parent == null ? parent : node.parent.destination);
                completed.incrementAndGet();
                updateProgress();
            }
        }
        copies = Executors.newFixedThreadPool(4);
        CompletionService<Void> results = new ExecutorCompletionService<>(copies);
        for (Node node : nodes) {
            if (node.folder()) continue;
            results.submit(() -> {
                checkCancelled();
                drive.copy(node.info, node.name, node.parent == null ? parent : node.parent.destination);
                copied.incrementAndGet();
                completed.incrementAndGet();
                updateProgress();
                return null;
            });
        }
        for (int index = 0; index < totalFiles; index++) {
            checkCancelled();
            try { results.take().get(); }
            catch (ExecutionException error) { throw new IOException(error.getCause().getMessage()); }
        }
    }

    private void scan(DriveClient drive, Node node, Set<String> ancestors) throws Exception {
        checkCancelled();
        nodes.add(node);
        if (!node.folder()) { totalFiles++; return; }
        String id = node.info.getString("id");
        if (ancestors.contains(id)) throw new IOException("This folder contains a circular shortcut.");
        Set<String> path = new HashSet<>(ancestors);
        path.add(id);
        publish("scanning", "Scanning " + importName + "…", null, null);
        for (JSONObject child : drive.children(node.info)) {
            scan(drive, new Node(drive.resolve(child), child.getString("name"), node), path);
        }
    }

    private synchronized void updateProgress() throws InterruptedException {
        checkCancelled();
        int percent = totalUnits == 0 ? 0 : Math.min(99, completed.get() * 100 / totalUnits);
        publish("copying", "Copying " + importName + "…", percent, null);
    }

    @Override public void onDestroy() {
        if (active == this) {
            cancelled.set(true);
            if (copies != null) copies.shutdownNow();
            if (worker != null) worker.shutdownNow();
            if (wake != null && wake.isHeld()) wake.release();
            active = null;
        }
        super.onDestroy();
    }
    @Override public IBinder onBind(Intent intent) { return null; }
}
