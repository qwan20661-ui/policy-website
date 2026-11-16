import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.util.Properties;

public class QueryPolicyByKeyword {
    public static void main(String[] args) throws Exception {
        if (args.length < 5) {
            throw new IllegalArgumentException("Usage: java QueryPolicyByKeyword <host> <port> <user> <password> <keyword>");
        }
        String host = args[0];
        String port = args[1];
        String user = args[2];
        String password = args[3];
        String keyword = args[4];

        Class.forName("dm.jdbc.driver.DmDriver");
        Properties props = new Properties();
        props.setProperty("user", user);
        props.setProperty("password", password);
        props.setProperty("localTimezone", "Asia/Shanghai");
        props.setProperty("connectTimeout", "5000");
        props.setProperty("socketTimeout", "30000");

        try (Connection conn = DriverManager.getConnection("jdbc:dm://" + host + ":" + port, props);
             PreparedStatement stmt = conn.prepareStatement(
                 "SELECT p.ID, p.POLICY_TITLE, o.POLICY_ID, o.POLICY_TITLE AS ORIGINAL_TITLE, o.FILE_PATH, " +
                 "(SELECT COUNT(*) FROM SYSDBA.POLICY_ATTACHMENT a WHERE TRIM(a.POLICY_ID) = CAST(p.ID AS VARCHAR(50)) AND a.DEL_FLAG = '0') AS ATTACH_COUNT " +
                 "FROM SYSDBA.POLICY p " +
                 "LEFT JOIN SYSDBA.POLICY_ORIGINAL_V2 o ON o.POLICY_ID = CAST(p.ID AS VARCHAR(50)) " +
                 "WHERE p.POLICY_TITLE LIKE ? OR o.POLICY_TITLE LIKE ? " +
                 "ORDER BY p.ID")) {
            String like = "%" + keyword + "%";
            stmt.setString(1, like);
            stmt.setString(2, like);
            try (ResultSet rs = stmt.executeQuery()) {
                while (rs.next()) {
                    System.out.println(
                        "ID=" + rs.getString("ID") +
                        "\tPOLICY_TITLE=" + rs.getString("POLICY_TITLE") +
                        "\tORIGINAL_POLICY_ID=" + rs.getString("POLICY_ID") +
                        "\tORIGINAL_TITLE=" + rs.getString("ORIGINAL_TITLE") +
                        "\tFILE_PATH=" + rs.getString("FILE_PATH") +
                        "\tATTACH_COUNT=" + rs.getInt("ATTACH_COUNT")
                    );
                }
            }
        }
    }
}
