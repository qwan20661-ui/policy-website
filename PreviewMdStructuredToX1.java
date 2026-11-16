import java.io.BufferedWriter;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.sql.Clob;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.Statement;
import java.sql.Timestamp;
import java.util.Properties;

public class PreviewMdStructuredToX1 {
    private static final int PREVIEW_LIMIT = 600;

    public static void main(String[] args) throws Exception {
        Class.forName("dm.jdbc.driver.DmDriver");
        Properties props = new Properties();
        props.setProperty("user", "SYSDBA");
        String password = System.getenv("DM_PASSWORD");
        if (password == null || password.trim().isEmpty()) {
            throw new IllegalStateException("DM_PASSWORD environment variable is required");
        }
        props.setProperty("password", password);
        props.setProperty("localTimezone", "Asia/Shanghai");

        Path outDir = Paths.get("tmp", "md_structured_x1_preview");
        Files.createDirectories(outDir);
        Path csv = outDir.resolve("matched_preview.csv");
        Path policyCsv = outDir.resolve("matched_policy_preview.csv");
        Path duplicateCsv = outDir.resolve("duplicate_matches.csv");
        Path summary = outDir.resolve("summary.txt");

        try (Connection conn = DriverManager.getConnection(System.getenv().getOrDefault("KEJIJU_JDBC_DB_URL", "jdbc:dm://127.0.0.1:5236"), props)) {
            writePreview(conn, csv);
            writePolicyPreview(conn, policyCsv);
            writeDuplicatePreview(conn, duplicateCsv);
            writeSummary(conn, summary, csv);
        }

        System.out.println("CSV=" + csv.toAbsolutePath());
        System.out.println("POLICY_CSV=" + policyCsv.toAbsolutePath());
        System.out.println("DUPLICATE_CSV=" + duplicateCsv.toAbsolutePath());
        System.out.println("SUMMARY=" + summary.toAbsolutePath());
    }

