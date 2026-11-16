/**
 * 导出融合主政策上下级关系 — 一表一行
 *
 * 每条主政策一行，列出全部上级名称和全部下级名称，没有则留空。
 *
 * 编译运行:
 *   javac -encoding UTF-8 -cp "lib\*;lib\jdbc\*" ExportFusionRelations.java
 *   java  -cp ".;target;lib\*;lib\jdbc\*" ExportFusionRelations
 */

import java.io.*;
import java.sql.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

public class ExportFusionRelations {

    private static final String DB_URL = System.getenv().getOrDefault("KEJIJU_JDBC_DB_URL", "jdbc:dm://127.0.0.1:5236");
    private static final String DB_USER = "SYSDBA";
    private static final String DB_PASSWORD = System.getenv("KEJIJU_DB_PASSWORD");
    private static final String OUTPUT_DIR = "exports";

    public static void main(String[] args) {
        try {
            Class.forName("dm.jdbc.driver.DmDriver");
            try (Connection conn = DriverManager.getConnection(DB_URL, DB_USER, DB_PASSWORD)) {
                System.out.println("✅ 数据库连接成功");
                new File(OUTPUT_DIR).mkdirs();
                exportOneTable(conn);
                System.out.println("\n✅ 导出完成! 文件: " + new File(OUTPUT_DIR, "融合政策_上下级关系表.csv").getAbsolutePath());
            }
        } catch (Exception e) {
            System.err.println("❌ 错误: " + e.getMessage());
            e.printStackTrace();
        }
    }

    static void exportOneTable(Connection conn) throws Exception {
        // 查询所有主政策
        Map<String, Map<String, String>> allPolicies = queryAllPolicies(conn);

        // 查询所有有效父子关系
        List<Map<String, String>> relations = queryRelations(conn);

        // 构建 父→子 & 子→父 映射
        Map<String, List<Map<String, String>>> childrenByParent = new LinkedHashMap<>();
        Map<String, List<Map<String, String>>> parentsByChild = new LinkedHashMap<>();

        for (Map<String, String> rel : relations) {
            String pid = rel.get("parentId");
            String cid = rel.get("childId");
            childrenByParent.computeIfAbsent(pid, k -> new ArrayList<>()).add(rel);
            parentsByChild.computeIfAbsent(cid, k -> new ArrayList<>()).add(rel);
        }

        try (PrintWriter pw = newCsvWriter("融合政策_上下级关系表.csv")) {
            // 表头
            pw.println("序号,主政策ID,主政策名称(主文件标题),目录名称,审核状态,直属上级政策名称,直属下级政策名称");

            int seq = 0;
            for (Map.Entry<String, Map<String, String>> entry : allPolicies.entrySet()) {
                seq++;
                String policyId = entry.getKey();
                Map<String, String> policy = entry.getValue();
                String title = getDisplayTitle(policy);
                String dirTitle = policy.getOrDefault("directoryTitle", "");
                String reviewStatus = policy.getOrDefault("reviewStatus", "");

                // 获取所有直属上级的名称，用中文分号拼接
                List<Map<String, String>> parents = parentsByChild.getOrDefault(policyId, Collections.emptyList());
                List<String> parentNames = new ArrayList<>();
                for (Map<String, String> p : parents) {
                    String pt = getDisplayTitle(allPolicies.get(p.get("parentId")));
                    if (pt != null && !pt.isEmpty()) parentNames.add(pt);
                }
                String parentText = parentNames.isEmpty() ? "" : String.join("；", parentNames);

                // 获取所有直属下级的名称，用中文分号拼接
                List<Map<String, String>> children = childrenByParent.getOrDefault(policyId, Collections.emptyList());
                List<String> childNames = new ArrayList<>();
                for (Map<String, String> c : children) {
                    String ct = getDisplayTitle(allPolicies.get(c.get("childId")));
                    if (ct != null && !ct.isEmpty()) childNames.add(ct);
                }
                String childText = childNames.isEmpty() ? "" : String.join("；", childNames);

                // 一行一条主政策，多个上级/下级用中文分号隔开放在同一个单元格
                pw.println(seq + ","
                    + csvEscape(policyId) + ","
                    + csvEscape(title) + ","
                    + csvEscape(dirTitle) + ","
                    + csvEscape(reviewStatus) + ","
                    + csvEscape(parentText) + ","
                    + csvEscape(childText));
            }
        }

        System.out.println("  💡 多个上级或下级政策名称用中文分号(；)隔开");
        System.out.println("  💡 无上下级的主政策，对应列留空");
    }

