package local.drivelink.importer;

import android.content.Context;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import org.json.JSONObject;
import java.io.*;
import java.security.KeyStore;
import javax.crypto.*;
import javax.crypto.spec.GCMParameterSpec;

/** Device-bound encrypted credentials. Connection files never enter the APK. */
final class AuthStore {
    private final Context context;
    AuthStore(Context context) { this.context = context.getApplicationContext(); }

    private SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (!store.containsAlias("drive-connection")) {
            KeyGenerator generator = KeyGenerator.getInstance("AES", "AndroidKeyStore");
            generator.init(new KeyGenParameterSpec.Builder("drive-connection",
                    KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
            generator.generateKey();
        }
        return (SecretKey) store.getKey("drive-connection", null);
    }

    synchronized void save(JSONObject connection) throws Exception {
        for (String field : new String[]{"client_id", "client_secret", "refresh_token"}) {
            if (connection.optString(field).isEmpty()) throw new IOException("Incomplete Drive connection file.");
        }
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, key());
        byte[] encrypted = cipher.doFinal(connection.toString().getBytes("UTF-8"));
        File staging = new File(context.getFilesDir(), "connection.new");
        try (DataOutputStream output = new DataOutputStream(new FileOutputStream(staging))) {
            output.writeInt(cipher.getIV().length);
            output.write(cipher.getIV());
            output.write(encrypted);
        }
        if (!staging.renameTo(new File(context.getFilesDir(), "connection.enc"))) {
            throw new IOException("Couldn't save the Google Drive connection.");
        }
    }

    synchronized JSONObject load() throws Exception {
        File pending = new File(context.getFilesDir(), "connection.pending");
        if (pending.isFile()) {
            try (InputStream input = new FileInputStream(pending)) {
                save(new JSONObject(read(input)));
            } finally {
                if (!pending.delete()) pending.deleteOnExit();
            }
        }
        try (DataInputStream input = new DataInputStream(new FileInputStream(
                new File(context.getFilesDir(), "connection.enc")))) {
            int size = input.readInt();
            if (size != 12) throw new IOException("Invalid saved connection.");
            byte[] iv = new byte[size];
            input.readFully(iv);
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            byte[] buffer = new byte[4096];
            int count;
            while ((count = input.read(buffer)) != -1) bytes.write(buffer, 0, count);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, iv));
            return new JSONObject(new String(cipher.doFinal(bytes.toByteArray()), "UTF-8"));
        }
    }

    boolean connected() {
        try { return !load().optString("refresh_token").isEmpty(); }
        catch (Exception error) { return false; }
    }

    static String read(InputStream input) throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] buffer = new byte[4096];
        int count;
        while ((count = input.read(buffer)) != -1) {
            if (output.size() + count > 10485760) throw new IOException("Response is too large.");
            output.write(buffer, 0, count);
        }
        return output.toString("UTF-8");
    }
}
