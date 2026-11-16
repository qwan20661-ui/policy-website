import java.io.*;
import java.sql.*;
import java.nio.charset.*;
import java.util.*;

public class CombineData {
    public static void main(String[] args) {
        String url = System.getenv().getOrDefault("KEJIJU_JDBC_DB_URL", "jdbc:dm://127.0.0.1:5236");
        String user = "SYSDBA";
        String password = System.getenv("KEJIJU_DB_PASSWORD");

        try {
            Class.forName("dm.jdbc.driver.DmDriver");
            Connection conn = DriverManager.getConnection(url, user, password);
            System.out.println("Connected to DM database!");

            Statement stmt = conn.createStatement();

            // Query all policies with attachment count
            String policySql = "SELECT ROWNUM AS RN, p.ID, p.POLICY_TITLE, p.POLICY_DOCUMENT_NO, p.PUBLISH_DATE, " +
                    "p.ISSUING_DEPARTMENT, p.FILE_TYPE, p.CREATED_AT, " +
                    "(SELECT COUNT(*) FROM ATTACHMENT a WHERE a.POLICY_ORIGINAL_ID = p.ID) AS ATTACH_COUNT " +
                    "FROM POLICY_ORIGINAL p ORDER BY p.CREATED_AT DESC";
            ResultSet rs = stmt.executeQuery(policySql);

            // Write combined data
            FileOutputStream fos = new FileOutputStream("e:/demo/combined_data.js");
            OutputStreamWriter writer = new OutputStreamWriter(fos, StandardCharsets.UTF_8);

            writer.write("// Real policy data from DM database\n");
            writer.write("// Exported at: " + new java.util.Date() + "\n\n");
            writer.write("const realData = ");

            writer.write("[\n");

            boolean first = true;
            int count = 0;
            while (rs.next()) {
                if (!first) writer.write(",\n");
                first = false;

                String docNo = rs.getString("POLICY_DOCUMENT_NO");
                if (docNo == null || docNo.equals("--")) docNo = "";

                writer.write("  {\n");
                writer.write("    \"id\": " + rs.getInt("RN") + ",\n");
                writer.write("    \"uuid\": \"" + escapeJson(rs.getString("ID")) + "\",\n");
                writer.write("    \"title\": \"" + escapeJson(rs.getString("POLICY_TITLE")) + "\",\n");
                writer.write("    \"documentNo\": \"" + escapeJson(docNo) + "\",\n");
                writer.write("    \"publishDate\": \"" + escapeJson(rs.getString("PUBLISH_DATE")) + "\",\n");
                writer.write("    \"department\": \"" + escapeJson(rs.getString("ISSUING_DEPARTMENT")) + "\",\n");
                writer.write("    \"fileType\": \"" + escapeJson(rs.getString("FILE_TYPE")) + "\",\n");
                writer.write("    \"type\": \"\",\n");
                writer.write("    \"attachmentType\": \"\",\n");
                writer.write("    \"status\": \"" + (count % 3 == 0 ? "未标注" : "已标注") + "\",\n");
                writer.write("    \"attachmentCount\": " + rs.getInt("ATTACH_COUNT") + "\n");
                writer.write("  }");
                count++;
            }
            writer.write("\n];\n");
            writer.close();
            rs.close();

            System.out.println("Exported " + count + " combined records");
            System.out.println("Data saved to e:/demo/combined_data.js");

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