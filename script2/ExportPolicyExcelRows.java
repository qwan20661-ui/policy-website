import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.util.Properties;

public class ExportPolicyExcelRows {
    public static void main(String[] args) throws Exception {
        if (args.length < 4) {
            throw new IllegalArgumentException("用法: java ExportPolicyExcelRows <host> <port> <user> <password> [status]");
        }
        String host = args[0];
        String port = args[1];
        String user = args[2];
        String password = args[3];
        String markStatus = args.length >= 5 ? nullToEmpty(args[4]).trim() : "";

        Class.forName("dm.jdbc.driver.DmDriver");

        Properties props = new Properties();
        props.setProperty("user", user);
        props.setProperty("password", password);
        props.setProperty("localTimezone", "Asia/Shanghai");
        props.setProperty("connectTimeout", "5000");
        props.setProperty("socketTimeout", "30000");

        try (Connection conn = DriverManager.getConnection("jdbc:dm://" + host + ":" + port, props);
             PreparedStatement stmt = conn.prepareStatement(buildSql(markStatus));
             ResultSet rs = stmt.executeQuery()) {
            System.out.println("policy_id,policy_title,policy_attribute,has_attachment,attachment_count,issuing_department");
            while (rs.next()) {
                String policyAttribute = normalizePolicyType(rs.getString("POLICY_ATTRIBUTE"));
                if (isHiddenPolicyAttribute(policyAttribute)) {
                    // 隐藏属性是否导出交由上层脚本决定，这里统一输出完整结果。
                }
                int attachmentCount = rs.getInt("ATTACHMENT_COUNT");
                String metadataDepartment = nullToEmpty(rs.getString("METADATA_DEPARTMENT"));
                String originalDepartment = nullToEmpty(rs.getString("ISSUE_DEPARTMENT"));
                String displayDepartment = metadataDepartment.isEmpty() ? originalDepartment : metadataDepartment;
                System.out.println(
                    csv(String.valueOf(rs.getLong("ID"))) + "," +
                    csv(nullToEmpty(rs.getString("POLICY_TITLE"))) + "," +
                    csv(policyAttribute) + "," +
                    csv(attachmentCount > 0 ? "是" : "否") + "," +
                    csv(String.valueOf(attachmentCount)) + "," +
                    csv(displayDepartment)
                );
            }
        }
    }

    private static String buildSql(String markStatus) {
        StringBuilder sql = new StringBuilder();
        sql.append("SELECT ")
            .append("    p.ID, ")
            .append("    p.POLICY_TITLE, ")
            .append("    p.CREATED_AT, ")
            .append("    (SELECT o.ISSUE_DEPARTMENT FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID = CAST(p.ID AS VARCHAR(50)) AND ROWNUM = 1) AS ISSUE_DEPARTMENT, ")
            .append("    (SELECT MAX(TRIM(m.ISSUING_DEPARTMENT)) FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID = p.ID) AS METADATA_DEPARTMENT, ")
            .append("    (SELECT MAX(TRIM(m.POLICY_ATTRIBUTE)) FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID = p.ID) AS POLICY_ATTRIBUTE, ")
            .append("    (SELECT COUNT(*) FROM SYSDBA.POLICY_ATTACHMENT pa WHERE TRIM(pa.POLICY_ID) = CAST(p.ID AS VARCHAR(50)) AND pa.DEL_FLAG = '0') AS ATTACHMENT_COUNT ")
            .append("FROM SYSDBA.POLICY p ")
            .append("LEFT JOIN (SELECT DISTINCT TRIM(POLICY_ID) AS PID FROM SYSDBA.POLICY_ATTACHMENT WHERE DEL_FLAG = '0') a ")
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
        return sql.toString();
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