    private static void writePreview(Connection conn, Path csv) throws Exception {
        String sql =
            "SELECT f.ID FUSION_POLICY_ID, f.SOURCE_POLICY_ID, f.MAIN_FILE_TITLE, " +
            "       s.ID STRUCTURED_ID, s.SOURCE_MD_PATH, s.POLICY_TITLE, " +
            "       s.PUBLISH_REGION SRC_APPLICABLE_SCOPE, s.ISSUE_DEPARTMENT SRC_ISSUING_DEPARTMENT, " +
            "       s.DECLARE_START_TIME SRC_APPLICATION_START_TIME, s.DECLARE_END_TIME SRC_APPLICATION_END_TIME, " +
            "       s.SUPPORT_CONTENT SRC_SUPPORT_CONTENT, s.APPLICATION_MATERIALS SRC_APPLICATION_MATERIALS, " +
            "       s.CONTACT_INFO SRC_CONTACT_INFO, " +
            "       (SELECT m.ID FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID=f.SOURCE_POLICY_ID AND ROWNUM=1) POLICY_METADATA_ID, " +
            "       (SELECT m.APPLICABLE_SCOPE FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID=f.SOURCE_POLICY_ID AND ROWNUM=1) OLD_APPLICABLE_SCOPE, " +
            "       (SELECT m.ISSUING_DEPARTMENT FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID=f.SOURCE_POLICY_ID AND ROWNUM=1) OLD_ISSUING_DEPARTMENT, " +
            "       (SELECT c.ID FROM SYSDBA.APPLICATION_CONDITION c WHERE c.POLICY_ID=f.SOURCE_POLICY_ID AND ROWNUM=1) APPLICATION_CONDITION_ID, " +
            "       (SELECT c.APPLICATION_START_TIME FROM SYSDBA.APPLICATION_CONDITION c WHERE c.POLICY_ID=f.SOURCE_POLICY_ID AND ROWNUM=1) OLD_APPLICATION_START_TIME, " +
            "       (SELECT c.APPLICATION_END_TIME FROM SYSDBA.APPLICATION_CONDITION c WHERE c.POLICY_ID=f.SOURCE_POLICY_ID AND ROWNUM=1) OLD_APPLICATION_END_TIME, " +
            "       (SELECT sm.ID FROM SYSDBA.SUPPORT_METHOD sm WHERE sm.POLICY_ID=f.SOURCE_POLICY_ID AND ROWNUM=1) SUPPORT_METHOD_ID, " +
            "       (SELECT sm.SUPPORT_CONTENT_DESC FROM SYSDBA.SUPPORT_METHOD sm WHERE sm.POLICY_ID=f.SOURCE_POLICY_ID AND ROWNUM=1) OLD_SUPPORT_CONTENT, " +
            "       (SELECT am.ID FROM SYSDBA.APPLICATION_MATERIAL am WHERE am.POLICY_ID=f.SOURCE_POLICY_ID AND ROWNUM=1) APPLICATION_MATERIAL_ID, " +
            "       (SELECT am.APPLICATION_MATERIALS FROM SYSDBA.APPLICATION_MATERIAL am WHERE am.POLICY_ID=f.SOURCE_POLICY_ID AND ROWNUM=1) OLD_APPLICATION_MATERIALS, " +
            "       (SELECT p.CONTACT_INFO FROM SYSDBA.POLICY p WHERE p.ID=f.SOURCE_POLICY_ID AND ROWNUM=1) OLD_CONTACT_INFO " +
            "  FROM SYSDBA.X1_FUSION_POLICY f " +
            "  JOIN SYSDBA.POLICY_MD_STRUCTURED s ON TRIM(s.POLICY_TITLE)=TRIM(f.MAIN_FILE_TITLE) " +
            " WHERE NVL(TRIM(f.DEL_FLAG),'0')='0' " +
            "   AND NVL(TRIM(s.DEL_FLAG),'0')='0' " +
            "   AND f.SOURCE_POLICY_ID IS NOT NULL " +
            " ORDER BY f.ID, s.CREATED_AT, s.ID";

        try (PreparedStatement ps = conn.prepareStatement(sql);
             ResultSet rs = ps.executeQuery();
             BufferedWriter w = Files.newBufferedWriter(csv, StandardCharsets.UTF_8)) {
            w.write(String.join(",",
                "fusion_policy_id",
                "source_policy_id",
                "main_file_title",
                "structured_id",
                "source_md_path",
                "src_policy_title",
                "target_table_column",
                "target_record_id",
                "old_value_preview",
                "new_value_preview",
                "action"
            ));
            w.newLine();
            while (rs.next()) {
                writeField(w, rs, "POLICY_METADATA.APPLICABLE_SCOPE", rs.getString("POLICY_METADATA_ID"), clobOrString(rs, "OLD_APPLICABLE_SCOPE"), rs.getString("SRC_APPLICABLE_SCOPE"));
                writeField(w, rs, "POLICY_METADATA.ISSUING_DEPARTMENT", rs.getString("POLICY_METADATA_ID"), clobOrString(rs, "OLD_ISSUING_DEPARTMENT"), rs.getString("SRC_ISSUING_DEPARTMENT"));
                writeField(w, rs, "APPLICATION_CONDITION.APPLICATION_START_TIME", rs.getString("APPLICATION_CONDITION_ID"), rs.getString("OLD_APPLICATION_START_TIME"), formatDate(rs.getTimestamp("SRC_APPLICATION_START_TIME")));
                writeField(w, rs, "APPLICATION_CONDITION.APPLICATION_END_TIME", rs.getString("APPLICATION_CONDITION_ID"), rs.getString("OLD_APPLICATION_END_TIME"), formatDate(rs.getTimestamp("SRC_APPLICATION_END_TIME")));
                writeField(w, rs, "SUPPORT_METHOD.SUPPORT_CONTENT_DESC", rs.getString("SUPPORT_METHOD_ID"), clobOrString(rs, "OLD_SUPPORT_CONTENT"), clobOrString(rs, "SRC_SUPPORT_CONTENT"));
                writeField(w, rs, "APPLICATION_MATERIAL.APPLICATION_MATERIALS", rs.getString("APPLICATION_MATERIAL_ID"), clobOrString(rs, "OLD_APPLICATION_MATERIALS"), clobOrString(rs, "SRC_APPLICATION_MATERIALS"));
                writeField(w, rs, "POLICY.CONTACT_INFO", rs.getString("SOURCE_POLICY_ID"), clobOrString(rs, "OLD_CONTACT_INFO"), clobOrString(rs, "SRC_CONTACT_INFO"));
            }
        }
    }

