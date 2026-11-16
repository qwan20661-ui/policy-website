import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.util.Properties;

public class FusionMissingAudit {
    public static void main(String[] args) throws Exception {
        if (args.length < 5) {
            throw new IllegalArgumentException("Usage: java FusionMissingAudit <host> <port> <user> <password> <fusionId...>");
        }
        String host = args[0], port = args[1], user = args[2], password = args[3];
        Class.forName("dm.jdbc.driver.DmDriver");
        Properties props = new Properties();
        props.setProperty("user", user);
        props.setProperty("password", password);
        props.setProperty("localTimezone", "Asia/Shanghai");
        props.setProperty("connectTimeout", "5000");
        props.setProperty("socketTimeout", "30000");
        try (Connection conn = DriverManager.getConnection("jdbc:dm://" + host + ":" + port, props);
             PreparedStatement fusion = conn.prepareStatement(
                 "SELECT ID,DIRECTORY_TITLE FROM SYSDBA.X1_FUSION_POLICY WHERE ID=? AND DEL_FLAG='0'");
             PreparedStatement docs = conn.prepareStatement(
                 "SELECT POLICY_ATTRIBUTE,COUNT(*) C FROM SYSDBA.X1_POLICY_DOCUMENT WHERE FUSION_POLICY_ID=? AND DEL_FLAG='0' GROUP BY POLICY_ATTRIBUTE ORDER BY POLICY_ATTRIBUTE");
             PreparedStatement sourceDocs = conn.prepareStatement(
                 "SELECT d.SOURCE_POLICY_ID,d.POLICY_ATTRIBUTE,d.POLICY_TITLE," +
                 "(SELECT COUNT(*) FROM SYSDBA.POLICY_ATTACHMENT a WHERE TRIM(a.POLICY_ID)=TRIM(d.SOURCE_POLICY_ID) AND a.DEL_FLAG='0') AS ATTACH_COUNT " +
                 "FROM SYSDBA.X1_POLICY_DOCUMENT d WHERE d.FUSION_POLICY_ID=? AND d.DEL_FLAG='0' AND d.SOURCE_POLICY_ID IS NOT NULL ORDER BY d.DISPLAY_ORDER,d.ID")) {
            for (int i = 4; i < args.length; i++) {
                String id = args[i];
                fusion.setString(1, id);
                String title = "";
                try (ResultSet rs = fusion.executeQuery()) {
                    if (rs.next()) title = rs.getString("DIRECTORY_TITLE");
                }
                System.out.println("FUSION\t" + id + "\t" + title);
                docs.setString(1, id);
                try (ResultSet rs = docs.executeQuery()) {
                    while (rs.next()) System.out.println("TYPE_COUNT\t" + rs.getString("POLICY_ATTRIBUTE") + "\t" + rs.getInt("C"));
                }
                sourceDocs.setString(1, id);
                try (ResultSet rs = sourceDocs.executeQuery()) {
                    while (rs.next()) {
                        String type = rs.getString("POLICY_ATTRIBUTE");
                        int attach = rs.getInt("ATTACH_COUNT");
                        if ("申报通知".equals(type) || "结果公告".equals(type) || attach == 0) {
                            System.out.println("SOURCE\t" + rs.getString("SOURCE_POLICY_ID") + "\t" + type + "\tattach=" + attach + "\t" + rs.getString("POLICY_TITLE"));
                        }
                    }
                }
            }
        }
    }
}
