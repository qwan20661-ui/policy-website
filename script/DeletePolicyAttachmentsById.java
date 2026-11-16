import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.util.Properties;

public class DeletePolicyAttachmentsById {
    public static void main(String[] args) throws Exception {
        if (args.length < 6) {
            throw new IllegalArgumentException(
                "Usage: java DeletePolicyAttachmentsById <host> <port> <user> <password> <policyId> <attachId...>"
            );
        }
        String host = args[0];
        String port = args[1];
        String user = args[2];
        String password = args[3];
        String policyId = args[4];

        Class.forName("dm.jdbc.driver.DmDriver");
        Properties props = new Properties();
        props.setProperty("user", user);
        props.setProperty("password", password);
        props.setProperty("localTimezone", "Asia/Shanghai");
        props.setProperty("connectTimeout", "5000");
        props.setProperty("socketTimeout", "30000");

        try (Connection conn = DriverManager.getConnection("jdbc:dm://" + host + ":" + port, props);
             PreparedStatement stmt = conn.prepareStatement(
                 "UPDATE SYSDBA.POLICY_ATTACHMENT SET DEL_FLAG='1', UPDATE_AT=CURRENT_TIMESTAMP " +
                 "WHERE TRIM(POLICY_ID)=? AND ATTACH_ID=?")) {
            conn.setAutoCommit(false);
            int updated = 0;
            for (int i = 5; i < args.length; i++) {
                stmt.setString(1, policyId);
                stmt.setString(2, args[i]);
                updated += stmt.executeUpdate();
            }
            conn.commit();
            System.out.println("POLICY_ATTACHMENT soft_deleted=" + updated);
        }
    }
}
