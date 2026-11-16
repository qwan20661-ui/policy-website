import java.sql.*;
import java.util.Properties;

/**
 * 查询指定达梦表的字段结构信息（只读）
 * Usage: java DescribeTable <TABLE_NAME>
 */
public class DescribeTable {
    public static void main(String[] args) throws Exception {
        if (args.length < 1) {
            System.err.println("Usage: java DescribeTable <TABLE_NAME>");
            System.exit(1);
        }
        String user = "SYSDBA", password = System.getenv("KEJIJU_DB_PASSWORD");
        Class.forName("dm.jdbc.driver.DmDriver");
        Properties props = new Properties();
        props.setProperty("user", user);
        props.setProperty("password", password);
        try (Connection conn = DriverManager.getConnection(System.getenv().getOrDefault("KEJIJU_JDBC_DB_URL", "jdbc:dm://127.0.0.1:5236"), props)) {
            String table = args[0].toUpperCase();
            DatabaseMetaData meta = conn.getMetaData();
            System.out.println("表名: " + table);
            System.out.println("序号\t字段名\t类型\t长度\t可空\t默认值\t说明");
            int i = 0;
            try (ResultSet rs = meta.getColumns(null, "SYSDBA", table, null)) {
                while (rs.next()) {
                    i++;
                    String colName = rs.getString("COLUMN_NAME");
                    String typeName = rs.getString("TYPE_NAME");
                    int colSize = rs.getInt("COLUMN_SIZE");
                    int nullable = rs.getInt("NULLABLE");
                    String defVal = rs.getString("COLUMN_DEF");
                    String remarks = rs.getString("REMARKS");
                    System.out.printf("%d\t%s\t%s\t%d\t%s\t%s\t%s%n",
                        i, colName, typeName, colSize,
                        nullable == 1 ? "Y" : "N",
                        defVal != null ? defVal : "",
                        remarks != null ? remarks : ""
                    );
                }
            }
            System.out.println("\n共计: " + i + " 个字段");
        }
    }
}
