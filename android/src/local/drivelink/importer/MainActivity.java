package local.drivelink.importer;

import android.Manifest;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.*;
import android.webkit.*;
import org.json.JSONObject;
import java.io.InputStream;

public final class MainActivity extends Activity {
    private WebView web;
    private AuthStore auth;

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        auth = new AuthStore(this);
        auth.connected();
        web = new WebView(this);
        web.setBackgroundColor(Color.rgb(16,19,33));
        web.getSettings().setJavaScriptEnabled(true);
        web.getSettings().setDomStorageEnabled(false);
        web.getSettings().setAllowContentAccess(false);
        web.getSettings().setAllowFileAccessFromFileURLs(false);
        web.getSettings().setAllowUniversalAccessFromFileURLs(false);
        web.addJavascriptInterface(new Bridge(), "Android");
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) { return true; }
            @Override public void onPageFinished(WebView view, String url) { acceptSharedLink(getIntent()); }
        });
        setContentView(web);
        web.loadUrl("file:///android_asset/index.html");
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        acceptSharedLink(intent);
    }

    private void acceptSharedLink(Intent intent) {
        if (Intent.ACTION_SEND.equals(intent.getAction())) {
            String value = intent.getStringExtra(Intent.EXTRA_TEXT);
            if (value != null) web.evaluateJavascript("receiveLink(" + JSONObject.quote(value) + ")", null);
            intent.setAction(Intent.ACTION_MAIN);
        }
    }

    private void importConnection() {
        new AlertDialog.Builder(this).setTitle("Connect Google Drive")
            .setMessage("In the desktop app, connect Google Drive and choose Pair phone. Open that connection file here. It gives this phone access to the same Drive account.")
            .setPositiveButton("Choose connection file", (dialog, which) -> {
                Intent picker = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                picker.setType("application/json");
                picker.addCategory(Intent.CATEGORY_OPENABLE);
                startActivityForResult(picker, 41);
            }).setNegativeButton("Cancel", null).show();
    }

    @Override protected void onActivityResult(int request, int result, Intent intent) {
        super.onActivityResult(request, result, intent);
        if (request == 41 && result == RESULT_OK && intent != null) {
            try (InputStream input = getContentResolver().openInputStream(intent.getData())) {
                if (input == null) throw new Exception("Couldn't open that connection file.");
                auth.save(new JSONObject(AuthStore.read(input)));
                new AlertDialog.Builder(this).setTitle("Google Drive connected")
                    .setMessage("The connection is stored securely on this phone. You can delete the exported connection file.")
                    .setPositiveButton("Done", null).show();
            } catch (Exception error) {
                new AlertDialog.Builder(this).setMessage("Couldn't import that connection file.")
                    .setPositiveButton("OK", null).show();
            }
        }
    }

    final class Bridge {
        @JavascriptInterface public String get_state() {
            try {
                JSONObject result = ImportService.snapshot(MainActivity.this);
                result.put("connected", auth.connected());
                result.put("platform", "android");
                return result.toString();
            } catch (Exception error) { return "{}"; }
        }
        @JavascriptInterface public String start_import(String link, String name) {
            runOnUiThread(() -> {
                if (ImportService.isBusy()) return;
                if (!auth.connected()) { importConnection(); return; }
                if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                    requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 42);
                }
                Intent intent = new Intent(MainActivity.this, ImportService.class);
                intent.putExtra("link", link);
                intent.putExtra("name", name);
                startForegroundService(intent);
            });
            return get_state();
        }
        @JavascriptInterface public String paste() {
            try {
                android.content.ClipboardManager clipboard = (android.content.ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
                CharSequence value = clipboard.hasPrimaryClip()
                    ? clipboard.getPrimaryClip().getItemAt(0).coerceToText(MainActivity.this) : "";
                return JSONObject.quote(value == null ? "" : value.toString());
            } catch (Exception error) { return "\"\""; }
        }
        @JavascriptInterface public String cancel() {
            ImportService.cancelCurrent();
            return get_state();
        }
        @JavascriptInterface public String connect() { runOnUiThread(() -> importConnection()); return "null"; }
        @JavascriptInterface public String open_drive() {
            runOnUiThread(() -> startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse("https://drive.google.com/drive/my-drive"))));
            return "null";
        }
    }
}