    private static void writePolicyPreview(Connection conn, Path csv) throws Exception {
        String sql =
            "SELECT f.ID FUSION_POLICY_ID, f.SOURCE_POLICY_ID, f.MAIN_FILE_TITLE, " +
            "       s.ID STRUCTURED_ID, s.SOURCE_MD_PATH, s.POLICY_TITLE, " +
            "       s.PUBLISH_REGION SRC_APPLICABLE_SCOPE, s.ISSUE_DEPARTMENT SRC_ISSUING_DEPARTMENT, " +
            "       s.DECLARE_START_TIME SRC_APPLICATION_START_TIME, s.DECLARE_END_TIME SRC_APPLICATION_END_TIME, " +
            "       s.SUPPORT_CONTENT SRC_SUPPORT_CONTENT, s.APPLICATION_MATERIALS SRC_APPLICATION_MATERIALS, " +
            "       s.CONTACT_INFO SRC_CONTACT_INFO, " +
            "       (SELECT m.APPLICABLE_SCOPE FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID=f.SOURCE_POLICY_ID AND ROWNUM=1) OLD_APPLICABLE_SCOPE, " +
            "       (SELECT m.ISSUING_DEPARTMENT FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID=f.SOURCE_POLICY_ID AND ROWNUM=1) OLD_ISSUING_DEPARTMENT, " +
            "       (SELECT c.APPLICATION_START_TIME FROM SYSDBA.APPLICATION_CONDITION c WHERE c.POLICY_ID=f.SOURCE_POLICY_ID AND ROWNUM=1) OLD_APPLICATION_START_TIME, " +
            "       (SELECT c.APPLICATION_END_TIME FROM SYSDBA.APPLICATION_CONDITION c WHERE c.POLICY_ID=f.SOURCE_POLICY_ID AND ROWNUM=1) OLD_APPLICATION_END_TIME, " +
            "       (SELECT sm.SUPPORT_CONTENT_DESC FROM SYSDBA.SUPPORT_METHOD sm WHERE sm.POLICY_ID=f.SOURCE_POLICY_ID AND ROWNUM=1) OLD_SUPPORT_CONTENT, " +
            "       (SELECT am.APPLICATION_MATERIALS FROM SYSDBA.APPLICATION_MATERIAL am WHERE am.POLICY_ID=f.SOURCE_POLICY_ID AND ROWNUM=1) OLD_APPLICATION_MATERIALS, " +
            "       (SELECT p.CONTACT_INFO FROM SYSDBA.POLICY p WHERE p.ID=f.SOURCE_POLICY_ID AND ROWNUM=1) OLD_CONTACT_INFO " +
            "  FROM SYSDBA.X1_FUSION_POLICY f " +
            "  JOIN SYSDBA.POLICY_MD_STRUCTURED s ON TRIM(s.POLICY_TITLE)=TRIM(f.MAIN_FILE_TITLE) " +
            " WHERE NVL(TRIM(f.DEL_FLAG),'0')='0' " +
            "   AND NVL(TRIM(s.DEL_FLAG),'0')='0' " +
            "   AND f.SOURCE_POLICY_ID IS NOT NULL " +
            " ORDER BY f.ID, s.CREATED_AT, s.ID";

        try (PreparedStatement ps = conn.prepareStatement(sql);
             ResultSet rs = ps.executeQuery();
             BufferedWriter w = Files.newBufferedWriter(csv, StandardCharsets.UTF_8)) {
            w.write(String.join(",",
                "fusion_policy_id",
                "source_policy_id",
                "main_file_title",
                "structured_id",
                "source_md_path",
                "src_policy_title",
                "old_applicable_scope",
                "new_applicable_scope",
                "old_issuing_department",
                "new_issuing_department",
                "old_application_start_time",
                "new_application_start_time",
                "old_application_end_time",
                "new_application_end_time",
                "old_support_content_preview",
                "new_support_content_preview",
                "old_application_materials_preview",
                "new_application_materials_preview",
                "old_contact_info_preview",
                "new_contact_info_preview"
            ));
            w.newLine();
            while (rs.next()) {
                w.write(String.join(",",
                    csv(rs.getString("FUSION_POLICY_ID")),
                    csv(rs.getString("SOURCE_POLICY_ID")),
                    csv(rs.getString("MAIN_FILE_TITLE")),
                    csv(rs.getString("STRUCTURED_ID")),
                    csv(rs.getString("SOURCE_MD_PATH")),
                    csv(rs.getString("POLICY_TITLE")),
                    csv(preview(clobOrString(rs, "OLD_APPLICABLE_SCOPE"))),
                    csv(preview(rs.getString("SRC_APPLICABLE_SCOPE"))),
                    csv(preview(clobOrString(rs, "OLD_ISSUING_DEPARTMENT"))),
                    csv(preview(rs.getString("SRC_ISSUING_DEPARTMENT"))),
                    csv(preview(rs.getString("OLD_APPLICATION_START_TIME"))),
                    csv(formatDate(rs.getTimestamp("SRC_APPLICATION_START_TIME"))),
                    csv(preview(rs.getString("OLD_APPLICATION_END_TIME"))),
                    csv(formatDate(rs.getTimestamp("SRC_APPLICATION_END_TIME"))),
                    csv(preview(clobOrString(rs, "OLD_SUPPORT_CONTENT"))),
                    csv(preview(clobOrString(rs, "SRC_SUPPORT_CONTENT"))),
                    csv(preview(clobOrString(rs, "OLD_APPLICATION_MATERIALS"))),
                    csv(preview(clobOrString(rs, "SRC_APPLICATION_MATERIALS"))),
                    csv(preview(clobOrString(rs, "OLD_CONTACT_INFO"))),
                    csv(preview(clobOrString(rs, "SRC_CONTACT_INFO")))
                ));
                w.newLine();
            }
        }
    }

