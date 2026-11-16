import java.io.BufferedReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.Date;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.UUID;

public class ImportCrawledPolicies20260716 {
    public static void main(String[] args) throws Exception {
        if (args.length < 6) {
            throw new IllegalArgumentException("Usage: java ImportCrawledPolicies20260716 <policy.tsv> <attach.tsv> <host> <port> <user> <password> [dryRun]");
        }
        Path policyTsv = Path.of(args[0]);
        Path attachTsv = Path.of(args[1]);
        String host = args[2];
        String port = args[3];
        String user = args[4];
        String password = args[5];
        boolean dryRun = args.length >= 7 && Boolean.parseBoolean(args[6]);

        Class.forName("dm.jdbc.driver.DmDriver");
        Properties props = new Properties();
        props.setProperty("user", user);
        props.setProperty("password", password);
        props.setProperty("localTimezone", "Asia/Shanghai");
        props.setProperty("connectTimeout", "5000");
        props.setProperty("socketTimeout", "30000");

        try (Connection conn = DriverManager.getConnection("jdbc:dm://" + host + ":" + port, props)) {
            conn.setAutoCommit(false);
            int insertedPolicies = 0;
            int skippedPolicies = 0;
            int insertedAttachments = 0;
            Map<String, Long> titleToPolicyId = new LinkedHashMap<>();
            try {
                for (Map<String, String> row : readRows(policyTsv)) {
                    String title = row.get("REQUESTED_TITLE");
                    Long existingId = findExistingPolicyId(conn, title);
                    if (existingId != null) {
                        titleToPolicyId.put(title, existingId);
                        skippedPolicies++;
                        System.out.println("SKIP_EXISTS\t" + existingId + "\t" + title);
                        continue;
                    }
                    long policyId = insertPolicy(conn, row);
                    insertOriginal(conn, policyId, row);
                    insertMetadata(conn, policyId, row);
                    titleToPolicyId.put(title, policyId);
                    insertedPolicies++;
                    System.out.println("INSERT_POLICY\t" + policyId + "\t" + title);
                }
                for (Map<String, String> row : readRows(attachTsv)) {
                    Long policyId = titleToPolicyId.get(row.get("REQUESTED_TITLE"));
                    if (policyId == null) continue;
                    if (attachmentExists(conn, policyId, row.get("FILE_PATH"), row.get("ATTACH_NAME"))) {
                        continue;
                    }
                    insertAttachment(conn, policyId, row);
                    insertedAttachments++;
                    System.out.println("INSERT_ATTACH\t" + policyId + "\t" + row.get("ATTACH_NAME"));
                }
                if (dryRun) {
                    conn.rollback();
                    System.out.println("DRY_RUN rollback complete");
                } else {
                    conn.commit();
                    System.out.println("COMMIT complete");
                }
                System.out.println("insertedPolicies=" + insertedPolicies + ", skippedPolicies=" + skippedPolicies + ", insertedAttachments=" + insertedAttachments);
            } catch (Exception ex) {
                conn.rollback();
                throw ex;
            }
        }
    }

    static Long findExistingPolicyId(Connection conn, String title) throws Exception {
        try (PreparedStatement ps = conn.prepareStatement(
                "SELECT ID FROM SYSDBA.POLICY WHERE POLICY_TITLE=? ORDER BY ID")) {
            ps.setString(1, title);
            try (ResultSet rs = ps.executeQuery()) {
                return rs.next() ? rs.getLong(1) : null;
            }
        }
    }

