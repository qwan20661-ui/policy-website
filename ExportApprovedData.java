import java.io.BufferedWriter;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.sql.Statement;
import java.util.Properties;

public class ExportApprovedData {
    private static final String URL = System.getenv().getOrDefault("KEJIJU_JDBC_DB_URL", "jdbc:dm://127.0.0.1:5236");
    private static final String USER = "SYSDBA";
    private static final String PASSWORD = System.getenv("KEJIJU_DB_PASSWORD");
    private static final Path OUTPUT_DIR = Paths.get("exports");

    public static void main(String[] args) throws Exception {
        Class.forName("dm.jdbc.driver.DmDriver");

        Properties props = new Properties();
        props.setProperty("user", USER);
        props.setProperty("password", PASSWORD);
        props.setProperty("localTimezone", "Asia/Shanghai");

        Files.createDirectories(OUTPUT_DIR);
        String stamp = new java.text.SimpleDateFormat("yyyyMMdd_HHmmss").format(new java.util.Date());

        try (Connection conn = DriverManager.getConnection(URL, props)) {
            exportQuery(
                conn,
                "policy_attachment_approved_" + stamp,
                "SELECT * FROM SYSDBA.POLICY_ATTACHMENT WHERE ATTACH_REVIEW_STATUS = '已通过' ORDER BY POLICY_ID, ATTACH_ID"
            );
            exportQuery(
                conn,
                "policy_metadata_approved_" + stamp,
                "SELECT * FROM SYSDBA.POLICY_METADATA WHERE REVIEW_STATUS = '已通过' ORDER BY POLICY_ID, ID"
            );
        }
    }

    private static void exportQuery(Connection conn, String baseName, String sql) throws Exception {
        Path csvPath = OUTPUT_DIR.resolve(baseName + ".csv");
        Path jsonPath = OUTPUT_DIR.resolve(baseName + ".json");

        int count;
        try (Statement stmt = conn.createStatement(); ResultSet rs = stmt.executeQuery(sql)) {
            count = writeCsv(rs, csvPath);
        }

        try (Statement stmt = conn.createStatement(); ResultSet rs = stmt.executeQuery(sql)) {
            writeJson(rs, jsonPath);
        }

        System.out.println(baseName + ": " + count + " rows");
        System.out.println("  CSV : " + csvPath.toAbsolutePath());
        System.out.println("  JSON: " + jsonPath.toAbsolutePath());
    }

    private static int writeCsv(ResultSet rs, Path path) throws Exception {
        ResultSetMetaData meta = rs.getMetaData();
        int columns = meta.getColumnCount();
        int count = 0;

        try (BufferedWriter writer = new BufferedWriter(new OutputStreamWriter(Files.newOutputStream(path), StandardCharsets.UTF_8))) {
            writer.write('\uFEFF');
            for (int i = 1; i <= columns; i++) {
                if (i > 1) writer.write(",");
                writer.write(csv(meta.getColumnLabel(i)));
            }
            writer.newLine();

            while (rs.next()) {
                for (int i = 1; i <= columns; i++) {
                    if (i > 1) writer.write(",");
                    writer.write(csv(rs.getString(i)));
                }
                writer.newLine();
                count++;
            }
        }

        return count;
    }

    private static void writeJson(ResultSet rs, Path path) throws Exception {
        ResultSetMetaData meta = rs.getMetaData();
        int columns = meta.getColumnCount();

        try (BufferedWriter writer = Files.newBufferedWriter(path, StandardCharsets.UTF_8)) {
            writer.write("[");
            boolean firstRow = true;
            while (rs.next()) {
                if (!firstRow) writer.write(",");
                firstRow = false;
                writer.newLine();
                writer.write("  {");
                for (int i = 1; i <= columns; i++) {
                    if (i > 1) writer.write(",");
                    writer.newLine();
                    writer.write("    \"");
                    writer.write(json(meta.getColumnLabel(i)));
                    writer.write("\": ");
                    String value = rs.getString(i);
                    if (value == null) {
                        writer.write("null");
                    } else {
                        writer.write("\"");
                        writer.write(json(value));
                        writer.write("\"");
                    }
                }
                writer.newLine();
                writer.write("  }");
            }
            writer.newLine();
            writer.write("]");
            writer.newLine();
        }
    }

    private static String csv(String value) {
        if (value == null) return "";
        return "\"" + value.replace("\"", "\"\"") + "\"";
    }

    private static String json(String value) {
        if (value == null) return "";
        StringBuilder out = new StringBuilder(value.length() + 16);
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '\\':
                    out.append("\\\\");
                    break;
                case '"':
                    out.append("\\\"");
                    break;
                case '\b':
                    out.append("\\b");
                    break;
                case '\f':
                    out.append("\\f");
                    break;
                case '\n':
                    out.append("\\n");
                    break;
                case '\r':
                    out.append("\\r");
                    break;
                case '\t':
                    out.append("\\t");
                    break;
                default:
                    if (c < 0x20) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
            }
        }
        return out.toString();
    }
}