    static Map<String, Map<String, String>> queryAllPolicies(Connection conn) throws SQLException {
        Map<String, Map<String, String>> result = new LinkedHashMap<>();
        String sql = "SELECT ID, MAIN_FILE_TITLE, DIRECTORY_TITLE, MARK_REVIEW_STATUS, DEL_FLAG "
            + "FROM SYSDBA.X1_FUSION_POLICY "
            + "WHERE TRIM(NVL(DEL_FLAG,'0')) = '0' "  // 只导未删除的，和页面一致
            + "ORDER BY ID";
        try (Statement stmt = conn.createStatement(); ResultSet rs = stmt.executeQuery(sql)) {
            while (rs.next()) {
                Map<String, String> p = new LinkedHashMap<>();
                String id = nullToEmpty(rs.getString("ID"));
                p.put("mainFileTitle", nullToEmpty(rs.getString("MAIN_FILE_TITLE")));
                p.put("directoryTitle", nullToEmpty(rs.getString("DIRECTORY_TITLE")));
                p.put("reviewStatus", nullToEmpty(rs.getString("MARK_REVIEW_STATUS")));
                p.put("delFlag", nullToEmpty(rs.getString("DEL_FLAG")));
                result.put(id, p);
            }
        }
        System.out.println("  📊 查询到主政策总数: " + result.size() + " 条");
        return result;
    }

    static List<Map<String, String>> queryRelations(Connection conn) throws SQLException {
        List<Map<String, String>> result = new ArrayList<>();
        String sql = "SELECT r.PARENT_FUSION_POLICY_ID, r.CHILD_FUSION_POLICY_ID, r.DEL_FLAG "
            + "FROM SYSDBA.X1_FUSION_POLICY_RELATION r "
            + "WHERE r.DEL_FLAG = '0' "
            + "ORDER BY r.PARENT_FUSION_POLICY_ID, r.CHILD_FUSION_POLICY_ID";
        try (Statement stmt = conn.createStatement(); ResultSet rs = stmt.executeQuery(sql)) {
            while (rs.next()) {
                Map<String, String> rel = new LinkedHashMap<>();
                rel.put("parentId", nullToEmpty(rs.getString("PARENT_FUSION_POLICY_ID")));
                rel.put("childId", nullToEmpty(rs.getString("CHILD_FUSION_POLICY_ID")));
                result.add(rel);
            }
        }
        System.out.println("  📊 查询到有效父子关系: " + result.size() + " 条");
        return result;
    }

    static String getDisplayTitle(Map<String, String> policy) {
        if (policy == null) return "-";
        String mft = policy.getOrDefault("mainFileTitle", "").trim();
        String dt = policy.getOrDefault("directoryTitle", "").trim();
        if (!mft.isEmpty()) return mft;
        if (!dt.isEmpty()) return dt;
        return "(无标题)";
    }

    static PrintWriter newCsvWriter(String filename) throws IOException {
        File file = new File(OUTPUT_DIR, filename);
        FileOutputStream fos = new FileOutputStream(file);
        fos.write(new byte[]{(byte)0xEF, (byte)0xBB, (byte)0xBF}); // UTF-8 BOM
        OutputStreamWriter osw = new OutputStreamWriter(fos, StandardCharsets.UTF_8);
        return new PrintWriter(new BufferedWriter(osw));
    }

    static String csvEscape(String s) {
        if (s == null || s.isEmpty()) return "";
        if (s.contains(",") || s.contains("\"") || s.contains("\n") || s.contains("\r")) {
            return "\"" + s.replace("\"", "\"\"") + "\"";
        }
        return s;
    }

    static String nullToEmpty(String s) {
        return s == null ? "" : s.trim();
    }
}