    static long insertPolicy(Connection conn, Map<String, String> row) throws Exception {
        try (PreparedStatement ps = conn.prepareStatement(
                "INSERT INTO SYSDBA.POLICY (FILE_NAME, ABSTRACT, CONTACT_INFO, APPLICATION_VOLUME, POLICY_TITLE, CREATED_AT, UPDATED_AT) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                Statement.RETURN_GENERATED_KEYS)) {
            String title = row.get("REQUESTED_TITLE");
            ps.setString(1, fit(title + ".md", 255));
            ps.setString(2, "");
            ps.setString(3, "");
            ps.setString(4, "");
            ps.setString(5, fit(title, 1000));
            ps.executeUpdate();
            try (ResultSet rs = ps.getGeneratedKeys()) {
                if (rs.next()) return rs.getLong(1);
            }
        }
        throw new IllegalStateException("No generated POLICY.ID returned for " + row.get("REQUESTED_TITLE"));
    }

    static void insertOriginal(Connection conn, long policyId, Map<String, String> row) throws Exception {
        try (PreparedStatement ps = conn.prepareStatement(
                "INSERT INTO SYSDBA.POLICY_ORIGINAL_V2 (POLICY_ID, POLICY_TITLE, POLICY_URL, POLICY_CODE, PUBLISH_DATE, FILE_TYPE, ISSUE_DEPARTMENT, FILE_PATH, CONTENT, CREATE_AT, UPDATE_AT, DEL_FLAG) " +
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, '0')")) {
            String content = Files.readString(Path.of(row.get("CONTENT_FILE")), StandardCharsets.UTF_8);
            ps.setString(1, String.valueOf(policyId));
            ps.setString(2, fit(row.get("REQUESTED_TITLE"), 500));
            ps.setString(3, fit(row.get("URL"), 1000));
            ps.setString(4, fit(row.get("POLICY_CODE"), 200));
            ps.setDate(5, Date.valueOf(row.get("PUBLISH_DATE")));
            ps.setString(6, "md");
            ps.setString(7, fit(row.get("ISSUE_DEPARTMENT"), 200));
            ps.setString(8, fit(row.get("FILE_PATH"), 2000));
            ps.setString(9, content);
            ps.executeUpdate();
        }
    }

    static void insertMetadata(Connection conn, long policyId, Map<String, String> row) throws Exception {
        try (PreparedStatement ps = conn.prepareStatement(
                "INSERT INTO SYSDBA.POLICY_METADATA (POLICY_ID, POLICY_ATTRIBUTE, ISSUING_DEPARTMENT, POLICY_TITLE_MAIN, POLICY_ORIGINAL_TITLE, REVIEW_STATUS, CREATED_AT, UPDATED_AT) " +
                "VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")) {
            ps.setLong(1, policyId);
            ps.setString(2, fit(row.get("POLICY_ATTRIBUTE"), 100));
            ps.setString(3, fit(row.get("ISSUE_DEPARTMENT"), 255));
            ps.setString(4, fit(row.get("REQUESTED_TITLE"), 255));
            ps.setString(5, fit(row.get("PAGE_TITLE"), 255));
            ps.setString(6, "未通过");
            ps.executeUpdate();
        }
    }

    static boolean attachmentExists(Connection conn, long policyId, String filePath, String name) throws Exception {
        try (PreparedStatement ps = conn.prepareStatement(
                "SELECT 1 FROM SYSDBA.POLICY_ATTACHMENT WHERE POLICY_ID=? AND DEL_FLAG='0' AND (FILE_PATH=? OR ATTACH_NAME=?)")) {
            ps.setLong(1, policyId);
            ps.setString(2, filePath);
            ps.setString(3, name);
            try (ResultSet rs = ps.executeQuery()) {
                return rs.next();
            }
        }
    }

    static void insertAttachment(Connection conn, long policyId, Map<String, String> row) throws Exception {
        try (PreparedStatement ps = conn.prepareStatement(
                "INSERT INTO SYSDBA.POLICY_ATTACHMENT (ATTACH_ID, POLICY_ID, ATTACH_NAME, ATTACH_SHOW_NAME, FILE_PATH, FILE_SUFFIX, CREATE_AT, UPDATE_AT, DEL_FLAG, ATTACH_TYPE, ATTACH_REVIEW_STATUS) " +
                "VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, '0', ?, ?)")) {
            ps.setString(1, row.get("ATTACH_ID") == null || row.get("ATTACH_ID").isBlank() ? UUID.randomUUID().toString() : row.get("ATTACH_ID"));
            ps.setLong(2, policyId);
            ps.setString(3, fit(row.get("ATTACH_NAME"), 500));
            ps.setString(4, fit(row.get("ATTACH_SHOW_NAME"), 500));
            ps.setString(5, fit(row.get("FILE_PATH"), 500));
            ps.setString(6, fit(row.get("FILE_SUFFIX"), 20));
            ps.setString(7, fit(row.get("ATTACH_TYPE"), 50));
            ps.setString(8, fit(row.get("ATTACH_REVIEW_STATUS"), 50));
            ps.executeUpdate();
        }
    }

    static List<Map<String, String>> readRows(Path path) throws Exception {
        List<Map<String, String>> rows = new ArrayList<>();
        if (!Files.exists(path)) return rows;
        try (BufferedReader reader = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
            String header = reader.readLine();
            if (header == null) return rows;
            if (header.length() > 0 && header.charAt(0) == '\ufeff') header = header.substring(1);
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

    static String fit(String value, int max) {
        if (value == null) return "";
        return value.length() <= max ? value : value.substring(0, max);
    }
}
