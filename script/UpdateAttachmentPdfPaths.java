import java.io.BufferedReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Properties;

public class UpdateAttachmentPdfPaths {
    public static void main(String[] args) throws Exception {
        if (args.length < 6) {
            throw new IllegalArgumentException(
                "Usage: java UpdateAttachmentPdfPaths <tsv> <host> <port> <user> <password> [dryRun]"
            );
        }

        Path tsv = Path.of(args[0]);
        String host = args[1];
        String port = args[2];
        String user = args[3];
        String password = args[4];
        boolean dryRun = args.length >= 6 && Boolean.parseBoolean(args[5]);

        Class.forName("dm.jdbc.driver.DmDriver");
        Properties props = new Properties();
        props.setProperty("user", user);
        props.setProperty("password", password);
        props.setProperty("localTimezone", "Asia/Shanghai");
        props.setProperty("connectTimeout", "5000");
        props.setProperty("socketTimeout", "30000");

        try (Connection conn = DriverManager.getConnection("jdbc:dm://" + host + ":" + port, props)) {
            conn.setAutoCommit(false);
            int updated = 0;
            try (PreparedStatement stmt = conn.prepareStatement(
                    "UPDATE SYSDBA.POLICY_ATTACHMENT " +
                    "SET ATTACH_NAME = ?, ATTACH_SHOW_NAME = ?, FILE_PATH = ?, FILE_SUFFIX = 'pdf', UPDATE_AT = CURRENT_TIMESTAMP " +
                    "WHERE ATTACH_ID = ? AND TRIM(POLICY_ID) = ? AND DEL_FLAG = '0'")) {
                for (Map<String, String> row : readRows(tsv)) {
                    Path file = Path.of(row.get("FILE_PATH"));
                    if (!Files.isRegularFile(file)) {
                        throw new IllegalArgumentException("PDF file not found: " + row.get("FILE_PATH"));
                    }
                    stmt.setString(1, fit(row.get("ATTACH_NAME"), 500));
                    stmt.setString(2, fit(row.get("ATTACH_NAME"), 500));
                    stmt.setString(3, fit(row.get("FILE_PATH").replace('\\', '/'), 500));
                    stmt.setString(4, row.get("ATTACH_ID"));
                    stmt.setString(5, row.get("POLICY_ID"));
                    int count = stmt.executeUpdate();
                    if (count != 1) {
                        throw new IllegalStateException("Expected to update 1 row for ATTACH_ID=" + row.get("ATTACH_ID") + ", updated=" + count);
                    }
                    updated += count;
                }
                if (dryRun) {
                    conn.rollback();
                    System.out.println("DRY_RUN rollback complete");
                } else {
                    conn.commit();
                    System.out.println("COMMIT complete");
                }
                System.out.println("POLICY_ATTACHMENT updated=" + updated);
            } catch (Exception ex) {
                conn.rollback();
                throw ex;
            }
        }
    }

    static String fit(String value, int max) {
        if (value == null) return "";
        return value.length() <= max ? value : value.substring(0, max);
    }

    static java.util.List<Map<String, String>> readRows(Path path) throws Exception {
        java.util.List<Map<String, String>> rows = new java.util.ArrayList<>();
        try (BufferedReader reader = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
            String header = reader.readLine();
            if (header == null) return rows;
            String[] columns = header.split("\t", -1);
            String line;
            while ((line = reader.readLine()) != null) {
                String[] values = line.split("\t", -1);
                Map<String, String> row = new LinkedHashMap<>();
                for (int i = 0; i < columns.length; i++) {
                    row.put(columns[i], i < values.length ? values[i] : "");
                }
                rows.add(row);
            }
        }
        return rows;
    }
}
