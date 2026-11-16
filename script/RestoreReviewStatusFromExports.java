import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Properties;
import java.util.Set;

public class RestoreReviewStatusFromExports {
    private static final String DB_URL = System.getenv().getOrDefault("KEJIJU_JDBC_DB_URL", "jdbc:dm://127.0.0.1:5236");
    private static final String DB_USER = "SYSDBA";
    private static final String DB_PASSWORD = System.getenv("KEJIJU_DB_PASSWORD");
    private static final String APPROVED = "\u5df2\u901a\u8fc7";
    private static final String PENDING = "\u672a\u901a\u8fc7";

    private static final Path METADATA_CSV = Paths.get("exports", "policy_metadata_approved_20260710_175701.csv");
    private static final Path ATTACHMENT_CSV = Paths.get("exports", "policy_attachment_approved_20260710_175701.csv");

    public static void main(String[] args) throws Exception {
        Set<String> approvedMetadataIds = readColumn(METADATA_CSV, "ID");
        Set<String> approvedAttachmentIds = readColumn(ATTACHMENT_CSV, "ATTACH_ID");

        Class.forName("dm.jdbc.driver.DmDriver");
        Properties props = new Properties();
        props.setProperty("user", DB_USER);
        props.setProperty("password", DB_PASSWORD);
        props.setProperty("localTimezone", "Asia/Shanghai");

        try (Connection conn = DriverManager.getConnection(DB_URL, props)) {
            conn.setAutoCommit(false);

            String ts = DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss").format(LocalDateTime.now());
            exportCurrent(conn, Paths.get("exports", "pre_restore_policy_metadata_review_status_" + ts + ".csv"),
                "SELECT ID, POLICY_ID, REVIEW_STATUS FROM SYSDBA.POLICY_METADATA ORDER BY ID");
            exportCurrent(conn, Paths.get("exports", "pre_restore_policy_attachment_review_status_" + ts + ".csv"),
                "SELECT ATTACH_ID, POLICY_ID, ATTACH_REVIEW_STATUS FROM SYSDBA.POLICY_ATTACHMENT ORDER BY ATTACH_ID");

            dropConstraintIfExists(conn, "POLICY_METADATA", "CK_POLICY_METADATA_REVIEW_STATUS");
            dropConstraintIfExists(conn, "POLICY_ATTACHMENT", "CK_POLICY_ATTACHMENT_REVIEW_STATUS");

            execute(conn, "ALTER TABLE SYSDBA.POLICY_METADATA MODIFY REVIEW_STATUS VARCHAR2(50) DEFAULT '" + PENDING + "'");
            execute(conn, "ALTER TABLE SYSDBA.POLICY_ATTACHMENT MODIFY ATTACH_REVIEW_STATUS VARCHAR2(50) DEFAULT '" + PENDING + "'");

            int metadataPending = execute(conn, "UPDATE SYSDBA.POLICY_METADATA SET REVIEW_STATUS = '" + PENDING + "'");
            int attachmentPending = execute(conn, "UPDATE SYSDBA.POLICY_ATTACHMENT SET ATTACH_REVIEW_STATUS = '" + PENDING + "'");

            int metadataApproved = updateApproved(conn,
                "UPDATE SYSDBA.POLICY_METADATA SET REVIEW_STATUS = ? WHERE ID = ?",
                approvedMetadataIds);
            int attachmentApproved = updateApproved(conn,
                "UPDATE SYSDBA.POLICY_ATTACHMENT SET ATTACH_REVIEW_STATUS = ? WHERE ATTACH_ID = ?",
                approvedAttachmentIds);

            execute(conn, "ALTER TABLE SYSDBA.POLICY_METADATA ADD CONSTRAINT CK_POLICY_METADATA_REVIEW_STATUS CHECK (REVIEW_STATUS IS NULL OR REVIEW_STATUS IN ('" + PENDING + "','" + APPROVED + "'))");
            execute(conn, "ALTER TABLE SYSDBA.POLICY_ATTACHMENT ADD CONSTRAINT CK_POLICY_ATTACHMENT_REVIEW_STATUS CHECK (ATTACH_REVIEW_STATUS IS NULL OR ATTACH_REVIEW_STATUS IN ('" + PENDING + "','" + APPROVED + "'))");

            conn.commit();

            System.out.println("metadata_csv_approved=" + approvedMetadataIds.size());
            System.out.println("attachment_csv_approved=" + approvedAttachmentIds.size());
            System.out.println("metadata_rows_set_pending=" + metadataPending);
            System.out.println("attachment_rows_set_pending=" + attachmentPending);
            System.out.println("metadata_rows_restored_approved=" + metadataApproved);
            System.out.println("attachment_rows_restored_approved=" + attachmentApproved);
        } catch (Exception e) {
            throw e;
        }
    }

