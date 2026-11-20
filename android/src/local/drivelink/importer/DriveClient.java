package local.drivelink.importer;

import org.json.*;
import java.io.*;
import java.net.*;
import java.util.*;

final class DriveClient {
    static final String FOLDER = "application/vnd.google-apps.folder";
    static final String SHORTCUT = "application/vnd.google-apps.shortcut";
    private final AuthStore auth;
    private JSONObject connection;
    private final ImportService owner;

    DriveClient(AuthStore auth, ImportService owner) throws Exception {
        this.auth = auth;
        this.owner = owner;
        connection = auth.load();
    }

    static String encode(String text) throws Exception { return URLEncoder.encode(text, "UTF-8"); }

    private synchronized String token(boolean renew) throws Exception {
        if (renew || connection.optLong("expiryMillis") < System.currentTimeMillis() + 60000) {
            owner.checkCancelled();
            String body = "grant_type=refresh_token&client_id=" + encode(connection.getString("client_id"))
                + "&client_secret=" + encode(connection.getString("client_secret"))
                + "&refresh_token=" + encode(connection.getString("refresh_token"));
            HttpURLConnection request = (HttpURLConnection) new URL("https://oauth2.googleapis.com/token").openConnection();
            request.setConnectTimeout(15000);
            request.setReadTimeout(25000);
            request.setRequestMethod("POST");
            request.setDoOutput(true);
            request.setRequestProperty("Content-Type", "application/x-www-form-urlencoded");
            try {
                try (OutputStream output = request.getOutputStream()) { output.write(body.getBytes("UTF-8")); }
                if (request.getResponseCode() != 200) throw new IOException("Google connection expired. Pair this phone again from the desktop app.");
                JSONObject response;
                try (InputStream input = request.getInputStream()) { response = new JSONObject(AuthStore.read(input)); }
                connection.put("access_token", response.getString("access_token"));
                connection.put("expiryMillis", System.currentTimeMillis() + response.optLong("expires_in", 3600) * 1000);
                auth.save(connection);
            } finally { request.disconnect(); }
        }
        return connection.getString("access_token");
    }

    JSONObject request(String path, JSONObject body, String id, String key) throws Exception {
        for (int attempt = 0; attempt < 4; attempt++) {
            owner.checkCancelled();
            HttpURLConnection request = (HttpURLConnection) new URL("https://www.googleapis.com/drive/v3/" + path).openConnection();
            request.setConnectTimeout(15000);
            request.setReadTimeout(45000);
            request.setRequestProperty("Authorization", "Bearer " + token(false));
            if (key != null && !key.isEmpty()) request.setRequestProperty("X-Goog-Drive-Resource-Keys", id + "/" + key);
            try {
                if (body != null) {
                    request.setRequestMethod("POST");
                    request.setDoOutput(true);
                    request.setRequestProperty("Content-Type", "application/json");
                    try (OutputStream output = request.getOutputStream()) { output.write(body.toString().getBytes("UTF-8")); }
                }
                int code = request.getResponseCode();
                JSONObject response;
                try (InputStream input = code >= 400 ? request.getErrorStream() : request.getInputStream()) {
                    response = input == null ? new JSONObject() : new JSONObject(AuthStore.read(input));
                }
                if (code < 400) return response;
                if (code == 401 && attempt == 0) { token(true); continue; }
                JSONObject error = response.optJSONObject("error");
                String message = error == null ? "Google Drive request failed." : error.optString("message");
                String reasons = error == null ? "" : error.toString();
                boolean limited = code == 429 || (code == 403 &&
                    (reasons.contains("rateLimitExceeded") || reasons.contains("RATE_LIMIT_EXCEEDED")));
                if (attempt < 3 && (limited || (body == null && code >= 500))) {
                    owner.retrying();
                    for (int count = 0; count < (attempt + 1) * 20; count++) {
                        owner.checkCancelled();
                        Thread.sleep(250);
                    }
                    continue;
                }
                throw new IOException(message);
            } finally { request.disconnect(); }
        }
        throw new IOException("Google Drive is busy. Try again shortly.");
    }

    JSONObject metadata(String id, String key) throws Exception {
        return request("files/" + id + "?supportsAllDrives=true&fields=id,name,mimeType,resourceKey,shortcutDetails", null, id, key);
    }

    JSONObject resolve(JSONObject info) throws Exception {
        Set<String> seen = new HashSet<>();
        while (SHORTCUT.equals(info.getString("mimeType"))) {
            if (!seen.add(info.getString("id"))) throw new IOException("This Drive shortcut points to a cycle.");
            JSONObject target = info.getJSONObject("shortcutDetails");
            info = metadata(target.getString("targetId"), target.optString("targetResourceKey"));
        }
        return info;
    }

    List<JSONObject> children(JSONObject folder) throws Exception {
        List<JSONObject> results = new ArrayList<>();
        String page = "";
        do {
            String query = encode("'" + folder.getString("id") + "' in parents and trashed = false");
            JSONObject response = request("files?q=" + query + "&supportsAllDrives=true&includeItemsFromAllDrives=true"
                + "&pageSize=1000&fields=nextPageToken,files(id,name,mimeType,resourceKey,shortcutDetails)&pageToken=" + encode(page),
                null, folder.getString("id"), folder.optString("resourceKey"));
            JSONArray files = response.getJSONArray("files");
            for (int i = 0; i < files.length(); i++) results.add(files.getJSONObject(i));
            page = response.optString("nextPageToken");
        } while (!page.isEmpty());
        return results;
    }

    String createFolder(String name, String parent) throws Exception {
        JSONObject body = new JSONObject().put("name", name).put("mimeType", FOLDER).put("parents", new JSONArray().put(parent));
        return request("files?fields=id&supportsAllDrives=true", body, "", "").getString("id");
    }

    String importsFolder() throws Exception {
        String query = "'root' in parents and trashed = false and mimeType = '" + FOLDER + "' and name = 'Drive Link Imports'";
        JSONArray matches = request("files?q=" + encode(query) + "&fields=files(id)&pageSize=1", null, "", "").getJSONArray("files");
        return matches.length() == 0 ? createFolder("Drive Link Imports", "root") : matches.getJSONObject(0).getString("id");
    }

    void copy(JSONObject info, String name, String parent) throws Exception {
        JSONObject body = new JSONObject().put("name", name).put("parents", new JSONArray().put(parent));
        request("files/" + info.getString("id") + "/copy?supportsAllDrives=true&fields=id",
                body, info.getString("id"), info.optString("resourceKey"));
    }
}
