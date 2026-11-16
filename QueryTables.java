import java.sql.*;
import java.util.Properties;
public class QueryTables {
    public static void main(String[] args) throws Exception {
        Class.forName("dm.jdbc.driver.DmDriver");
        Properties props = new Properties();
        props.setProperty("user", "SYSDBA");
        props.setProperty("password", System.getenv("KEJIJU_DB_PASSWORD"));
        props.setProperty("localTimezone", "Asia/Shanghai");
        Connection conn = DriverManager.getConnection(System.getenv().getOrDefault("KEJIJU_JDBC_DB_URL", "jdbc:dm://127.0.0.1:5236"), props);
        System.out.println("OK, 连接成功");
        Statement stmt = conn.createStatement();
        ResultSet rs = stmt.executeQuery("SELECT TABLE_NAME FROM ALL_TABLES WHERE OWNER='SYSDBA' ORDER BY TABLE_NAME");
        int n = 0;
        while (rs.next()) System.out.println("  " + (++n) + ". " + rs.getString("TABLE_NAME"));
        rs.close(); stmt.close();
        System.out.println("\n共计: " + n + " 张表");
        conn.close();
    }
}
