import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.util.Properties;

public class ExportPolicyOriginalForIds {
    public static void main(String[] args) throws Exception {
        if (args.length < 5) {
            throw new IllegalArgumentException("Usage: java ExportPolicyOriginalForIds <host> <port> <user> <password> <policyId...>");
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
             PreparedStatement stmt = conn.prepareStatement(
                 "SELECT p.ID,p.POLICY_TITLE," +
                 "(SELECT o.FILE_PATH FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=CAST(p.ID AS VARCHAR(50)) AND ROWNUM=1) AS FILE_PATH," +
                 "(SELECT o.POLICY_URL FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=CAST(p.ID AS VARCHAR(50)) AND ROWNUM=1) AS POLICY_URL," +
                 "(SELECT MAX(TRIM(m.POLICY_ATTRIBUTE)) FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID=p.ID) AS POLICY_ATTRIBUTE " +
                 "FROM SYSDBA.POLICY p WHERE p.ID=?")) {
            System.out.println("POLICY_ID\tPOLICY_TITLE\tFILE_PATH\tPOLICY_URL\tPOLICY_ATTRIBUTE");
            for (int i = 4; i < args.length; i++) {
                stmt.setString(1, args[i]);
                try (ResultSet rs = stmt.executeQuery()) {
                    if (rs.next()) {
                        System.out.println(clean(rs.getString("ID")) + "\t" +
                            clean(rs.getString("POLICY_TITLE")) + "\t" +
                            clean(rs.getString("FILE_PATH")) + "\t" +
                            clean(rs.getString("POLICY_URL")) + "\t" +
                            clean(rs.getString("POLICY_ATTRIBUTE")));
                    }
                }
            }
        }
    }

    static String clean(String s) {
        if (s == null) return "";
        return s.replace("\t", " ").replace("\r", " ").replace("\n", " ");
    }
}
