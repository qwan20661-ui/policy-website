import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.util.Properties;

public class NormalizeDocs1FilePaths20260720 {
    public static void main(String[] args) throws Exception {
        if (args.length < 5) {
            throw new IllegalArgumentException("Usage: java NormalizeDocs1FilePaths20260720 <host> <port> <user> <password> <dryRun>");
        }
        String host = args[0];
        String port = args[1];
        String user = args[2];
        String password = args[3];
        boolean dryRun = Boolean.parseBoolean(args[4]);

        Class.forName("dm.jdbc.driver.DmDriver");
        Properties props = new Properties();
        props.setProperty("user", user);
        props.setProperty("password", password);
        props.setProperty("localTimezone", "Asia/Shanghai");
        props.setProperty("connectTimeout", "5000");
        props.setProperty("socketTimeout", "30000");

        try (Connection conn = DriverManager.getConnection("jdbc:dm://" + host + ":" + port, props)) {
            conn.setAutoCommit(false);
            printSamples(conn, "POLICY_ORIGINAL_V2", "POLICY_ID");
            printSamples(conn, "POLICY_ATTACHMENT", "POLICY_ID");

            int originalUpdated = updateTable(conn, "POLICY_ORIGINAL_V2");
            int attachmentUpdated = updateTable(conn, "POLICY_ATTACHMENT");

            if (dryRun) {
                conn.rollback();
                System.out.println("DRY_RUN rollback complete");
            } else {
                conn.commit();
                System.out.println("COMMIT complete");
            }
            System.out.println("POLICY_ORIGINAL_V2 updated=" + originalUpdated);
            System.out.println("POLICY_ATTACHMENT updated=" + attachmentUpdated);
        }
    }

    static void printSamples(Connection conn, String table, String idColumn) throws Exception {
        try (PreparedStatement ps = conn.prepareStatement(
                "SELECT " + idColumn + ", FILE_PATH FROM SYSDBA." + table + " " +
                "WHERE FILE_PATH LIKE 'E:/demo/docs1/%' OR FILE_PATH LIKE 'E:\\\\demo\\\\docs1\\\\%' " +
                "ORDER BY UPDATE_AT DESC")) {
            try (ResultSet rs = ps.executeQuery()) {
                int count = 0;
                while (rs.next() && count < 5) {
                    count++;
                    System.out.println("SAMPLE\t" + table + "\t" + rs.getString(1) + "\t" + rs.getString(2));
                }
                if (count == 0) {
                    System.out.println("SAMPLE\t" + table + "\tNONE");
                }
            }
        }
    }

    static int updateTable(Connection conn, String table) throws Exception {
        try (PreparedStatement ps = conn.prepareStatement(
                "UPDATE SYSDBA." + table + " " +
                "SET FILE_PATH = REPLACE(REPLACE(FILE_PATH, 'E:/demo/docs1/', 'docs1/'), 'E:\\\\demo\\\\docs1\\\\', 'docs1/'), " +
                "UPDATE_AT = CURRENT_TIMESTAMP " +
                "WHERE FILE_PATH LIKE 'E:/demo/docs1/%' OR FILE_PATH LIKE 'E:\\\\demo\\\\docs1\\\\%'")) {
            return ps.executeUpdate();
        }
    }
}
