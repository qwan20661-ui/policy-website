import java.io.BufferedWriter;
import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class PrecheckPolicyTitleMatches {
    private static final String DB_URL = System.getenv().getOrDefault("KEJIJU_JDBC_DB_URL", "jdbc:dm://127.0.0.1:5236");
    private static final String DB_USER = "SYSDBA";
    private static final String DB_PASSWORD = System.getenv("KEJIJU_DB_PASSWORD");

    private static final Pattern MAIN_RE = Pattern.compile("^###\\s+(\\d+)\\.\\s+(.+?)\\s*$");
    private static final Pattern DOC_RE = Pattern.compile("^\\d+\\.\\s+\\[(指导政策|实施细则|申报通知|结果公告)\\]\\s+(.+?)(?:（发布时间：([0-9]{4}-[0-9]{2}-[0-9]{2})）)?\\s*$");

    private static final class MdDoc {
        int mainNo;
        String mainTitle;
        String type;
        String title;
        int lineNo;
    }

    private static final class DbHit {
        String policyId;
        String policyTitle;
        String originalTitle;
        String publishDate;
        String filePath;
        String fileType;
    }

    public static void main(String[] args) throws Exception {
        Path mdPath = findMd();
        List<MdDoc> docs = parseMd(mdPath);
        LinkedHashMap<String, List<MdDoc>> docsByTitle = groupByTitle(docs);

        Class.forName("dm.jdbc.driver.DmDriver");
        Properties props = new Properties();
        props.setProperty("user", DB_USER);
        props.setProperty("password", DB_PASSWORD);
        try (Connection conn = DriverManager.getConnection(DB_URL, props)) {
            Map<String, List<DbHit>> hitsByTitle = queryHits(conn, docsByTitle.keySet());
            writeReport(mdPath, docs, docsByTitle, hitsByTitle);
        }
    }

    private static Path findMd() throws IOException {
        try {
            return Files.list(Paths.get("."))
                .filter(p -> {
                    String name = p.getFileName().toString();
                    return name.startsWith("8.4") && name.endsWith(".md");
                })
                .findFirst()
                .orElseThrow(() -> new IOException("8.4*.md not found"));
        } catch (IOException e) {
            throw e;
        }
    }

    private static List<MdDoc> parseMd(Path mdPath) throws IOException {
        List<String> lines = Files.readAllLines(mdPath, StandardCharsets.UTF_8);
        List<MdDoc> docs = new ArrayList<>();
        int currentNo = -1;
        String currentTitle = "";
        for (int i = 0; i < lines.size(); i++) {
            String line = lines.get(i);
            Matcher main = MAIN_RE.matcher(line);
            if (main.matches()) {
                currentNo = Integer.parseInt(main.group(1));
                currentTitle = main.group(2).trim();
                continue;
            }
            Matcher doc = DOC_RE.matcher(line);
            if (doc.matches() && currentNo > 0) {
                MdDoc item = new MdDoc();
                item.mainNo = currentNo;
                item.mainTitle = currentTitle;
                item.type = doc.group(1).trim();
                item.title = doc.group(2).trim();
                item.lineNo = i + 1;
                docs.add(item);
            }
        }
        return docs;
    }

    private static LinkedHashMap<String, List<MdDoc>> groupByTitle(List<MdDoc> docs) {
        LinkedHashMap<String, List<MdDoc>> grouped = new LinkedHashMap<>();
        for (MdDoc doc : docs) {
            grouped.computeIfAbsent(doc.title, k -> new ArrayList<>()).add(doc);
        }
        return grouped;
    }

    private static Map<String, List<DbHit>> queryHits(Connection conn, Set<String> titles) throws SQLException {
        Map<String, List<DbHit>> result = new LinkedHashMap<>();
        String sql =
            "SELECT CAST(p.ID AS VARCHAR(50)) AS POLICY_ID, p.POLICY_TITLE, " +
            "       o.POLICY_TITLE AS ORIGINAL_TITLE, o.PUBLISH_DATE, o.FILE_PATH, o.FILE_TYPE " +
            "FROM SYSDBA.POLICY p " +
            "LEFT JOIN SYSDBA.POLICY_ORIGINAL_V2 o ON o.POLICY_ID = CAST(p.ID AS VARCHAR(50)) " +
            "WHERE TRIM(p.POLICY_TITLE) = ? " +
            "   OR TRIM(o.POLICY_TITLE) = ? " +
            "ORDER BY CAST(p.ID AS VARCHAR(50)), o.PUBLISH_DATE, o.FILE_PATH";
        try (PreparedStatement ps = conn.prepareStatement(sql)) {
            for (String title : titles) {
                ps.setString(1, title);
                ps.setString(2, title);
                List<DbHit> hits = new ArrayList<>();
                try (ResultSet rs = ps.executeQuery()) {
                    while (rs.next()) {
                        DbHit hit = new DbHit();
                        hit.policyId = nvl(rs.getString("POLICY_ID"));
                        hit.policyTitle = nvl(rs.getString("POLICY_TITLE"));
                        hit.originalTitle = nvl(rs.getString("ORIGINAL_TITLE"));
                        hit.publishDate = nvl(rs.getString("PUBLISH_DATE"));
                        hit.filePath = nvl(rs.getString("FILE_PATH"));
                        hit.fileType = nvl(rs.getString("FILE_TYPE"));
                        hits.add(hit);
                    }
                }
                result.put(title, hits);
            }
        }
        return result;
    }

    private static void writeReport(Path mdPath, List<MdDoc> docs, LinkedHashMap<String, List<MdDoc>> docsByTitle, Map<String, List<DbHit>> hitsByTitle) throws IOException {
        Path out = Paths.get("tmp", "policy_title_match_precheck.md");
        Files.createDirectories(out.getParent());
        int uniqueTitleCount = docsByTitle.size();
        int mdDuplicateTitleGroups = 0;
        int mdDuplicateRows = 0;
        int unmatchedTitleGroups = 0;
        int uniqueHitTitleGroups = 0;
        int multiHitTitleGroups = 0;
        int totalDbRows = 0;
        int sameMainPolicyIdDuplicateGroups = 0;
        int sameMainPolicyIdDuplicateRows = 0;
        int ambiguousRows = 0;
        int unmatchedRows = 0;
        int uniqueRows = 0;

        Map<String, List<MdDoc>> fullyDuplicateGroups = new LinkedHashMap<>();
        Map<String, List<MdDoc>> ambiguousGroups = new LinkedHashMap<>();

        for (Map.Entry<String, List<MdDoc>> entry : docsByTitle.entrySet()) {
            String title = entry.getKey();
            List<MdDoc> rows = entry.getValue();
            List<DbHit> hits = hitsByTitle.getOrDefault(title, Collections.emptyList());
            Set<String> policyIds = distinctPolicyIds(hits);
            totalDbRows += hits.size();
            if (rows.size() > 1) {
                mdDuplicateTitleGroups++;
                mdDuplicateRows += rows.size();
            }
            if (policyIds.isEmpty()) {
                unmatchedTitleGroups++;
                unmatchedRows += rows.size();
            } else if (policyIds.size() == 1) {
                uniqueHitTitleGroups++;
                uniqueRows += rows.size();
            } else {
                multiHitTitleGroups++;
                ambiguousRows += rows.size();
                ambiguousGroups.put(title, rows);
            }
            if (policyIds.size() == 1 && rows.size() > 1) {
                Map<String, Integer> mainCounts = new HashMap<>();
                String policyId = policyIds.iterator().next();
                for (MdDoc row : rows) {
                    String key = row.mainTitle + "\u0001" + policyId;
                    mainCounts.put(key, mainCounts.getOrDefault(key, 0) + 1);
                }
                for (Map.Entry<String, Integer> mainCount : mainCounts.entrySet()) {
                    if (mainCount.getValue() > 1) {
                        sameMainPolicyIdDuplicateGroups++;
                        sameMainPolicyIdDuplicateRows += mainCount.getValue();
                        fullyDuplicateGroups.put(title, rows);
                        break;
                    }
                }
            }
        }

        try (BufferedWriter w = Files.newBufferedWriter(out, StandardCharsets.UTF_8)) {
            w.write("# 8.4 md 政策标题匹配只读预检\n\n");
            w.write("- 源文件: `" + mdPath.toAbsolutePath() + "`\n");
            w.write("- md 关联政策行数: " + docs.size() + "\n");
            w.write("- md 去重后标题数: " + uniqueTitleCount + "\n");
            w.write("- md 内部重复标题组数: " + mdDuplicateTitleGroups + "\n");
            w.write("- md 内部重复标题涉及行数: " + mdDuplicateRows + "\n");
            w.write("- 数据库未命中标题组数: " + unmatchedTitleGroups + "\n");
            w.write("- 数据库未命中 md 行数: " + unmatchedRows + "\n");
            w.write("- 数据库唯一 POLICY_ID 命中标题组数: " + uniqueHitTitleGroups + "\n");
            w.write("- 数据库唯一 POLICY_ID 命中 md 行数: " + uniqueRows + "\n");
            w.write("- 数据库多 POLICY_ID 命中标题组数: " + multiHitTitleGroups + "\n");
            w.write("- 数据库多 POLICY_ID 命中 md 行数: " + ambiguousRows + "\n");
            w.write("- 数据库返回原始行数: " + totalDbRows + "\n");
            w.write("- 同一主政策下同一 POLICY_ID 完全重复组数: " + sameMainPolicyIdDuplicateGroups + "\n");
            w.write("- 同一主政策下同一 POLICY_ID 完全重复涉及 md 行数: " + sameMainPolicyIdDuplicateRows + "\n\n");

            writeDuplicateSection(w, "## 同一主政策下同一 POLICY_ID 完全重复", fullyDuplicateGroups, hitsByTitle);
            writeDuplicateSection(w, "## 数据库多 POLICY_ID 命中标题", ambiguousGroups, hitsByTitle);
            writeUnmatchedSection(w, docsByTitle, hitsByTitle);
            writeAllMdDuplicateSection(w, docsByTitle, hitsByTitle);
        }
        System.out.println(out.toAbsolutePath());
    }

    private static void writeDuplicateSection(BufferedWriter w, String title, Map<String, List<MdDoc>> groups, Map<String, List<DbHit>> hitsByTitle) throws IOException {
        w.write(title + "\n\n");
        if (groups.isEmpty()) {
            w.write("无。\n\n");
            return;
        }
        int index = 1;
        for (Map.Entry<String, List<MdDoc>> entry : groups.entrySet()) {
            w.write(index++ + ". " + entry.getKey() + "\n");
            writeMdRows(w, entry.getValue());
            writeHits(w, hitsByTitle.get(entry.getKey()));
            w.write("\n");
        }
    }

    private static void writeUnmatchedSection(BufferedWriter w, LinkedHashMap<String, List<MdDoc>> docsByTitle, Map<String, List<DbHit>> hitsByTitle) throws IOException {
        w.write("## 数据库未命中标题\n\n");
        int index = 1;
        for (Map.Entry<String, List<MdDoc>> entry : docsByTitle.entrySet()) {
            if (!distinctPolicyIds(hitsByTitle.getOrDefault(entry.getKey(), Collections.emptyList())).isEmpty()) {
                continue;
            }
            w.write(index++ + ". " + entry.getKey() + "\n");
            writeMdRows(w, entry.getValue());
            w.write("\n");
        }
        if (index == 1) {
            w.write("无。\n\n");
        }
    }

    private static void writeAllMdDuplicateSection(BufferedWriter w, LinkedHashMap<String, List<MdDoc>> docsByTitle, Map<String, List<DbHit>> hitsByTitle) throws IOException {
        w.write("## md 内部重复标题完整清单\n\n");
        int index = 1;
        for (Map.Entry<String, List<MdDoc>> entry : docsByTitle.entrySet()) {
            if (entry.getValue().size() <= 1) {
                continue;
            }
            w.write(index++ + ". " + entry.getKey() + "\n");
            writeMdRows(w, entry.getValue());
            writeHits(w, hitsByTitle.get(entry.getKey()));
            w.write("\n");
        }
        if (index == 1) {
            w.write("无。\n\n");
        }
    }

    private static void writeMdRows(BufferedWriter w, List<MdDoc> rows) throws IOException {
        for (MdDoc row : rows) {
            w.write("   - md line " + row.lineNo + " [" + row.type + "] #" + row.mainNo + " " + row.mainTitle + "\n");
        }
    }

    private static void writeHits(BufferedWriter w, List<DbHit> hits) throws IOException {
        Set<String> seen = new LinkedHashSet<>();
        if (hits == null || hits.isEmpty()) {
            w.write("   - DB: 未命中\n");
            return;
        }
        for (DbHit hit : hits) {
            String key = hit.policyId + "\u0001" + hit.publishDate + "\u0001" + hit.filePath;
            if (!seen.add(key)) {
                continue;
            }
            w.write("   - DB POLICY_ID=" + hit.policyId + " PUBLISH_DATE=" + blank(hit.publishDate) + " FILE_TYPE=" + blank(hit.fileType) + "\n");
            w.write("     FILE_PATH=" + blank(hit.filePath) + "\n");
            if (!hit.originalTitle.isEmpty() && !hit.originalTitle.equals(hit.policyTitle)) {
                w.write("     ORIGINAL_TITLE=" + hit.originalTitle + "\n");
            }
        }
    }

    private static Set<String> distinctPolicyIds(List<DbHit> hits) {
        Set<String> ids = new LinkedHashSet<>();
        for (DbHit hit : hits) {
            if (!hit.policyId.isEmpty()) {
                ids.add(hit.policyId);
            }
        }
        return ids;
    }

    private static String nvl(String value) {
        return value == null ? "" : value.trim();
    }

    private static String blank(String value) {
        return value == null || value.trim().isEmpty() ? "-" : value.trim();
    }
}
