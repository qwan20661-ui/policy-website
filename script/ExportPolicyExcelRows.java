import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.util.Properties;

public class ExportPolicyExcelRows {
    public static void main(String[] args) throws Exception {
        if (args.length < 4) {
            throw new IllegalArgumentException("用法: java ExportPolicyExcelRows <host> <port> <user> <password> [status] [limit]");
        }
        String host = args[0];
        String port = args[1];
        String user = args[2];
        String password = args[3];
        String markStatus = args.length >= 5 ? nullToEmpty(args[4]).trim() : "";
        Integer limit = args.length >= 6 ? parseLimit(args[5]) : null;

        Class.forName("dm.jdbc.driver.DmDriver");

        Properties props = new Properties();
        props.setProperty("user", user);
        props.setProperty("password", password);
        props.setProperty("localTimezone", "Asia/Shanghai");
        props.setProperty("connectTimeout", "5000");
        props.setProperty("socketTimeout", "30000");

        try (Connection conn = DriverManager.getConnection("jdbc:dm://" + host + ":" + port, props);
             PreparedStatement stmt = conn.prepareStatement(buildSql(markStatus, limit));
             ResultSet rs = stmt.executeQuery()) {
            System.out.println("policy_id,policy_title,mark_status,publish_date,policy_attribute,manual_annotation,machine_annotation,has_attachment,attachment_count,issuing_department");
            while (rs.next()) {
                String policyAttribute = normalizePolicyType(rs.getString("POLICY_ATTRIBUTE"));
                if (isHiddenPolicyAttribute(policyAttribute)) {
                    // 隐藏属性是否导出交由上层脚本决定，这里统一输出完整结果。
                }
                int attachmentCount = rs.getInt("ATTACHMENT_COUNT");
                String metadataDepartment = nullToEmpty(rs.getString("METADATA_DEPARTMENT"));
                String originalDepartment = nullToEmpty(rs.getString("ISSUE_DEPARTMENT"));
                String displayDepartment = metadataDepartment.isEmpty() ? originalDepartment : metadataDepartment;
                boolean manuallyAnnotated = rs.getInt("POLICY_REVIEWED") == 1;
                String manualAnnotation = manuallyAnnotated ? policyAttribute : "";
                String machineAnnotation = manuallyAnnotated ? "" : policyAttribute;
                System.out.println(
                    csv(String.valueOf(rs.getLong("ID"))) + "," +
                    csv(nullToEmpty(rs.getString("POLICY_TITLE"))) + "," +
                    csv(nullToEmpty(rs.getString("MARK_STATUS"))) + "," +
                    csv(nullToEmpty(rs.getString("PUBLISH_DATE"))) + "," +
                    csv(policyAttribute) + "," +
                    csv(manualAnnotation) + "," +
                    csv(machineAnnotation) + "," +
                    csv(attachmentCount > 0 ? "是" : "否") + "," +
                    csv(String.valueOf(attachmentCount)) + "," +
                    csv(displayDepartment)
                );
            }
        }
    }

