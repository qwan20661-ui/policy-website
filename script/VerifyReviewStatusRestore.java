import java.io.BufferedReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Properties;
import java.util.Set;

public class VerifyReviewStatusRestore {
    private static final String DB_URL = System.getenv().getOrDefault("KEJIJU_JDBC_DB_URL", "jdbc:dm://127.0.0.1:5236");
    private static final String DB_USER = "SYSDBA";
    private static final String DB_PASSWORD = System.getenv("KEJIJU_DB_PASSWORD");
    private static final String APPROVED = "\u5df2\u901a\u8fc7";
    private static final String PENDING = "\u672a\u901a\u8fc7";

    public static void main(String[] args) throws Exception {
        Class.forName("dm.jdbc.driver.DmDriver");
        Properties props = new Properties();
        props.setProperty("user", DB_USER);
        props.setProperty("password", DB_PASSWORD);
        props.setProperty("localTimezone", "Asia/Shanghai");

        try (Connection conn = DriverManager.getConnection(DB_URL, props)) {
            System.out.println("metadata_approved=" + count(conn, "SELECT COUNT(*) FROM SYSDBA.POLICY_METADATA WHERE REVIEW_STATUS = ?", APPROVED));
            System.out.println("metadata_pending=" + count(conn, "SELECT COUNT(*) FROM SYSDBA.POLICY_METADATA WHERE REVIEW_STATUS = ?", PENDING));
            System.out.println("metadata_question_marks=" + count(conn, "SELECT COUNT(*) FROM SYSDBA.POLICY_METADATA WHERE REVIEW_STATUS = ?", "???"));
            System.out.println("metadata_other=" + count(conn, "SELECT COUNT(*) FROM SYSDBA.POLICY_METADATA WHERE REVIEW_STATUS IS NOT NULL AND REVIEW_STATUS NOT IN (?, ?)", PENDING, APPROVED));

            System.out.println("attachment_approved=" + count(conn, "SELECT COUNT(*) FROM SYSDBA.POLICY_ATTACHMENT WHERE ATTACH_REVIEW_STATUS = ?", APPROVED));
            System.out.println("attachment_pending=" + count(conn, "SELECT COUNT(*) FROM SYSDBA.POLICY_ATTACHMENT WHERE ATTACH_REVIEW_STATUS = ?", PENDING));
            System.out.println("attachment_question_marks=" + count(conn, "SELECT COUNT(*) FROM SYSDBA.POLICY_ATTACHMENT WHERE ATTACH_REVIEW_STATUS = ?", "???"));
            System.out.println("attachment_other=" + count(conn, "SELECT COUNT(*) FROM SYSDBA.POLICY_ATTACHMENT WHERE ATTACH_REVIEW_STATUS IS NOT NULL AND ATTACH_REVIEW_STATUS NOT IN (?, ?)", PENDING, APPROVED));

            CsvData metadata = readCsv(Paths.get("exports", "policy_metadata_approved_20260710_175701.csv"), "ID", "POLICY_ID");
            CsvData attachment = readCsv(Paths.get("exports", "policy_attachment_approved_20260710_175701.csv"), "ATTACH_ID", "POLICY_ID");

            System.out.println("metadata_csv_ids_found=" + countFound(conn, "SELECT COUNT(*) FROM SYSDBA.POLICY_METADATA WHERE ID = ?", metadata.ids));
            System.out.println("metadata_csv_policy_ids_found=" + countFound(conn, "SELECT COUNT(*) FROM SYSDBA.POLICY_METADATA WHERE POLICY_ID = ?", metadata.policyIds));
            System.out.println("attachment_csv_ids_found=" + countFound(conn, "SELECT COUNT(*) FROM SYSDBA.POLICY_ATTACHMENT WHERE ATTACH_ID = ?", attachment.ids));
        }
    }

    private static int count(Connection conn, String sql, String... params) throws Exception {
        try (PreparedStatement stmt = conn.prepareStatement(sql)) {
            for (int i = 0; i < params.length; i++) stmt.setString(i + 1, params[i]);
            try (ResultSet rs = stmt.executeQuery()) {
                rs.next();
                return rs.getInt(1);
            }
        }
    }

    private static int countFound(Connection conn, String sql, Set<String> ids) throws Exception {
        int found = 0;
        try (PreparedStatement stmt = conn.prepareStatement(sql)) {
            for (String id : ids) {
                stmt.setString(1, id);
                try (ResultSet rs = stmt.executeQuery()) {
                    if (rs.next() && rs.getInt(1) > 0) found++;
                }
            }
        }
        return found;
    }

    private static CsvData readCsv(Path csvPath, String idColumn, String policyIdColumn) throws Exception {
        try (BufferedReader reader = Files.newBufferedReader(csvPath, StandardCharsets.UTF_8)) {
            String header = reader.readLine();
            List<String> headers = parseCsvLine(header);
            if (!headers.isEmpty() && headers.get(0).startsWith("\uFEFF")) {
                headers.set(0, headers.get(0).substring(1));
            }
            int idIndex = headers.indexOf(idColumn);
            int policyIdIndex = headers.indexOf(policyIdColumn);
            CsvData data = new CsvData();
            String line;
            while ((line = reader.readLine()) != null) {
                List<String> cells = parseCsvLine(line);
                if (idIndex >= 0 && idIndex < cells.size()) data.ids.add(cells.get(idIndex).trim());
                if (policyIdIndex >= 0 && policyIdIndex < cells.size()) data.policyIds.add(cells.get(policyIdIndex).trim());
            }
            data.ids.remove("");
            data.policyIds.remove("");
            return data;
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

    private static class CsvData {
        final Set<String> ids = new HashSet<>();
        final Set<String> policyIds = new HashSet<>();
    }
}