    private static void writeDuplicatePreview(Connection conn, Path csv) throws Exception {
        String sql =
            "SELECT f.ID FUSION_POLICY_ID, f.SOURCE_POLICY_ID, f.MAIN_FILE_TITLE, " +
            "       s.ID STRUCTURED_ID, s.SOURCE_MD_PATH, s.CREATED_AT " +
            "  FROM SYSDBA.X1_FUSION_POLICY f " +
            "  JOIN SYSDBA.POLICY_MD_STRUCTURED s ON TRIM(s.POLICY_TITLE)=TRIM(f.MAIN_FILE_TITLE) " +
            " WHERE NVL(TRIM(f.DEL_FLAG),'0')='0' " +
            "   AND NVL(TRIM(s.DEL_FLAG),'0')='0' " +
            "   AND f.SOURCE_POLICY_ID IS NOT NULL " +
            "   AND f.ID IN ( " +
            "       SELECT f2.ID FROM SYSDBA.X1_FUSION_POLICY f2 " +
            "       JOIN SYSDBA.POLICY_MD_STRUCTURED s2 ON TRIM(s2.POLICY_TITLE)=TRIM(f2.MAIN_FILE_TITLE) " +
            "       WHERE NVL(TRIM(f2.DEL_FLAG),'0')='0' AND NVL(TRIM(s2.DEL_FLAG),'0')='0' AND f2.SOURCE_POLICY_ID IS NOT NULL " +
            "       GROUP BY f2.ID HAVING COUNT(DISTINCT s2.ID)>1 " +
            "   ) " +
            " ORDER BY f.ID, s.CREATED_AT, s.ID";

        try (PreparedStatement ps = conn.prepareStatement(sql);
             ResultSet rs = ps.executeQuery();
             BufferedWriter w = Files.newBufferedWriter(csv, StandardCharsets.UTF_8)) {
            w.write("fusion_policy_id,source_policy_id,main_file_title,structured_id,source_md_path,structured_created_at");
            w.newLine();
            while (rs.next()) {
                w.write(String.join(",",
                    csv(rs.getString("FUSION_POLICY_ID")),
                    csv(rs.getString("SOURCE_POLICY_ID")),
                    csv(rs.getString("MAIN_FILE_TITLE")),
                    csv(rs.getString("STRUCTURED_ID")),
                    csv(rs.getString("SOURCE_MD_PATH")),
                    csv(rs.getString("CREATED_AT"))
                ));
                w.newLine();
            }
        }
    }

