import java.io.BufferedReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.util.Properties;

public class QueryPoliciesBatch {
    public static void main(String[] args) throws Exception {
        if (args.length < 5) {
            throw new IllegalArgumentException("Usage: java QueryPoliciesBatch <titles.txt> <host> <port> <user> <password>");
        }
        Path titles = Path.of(args[0]);
        String host = args[1];
        String port = args[2];
        String user = args[3];
        String password = args[4];

        Class.forName("dm.jdbc.driver.DmDriver");
        Properties props = new Properties();
        props.setProperty("user", user);
        props.setProperty("password", password);
        props.setProperty("localTimezone", "Asia/Shanghai");
        props.setProperty("connectTimeout", "5000");
        props.setProperty("socketTimeout", "30000");

        try (Connection conn = DriverManager.getConnection("jdbc:dm://" + host + ":" + port, props);
             PreparedStatement stmt = conn.prepareStatement(
                 "SELECT p.ID, p.POLICY_TITLE, o.FILE_PATH, o.POLICY_URL " +
                 "FROM SYSDBA.POLICY p " +
                 "LEFT JOIN SYSDBA.POLICY_ORIGINAL_V2 o ON o.POLICY_ID = CAST(p.ID AS VARCHAR(50)) AND NVL(o.DEL_FLAG, '0') = '0' " +
                 "WHERE p.POLICY_TITLE = ? OR o.POLICY_TITLE = ? " +
                 "ORDER BY p.ID")) {
            try (BufferedReader reader = Files.newBufferedReader(titles, StandardCharsets.UTF_8)) {
                String title;
                while ((title = reader.readLine()) != null) {
                    title = title.trim();
                    if (title.isEmpty() || title.startsWith("#")) continue;
                    stmt.setString(1, title);
                    stmt.setString(2, title);
                    boolean found = false;
                    try (ResultSet rs = stmt.executeQuery()) {
                        while (rs.next()) {
                            found = true;
                            System.out.println("FOUND\t" + title + "\t" + rs.getLong("ID") + "\t" + nvl(rs.getString("POLICY_TITLE")) + "\t" + nvl(rs.getString("FILE_PATH")) + "\t" + nvl(rs.getString("POLICY_URL")));
                        }
                    }
                    if (!found) {
                        System.out.println("MISSING\t" + title);
                    }
                }
            }
        }
    }

    private static String nvl(String value) {
        return value == null ? "" : value.replace('\t', ' ');
    }
}
