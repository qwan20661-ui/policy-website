import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.Node;
import org.w3c.dom.NodeList;
import org.xml.sax.InputSource;

import javax.xml.parsers.DocumentBuilder;
import javax.xml.parsers.DocumentBuilderFactory;
import java.io.ByteArrayInputStream;
import java.io.InputStream;
import java.io.StringReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.TreeMap;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

public class ImportPolicyAttributes {
    private static final String DEFAULT_XLSX = "E:\\policy\\rule\\局重点政策汇总(4).xlsx";
    private static final String DEFAULT_SHEET = "政策和附件属性标注-2026-7-22";
    private static final String DEFAULT_TITLE_COL = "政策标题";
    private static final String DEFAULT_TYPE_COL = "政策类型";
    private static final String DEFAULT_URL = System.getenv().getOrDefault("KEJIJU_JDBC_DB_URL", "jdbc:dm://127.0.0.1:5236");
    private static final String DEFAULT_USER = "SYSDBA";
    private static final String DEFAULT_PASSWORD = System.getenv("KEJIJU_DB_PASSWORD");
    private static final String OPERATOR = "ImportPolicyAttributes";

    public static void main(String[] args) throws Exception {
        Config config = Config.parse(args);
        if (config.help) {
            Config.printHelp();
            return;
        }

        System.out.println("Excel: " + config.xlsxPath.toAbsolutePath());
        System.out.println("Sheet: " + config.sheetName);
        System.out.println("Mode : " + (config.execute ? "EXECUTE/COMMIT" : "DRY-RUN/ROLLBACK"));
        System.out.println("Limit: " + config.limit + " matched spreadsheet row(s)");

        SheetData sheet = XlsxReader.readSheet(config.xlsxPath, config.sheetName);
        Header header = locateHeader(sheet.rows, config.titleColumn, config.typeColumn);
        System.out.println("Header row: " + (header.rowIndex + 1));
        System.out.println("Title column: " + header.titleColumn + " / Type column: " + header.typeColumn);

        Class.forName("dm.jdbc.driver.DmDriver");
        Properties props = new Properties();
        props.setProperty("user", config.user);
        props.setProperty("password", config.password);
        props.setProperty("localTimezone", "Asia/Shanghai");

        int scanned = 0;
        int matchedRows = 0;
        int metadataRows = 0;
        int attachmentRows = 0;
        int skippedNoMatch = 0;
        int skippedBlank = 0;

        try (Connection conn = DriverManager.getConnection(config.url, props)) {
            conn.setAutoCommit(false);
            ensureExpectedColumns(conn);

            for (int i = header.rowIndex + 1; i < sheet.rows.size(); i++) {
                if (matchedRows >= config.limit) break;

                Map<Integer, String> row = sheet.rows.get(i);
                String title = clean(row.get(header.titleColumn));
                String policyType = clean(row.get(header.typeColumn));
                if (title.isEmpty() && policyType.isEmpty()) {
                    skippedBlank++;
                    continue;
                }
                if (title.isEmpty() || policyType.isEmpty()) {
                    System.out.println("[SKIP] Excel row " + (i + 1) + ": title/type is blank");
                    skippedBlank++;
                    continue;
                }
                String dbPolicyType = normalizePolicyType(policyType);

                scanned++;
                List<PolicyRow> policies = findPoliciesByTitle(conn, title);
                if (policies.isEmpty()) {
                    System.out.println("[NO MATCH] Excel row " + (i + 1) + ": " + title);
                    skippedNoMatch++;
                    continue;
                }

                matchedRows++;
                System.out.println();
                System.out.println("=== Matched Excel row " + (i + 1) + " ===");
                System.out.println("Policy title: " + title);
                System.out.println("Policy type : " + policyType + (policyType.equals(dbPolicyType) ? "" : " -> " + dbPolicyType));
                if (policies.size() > 1) {
                    System.out.println("[WARN] " + policies.size() + " POLICY rows share this exact title; all matched rows will be updated.");
                }

                for (PolicyRow policy : policies) {
                    MetadataBefore before = loadMetadataBefore(conn, policy.id);
                    int updated = upsertPolicyMetadata(conn, policy.id, dbPolicyType);
                    int attachmentUpdated = 0;
                    if (isResultAnnouncement(dbPolicyType)) {
                        attachmentUpdated = updateResultAnnouncementAttachments(conn, policy.id);
                    }
                    MetadataBefore after = loadMetadataBefore(conn, policy.id);

                    metadataRows += updated;
                    attachmentRows += attachmentUpdated;

                    System.out.println("POLICY.ID=" + policy.id);
                    System.out.println("  metadata before: attribute=" + nvl(before.policyAttribute) + ", review=" + nvl(before.reviewStatus));
                    System.out.println("  metadata after : attribute=" + nvl(after.policyAttribute) + ", review=" + nvl(after.reviewStatus));
                    System.out.println("  metadata rows  : " + updated);
                    System.out.println("  attachment rows: " + attachmentUpdated + (isResultAnnouncement(dbPolicyType) ? " (ATTACH_TYPE='结果公告类')" : " (not result announcement)"));
                }
            }

            if (config.execute) {
                conn.commit();
                System.out.println();
                System.out.println("[COMMIT] Database changes committed.");
            } else {
                conn.rollback();
                System.out.println();
                System.out.println("[ROLLBACK] Dry-run finished; database was not changed. Add --execute to commit.");
            }
        }

        System.out.println();
        System.out.println("Summary");
        System.out.println("  scanned nonblank rows : " + scanned);
        System.out.println("  matched Excel rows    : " + matchedRows);
        System.out.println("  metadata rows touched : " + metadataRows);
        System.out.println("  attachment rows touched: " + attachmentRows);
        System.out.println("  skipped no match      : " + skippedNoMatch);
        System.out.println("  skipped blank/incomplete: " + skippedBlank);
    }

