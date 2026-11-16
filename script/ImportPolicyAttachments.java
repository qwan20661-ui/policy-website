import java.io.BufferedReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.Statement;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Properties;

public class ImportPolicyAttachments {
    public static void main(String[] args) throws Exception {
        if (args.length < 5) {
            throw new IllegalArgumentException(
                "Usage: java ImportPolicyAttachments <tsv> <host> <port> <user> <password> [dryRun]"
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
            int inserted = 0;
            int skipped = 0;
            try (PreparedStatement exists = conn.prepareStatement(
                    "SELECT 1 FROM SYSDBA.POLICY_ATTACHMENT WHERE TRIM(POLICY_ID)=? AND DEL_FLAG='0' AND (FILE_PATH=? OR ATTACH_NAME=?)");
                 PreparedStatement insert = conn.prepareStatement(
                    "INSERT INTO SYSDBA.POLICY_ATTACHMENT " +
                    "(ATTACH_ID, POLICY_ID, ATTACH_NAME, ATTACH_SHOW_NAME, FILE_PATH, FILE_SUFFIX, CREATE_AT, UPDATE_AT, DEL_FLAG, ATTACH_TYPE, ATTACH_REVIEW_STATUS) " +
                    "VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, '0', ?, ?)")) {
                for (Map<String, String> row : readRows(tsv)) {
                    String policyId = row.get("POLICY_ID");
                    String filePath = row.get("FILE_PATH");
                    String attachName = row.get("ATTACH_NAME");
                    exists.setString(1, policyId);
                    exists.setString(2, filePath);
                    exists.setString(3, attachName);
                    try (ResultSet rs = exists.executeQuery()) {
                        if (rs.next()) {
                            skipped++;
                            continue;
                        }
                    }
                    insert.setString(1, row.get("ATTACH_ID"));
                    insert.setString(2, policyId);
                    insert.setString(3, fit(attachName, 500));
                    insert.setString(4, fit(row.get("ATTACH_SHOW_NAME"), 500));
                    insert.setString(5, fit(filePath, 500));
                    insert.setString(6, fit(row.get("FILE_SUFFIX"), 20));
                    insert.setString(7, fit(row.get("ATTACH_TYPE"), 50));
                    insert.setString(8, fit(row.get("ATTACH_REVIEW_STATUS"), 20));
                    insert.executeUpdate();
                    inserted++;
                }
                if (dryRun) {
                    conn.rollback();
                    System.out.println("DRY_RUN rollback complete");
                } else {
                    conn.commit();
                    System.out.println("COMMIT complete");
                }
                System.out.println("POLICY_ATTACHMENT inserted=" + inserted + ", skipped=" + skipped);
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