    private static String buildSql(String markStatus, Integer limit) {
        StringBuilder sql = new StringBuilder();
        sql.append("SELECT ")
            .append("    p.ID, ")
            .append("    p.POLICY_TITLE, ")
            .append("    p.CREATED_AT, ")
            .append("    CASE ")
            .append("        WHEN NVL(m.POLICY_REVIEWED, 0) = 1 ")
            .append("             AND NVL(a.ATTACHMENT_COUNT, 0) = NVL(a.REVIEWED_ATTACHMENT_COUNT, 0) THEN '已标注' ")
            .append("        WHEN NVL(m.POLICY_REVIEWED, 0) = 0 ")
            .append("             AND NVL(a.REVIEWED_ATTACHMENT_COUNT, 0) = 0 THEN '未标注' ")
            .append("        ELSE '标注中' ")
            .append("    END AS MARK_STATUS, ")
            .append("    (SELECT o.PUBLISH_DATE FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID = CAST(p.ID AS VARCHAR(50)) AND ROWNUM = 1) AS PUBLISH_DATE, ")
            .append("    (SELECT o.ISSUE_DEPARTMENT FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID = CAST(p.ID AS VARCHAR(50)) AND ROWNUM = 1) AS ISSUE_DEPARTMENT, ")
            .append("    m.METADATA_DEPARTMENT, ")
            .append("    m.POLICY_ATTRIBUTE, ")
            .append("    NVL(m.POLICY_REVIEWED, 0) AS POLICY_REVIEWED, ")
            .append("    NVL(a.ATTACHMENT_COUNT, 0) AS ATTACHMENT_COUNT ")
            .append("FROM SYSDBA.POLICY p ")
            .append("LEFT JOIN (SELECT POLICY_ID, MAX(TRIM(ISSUING_DEPARTMENT)) AS METADATA_DEPARTMENT, MAX(TRIM(POLICY_ATTRIBUTE)) AS POLICY_ATTRIBUTE, MAX(CASE WHEN REVIEW_STATUS = '已通过' THEN 1 ELSE 0 END) AS POLICY_REVIEWED FROM SYSDBA.POLICY_METADATA GROUP BY POLICY_ID) m ")
            .append("    ON m.POLICY_ID = p.ID ")
            .append("LEFT JOIN (SELECT TRIM(POLICY_ID) AS PID, COUNT(*) AS ATTACHMENT_COUNT, SUM(CASE WHEN ATTACH_REVIEW_STATUS = '已通过' THEN 1 ELSE 0 END) AS REVIEWED_ATTACHMENT_COUNT FROM SYSDBA.POLICY_ATTACHMENT WHERE DEL_FLAG = '0' GROUP BY TRIM(POLICY_ID)) a ")
            .append("    ON a.PID = CAST(p.ID AS VARCHAR(50)) ")
            .append("WHERE 1=1 ");

        if ("已标注".equals(markStatus)) {
            sql.append("AND EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA fm WHERE fm.POLICY_ID = p.ID AND fm.REVIEW_STATUS = '已通过') ")
                .append("AND NOT EXISTS (SELECT 1 FROM SYSDBA.POLICY_ATTACHMENT pa WHERE TRIM(pa.POLICY_ID) = CAST(p.ID AS VARCHAR(50)) AND pa.DEL_FLAG = '0' AND (pa.ATTACH_REVIEW_STATUS IS NULL OR pa.ATTACH_REVIEW_STATUS <> '已通过')) ");
        } else if ("未标注".equals(markStatus)) {
            sql.append("AND NOT EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA fm WHERE fm.POLICY_ID = p.ID AND fm.REVIEW_STATUS = '已通过') ")
                .append("AND NOT EXISTS (SELECT 1 FROM SYSDBA.POLICY_ATTACHMENT pa WHERE TRIM(pa.POLICY_ID) = CAST(p.ID AS VARCHAR(50)) AND pa.DEL_FLAG = '0' AND pa.ATTACH_REVIEW_STATUS = '已通过') ");
        } else if ("标注中".equals(markStatus)) {
            sql.append("AND (NOT EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA fm WHERE fm.POLICY_ID = p.ID AND fm.REVIEW_STATUS = '已通过') ")
                .append("OR EXISTS (SELECT 1 FROM SYSDBA.POLICY_ATTACHMENT pa WHERE TRIM(pa.POLICY_ID) = CAST(p.ID AS VARCHAR(50)) AND pa.DEL_FLAG = '0' AND (pa.ATTACH_REVIEW_STATUS IS NULL OR pa.ATTACH_REVIEW_STATUS <> '已通过'))) ")
                .append("AND (EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA fm WHERE fm.POLICY_ID = p.ID AND fm.REVIEW_STATUS = '已通过') ")
                .append("OR EXISTS (SELECT 1 FROM SYSDBA.POLICY_ATTACHMENT pa WHERE TRIM(pa.POLICY_ID) = CAST(p.ID AS VARCHAR(50)) AND pa.DEL_FLAG = '0' AND pa.ATTACH_REVIEW_STATUS = '已通过')) ");
        }

        sql.append("ORDER BY CASE WHEN a.PID IS NOT NULL THEN 0 ELSE 1 END, p.CREATED_AT DESC, p.ID DESC");
        if (limit != null) {
            sql.append(" LIMIT ").append(limit);
        }
        return sql.toString();
    }

    private static int parseLimit(String value) {
        try {
            int limit = Integer.parseInt(value);
            if (limit < 1) {
                throw new IllegalArgumentException("limit 必须大于等于 1");
            }
            return limit;
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException("limit 必须是整数", e);
        }
    }

    private static String normalizePolicyType(String value) {
        String text = nullToEmpty(value).trim();
        return "通知公告".equals(text) ? "申报通知" : text;
    }

    private static boolean isHiddenPolicyAttribute(String value) {
        return "主政策".equals(value) || "主属性".equals(value);
    }

    private static String nullToEmpty(String value) {
        return value == null ? "" : value;
    }

    private static String csv(String value) {
        String text = value == null ? "" : value;
        text = text.replace("\r", " ").replace("\n", " ");
        return "\"" + text.replace("\"", "\"\"") + "\"";
    }
}