    private static void ensureExpectedColumns(Connection conn) throws SQLException {
        requireColumn(conn, "POLICY", "ID");
        requireColumn(conn, "POLICY", "POLICY_TITLE");
        requireColumn(conn, "POLICY_METADATA", "POLICY_ID");
        requireColumn(conn, "POLICY_METADATA", "POLICY_ATTRIBUTE");
        requireColumn(conn, "POLICY_METADATA", "REVIEW_STATUS");
        requireColumn(conn, "POLICY_ATTACHMENT", "POLICY_ID");
        requireColumn(conn, "POLICY_ATTACHMENT", "ATTACH_TYPE");
    }

    private static void requireColumn(Connection conn, String tableName, String columnName) throws SQLException {
        try (PreparedStatement ps = conn.prepareStatement(
                "SELECT 1 FROM ALL_TAB_COLUMNS WHERE OWNER='SYSDBA' AND TABLE_NAME=? AND COLUMN_NAME=?")) {
            ps.setString(1, tableName);
            ps.setString(2, columnName);
            try (ResultSet rs = ps.executeQuery()) {
                if (!rs.next()) {
                    throw new SQLException("Missing expected column SYSDBA." + tableName + "." + columnName);
                }
            }
        }
    }

    private static Header locateHeader(List<Map<Integer, String>> rows, String titleColumn, String typeColumn) {
        String titleKey = normalizeHeader(titleColumn);
        String typeKey = normalizeHeader(typeColumn);

        for (int r = 0; r < Math.min(rows.size(), 30); r++) {
            Map<Integer, String> row = rows.get(r);
            Integer titleCol = null;
            Integer typeCol = null;
            for (Map.Entry<Integer, String> entry : row.entrySet()) {
                String key = normalizeHeader(entry.getValue());
                if (titleKey.equals(key)) titleCol = entry.getKey();
                if (typeKey.equals(key)) typeCol = entry.getKey();
            }
            if (titleCol != null && typeCol != null) {
                return new Header(r, titleCol, typeCol);
            }
            if (titleCol == null && typeCol != null) {
                System.out.println("[INFO] Title header not found; using column A as policy title.");
                return new Header(r, 0, typeCol);
            }
        }

        throw new IllegalArgumentException("Cannot find header columns: " + titleColumn + " / " + typeColumn + "\n" + previewRows(rows, 12));
    }

    private static String previewRows(List<Map<Integer, String>> rows, int maxRows) {
        StringBuilder sb = new StringBuilder("Top non-empty sheet rows:\n");
        for (int r = 0; r < Math.min(rows.size(), maxRows); r++) {
            Map<Integer, String> row = rows.get(r);
            if (row.isEmpty()) continue;
            sb.append("  row ").append(r + 1).append(": ");
            int printed = 0;
            for (Map.Entry<Integer, String> entry : row.entrySet()) {
                if (printed++ > 0) sb.append(" | ");
                sb.append(columnName(entry.getKey())).append("=").append(clean(entry.getValue()));
                if (printed >= 12) break;
            }
            sb.append('\n');
        }
        return sb.toString();
    }

