import java.sql.Connection;
import java.sql.DatabaseMetaData;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.util.Properties;

public class DescribeTables {
    public static void main(String[] args) throws Exception {
        if (args.length < 5) {
            throw new IllegalArgumentException("Usage: java DescribeTables <host> <port> <user> <password> <table...>");
        }
        Class.forName("dm.jdbc.driver.DmDriver");
        Properties props = new Properties();
        props.setProperty("user", args[2]);
        props.setProperty("password", args[3]);
        props.setProperty("localTimezone", "Asia/Shanghai");
        props.setProperty("connectTimeout", "10000");
        props.setProperty("socketTimeout", "30000");
        try (Connection conn = DriverManager.getConnection("jdbc:dm://" + args[0] + ":" + args[1], props)) {
            DatabaseMetaData meta = conn.getMetaData();
            for (int i = 4; i < args.length; i++) {
                String table = args[i].toUpperCase();
                System.out.println("TABLE=" + table);
                try (ResultSet rs = meta.getColumns(null, "SYSDBA", table, null)) {
                    while (rs.next()) {
                        System.out.println(
                            rs.getString("COLUMN_NAME") + "\t" +
                            rs.getString("TYPE_NAME") + "\t" +
                            rs.getInt("COLUMN_SIZE") + "\t" +
                            rs.getInt("NULLABLE")
                        );
                    }
                }
            }
        }
    }
}
