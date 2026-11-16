import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.util.Properties;

public class UpdatePolicyOriginalContent {
    public static void main(String[] args) throws Exception {
        if (args.length < 8) {
            throw new IllegalArgumentException(
                "Usage: java UpdatePolicyOriginalContent <host> <port> <user> <password> <policyId> <relativeFilePath> <policyUrl> <dryRun>"
            );
        }

        String host = args[0];
        String port = args[1];
        String user = args[2];
        String password = args[3];
        String policyId = args[4];
        String relativeFilePath = args[5].replace('\\', '/');
        String policyUrl = args[6];
        boolean dryRun = Boolean.parseBoolean(args[7]);

        Path contentPath = Path.of(relativeFilePath);
        if (!Files.isRegularFile(contentPath)) {
            throw new IllegalArgumentException("Markdown file not found: " + relativeFilePath);
        }
        String content = Files.readString(contentPath, StandardCharsets.UTF_8);

        Class.forName("dm.jdbc.driver.DmDriver");
        Properties props = new Properties();
        props.setProperty("user", user);
        props.setProperty("password", password);
        props.setProperty("localTimezone", "Asia/Shanghai");
        props.setProperty("connectTimeout", "5000");
        props.setProperty("socketTimeout", "30000");

        try (Connection conn = DriverManager.getConnection("jdbc:dm://" + host + ":" + port, props)) {
            conn.setAutoCommit(false);
            try (PreparedStatement stmt = conn.prepareStatement(
                    "UPDATE SYSDBA.POLICY_ORIGINAL_V2 " +
                    "SET CONTENT = ?, FILE_PATH = ?, FILE_TYPE = 'Markdown', POLICY_URL = ?, UPDATE_AT = CURRENT_TIMESTAMP " +
                    "WHERE POLICY_ID = ? AND DEL_FLAG = '0'")) {
                stmt.setString(1, content);
                stmt.setString(2, fit(relativeFilePath, 500));
                stmt.setString(3, fit(policyUrl, 1000));
                stmt.setString(4, policyId);
                int updated = stmt.executeUpdate();
                if (updated != 1) {
                    conn.rollback();
                    throw new IllegalStateException("Expected to update 1 row, updated=" + updated);
                }
            }

            if (dryRun) {
                conn.rollback();
                System.out.println("DRY_RUN rollback complete");
            } else {
                conn.commit();
                System.out.println("COMMIT complete");
            }
            System.out.println("POLICY_ORIGINAL_V2 updated=1");
        }
    }

    static String fit(String value, int max) {
        if (value == null) return "";
        return value.length() <= max ? value : value.substring(0, max);
    }
}
