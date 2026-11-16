import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.util.Properties;

public class QueryPolicyAttachments {
    public static void main(String[] args) throws Exception {
        if (args.length < 5) {
            throw new IllegalArgumentException("Usage: java QueryPolicyAttachments <host> <port> <user> <password> <policyId...>");
        }
        String host = args[0];
        String port = args[1];
        String user = args[2];
        String password = args[3];

        Class.forName("dm.jdbc.driver.DmDriver");
        Properties props = new Properties();
        props.setProperty("user", user);
        props.setProperty("password", password);
        props.setProperty("localTimezone", "Asia/Shanghai");
        props.setProperty("connectTimeout", "5000");
        props.setProperty("socketTimeout", "30000");

        try (Connection conn = DriverManager.getConnection("jdbc:dm://" + host + ":" + port, props);
             PreparedStatement stmt = conn.prepareStatement(
                 "SELECT ATTACH_ID, POLICY_ID, ATTACH_NAME, FILE_PATH, FILE_SUFFIX, ATTACH_TYPE, ATTACH_REVIEW_STATUS " +
                 "FROM SYSDBA.POLICY_ATTACHMENT WHERE TRIM(POLICY_ID)=? AND DEL_FLAG='0' ORDER BY CREATE_AT")) {
            for (int i = 4; i < args.length; i++) {
                String policyId = args[i];
                System.out.println("POLICY_ID=" + policyId);
                stmt.setString(1, policyId);
                try (ResultSet rs = stmt.executeQuery()) {
                    int count = 0;
                    while (rs.next()) {
                        count++;
                        System.out.println(
                            rs.getString("ATTACH_ID") + "\t" +
                            rs.getString("ATTACH_NAME") + "\t" +
                            rs.getString("FILE_SUFFIX") + "\t" +
                            rs.getString("ATTACH_TYPE") + "\t" +
                            rs.getString("ATTACH_REVIEW_STATUS") + "\t" +
                            rs.getString("FILE_PATH")
                        );
                    }
                    System.out.println("COUNT=" + count);
                }
            }
        }
    }
}