    private static void writeField(BufferedWriter w, ResultSet rs, String target, String targetRecordId, String oldValue, String newValue) throws Exception {
        if (isBlank(newValue)) return;
        String action = isBlank(targetRecordId) ? "INSERT_TARGET_ROW" : (same(oldValue, newValue) ? "NO_CHANGE" : "UPDATE");
        w.write(String.join(",",
            csv(rs.getString("FUSION_POLICY_ID")),
            csv(rs.getString("SOURCE_POLICY_ID")),
            csv(rs.getString("MAIN_FILE_TITLE")),
            csv(rs.getString("STRUCTURED_ID")),
            csv(rs.getString("SOURCE_MD_PATH")),
            csv(rs.getString("POLICY_TITLE")),
            csv(target),
            csv(targetRecordId),
            csv(preview(oldValue)),
            csv(preview(newValue)),
            csv(action)
        ));
        w.newLine();
    }

    private static void writeSummary(Connection conn, Path summary, Path csv) throws Exception {
        try (BufferedWriter w = Files.newBufferedWriter(summary, StandardCharsets.UTF_8)) {
            w.write("matched_pairs=" + scalar(conn,
                "SELECT COUNT(*) FROM SYSDBA.X1_FUSION_POLICY f JOIN SYSDBA.POLICY_MD_STRUCTURED s ON TRIM(s.POLICY_TITLE)=TRIM(f.MAIN_FILE_TITLE) " +
                "WHERE NVL(TRIM(f.DEL_FLAG),'0')='0' AND NVL(TRIM(s.DEL_FLAG),'0')='0' AND f.SOURCE_POLICY_ID IS NOT NULL"));
            w.newLine();
            w.write("matched_fusion_policies=" + scalar(conn,
                "SELECT COUNT(DISTINCT f.ID) FROM SYSDBA.X1_FUSION_POLICY f JOIN SYSDBA.POLICY_MD_STRUCTURED s ON TRIM(s.POLICY_TITLE)=TRIM(f.MAIN_FILE_TITLE) " +
                "WHERE NVL(TRIM(f.DEL_FLAG),'0')='0' AND NVL(TRIM(s.DEL_FLAG),'0')='0' AND f.SOURCE_POLICY_ID IS NOT NULL"));
            w.newLine();
            w.write("matched_structured_rows=" + scalar(conn,
                "SELECT COUNT(DISTINCT s.ID) FROM SYSDBA.X1_FUSION_POLICY f JOIN SYSDBA.POLICY_MD_STRUCTURED s ON TRIM(s.POLICY_TITLE)=TRIM(f.MAIN_FILE_TITLE) " +
                "WHERE NVL(TRIM(f.DEL_FLAG),'0')='0' AND NVL(TRIM(s.DEL_FLAG),'0')='0' AND f.SOURCE_POLICY_ID IS NOT NULL"));
            w.newLine();
            w.write("duplicate_title_pairs=" + scalar(conn,
                "SELECT COUNT(*) FROM (SELECT TRIM(s.POLICY_TITLE) title, COUNT(*) c FROM SYSDBA.POLICY_MD_STRUCTURED s " +
                "WHERE NVL(TRIM(s.DEL_FLAG),'0')='0' GROUP BY TRIM(s.POLICY_TITLE) HAVING COUNT(*)>1)"));
            w.newLine();
            w.write("preview_csv=" + csv.toAbsolutePath());
            w.newLine();
        }
    }

    private static String scalar(Connection conn, String sql) throws Exception {
        try (Statement st = conn.createStatement(); ResultSet rs = st.executeQuery(sql)) {
            return rs.next() ? rs.getString(1) : "";
        }
    }

    private static String formatDate(Timestamp ts) {
        if (ts == null) return "";
        return ts.toLocalDateTime().toLocalDate().toString();
    }

    private static String clobOrString(ResultSet rs, String col) throws Exception {
        Object o = rs.getObject(col);
        if (o == null) return "";
        if (o instanceof Clob) {
            Clob c = (Clob) o;
            long len = c.length();
            return len == 0 ? "" : c.getSubString(1, (int) Math.min(len, 4000));
        }
        return String.valueOf(o);
    }

    private static String preview(String value) {
        if (value == null) return "";
        String s = value.replace("\r\n", "\n").replace("\r", "\n").trim();
        if (s.length() <= PREVIEW_LIMIT) return s;
        return s.substring(0, PREVIEW_LIMIT) + "...";
    }

    private static boolean same(String a, String b) {
        return preview(a).equals(preview(b));
    }

    private static boolean isBlank(String s) {
        return s == null || s.trim().isEmpty();
    }

    private static String csv(String s) {
        if (s == null) s = "";
        return "\"" + s.replace("\"", "\"\"") + "\"";
    }
}