    private static Set<String> readColumn(Path csvPath, String columnName) throws Exception {
        try (BufferedReader reader = Files.newBufferedReader(csvPath, StandardCharsets.UTF_8)) {
            String header = reader.readLine();
            if (header == null) throw new IllegalArgumentException("Empty CSV: " + csvPath);
            List<String> headers = parseCsvLine(header);
            if (!headers.isEmpty()) {
                headers.set(0, stripBom(headers.get(0)));
            }
            int index = headers.indexOf(columnName);
            if (index < 0) throw new IllegalArgumentException("Missing column " + columnName + " in " + csvPath);

            Set<String> values = new HashSet<>();
            String line;
            while ((line = reader.readLine()) != null) {
                if (line.trim().isEmpty()) continue;
                List<String> cells = parseCsvLine(line);
                if (index < cells.size()) {
                    String value = cells.get(index).trim();
                    if (!value.isEmpty()) values.add(value);
                }
            }
            return values;
        }
    }

    private static List<String> parseCsvLine(String line) {
        List<String> result = new ArrayList<>();
        StringBuilder cell = new StringBuilder();
        boolean quoted = false;
        for (int i = 0; i < line.length(); i++) {
            char ch = line.charAt(i);
            if (quoted) {
                if (ch == '"') {
                    if (i + 1 < line.length() && line.charAt(i + 1) == '"') {
                        cell.append('"');
                        i++;
                    } else {
                        quoted = false;
                    }
                } else {
                    cell.append(ch);
                }
            } else if (ch == '"') {
                quoted = true;
            } else if (ch == ',') {
                result.add(cell.toString());
                cell.setLength(0);
            } else {
                cell.append(ch);
            }
        }
        result.add(cell.toString());
        return result;
    }

    private static String stripBom(String value) {
        return value != null && !value.isEmpty() && value.charAt(0) == '\uFEFF'
            ? value.substring(1)
            : value;
    }

    private static void exportCurrent(Connection conn, Path path, String sql) throws Exception {
        Files.createDirectories(path.getParent());
        try (Statement stmt = conn.createStatement();
             ResultSet rs = stmt.executeQuery(sql);
             BufferedWriter writer = Files.newBufferedWriter(path, StandardCharsets.UTF_8)) {
            ResultSetMetaData md = rs.getMetaData();
            for (int i = 1; i <= md.getColumnCount(); i++) {
                if (i > 1) writer.write(",");
                writeCsvCell(writer, md.getColumnLabel(i));
            }
            writer.newLine();
            while (rs.next()) {
                for (int i = 1; i <= md.getColumnCount(); i++) {
                    if (i > 1) writer.write(",");
                    writeCsvCell(writer, rs.getString(i));
                }
                writer.newLine();
            }
        }
    }

    private static void writeCsvCell(BufferedWriter writer, String value) throws Exception {
        String text = value == null ? "" : value;
        writer.write('"');
        writer.write(text.replace("\"", "\"\""));
        writer.write('"');
    }

    private static void dropConstraintIfExists(Connection conn, String tableName, String constraintName) throws SQLException {
        try (PreparedStatement stmt = conn.prepareStatement(
                "SELECT COUNT(*) FROM ALL_CONSTRAINTS WHERE OWNER='SYSDBA' AND TABLE_NAME=? AND CONSTRAINT_NAME=?")) {
            stmt.setString(1, tableName);
            stmt.setString(2, constraintName);
            try (ResultSet rs = stmt.executeQuery()) {
                if (rs.next() && rs.getInt(1) > 0) {
                    execute(conn, "ALTER TABLE SYSDBA." + tableName + " DROP CONSTRAINT " + constraintName);
                }
            }
        }
    }

    private static int execute(Connection conn, String sql) throws SQLException {
        try (Statement stmt = conn.createStatement()) {
            return stmt.executeUpdate(sql);
        }
    }

    private static int updateApproved(Connection conn, String sql, Set<String> ids) throws SQLException {
        int updated = 0;
        try (PreparedStatement stmt = conn.prepareStatement(sql)) {
            for (String id : ids) {
                stmt.setString(1, APPROVED);
                stmt.setString(2, id);
                updated += stmt.executeUpdate();
            }
        }
        return updated;
    }
}
