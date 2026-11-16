import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.util.Properties;

public class QueryFusionRow {
    public static void main(String[] args) throws Exception {
        if (args.length < 5) {
            throw new IllegalArgumentException("Usage: java QueryFusionRow <host> <port> <user> <password> <fusionId...>");
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
             PreparedStatement fusion = conn.prepareStatement(
                 "SELECT ID,SOURCE_POLICY_ID,DIRECTORY_TITLE,MAIN_FILE_TITLE,MAIN_FILE_PATH,STATUS,SORT_NO " +
                 "FROM SYSDBA.X1_FUSION_POLICY WHERE ID=? AND DEL_FLAG='0'");
             PreparedStatement docs = conn.prepareStatement(
                 "SELECT ID,FUSION_POLICY_ID,SOURCE_POLICY_ID,POLICY_ATTRIBUTE,POLICY_TITLE,FILE_NAME,FILE_PATH,FILE_SUFFIX,DISPLAY_ORDER " +
                 "FROM SYSDBA.X1_POLICY_DOCUMENT WHERE FUSION_POLICY_ID=? AND DEL_FLAG='0' ORDER BY DISPLAY_ORDER,ID")) {
            for (int i = 4; i < args.length; i++) {
                String id = args[i];
                System.out.println("FUSION_ID=" + id);
                fusion.setString(1, id);
                try (ResultSet rs = fusion.executeQuery()) {
                    if (rs.next()) {
                        System.out.println(
                            "ROW\tSOURCE_POLICY_ID=" + rs.getString("SOURCE_POLICY_ID") +
                            "\tDIRECTORY_TITLE=" + rs.getString("DIRECTORY_TITLE") +
                            "\tMAIN_FILE_TITLE=" + rs.getString("MAIN_FILE_TITLE") +
                            "\tMAIN_FILE_PATH=" + rs.getString("MAIN_FILE_PATH") +
                            "\tSTATUS=" + rs.getString("STATUS") +
                            "\tSORT_NO=" + rs.getString("SORT_NO")
                        );
                    } else {
                        System.out.println("ROW\tNOT_FOUND");
                    }
                }
                docs.setString(1, id);
                try (ResultSet rs = docs.executeQuery()) {
                    int count = 0;
                    while (rs.next()) {
                        count++;
                        System.out.println(
                            "DOC\tID=" + rs.getString("ID") +
                            "\tSOURCE_POLICY_ID=" + rs.getString("SOURCE_POLICY_ID") +
                            "\tTYPE=" + rs.getString("POLICY_ATTRIBUTE") +
                            "\tTITLE=" + rs.getString("POLICY_TITLE") +
                            "\tFILE=" + rs.getString("FILE_NAME") +
                            "\tPATH=" + rs.getString("FILE_PATH") +
                            "\tSUFFIX=" + rs.getString("FILE_SUFFIX") +
                            "\tORDER=" + rs.getString("DISPLAY_ORDER")
                        );
                    }
                    System.out.println("DOC_COUNT=" + count);
                }
            }
        }
    }
}