    private static String columnName(int zeroBasedColumn) {
        int n = zeroBasedColumn + 1;
        StringBuilder sb = new StringBuilder();
        while (n > 0) {
            int rem = (n - 1) % 26;
            sb.insert(0, (char) ('A' + rem));
            n = (n - 1) / 26;
        }
        return sb.toString();
    }

    private static List<PolicyRow> findPoliciesByTitle(Connection conn, String title) throws SQLException {
        List<PolicyRow> rows = new ArrayList<>();
        try (PreparedStatement ps = conn.prepareStatement(
                "SELECT ID, POLICY_TITLE FROM SYSDBA.POLICY WHERE TRIM(POLICY_TITLE) = ? ORDER BY ID")) {
            ps.setString(1, title.trim());
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    rows.add(new PolicyRow(rs.getString("ID"), rs.getString("POLICY_TITLE")));
                }
            }
        }
        return rows;
    }

    private static MetadataBefore loadMetadataBefore(Connection conn, String policyId) throws SQLException {
        try (PreparedStatement ps = conn.prepareStatement(
                "SELECT POLICY_ATTRIBUTE, REVIEW_STATUS FROM SYSDBA.POLICY_METADATA WHERE POLICY_ID = ? AND ROWNUM = 1")) {
            ps.setString(1, policyId);
            try (ResultSet rs = ps.executeQuery()) {
                if (rs.next()) {
                    return new MetadataBefore(rs.getString("POLICY_ATTRIBUTE"), rs.getString("REVIEW_STATUS"));
                }
            }
        }
        return new MetadataBefore(null, null);
    }

    private static int upsertPolicyMetadata(Connection conn, String policyId, String policyType) throws SQLException {
        try (PreparedStatement ps = conn.prepareStatement(
                "UPDATE SYSDBA.POLICY_METADATA " +
                        "SET POLICY_ATTRIBUTE = ?, REVIEW_STATUS = '已通过', UPDATED_BY = ?, UPDATED_AT = CURRENT_TIMESTAMP " +
                        "WHERE POLICY_ID = ?")) {
            ps.setString(1, policyType);
            ps.setString(2, OPERATOR);
            ps.setString(3, policyId);
            int updated = ps.executeUpdate();
            if (updated > 0) return updated;
        }

        try (PreparedStatement ps = conn.prepareStatement(
                "INSERT INTO SYSDBA.POLICY_METADATA " +
                        "(POLICY_ID, POLICY_ATTRIBUTE, REVIEW_STATUS, CREATED_AT, UPDATED_AT, CREATED_BY, UPDATED_BY) " +
                        "VALUES (?, ?, '已通过', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, ?)")) {
            ps.setString(1, policyId);
            ps.setString(2, policyType);
            ps.setString(3, OPERATOR);
            ps.setString(4, OPERATOR);
            return ps.executeUpdate();
        }
    }

    private static int updateResultAnnouncementAttachments(Connection conn, String policyId) throws SQLException {
        try (PreparedStatement ps = conn.prepareStatement(
                "UPDATE SYSDBA.POLICY_ATTACHMENT " +
                        "SET ATTACH_TYPE = '结果公告类', ATTACH_REVIEW_STATUS = '已通过', UPDATED_BY = ?, UPDATE_AT = CURRENT_TIMESTAMP " +
                        "WHERE TRIM(POLICY_ID) = ? AND NVL(DEL_FLAG, '0') = '0'")) {
            ps.setString(1, OPERATOR);
            ps.setString(2, policyId);
            return ps.executeUpdate();
        }
    }

    private static boolean isResultAnnouncement(String value) {
        String v = clean(value);
        return "结果公告".equals(v) || "结果公告类".equals(v);
    }

    private static String normalizePolicyType(String value) {
        String v = clean(value);
        if ("结果公示".equals(v)) return "结果公告";
        return v;
    }

    private static String clean(String value) {
        if (value == null) return "";
        return value.replace('\u00A0', ' ').trim();
    }

    private static String normalizeHeader(String value) {
        return clean(value).replace(" ", "").replace("\t", "").replace("\n", "");
    }

    private static String nvl(String value) {
        return value == null ? "<NULL>" : value;
    }

    static class Config {
        Path xlsxPath = Paths.get(DEFAULT_XLSX);
        String sheetName = DEFAULT_SHEET;
        String titleColumn = DEFAULT_TITLE_COL;
        String typeColumn = DEFAULT_TYPE_COL;
        String url = DEFAULT_URL;
        String user = DEFAULT_USER;
        String password = DEFAULT_PASSWORD;
        int limit = 1;
        boolean execute = false;
        boolean help = false;

        static Config parse(String[] args) {
            Config c = new Config();
            for (int i = 0; i < args.length; i++) {
                String arg = args[i];
                if ("--help".equals(arg) || "-h".equals(arg)) {
                    c.help = true;
                } else if ("--execute".equals(arg)) {
                    c.execute = true;
                } else if ("--xlsx".equals(arg)) {
                    c.xlsxPath = Paths.get(next(args, ++i, arg));
                } else if ("--sheet".equals(arg)) {
                    c.sheetName = next(args, ++i, arg);
                } else if ("--title-col".equals(arg)) {
                    c.titleColumn = next(args, ++i, arg);
                } else if ("--type-col".equals(arg)) {
                    c.typeColumn = next(args, ++i, arg);
                } else if ("--url".equals(arg)) {
                    c.url = next(args, ++i, arg);
                } else if ("--user".equals(arg)) {
                    c.user = next(args, ++i, arg);
                } else if ("--password".equals(arg)) {
                    c.password = next(args, ++i, arg);
                } else if ("--limit".equals(arg)) {
                    c.limit = Integer.parseInt(next(args, ++i, arg));
                    if (c.limit < 1) throw new IllegalArgumentException("--limit must be >= 1");
                } else {
                    throw new IllegalArgumentException("Unknown argument: " + arg);
                }
            }
            return c;
        }

        static String next(String[] args, int index, String flag) {
            if (index >= args.length) throw new IllegalArgumentException("Missing value for " + flag);
            return args[index];
        }

        static void printHelp() {
            System.out.println("Usage:");
            System.out.println("  javac -encoding UTF-8 -cp \"lib\\jdbc\\DmJdbcDriver8.jar\" ImportPolicyAttributes.java");
            System.out.println("  java -cp \".;lib\\jdbc\\DmJdbcDriver8.jar\" ImportPolicyAttributes [options]");
            System.out.println();
            System.out.println("Options:");
            System.out.println("  --execute              Commit changes. Without this flag, all changes are rolled back.");
            System.out.println("  --limit N              Number of matched spreadsheet rows to process. Default: 1");
            System.out.println("  --xlsx PATH            Excel file path. Default: " + DEFAULT_XLSX);
            System.out.println("  --sheet NAME           Sheet name. Default: " + DEFAULT_SHEET);
            System.out.println("  --title-col NAME       Header for policy title. Default: " + DEFAULT_TITLE_COL);
            System.out.println("  --type-col NAME        Header for policy type. Default: " + DEFAULT_TYPE_COL);
            System.out.println("  --url JDBC_URL         Default: " + DEFAULT_URL);
            System.out.println("  --user USER            Default: " + DEFAULT_USER);
            System.out.println("  --password PASSWORD    Default: project local Dameng password");
        }
    }

    static class Header {
        final int rowIndex;
        final int titleColumn;
        final int typeColumn;

        Header(int rowIndex, int titleColumn, int typeColumn) {
            this.rowIndex = rowIndex;
            this.titleColumn = titleColumn;
            this.typeColumn = typeColumn;
        }
    }

    static class PolicyRow {
        final String id;
        final String title;

        PolicyRow(String id, String title) {
            this.id = id;
            this.title = title;
        }
    }

    static class MetadataBefore {
        final String policyAttribute;
        final String reviewStatus;

        MetadataBefore(String policyAttribute, String reviewStatus) {
            this.policyAttribute = policyAttribute;
            this.reviewStatus = reviewStatus;
        }
    }

    static class SheetData {
        final List<Map<Integer, String>> rows;

        SheetData(List<Map<Integer, String>> rows) {
            this.rows = rows;
        }
    }

    static class XlsxReader {
        static SheetData readSheet(Path xlsx, String sheetName) throws Exception {
            try (ZipFile zip = new ZipFile(xlsx.toFile(), StandardCharsets.UTF_8)) {
                List<String> sharedStrings = readSharedStrings(zip);
                String sheetPath = findSheetPath(zip, sheetName);
                List<Map<Integer, String>> rows = readRows(zip, sheetPath, sharedStrings);
                return new SheetData(rows);
            }
        }

        private static List<String> readSharedStrings(ZipFile zip) throws Exception {
            List<String> values = new ArrayList<>();
            ZipEntry entry = zip.getEntry("xl/sharedStrings.xml");
            if (entry == null) return values;
            Document doc = parseXml(zip, entry);
            NodeList siNodes = doc.getElementsByTagName("si");
            for (int i = 0; i < siNodes.getLength(); i++) {
                Element si = (Element) siNodes.item(i);
                NodeList tNodes = si.getElementsByTagName("t");
                StringBuilder sb = new StringBuilder();
                for (int j = 0; j < tNodes.getLength(); j++) {
                    sb.append(tNodes.item(j).getTextContent());
                }
                values.add(sb.toString());
            }
            return values;
        }

        private static String findSheetPath(ZipFile zip, String sheetName) throws Exception {
            Document workbook = parseXml(zip, require(zip, "xl/workbook.xml"));
            Document rels = parseXml(zip, require(zip, "xl/_rels/workbook.xml.rels"));

            Map<String, String> relTargets = new HashMap<>();
            NodeList relNodes = rels.getElementsByTagName("Relationship");
            for (int i = 0; i < relNodes.getLength(); i++) {
                Element rel = (Element) relNodes.item(i);
                relTargets.put(rel.getAttribute("Id"), rel.getAttribute("Target"));
            }

            NodeList sheetNodes = workbook.getElementsByTagName("sheet");
            List<String> available = new ArrayList<>();
            for (int i = 0; i < sheetNodes.getLength(); i++) {
                Element sheet = (Element) sheetNodes.item(i);
                String name = sheet.getAttribute("name");
                available.add(name);
                if (sheetName.equals(name)) {
                    String relId = sheet.getAttribute("r:id");
                    if (relId == null || relId.isEmpty()) relId = sheet.getAttribute("id");
                    String target = relTargets.get(relId);
                    if (target == null || target.isEmpty()) {
                        throw new IllegalArgumentException("Cannot resolve worksheet relationship for sheet: " + sheetName);
                    }
                    return normalizeZipPath("xl/" + target);
                }
            }
            String normalizedRequested = normalizeSheetName(sheetName);
            for (int i = 0; i < sheetNodes.getLength(); i++) {
                Element sheet = (Element) sheetNodes.item(i);
                String name = sheet.getAttribute("name");
                if (normalizedRequested.equals(normalizeSheetName(name))) {
                    String relId = sheet.getAttribute("r:id");
                    if (relId == null || relId.isEmpty()) relId = sheet.getAttribute("id");
                    String target = relTargets.get(relId);
                    if (target == null || target.isEmpty()) {
                        throw new IllegalArgumentException("Cannot resolve worksheet relationship for sheet: " + name);
                    }
                    System.out.println("[INFO] Sheet name resolved by loose matching: " + name);
                    return normalizeZipPath("xl/" + target);
                }
            }
            throw new IllegalArgumentException("Sheet not found: " + sheetName + ". Available sheets: " + available);
        }

        private static String normalizeSheetName(String name) {
            if (name == null) return "";
            return name.replace(" ", "")
                    .replace("\t", "")
                    .replace("-", "")
                    .replace("－", "")
                    .replace("_", "")
                    .trim();
        }

        private static List<Map<Integer, String>> readRows(ZipFile zip, String sheetPath, List<String> sharedStrings) throws Exception {
            Document sheet = parseXml(zip, require(zip, sheetPath));
            NodeList rowNodes = sheet.getElementsByTagName("row");
            List<Map<Integer, String>> rows = new ArrayList<>();

            for (int i = 0; i < rowNodes.getLength(); i++) {
                Element rowElement = (Element) rowNodes.item(i);
                int rowNumber = parseInt(rowElement.getAttribute("r"), i + 1);
                while (rows.size() < rowNumber) {
                    rows.add(new TreeMap<Integer, String>());
                }
                Map<Integer, String> row = rows.get(rowNumber - 1);
                NodeList cellNodes = rowElement.getElementsByTagName("c");
                int fallbackColumn = 0;
                for (int c = 0; c < cellNodes.getLength(); c++) {
                    Element cell = (Element) cellNodes.item(c);
                    int column = cell.hasAttribute("r") ? columnIndex(cell.getAttribute("r")) : fallbackColumn;
                    fallbackColumn = column + 1;
                    String value = cellValue(cell, sharedStrings);
                    if (!value.isEmpty()) row.put(column, value);
                }
            }
            return rows;
        }

        private static String cellValue(Element cell, List<String> sharedStrings) {
            String type = cell.getAttribute("t");
            if ("inlineStr".equals(type)) {
                NodeList tNodes = cell.getElementsByTagName("t");
                StringBuilder sb = new StringBuilder();
                for (int i = 0; i < tNodes.getLength(); i++) {
                    sb.append(tNodes.item(i).getTextContent());
                }
                return sb.toString().trim();
            }

            String raw = "";
            NodeList vNodes = cell.getElementsByTagName("v");
            if (vNodes.getLength() > 0) raw = vNodes.item(0).getTextContent();
            if (raw == null) raw = "";

            if ("s".equals(type)) {
                int idx = parseInt(raw, -1);
                if (idx >= 0 && idx < sharedStrings.size()) return sharedStrings.get(idx).trim();
                return "";
            }
            return raw.trim();
        }

        private static Document parseXml(ZipFile zip, ZipEntry entry) throws Exception {
            try (InputStream in = zip.getInputStream(entry)) {
                byte[] bytes = readAllBytes(in);
                return parseXml(bytes);
            }
        }

        private static Document parseXml(byte[] bytes) throws Exception {
            DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
            factory.setNamespaceAware(false);
            disableExternalEntities(factory);
            DocumentBuilder builder = factory.newDocumentBuilder();
            InputSource source = new InputSource(new StringReader(new String(bytes, StandardCharsets.UTF_8)));
            return builder.parse(source);
        }

        private static void disableExternalEntities(DocumentBuilderFactory factory) {
            trySetFeature(factory, "http://apache.org/xml/features/disallow-doctype-decl", true);
            trySetFeature(factory, "http://xml.org/sax/features/external-general-entities", false);
            trySetFeature(factory, "http://xml.org/sax/features/external-parameter-entities", false);
            factory.setExpandEntityReferences(false);
        }

        private static void trySetFeature(DocumentBuilderFactory factory, String name, boolean value) {
            try {
                factory.setFeature(name, value);
            } catch (Exception ignored) {
                // Some JDK XML parsers do not support every hardening feature.
            }
        }

        private static ZipEntry require(ZipFile zip, String path) {
            ZipEntry entry = zip.getEntry(path);
            if (entry == null) throw new IllegalArgumentException("Missing XLSX entry: " + path);
            return entry;
        }

        private static String normalizeZipPath(String path) {
            List<String> parts = new ArrayList<>();
            for (String part : path.replace('\\', '/').split("/")) {
                if (part.isEmpty() || ".".equals(part)) continue;
                if ("..".equals(part)) {
                    if (!parts.isEmpty()) parts.remove(parts.size() - 1);
                } else {
                    parts.add(part);
                }
            }
            return String.join("/", parts);
        }

        private static int columnIndex(String cellRef) {
            int value = 0;
            for (int i = 0; i < cellRef.length(); i++) {
                char ch = cellRef.charAt(i);
                if (ch >= 'A' && ch <= 'Z') {
                    value = value * 26 + (ch - 'A' + 1);
                } else if (ch >= 'a' && ch <= 'z') {
                    value = value * 26 + (ch - 'a' + 1);
                } else {
                    break;
                }
            }
            return value - 1;
        }

        private static int parseInt(String value, int fallback) {
            try {
                if (value == null || value.trim().isEmpty()) return fallback;
                return Integer.parseInt(value.trim());
            } catch (NumberFormatException e) {
                return fallback;
            }
        }

        private static byte[] readAllBytes(InputStream in) throws Exception {
            byte[] buffer = new byte[8192];
            int read;
            java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
            while ((read = in.read(buffer)) >= 0) {
                out.write(buffer, 0, read);
            }
            return out.toByteArray();
        }
    }
}
