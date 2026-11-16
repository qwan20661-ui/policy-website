import java.io.*;
import java.sql.*;
import java.nio.charset.*;

public class ExportData {
    public static void main(String[] args) {
        String url = System.getenv().getOrDefault("KEJIJU_JDBC_DB_URL", "jdbc:dm://127.0.0.1:5236");
        String user = "SYSDBA";
        String password = System.getenv("KEJIJU_DB_PASSWORD");

        try {
            // Load DM JDBC driver
            Class.forName("dm.jdbc.driver.DmDriver");

            // Connect to database
            Connection conn = DriverManager.getConnection(url, user, password);
            System.out.println("Connected to DM database successfully!");

            // Query POLICY_ORIGINAL
            String policySql = "SELECT ID, POLICY_TITLE, POLICY_DOCUMENT_NO, PUBLISH_DATE, ISSUING_DEPARTMENT, FILE_TYPE, CREATED_AT FROM POLICY_ORIGINAL ORDER BY CREATED_AT DESC";
            Statement stmt = conn.createStatement();
            ResultSet rs = stmt.executeQuery(policySql);

            // Write to file
            FileOutputStream fos = new FileOutputStream("e:/demo/policy_data.json");
            OutputStreamWriter writer = new OutputStreamWriter(fos, StandardCharsets.UTF_8);
            writer.write("[\n");

            boolean first = true;
            int count = 0;
            while (rs.next() && count < 100) {
                if (!first) writer.write(",\n");
                first = false;

                writer.write("  {\n");
                writer.write("    \"id\": \"" + escapeJson(rs.getString("ID")) + "\",\n");
                writer.write("    \"title\": \"" + escapeJson(rs.getString("POLICY_TITLE")) + "\",\n");
                writer.write("    \"documentNo\": \"" + escapeJson(rs.getString("POLICY_DOCUMENT_NO")) + "\",\n");
                writer.write("    \"publishDate\": \"" + escapeJson(rs.getString("PUBLISH_DATE")) + "\",\n");
                writer.write("    \"department\": \"" + escapeJson(rs.getString("ISSUING_DEPARTMENT")) + "\",\n");
                writer.write("    \"fileType\": \"" + escapeJson(rs.getString("FILE_TYPE")) + "\",\n");
                writer.write("    \"createdAt\": \"" + escapeJson(rs.getString("CREATED_AT")) + "\"\n");
                writer.write("  }");
                count++;
            }
            writer.write("\n]");
            writer.close();
            rs.close();
            System.out.println("Exported " + count + " policy records");

            // Query ATTACHMENT
            String attachSql = "SELECT ID, POLICY_ORIGINAL_ID, ATTACHMENT_NAME, FILE_PATH, CREATED_AT FROM ATTACHMENT ORDER BY CREATED_AT DESC";
            rs = stmt.executeQuery(attachSql);

            fos = new FileOutputStream("e:/demo/attachment_data.json");
            writer = new OutputStreamWriter(fos, StandardCharsets.UTF_8);
            writer.write("[\n");

            first = true;
            count = 0;
            while (rs.next() && count < 100) {
                if (!first) writer.write(",\n");
                first = false;

                writer.write("  {\n");
                writer.write("    \"id\": \"" + escapeJson(rs.getString("ID")) + "\",\n");
                writer.write("    \"policyId\": \"" + escapeJson(rs.getString("POLICY_ORIGINAL_ID")) + "\",\n");
                writer.write("    \"name\": \"" + escapeJson(rs.getString("ATTACHMENT_NAME")) + "\",\n");
                writer.write("    \"filePath\": \"" + escapeJson(rs.getString("FILE_PATH")) + "\",\n");
                writer.write("    \"createdAt\": \"" + escapeJson(rs.getString("CREATED_AT")) + "\"\n");
                writer.write("  }");
                count++;
            }
            writer.write("\n]");
            writer.close();
            rs.close();

            System.out.println("Exported " + count + " attachment records");
            System.out.println("Data exported to e:/demo/policy_data.json and e:/demo/attachment_data.json");

            stmt.close();
            conn.close();
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    private static String escapeJson(String s) {
        if (s == null) return "";
        return s.replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t");
    }
}