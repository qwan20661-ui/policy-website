package com.policy.jdbcservice;

import com.sun.net.httpserver.*;

import javax.net.ssl.*;
import java.io.*;
import java.net.*;
import java.sql.*;
import java.time.LocalDate;
import java.util.*;
import java.util.concurrent.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.security.MessageDigest;

import org.apache.poi.hwpf.HWPFDocument;
import org.apache.poi.hwpf.extractor.WordExtractor;
import org.apache.poi.hwpf.model.PicturesTable;
import org.apache.poi.hwpf.usermodel.Picture;
import org.apache.tika.Tika;
import org.apache.poi.xwpf.usermodel.IBodyElement;
import org.apache.poi.xwpf.usermodel.XWPFDocument;
import org.apache.poi.xwpf.usermodel.XWPFParagraph;
import org.apache.poi.xwpf.usermodel.XWPFPicture;
import org.apache.poi.xwpf.usermodel.XWPFPictureData;
import org.apache.poi.xwpf.usermodel.XWPFRun;
import org.apache.poi.xwpf.usermodel.XWPFTable;
import org.apache.poi.xwpf.usermodel.XWPFTableCell;
import org.apache.poi.xwpf.usermodel.XWPFTableRow;

/**
 * 政策解读系统 - JDBC 中间层服务
 *
 * 数据库: 达梦数据库
 * 用户名: SYSDBA
 * 模式: SYSDBA (用户即模式)
 */
public class JDBCService {

    // ==================== 数据库配置 ====================
 private static final String DB_HOST = "127.0.0.1";
private static final int DB_PORT = 5236;
private static final String DB_USER = "SYSDBA";
private static final String DB_PASSWORD =
        java.util.Objects.requireNonNull(System.getenv("KEJIJU_DB_PASSWORD"),
                "请先在启动 Java 的终端中设置 KEJIJU_DB_PASSWORD");
private static final String DB_SCHEMA = "SYSDBA";

    private static final String JDBC_URL = String.format(
        "jdbc:dm://%s:%d", DB_HOST, DB_PORT
    );

    private static final String JDBC_DRIVER = "dm.jdbc.driver.DmDriver";

    // 服务端口
    private static final int SERVICE_PORT = 8080;

    private static HttpServer server;
    private static Connection conn;
    private static final Tika TIKA = new Tika();
    private static final ExecutorService ATTACHMENT_PREVIEW_EXECUTOR = Executors.newFixedThreadPool(2);
    private static final long ATTACHMENT_PREVIEW_TIMEOUT_SECONDS = 3L;
    private static final ConcurrentHashMap<String, HierarchyLookupCacheEntry> HIERARCHY_LOOKUP_CACHE = new ConcurrentHashMap<>();
    private static final long HIERARCHY_LOOKUP_CACHE_TTL_MILLIS = 5L * 60L * 1000L;
    private static final Path APP_ROOT = Paths.get(System.getProperty("user.dir")).toAbsolutePath().normalize();
    private static final Path POLICYSQL_ROOT = Paths.get(
        System.getenv().getOrDefault("POLICYSQL_ROOT", "../policysql")
    ).toAbsolutePath().normalize();

    static {
        TIKA.setMaxStringLength(1_000_000);
    }

    @FunctionalInterface
    private interface HierarchyLookupLoader {
        Map<String, String> load() throws SQLException;
    }

    private static final class HierarchyLookupCacheEntry {
        private final Map<String, String> data;
        private final long loadedAtMillis;

        private HierarchyLookupCacheEntry(Map<String, String> data, long loadedAtMillis) {
            this.data = Collections.unmodifiableMap(new LinkedHashMap<>(data));
            this.loadedAtMillis = loadedAtMillis;
        }

        private boolean isFresh(long nowMillis) {
            return nowMillis - loadedAtMillis <= HIERARCHY_LOOKUP_CACHE_TTL_MILLIS;
        }
    }

    private static Path resolveAttachmentPath(String filePath) throws IOException {
        Path supplied = Paths.get(filePath);
        List<Path> candidates = new ArrayList<>();
        if (supplied.isAbsolute()) {
            candidates.add(supplied);
        } else {
            candidates.add(APP_ROOT.resolve(supplied));
            candidates.add(POLICYSQL_ROOT.resolve(supplied));
        }

        for (Path candidate : candidates) {
            Path normalized = candidate.toAbsolutePath().normalize();
            boolean allowed = normalized.startsWith(APP_ROOT) || normalized.startsWith(POLICYSQL_ROOT);
            if (allowed && Files.isRegularFile(normalized)) {
                return normalized;
            }
        }
        throw new FileNotFoundException("附件文件不存在或路径不在允许目录内: " + filePath);
    }

    private static String extractAttachmentText(Path file, String suffix) throws Exception {
        return extractAttachmentPreview(file, suffix).content;
    }

    private static AttachmentContentPreview extractAttachmentPreview(Path file, String suffix) throws Exception {
        String normalizedSuffix = suffix == null ? "" : suffix.toLowerCase(Locale.ROOT);
        if (Arrays.asList("zip", "rar", "7z", "gz", "tar").contains(normalizedSuffix)) {
            return AttachmentContentPreview.text("该附件为压缩包，暂不支持在线文本预览。");
        }
        if ("docx".equals(normalizedSuffix)) {
            return extractDocxPreview(file);
        }
        if ("doc".equals(normalizedSuffix)) {
            return extractDocPreview(file);
        }
        if (Arrays.asList("png", "jpg", "jpeg", "gif", "webp", "bmp").contains(normalizedSuffix)) {
            String mimeType = imageMimeType(normalizedSuffix, "");
            String dataUrl = "data:" + mimeType + ";base64," + Base64.getEncoder().encodeToString(Files.readAllBytes(file));
            return AttachmentContentPreview.html("", "<div class=\"doc-preview-image-wrap\"><img src=\"" + dataUrl + "\" alt=\"attachment image\" /></div>", 1);
        }
        String content = TIKA.parseToString(file.toFile()).trim();
        if (content.isEmpty() && Arrays.asList("txt", "md", "csv", "tsv", "json", "xml", "html", "htm", "log", "sql").contains(normalizedSuffix)) {
            content = new String(Files.readAllBytes(file), StandardCharsets.UTF_8).trim();
        }
        return AttachmentContentPreview.text(content.isEmpty() ? "未能从该附件中提取到可显示的文本内容。" : content);
    }

    private static AttachmentContentPreview extractDocxPreview(Path file) throws Exception {
        StringBuilder text = new StringBuilder();
        StringBuilder html = new StringBuilder();
        int[] imageCount = new int[] { 0 };
        try (InputStream in = Files.newInputStream(file);
             XWPFDocument document = new XWPFDocument(in)) {
            for (IBodyElement element : document.getBodyElements()) {
                appendDocxElement(element, text, html, imageCount);
            }
        }
        String plainText = text.toString().trim();
        String contentHtml = html.toString().trim();
        if (plainText.isEmpty() && contentHtml.isEmpty()) {
            plainText = "未能从该附件中提取到可显示的内容。";
        }
        return AttachmentContentPreview.html(plainText, contentHtml, imageCount[0]);
    }

    private static AttachmentContentPreview extractDocPreview(Path file) throws Exception {
        StringBuilder text = new StringBuilder();
        StringBuilder html = new StringBuilder();
        int imageCount = 0;
        try (InputStream in = Files.newInputStream(file);
             HWPFDocument document = new HWPFDocument(in);
             WordExtractor extractor = new WordExtractor(document)) {
            String plainText = nullToEmpty(extractor.getText()).trim();
            if (!plainText.isEmpty()) {
                text.append(plainText);
                html.append("<div class=\"doc-preview-text\">").append(escapeHtml(plainText)).append("</div>");
            }
            PicturesTable picturesTable = document.getPicturesTable();
            if (picturesTable != null) {
                for (Picture picture : picturesTable.getAllPictures()) {
                    String imageHtml = buildImageHtml(picture.getContent(), picture.getMimeType(), picture.suggestFileExtension(), imageCount + 1);
                    if (!imageHtml.isEmpty()) {
                        html.append(imageHtml);
                        imageCount++;
                    }
                }
            }
        }
        String plainText = text.toString().trim();
        String contentHtml = html.toString().trim();
        if (plainText.isEmpty() && contentHtml.isEmpty()) {
            plainText = "未能从该附件中提取到可显示的内容。";
        }
        return AttachmentContentPreview.html(plainText, contentHtml, imageCount);
    }

    private static void appendDocxElement(IBodyElement element, StringBuilder text, StringBuilder html, int[] imageCount) {
        if (element instanceof XWPFParagraph) {
            appendDocxParagraph((XWPFParagraph) element, text, html, imageCount);
        } else if (element instanceof XWPFTable) {
            html.append("<table class=\"doc-preview-table\"><tbody>");
            XWPFTable table = (XWPFTable) element;
            for (XWPFTableRow row : table.getRows()) {
                html.append("<tr>");
                for (XWPFTableCell cell : row.getTableCells()) {
                    html.append("<td>");
                    for (XWPFParagraph paragraph : cell.getParagraphs()) {
                        appendDocxParagraph(paragraph, text, html, imageCount);
                    }
                    html.append("</td>");
                }
                html.append("</tr>");
            }
            html.append("</tbody></table>");
            text.append('\n');
        }
    }

    private static void appendDocxParagraph(XWPFParagraph paragraph, StringBuilder text, StringBuilder html, int[] imageCount) {
        StringBuilder paragraphHtml = new StringBuilder();
        StringBuilder paragraphText = new StringBuilder();
        for (XWPFRun run : paragraph.getRuns()) {
            String runText = nullToEmpty(run.text());
            if (!runText.isEmpty()) {
                paragraphText.append(runText);
                paragraphHtml.append(escapeHtml(runText));
            }
            for (XWPFPicture picture : run.getEmbeddedPictures()) {
                XWPFPictureData data = picture.getPictureData();
                if (data == null) continue;
                String imageHtml = buildImageHtml(data.getData(), null, data.suggestFileExtension(), imageCount[0] + 1);
                if (!imageHtml.isEmpty()) {
                    paragraphHtml.append(imageHtml);
                    imageCount[0]++;
                }
            }
        }
        String textValue = paragraphText.toString().trim();
        if (!textValue.isEmpty()) {
            text.append(textValue).append('\n');
        }
        String htmlValue = paragraphHtml.toString().trim();
        if (!htmlValue.isEmpty()) {
            html.append("<p>").append(htmlValue).append("</p>");
        }
    }

    private static String buildImageHtml(byte[] bytes, String mimeType, String extension, int index) {
        if (bytes == null || bytes.length == 0) return "";
        String normalizedMime = mimeType == null || mimeType.trim().isEmpty()
            ? imageMimeType("", extension)
            : mimeType.trim();
        String dataUrl = "data:" + normalizedMime + ";base64," + Base64.getEncoder().encodeToString(bytes);
        return "<div class=\"doc-preview-image-wrap\"><img src=\"" + dataUrl + "\" alt=\"embedded image " + index + "\" /></div>";
    }

    private static String imageMimeType(String suffix, String extension) {
        String ext = firstNonEmpty(suffix, extension).toLowerCase(Locale.ROOT).replace(".", "");
        if ("jpg".equals(ext) || "jpeg".equals(ext)) return "image/jpeg";
        if ("png".equals(ext)) return "image/png";
        if ("gif".equals(ext)) return "image/gif";
        if ("webp".equals(ext)) return "image/webp";
        if ("bmp".equals(ext)) return "image/bmp";
        if ("wmf".equals(ext)) return "image/wmf";
        if ("emf".equals(ext)) return "image/emf";
        return "application/octet-stream";
    }

    private static String escapeHtml(String value) {
        return nullToEmpty(value)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\"", "&quot;")
            .replace("'", "&#39;")
            .replace("\n", "<br>");
    }

    private static class AttachmentContentPreview {
        final String content;
        final String contentHtml;
        final String renderType;
        final int imageCount;

        AttachmentContentPreview(String content, String contentHtml, String renderType, int imageCount) {
            this.content = content;
            this.contentHtml = contentHtml;
            this.renderType = renderType;
            this.imageCount = imageCount;
        }

        static AttachmentContentPreview text(String content) {
            return new AttachmentContentPreview(content, "", "text", 0);
        }

        static AttachmentContentPreview html(String content, String contentHtml, int imageCount) {
            return new AttachmentContentPreview(content, contentHtml, "html", imageCount);
        }
    }

    private static void ensurePolicyRemarkColumn() throws SQLException {
        String checkSql = "SELECT COUNT(*) AS CNT FROM ALL_TAB_COLUMNS " +
            "WHERE OWNER = 'SYSDBA' AND TABLE_NAME = 'POLICY_METADATA' AND COLUMN_NAME = 'REMARK'";
        PreparedStatement stmt = conn.prepareStatement(checkSql);
        ResultSet rs = stmt.executeQuery();
        boolean exists = rs.next() && rs.getInt("CNT") > 0;
        rs.close();
        stmt.close();
        if (!exists) {
            Statement alterStmt = conn.createStatement();
            alterStmt.executeUpdate("ALTER TABLE SYSDBA.POLICY_METADATA ADD REMARK VARCHAR2(2000)");
            alterStmt.close();
            System.out.println("[JDBC] 已新增 POLICY_METADATA.REMARK 字段");
        }
    }

    private static void ensurePolicyReviewStatusColumn() throws SQLException {
        String checkSql = "SELECT COUNT(*) AS CNT FROM ALL_TAB_COLUMNS " +
            "WHERE OWNER = 'SYSDBA' AND TABLE_NAME = 'POLICY_METADATA' AND COLUMN_NAME = 'REVIEW_STATUS'";
        PreparedStatement stmt = conn.prepareStatement(checkSql);
        ResultSet rs = stmt.executeQuery();
        boolean exists = rs.next() && rs.getInt("CNT") > 0;
        rs.close();
        stmt.close();
        if (!exists) {
            Statement alterStmt = conn.createStatement();
            alterStmt.executeUpdate("ALTER TABLE SYSDBA.POLICY_METADATA ADD REVIEW_STATUS VARCHAR2(20) DEFAULT '未通过'");
            alterStmt.close();
            System.out.println("[JDBC] 已新增 POLICY_METADATA.REVIEW_STATUS 字段");
        }
    }

    private static void ensureAttachmentReviewStatusColumn() throws SQLException {
        String checkSql = "SELECT COUNT(*) AS CNT FROM ALL_TAB_COLUMNS " +
            "WHERE OWNER = 'SYSDBA' AND TABLE_NAME = 'POLICY_ATTACHMENT' AND COLUMN_NAME = 'ATTACH_REVIEW_STATUS'";
        PreparedStatement stmt = conn.prepareStatement(checkSql);
        ResultSet rs = stmt.executeQuery();
        boolean exists = rs.next() && rs.getInt("CNT") > 0;
        rs.close();
        stmt.close();
        if (!exists) {
            Statement alterStmt = conn.createStatement();
            alterStmt.executeUpdate("ALTER TABLE SYSDBA.POLICY_ATTACHMENT ADD ATTACH_REVIEW_STATUS VARCHAR2(20) DEFAULT '未通过'");
            alterStmt.close();
            System.out.println("[JDBC] 已新增 POLICY_ATTACHMENT.ATTACH_REVIEW_STATUS 字段");
        }
    }

    private static void ensureFusionReviewUserIdColumns() throws SQLException {
        ensureTableColumn("X1_FUSION_POLICY", "REVIEWED_BY_ID", "BIGINT");
        ensureTableColumn("X1_POLICY_DOCUMENT", "REVIEWED_BY_ID", "BIGINT");
        conn.commit();
    }

    private static void ensureFusionPolicyFrontendVisibleColumn() throws SQLException {
        ensureTableColumn("X1_FUSION_POLICY", "FRONT_VISIBLE", "CHAR(1) DEFAULT '1'");
        try (Statement stmt = conn.createStatement()) {
            stmt.executeUpdate("UPDATE SYSDBA.X1_FUSION_POLICY SET FRONT_VISIBLE='1' WHERE FRONT_VISIBLE IS NULL OR TRIM(FRONT_VISIBLE)=''");
        }
        conn.commit();
    }

    private static void ensurePolicyReviewUserIdColumns() throws SQLException {
        ensureTableColumn("POLICY_METADATA", "REVIEWED_BY_ID", "BIGINT");
        ensureTableColumn("POLICY_ATTACHMENT", "REVIEWED_BY_ID", "BIGINT");
        conn.commit();
    }

    private static void ensureCategoryFieldInfoStatusColumn() throws SQLException {
        ensureTableColumn("CATEGORY_FIELD_INFO", "STATUS", "NUMBER(3) DEFAULT 1 NOT NULL");
        conn.commit();
    }

    private static void ensureTableColumn(String tableName, String columnName, String columnDefinition) throws SQLException {
        int exists = 0;
        try (PreparedStatement stmt = conn.prepareStatement(
                "SELECT COUNT(*) FROM ALL_TAB_COLUMNS WHERE OWNER='SYSDBA' AND TABLE_NAME=? AND COLUMN_NAME=?")) {
            stmt.setString(1, tableName);
            stmt.setString(2, columnName);
            try (ResultSet rs = stmt.executeQuery()) {
                if (rs.next()) exists = rs.getInt(1);
            }
        }
        if (exists > 0) return;
        try (Statement stmt = conn.createStatement()) {
            stmt.executeUpdate("ALTER TABLE SYSDBA." + tableName + " ADD " + columnName + " " + columnDefinition);
        }
        System.out.println("[JDBC] Added SYSDBA." + tableName + "." + columnName + " column");
    }

    private static void ensureX1DocumentAttributeConstraint() throws SQLException {
        int exists = 0;
        try (PreparedStatement stmt = conn.prepareStatement(
                "SELECT COUNT(*) FROM ALL_CONSTRAINTS WHERE OWNER='SYSDBA' AND TABLE_NAME='X1_POLICY_DOCUMENT' AND CONSTRAINT_NAME='CK_X1_DOCUMENT_ATTRIBUTE'");
             ResultSet rs = stmt.executeQuery()) {
            if (rs.next()) exists = rs.getInt(1);
        }
        if (exists > 0) {
            try (Statement stmt = conn.createStatement()) {
                stmt.executeUpdate("ALTER TABLE SYSDBA.X1_POLICY_DOCUMENT DROP CONSTRAINT CK_X1_DOCUMENT_ATTRIBUTE");
            }
        }
        try (Statement stmt = conn.createStatement()) {
            stmt.executeUpdate("ALTER TABLE SYSDBA.X1_POLICY_DOCUMENT ADD CONSTRAINT CK_X1_DOCUMENT_ATTRIBUTE CHECK " +
                "(POLICY_ATTRIBUTE IN ('实施细则','指导政策','通知公告','申报材料类','申报通知','结果公告'))");
        }
        conn.commit();
    }

    private static void ensureFusionTraceCacheTable() throws SQLException {
        int exists = 0;
        try (PreparedStatement stmt = conn.prepareStatement(
                "SELECT COUNT(*) FROM ALL_TABLES WHERE OWNER='SYSDBA' AND TABLE_NAME='FUSION_TRACE_CACHE'");
             ResultSet rs = stmt.executeQuery()) {
            if (rs.next()) exists = rs.getInt(1);
        }
        if (exists > 0) return;
        try (Statement stmt = conn.createStatement()) {
            stmt.executeUpdate(
                "CREATE TABLE SYSDBA.FUSION_TRACE_CACHE (" +
                "ID VARCHAR2(50) PRIMARY KEY," +
                "FUSION_POLICY_ID VARCHAR2(50)," +
                "FIELD_CODE VARCHAR2(300)," +
                "FIELD_NAME VARCHAR2(200)," +
                "FIELD_PATH VARCHAR2(500)," +
                "RECORD_TYPE VARCHAR2(100)," +
                "RECORD_ID VARCHAR2(100)," +
                "FINAL_VALUE CLOB," +
                "VALUE_HASH VARCHAR2(64)," +
                "SOURCE_POLICY_ID VARCHAR2(50)," +
                "SOURCE_POLICY VARCHAR2(500)," +
                "SOURCE_POLICY_ATTRIBUTE VARCHAR2(100)," +
                "SOURCE_TYPE VARCHAR2(100)," +
                "SOURCE_FILE_PATH VARCHAR2(1000)," +
                "TECHNICAL_PATH VARCHAR2(300)," +
                "EVIDENCE CLOB," +
                "DESCRIPTION CLOB," +
                "VALIDATION_STATUS VARCHAR2(50)," +
                "CONFIRM_STATUS VARCHAR2(50)," +
                "MATCH_METHOD VARCHAR2(50)," +
                "CONFIDENCE DECIMAL(10,4)," +
                "CONFIRMED_BY VARCHAR2(100)," +
                "CONFIRMED_AT TIMESTAMP," +
                "DEL_FLAG VARCHAR2(1) DEFAULT '0'," +
                "CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP," +
                "UPDATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP" +
                ")"
            );
        }
        try (Statement stmt = conn.createStatement()) {
            stmt.executeUpdate("CREATE INDEX IDX_FUSION_TRACE_CACHE_KEY ON SYSDBA.FUSION_TRACE_CACHE(FUSION_POLICY_ID, FIELD_CODE, VALUE_HASH, DEL_FLAG)");
        } catch (SQLException ignored) {}
        conn.commit();
        System.out.println("[JDBC] FUSION_TRACE_CACHE table created");
    }

    private static void ensureFusionPolicyRelationTable() throws SQLException {
        int exists = 0;
        try (PreparedStatement stmt = conn.prepareStatement(
                "SELECT COUNT(*) FROM ALL_TABLES WHERE OWNER='SYSDBA' AND TABLE_NAME='X1_FUSION_POLICY_RELATION'");
             ResultSet rs = stmt.executeQuery()) {
            if (rs.next()) exists = rs.getInt(1);
        }
        boolean created = false;
        if (exists <= 0) {
            try (Statement stmt = conn.createStatement()) {
                stmt.executeUpdate(
                    "CREATE TABLE SYSDBA.X1_FUSION_POLICY_RELATION (" +
                    "ID BIGINT PRIMARY KEY," +
                    "PARENT_FUSION_POLICY_ID BIGINT NOT NULL," +
                    "CHILD_FUSION_POLICY_ID BIGINT NOT NULL," +
                    "DEL_FLAG CHAR(1) DEFAULT '0'," +
                    "CREATED_BY VARCHAR2(100)," +
                    "UPDATED_BY VARCHAR2(100)," +
                    "CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP," +
                    "UPDATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP" +
                    ")"
                );
            }
            try (Statement stmt = conn.createStatement()) {
                stmt.executeUpdate("CREATE INDEX IDX_X1_FUSION_REL_CHILD ON SYSDBA.X1_FUSION_POLICY_RELATION(CHILD_FUSION_POLICY_ID, DEL_FLAG)");
            } catch (SQLException ignored) {}
            try (Statement stmt = conn.createStatement()) {
                stmt.executeUpdate("CREATE INDEX IDX_X1_FUSION_REL_PARENT ON SYSDBA.X1_FUSION_POLICY_RELATION(PARENT_FUSION_POLICY_ID, DEL_FLAG)");
            } catch (SQLException ignored) {}
            created = true;
        }
        ensureFusionPolicyRelationAuditColumns();
        conn.commit();
        if (created) {
            System.out.println("[JDBC] X1_FUSION_POLICY_RELATION table created");
        }
    }

    private static void ensureFusionPolicyRelationAuditColumns() throws SQLException {
        ensureFusionPolicyRelationColumn("CREATED_BY", "VARCHAR2(100)");
        ensureFusionPolicyRelationColumn("UPDATED_BY", "VARCHAR2(100)");
    }

    private static void ensureFusionPolicyRelationColumn(String columnName, String columnDefinition) throws SQLException {
        int exists = 0;
        try (PreparedStatement stmt = conn.prepareStatement(
                "SELECT COUNT(*) FROM ALL_TAB_COLUMNS WHERE OWNER='SYSDBA' AND TABLE_NAME='X1_FUSION_POLICY_RELATION' AND COLUMN_NAME=?")) {
            stmt.setString(1, columnName);
            try (ResultSet rs = stmt.executeQuery()) {
                if (rs.next()) exists = rs.getInt(1);
            }
        }
        if (exists > 0) return;
        try (Statement stmt = conn.createStatement()) {
            stmt.executeUpdate("ALTER TABLE SYSDBA.X1_FUSION_POLICY_RELATION ADD " + columnName + " " + columnDefinition);
        }
    }

    private static final String DEFAULT_AUDIT_OPERATOR = "系统";

    private static String resolveAuditValue(Map<String, String> data, String... keys) {
        if (data == null || keys == null) return "";
        for (String key : keys) {
            String value = nullToEmpty(data.getOrDefault(key, "")).trim();
            if (!value.isEmpty()) return value;
        }
        return "";
    }

    private static String resolveAuditTraceId(Map<String, String> data) {
        String traceId = resolveAuditValue(data, "traceId", "TRACE_ID");
        return traceId.isEmpty() ? UUID.randomUUID().toString().replace("-", "") : traceId;
    }

    private static String resolveAuditOperatorId(Map<String, String> data) {
        return resolveAuditValue(data, "operatorId", "userId", "reviewedById");
    }

    private static void setNullableBigIntString(PreparedStatement stmt, int parameterIndex, String value) throws SQLException {
        String normalized = nullToEmpty(value).trim();
        if (normalized.isEmpty()) {
            stmt.setNull(parameterIndex, Types.BIGINT);
            return;
        }
        try {
            stmt.setLong(parameterIndex, Long.parseLong(normalized));
        } catch (NumberFormatException e) {
            stmt.setNull(parameterIndex, Types.BIGINT);
        }
    }

    private static String resolveAuditOperatorId(Connection connection, Map<String, String> data) throws SQLException {
        String operatorId = resolveAuditOperatorId(data);
        if (!nullToEmpty(operatorId).trim().isEmpty() || connection == null) return operatorId;
        String operatorName = resolveAuditOperatorName(data);
        if (operatorName.isEmpty() || DEFAULT_AUDIT_OPERATOR.equals(operatorName)) return "";
        try (PreparedStatement stmt = connection.prepareStatement(
                "SELECT ID FROM SYSDBA.SYS_USER WHERE (USERNAME = ? OR REAL_NAME = ?) AND STATUS = 1 AND ROWNUM = 1")) {
            stmt.setString(1, operatorName);
            stmt.setString(2, operatorName);
            try (ResultSet rs = stmt.executeQuery()) {
                if (rs.next()) return nullToEmpty(rs.getString("ID")).trim();
            }
        }
        return "";
    }

    private static String resolveAuditOperatorName(Map<String, String> data) {
        return normalizeOperatorName(resolveAuditValue(data, "operatorName", "operator", "reviewedBy", "updatedBy", "createdBy"));
    }

    private static String resolveAuditModule(Map<String, String> data, String defaultModule) {
        String module = resolveAuditValue(data, "module");
        return module.isEmpty() ? defaultModule : module;
    }

    private static String resolveAuditAction(Map<String, String> data, String defaultAction) {
        String action = resolveAuditValue(data, "action");
        return action.isEmpty() ? defaultAction : action;
    }

    private static Map<String, Object> querySingleRow(String sql, Object... params) throws SQLException {
        return querySingleRow(conn, sql, params);
    }

    private static Map<String, Object> querySingleRow(Connection connection, String sql, Object... params) throws SQLException {
        try (PreparedStatement stmt = connection.prepareStatement(sql)) {
            for (int i = 0; i < params.length; i++) {
                stmt.setObject(i + 1, params[i]);
            }
            try (ResultSet rs = stmt.executeQuery()) {
                if (!rs.next()) return null;
                return resultSetRowToMap(rs);
            }
        }
    }

    private static List<Map<String, Object>> queryRows(String sql, Object... params) throws SQLException {
        return queryRows(conn, sql, params);
    }

    private static List<Map<String, Object>> queryRows(Connection connection, String sql, Object... params) throws SQLException {
        List<Map<String, Object>> rows = new ArrayList<>();
        try (PreparedStatement stmt = connection.prepareStatement(sql)) {
            for (int i = 0; i < params.length; i++) {
                stmt.setObject(i + 1, params[i]);
            }
            try (ResultSet rs = stmt.executeQuery()) {
                while (rs.next()) {
                    rows.add(resultSetRowToMap(rs));
                }
            }
        }
        return rows;
    }

    private static Map<String, Object> resultSetRowToMap(ResultSet rs) throws SQLException {
        Map<String, Object> row = new LinkedHashMap<>();
        ResultSetMetaData meta = rs.getMetaData();
        for (int i = 1; i <= meta.getColumnCount(); i++) {
            String label = meta.getColumnLabel(i);
            int type = meta.getColumnType(i);
            if (type == Types.CLOB || type == Types.NCLOB) {
                row.put(label, nullToEmpty(rs.getString(i)));
            } else if (type == Types.TIMESTAMP || type == Types.TIMESTAMP_WITH_TIMEZONE || type == Types.DATE) {
                row.put(label, nullToEmpty(rs.getString(i)));
            } else {
                Object value = rs.getObject(i);
                row.put(label, value);
            }
        }
        return row;
    }

    private static void insertAuditLog(String traceId, String module, String action,
                                       String targetTable, String targetId, String bizKey,
                                       String operatorId, String operatorName,
                                       Object beforeData, Object afterData, String remark) throws SQLException {
        insertAuditLog(conn, traceId, module, action, targetTable, targetId, bizKey, operatorId, operatorName, beforeData, afterData, remark);
    }

    private static void insertAuditLog(Connection connection, String traceId, String module, String action,
                                       String targetTable, String targetId, String bizKey,
                                       String operatorId, String operatorName,
                                       Object beforeData, Object afterData, String remark) throws SQLException {
        String sql = "INSERT INTO SYSDBA.OPERATION_AUDIT_LOG " +
            "(ID, TRACE_ID, MODULE, ACTION, TARGET_TABLE, TARGET_ID, BIZ_KEY, OPERATOR_ID, OPERATOR_NAME, BEFORE_JSON, AFTER_JSON, REMARK, CREATED_AT) " +
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)";
        try (PreparedStatement stmt = connection.prepareStatement(sql)) {
            stmt.setString(1, UUID.randomUUID().toString());
            stmt.setString(2, traceId);
            stmt.setString(3, module);
            stmt.setString(4, action);
            stmt.setString(5, targetTable);
            stmt.setString(6, targetId);
            stmt.setString(7, bizKey);
            stmt.setString(8, operatorId);
            stmt.setString(9, normalizeOperatorName(operatorName));
            stmt.setString(10, beforeData == null ? null : toJson(beforeData));
            stmt.setString(11, afterData == null ? null : toJson(afterData));
            stmt.setString(12, nullToEmpty(remark).trim());
            stmt.executeUpdate();
        }
    }

    // 检查并重新建立数据库连接（synchronized 防止多线程并发踩踏）
    private static synchronized void ensureConnection() throws SQLException {
        try {
            if (conn == null || conn.isClosed() || !conn.isValid(2)) {
                System.out.println("[JDBC] 连接已断开，正在重新建立...");
                if (conn != null && !conn.isClosed()) {
                    try { conn.close(); } catch (SQLException ignored) {}
                }
                Properties props = new Properties();
                props.setProperty("user", DB_USER);
                props.setProperty("password", DB_PASSWORD);
                props.setProperty("localTimezone", "Asia/Shanghai");
                props.setProperty("connectTimeout", "5000");   // 连接超时 5秒
                props.setProperty("socketTimeout", "30000");   // 查询超时 30秒
                DriverManager.setLoginTimeout(5);               // 登录超时 5秒
                conn = DriverManager.getConnection(JDBC_URL, props);
                initializeSessionTimezone(conn);
                System.out.println("[JDBC] 重新连接成功");
            }
        } catch (SQLException e) {
            System.err.println("[JDBC] 重新连接失败: " + e.getMessage());
            throw e;
        }
    }

    private static Connection createDedicatedConnection() throws SQLException {
        Properties props = new Properties();
        props.setProperty("user", DB_USER);
        props.setProperty("password", DB_PASSWORD);
        props.setProperty("localTimezone", "Asia/Shanghai");
        props.setProperty("connectTimeout", "5000");
        props.setProperty("socketTimeout", "30000");
        Connection connection = DriverManager.getConnection(JDBC_URL, props);
        initializeSessionTimezone(connection);
        return connection;
    }

    private static void initializeSessionTimezone(Connection connection) throws SQLException {
        if (connection == null) return;
        try (Statement stmt = connection.createStatement()) {
            // 达梦的 CURRENT_TIMESTAMP 依赖会话时区，显式固定为东八区，避免 JDBC 会话落库少 8 小时
            stmt.execute("SET TIME ZONE '+8:00'");
        }
    }

    private static final Set<String> FUSION_DOCUMENT_TYPES = new LinkedHashSet<>(Arrays.asList(
        "实施细则", "申报通知", "指导政策", "通知公告", "结果公告"
    ));
    private static final Set<String> FUSION_ATTACHMENT_TYPES = new LinkedHashSet<>(Arrays.asList(
        "实施细则类", "指导政策类", "申报材料类", "申报通知类", "结果公告类", "通知公告类", "其它"
    ));
    private static final String FUSION_REVIEW_STATUS_PENDING = "0";
    private static final String FUSION_REVIEW_STATUS_APPROVED = "1";
    private static final String STRUCTURE_REVIEW_STATUS_PENDING = "0";
    private static final String STRUCTURE_REVIEW_STATUS_APPROVED = "1";

    private static String normalizePolicyAttributeLabel(String value) {
        String type = value == null ? "" : value.trim();
        if ("通知公告".equals(type)) {
            return "申报通知";
        }
        return type;
    }

    private static String normalizeFusionDocumentType(String value) {
        String type = value == null ? "" : value.trim();
        if (type.endsWith("类")) {
            type = type.substring(0, type.length() - 1);
        }
        return normalizePolicyAttributeLabel(type);
    }

    private static boolean isAttachmentLikeFusionDocument(String rawPolicyAttribute, String rawFilePath, String rawFileSuffix) {
        String normalizedType = rawPolicyAttribute == null ? "" : rawPolicyAttribute.trim();
        if (FUSION_ATTACHMENT_TYPES.contains(normalizedType)) {
            return true;
        }
        String normalizedPath = rawFilePath == null ? "" : rawFilePath.trim().replace('\\', '/').toLowerCase();
        if (normalizedPath.contains("/attachments/")) {
            return true;
        }
        String normalizedSuffix = rawFileSuffix == null ? "" : rawFileSuffix.trim().toLowerCase();
        return "pdf".equals(normalizedSuffix) && normalizedPath.contains("/attachments/");
    }

    private static String buildFusionDocumentDedupKey(String sourcePolicyId, String rawFilePath, String displayFilePath, String id) {
        String normalizedSourcePolicyId = sourcePolicyId == null ? "" : sourcePolicyId.trim();
        if (!normalizedSourcePolicyId.isEmpty()) {
            return "sourcePolicyId:" + normalizedSourcePolicyId;
        }
        String path = rawFilePath == null ? "" : rawFilePath.trim();
        if (path.isEmpty()) {
            path = displayFilePath == null ? "" : displayFilePath.trim();
        }
        if (!path.isEmpty()) {
            return "filePath:" + path.replace('\\', '/').toLowerCase();
        }
        return "id:" + (id == null ? "" : id.trim());
    }

    // ==================== 核心列定义 ====================
    // POLICY_ORIGINAL_V2 表实际列:
    //   ID (VARCHAR), POLICY_TITLE (VARCHAR), URL (CLOB),
    //   POLICY_CODE (VARCHAR), PUBLISH_DATE (VARCHAR),
    //   FILE_TYPE (VARCHAR), ISSUE_DEPARTMENT (VARCHAR),
    //   FILE_PATH (VARCHAR), CREATE_AT (TIMESTAMP)
    // POLICY_ATTACHMENT 表: ATTACH_ID, POLICY_ID, ATTACH_NAME, ATTACH_SHOW_NAME, FILE_PATH, FILE_SUFFIX, CREATE_AT
    // 注意: TYPE, ATTACHMENT_TYPE, STATUS, UUID 等列不在数据库中，
    //       它们由前端或业务逻辑层维护。

    private static final String SELECT_COLUMNS =
        "SELECT p.POLICY_ID, p.POLICY_TITLE, p.POLICY_CODE, p.PUBLISH_DATE, " +
        "p.ISSUE_DEPARTMENT, p.FILE_TYPE, p.CREATE_AT, " +
        "(SELECT COUNT(*) FROM SYSDBA.POLICY_ATTACHMENT a WHERE a.POLICY_ID = p.POLICY_ID) AS POLICY_ATTACHMENT_COUNT, " +
        "(SELECT m.POLICY_ATTRIBUTE FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID = p.POLICY_ID AND ROWNUM = 1) AS POLICY_TYPE, " +
        "(SELECT m.REVIEW_STATUS FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID = p.POLICY_ID AND ROWNUM = 1) AS REVIEW_STATUS";

    private static final String FROM_TABLE = "FROM SYSDBA.POLICY_ORIGINAL_V2 p LEFT JOIN SYSDBA.POLICY_METADATA m ON p.POLICY_ID = m.POLICY_ID";

    public static void main(String[] args) {
        try {
            Class.forName(JDBC_DRIVER);
            System.out.println("[JDBC] 达梦驱动加载成功");

            try {
                ensureConnection();
                ensurePolicyReviewStatusColumn();
                ensurePolicyRemarkColumn();
                ensureAttachmentReviewStatusColumn();
                ensureX1DocumentAttributeConstraint();
                ensureFusionReviewUserIdColumns();
                ensureFusionPolicyFrontendVisibleColumn();
                ensurePolicyReviewUserIdColumns();
                ensureCategoryFieldInfoStatusColumn();
                ensureFusionPolicyRelationTable();
                System.out.println("[JDBC] 数据库连接成功: " + DB_HOST + ":" + DB_PORT);
            } catch (SQLException e) {
                System.err.println("[JDBC] 数据库初始化失败，服务仍会启动并在请求时重连: " + e.getMessage());
            }

            server = HttpServer.create(new InetSocketAddress(SERVICE_PORT), 50);

            // ==================== 原有接口 ====================
            server.createContext("/api/policies", new PoliciesHandler());
            server.createContext("/api/classification/policies", new ClassificationPoliciesHandler());
            server.createContext("/api/classification/policies/delete", new ClassificationPolicyDeleteHandler());
            server.createContext("/api/fusion-classification/policies", new FusionClassificationHandler(FUSION_REVIEW_STATUS_APPROVED, true));
            server.createContext("/api/fusion-classification/policies/policy-classification", new FusionClassificationHandler("", false));
            server.createContext("/api/fusion-classification/policies/create", new FusionPolicyCreateHandler());
            server.createContext("/api/fusion-classification/documents/create", new FusionDocumentCreateHandler());
            server.createContext("/api/fusion-classification/documents/candidates", new FusionDocumentCandidatesHandler());
            server.createContext("/api/fusion-classification/documents/link", new FusionDocumentLinkHandler());
            server.createContext("/api/fusion-classification/documents/type", new FusionDocumentTypeHandler());
            server.createContext("/api/fusion-classification/documents/content", new FusionDocumentContentHandler());
            server.createContext("/api/fusion-classification/policies/delete", new FusionPolicyDeleteHandler());
            server.createContext("/api/fusion-classification/policies/restore", new FusionPolicyRestoreHandler());
            server.createContext("/api/fusion-classification/policies/candidates", new FusionPolicyOriginalCandidatesHandler());
            server.createContext("/api/fusion-classification/policies/link-from-original", new FusionPolicyLinkFromOriginalHandler());
            server.createContext("/api/fusion-classification/policies/status", new FusionPolicyStatusHandler());
            server.createContext("/api/fusion-classification/policies/update", new FusionPolicyUpdateHandler());
            server.createContext("/api/fusion-classification/policies/parent/update", new FusionPolicyParentUpdateHandler());
            server.createContext("/api/fusion-classification/policies/review/approve", new FusionPolicyReviewApproveHandler());
            server.createContext("/api/fusion-classification/policies/review/revoke", new FusionPolicyReviewRevokeHandler());
            server.createContext("/api/fusion-classification/structure/review/approve", new FusionStructureReviewApproveHandler());
            server.createContext("/api/fusion-classification/structure/review/revoke", new FusionStructureReviewRevokeHandler());
            server.createContext("/api/fusion-classification/documents/delete", new FusionDocumentDeleteHandler());
            server.createContext("/api/fusion-classification/documents/restore", new FusionDocumentRestoreHandler());
            server.createContext("/api/fusion-classification/documents/move", new FusionDocumentMoveHandler());
            server.createContext("/api/fusion-classification/documents/update", new FusionDocumentUpdateHandler());
            server.createContext("/api/policies/update", new PolicyUpdateHandler());
            server.createContext("/api/policies/approve", new PolicyApproveHandler());
            server.createContext("/api/policies/revoke", new PolicyRevokeHandler());
            server.createContext("/api/policies/remark", new PolicyRemarkHandler());
            server.createContext("/api/statistics", new StatisticsHandler());
            server.createContext("/api/filterStats", new FilterStatsHandler());
            server.createContext("/api/policy-types", new PolicyTypesHandler());
            server.createContext("/api/policy-types/update", new PolicyTypeUpdateHandler());
            server.createContext("/api/attachment-types", new AttachmentTypesHandler());
            server.createContext("/api/attachment-types/update", new AttachmentTypeUpdateHandler());
            server.createContext("/api/attachment-types/approve", new AttachmentTypeApproveHandler());
            server.createContext("/api/attachment-types/revoke", new AttachmentTypeRevokeHandler());
            server.createContext("/api/policy-original/detail", new PolicyOriginalDetailHandler());
            server.createContext("/api/policy-original/content", new PolicyOriginalContentHandler());
            server.createContext("/api/policy/content", new PolicyOriginalContentHandler());
            server.createContext("/api/attachment/list", new AttachmentListHandler());
            server.createContext("/api/attachment/meta", new AttachmentMetaHandler());
            server.createContext("/api/attachment/content", new AttachmentContentHandler());
            server.createContext("/api/pematching/enterprise-match-results/enterprises", new EnterpriseMatchEnterprisesHandler());
            server.createContext("/api/pematching/enterprise-match-results", new EnterpriseMatchResultsHandler());
            server.createContext("/api/health", new HealthHandler());
            server.createContext("/api/fusion-classification/stats", new FusionStatsHandler());
            server.createContext("/api/fusion-classification/fuse", new FusionFuseHandler());
            server.createContext("/api/fusion-classification/fusion-detail", new FusionDetailHandler());
            server.createContext("/api/fusion-classification/fusion-detail/save", new FusionDetailSaveHandler());
            server.createContext("/api/fusion-trace/cache", new FusionTraceCacheHandler());
            server.createContext("/api/fusion-trace/save", new FusionTraceSaveHandler());
            server.createContext("/api/auth/login", new LoginHandler());

            // ==================== 基础数据字典接口 (合并新增) ====================
            server.createContext("/api/baseDataDict", new BaseDataDictHandler());
            server.createContext("/api/baseDataDict/create", new BaseDataDictCreateHandler());
            server.createContext("/api/baseDataDict/update", new BaseDataDictUpdateHandler());
            server.createContext("/api/baseDataDict/delete", new BaseDataDictDeleteHandler());
            server.createContext("/api/baseDataRecords", new BaseDataRecordsHandler());
            server.createContext("/api/baseDataRecords/create", new BaseDataRecordCreateHandler());
            server.createContext("/api/baseDataRecords/update", new BaseDataRecordUpdateHandler());
            server.createContext("/api/baseDataRecords/delete", new BaseDataRecordDeleteHandler());
            server.createContext("/api/baseDataRecords/clear", new BaseDataRecordClearHandler());

            // ==================== 申报条件字典接口 (合并新增) ====================
            server.createContext("/api/applicationConditionDict", new ApplicationConditionDictHandler());
            server.createContext("/api/applicationConditionDict/create", new ApplicationConditionDictCreateHandler());
            server.createContext("/api/applicationConditionDict/update", new ApplicationConditionDictUpdateHandler());
            server.createContext("/api/applicationConditionDict/delete", new ApplicationConditionDictDeleteHandler());

            // ==================== 申报条件字段信息接口 (CATEGORY_FIELD_INFO) ====================
            server.createContext("/api/categoryFieldInfo", new CategoryFieldInfoHandler());
            server.createContext("/api/categoryFieldInfo/create", new CategoryFieldInfoCreateHandler());
            server.createContext("/api/categoryFieldInfo/update", new CategoryFieldInfoUpdateHandler());
            server.createContext("/api/categoryFieldInfo/delete", new CategoryFieldInfoDeleteHandler());

            // ==================== 适用范围选项接口 (DIM_RULE_OPTION scope_level) ====================
            server.createContext("/api/rule-options/scope-level", new ScopeLevelOptionsHandler());

            // ==================== 行业领域接口 (SYS_TERRITORY_DATA_NEW / SYS_TERRITORY_INDUSTRY_22N) ====================
            server.createContext("/api/territory/industry-names", new TerritoryIndustryNamesHandler());
            server.createContext("/api/territory/industry-22n", new TerritoryIndustry22NHandler());
            server.createContext("/api/crawler/xmsme/import", new XmsmeCrawlerImportHandler());

            server.setExecutor(Executors.newFixedThreadPool(10));
            server.start();

            String sep = repeat('-', 55);
            System.out.println(sep);
            System.out.println("🚀 JDBC 中间层服务已启动");
            System.out.println(sep);
            System.out.println("📡 服务地址: http://localhost:" + SERVICE_PORT);
            System.out.println("📊 数据库: " + DB_HOST + ":" + DB_PORT + " 模式: " + DB_SCHEMA);
            System.out.println(sep);
            System.out.println("📋 可用接口:");
            System.out.println("   [政策归类标注]");
            System.out.println("   GET  /api/policies        - 获取政策列表");
            System.out.println("   GET  /api/statistics      - 获取统计数据");
            System.out.println("   GET  /api/filterStats     - 获取筛选统计");
            System.out.println("   POST /api/policies/update - 更新政策分类");
            System.out.println("   GET  /api/health          - 健康检查");
            System.out.println(sep);
            System.out.println("   [基础数据字典 - BASE_DATA_DICT] (新增)");
            System.out.println("   GET  /api/baseDataDict            - 获取基础数据列表");
            System.out.println("   POST /api/baseDataDict/create     - 创建基础数据");
            System.out.println("   POST /api/baseDataDict/update     - 更新基础数据");
            System.out.println("   POST /api/baseDataDict/delete     - 删除基础数据");
            System.out.println(sep);
            System.out.println("   [申报条件字典 - APPLICATION_CONDITION_DICT] (新增)");
            System.out.println("   GET  /api/applicationConditionDict            - 获取申报条件列表");
            System.out.println("   POST /api/applicationConditionDict/create     - 创建申报条件");
            System.out.println("   POST /api/applicationConditionDict/update     - 更新申报条件");
            System.out.println("   POST /api/applicationConditionDict/delete     - 删除申报条件");
            System.out.println(sep);

            while (true) {
                try {
                    Thread.sleep(60 * 60 * 1000L);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    break;
                }
            }

        } catch (ClassNotFoundException e) {
            System.err.println("[JDBC] 错误: 找不到达梦 JDBC 驱动!");
            System.exit(1);
        } catch (IOException e) {
            System.err.println("[JDBC] 服务启动失败: " + e.getMessage());
            System.exit(1);
        }
    }

    // ==================== 路由处理器 ====================

    /**
     * 获取政策列表
     */
    static class PoliciesHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);

            if ("OPTIONS".equals(exchange.getRequestMethod())) {
                sendResponse(exchange, 200, "{\"success\":true}");
                return;
            }

            try {
                ensureConnection(); // 检查并重新建立连接

                Map<String, String> params = parseQueryParams(exchange.getRequestURI().getQuery());
                int page = parseInt(params.getOrDefault("page", "1"), 1);
                int size = parseInt(params.getOrDefault("size", "10"), 10);
                String keyword = params.getOrDefault("keyword", "");
                String type = params.getOrDefault("type", "");
                String status = params.getOrDefault("status", "");
                String startDate = params.getOrDefault("startDate", "");
                String endDate = params.getOrDefault("endDate", "");
                String department = params.getOrDefault("department", "").trim();
                String scope = params.getOrDefault("scope", "").trim();

                int offset = (page - 1) * size;

                // 构建 SQL
                StringBuilder where = new StringBuilder(" WHERE 1=1");
                List<String> paramList = new ArrayList<>();

                if (!keyword.isEmpty()) {
                    where.append(" AND (p.POLICY_TITLE LIKE ? OR p.ISSUE_DEPARTMENT LIKE ?)");
                    paramList.add("%" + keyword + "%");
                    paramList.add("%" + keyword + "%");
                }

                if (!startDate.isEmpty()) {
                    where.append(" AND p.PUBLISH_DATE >= ?");
                    paramList.add(startDate);
                }

                if (!endDate.isEmpty()) {
                    where.append(" AND p.PUBLISH_DATE <= ?");
                    paramList.add(endDate);
                }

                if (!type.isEmpty()) {
                    where.append(" AND m.POLICY_ATTRIBUTE = ?");
                    paramList.add(type);
                }

                if (!department.isEmpty()) {
                    where.append(" AND m.ISSUING_DEPARTMENT LIKE ?");
                    paramList.add("%" + department + "%");
                }

                if (!scope.isEmpty()) {
                    where.append(" AND m.APPLICABLE_SCOPE LIKE ?");
                    paramList.add("%" + scope + "%");
                }

                if ("已标注".equals(status)) {
                    where.append(" AND m.REVIEW_STATUS = '已通过'");
                } else if ("未标注".equals(status)) {
                    where.append(" AND (m.REVIEW_STATUS IS NULL OR m.REVIEW_STATUS <> '已通过')");
                }

                // 获取总数
                String countSql = "SELECT COUNT(*) AS TOTAL " + FROM_TABLE + where;
                PreparedStatement countStmt = conn.prepareStatement(countSql);
                for (int i = 0; i < paramList.size(); i++) {
                    countStmt.setString(i + 1, paramList.get(i));
                }
                ResultSet countRs = countStmt.executeQuery();
                int total = 0;
                if (countRs.next()) {
                    total = countRs.getInt("TOTAL");
                }
                countRs.close();
                countStmt.close();

                // 分页查询
                String querySql = SELECT_COLUMNS + " " + FROM_TABLE + where +
                    " ORDER BY p.CREATE_AT DESC LIMIT ? OFFSET ?";
                paramList.add(String.valueOf(size));
                paramList.add(String.valueOf(offset));

                PreparedStatement stmt = conn.prepareStatement(querySql);
                for (int i = 0; i < paramList.size(); i++) {
                    stmt.setString(i + 1, paramList.get(i));
                }

                ResultSet rs = stmt.executeQuery();
                List<Map<String, Object>> dataList = new ArrayList<>();
                int idx = offset + 1;

                while (rs.next()) {
                    Map<String, Object> row = new LinkedHashMap<>();
                    String uuid = rs.getString("POLICY_ID");
                    row.put("id", idx++);
                    row.put("uuid", uuid);
                    row.put("title", nullToEmpty(rs.getString("POLICY_TITLE")));
                    row.put("documentNo", nullToEmpty(rs.getString("POLICY_CODE")));
                    row.put("publishDate", nullToEmpty(rs.getString("PUBLISH_DATE")));
                    row.put("department", nullToEmpty(rs.getString("ISSUE_DEPARTMENT")));
                    row.put("fileType", nullToEmpty(rs.getString("FILE_TYPE")));
                    String policyType = normalizeFusionDocumentType(rs.getString("POLICY_TYPE"));
                    String reviewStatus = nullToEmpty(rs.getString("REVIEW_STATUS"));
                    row.put("type", policyType);
                    row.put("status", "已通过".equals(reviewStatus) ? "已标注" : "未标注");
                    row.put("attachmentCount", rs.getInt("POLICY_ATTACHMENT_COUNT"));
                    dataList.add(row);
                }
                rs.close();
                stmt.close();

                Map<String, Object> response = new LinkedHashMap<>();
                response.put("success", true);
                response.put("data", dataList);
                Map<String, Object> pagination = new LinkedHashMap<>();
                pagination.put("page", page);
                pagination.put("size", size);
                pagination.put("total", total);
                pagination.put("pages", (int) Math.ceil((double) total / size));
                response.put("pagination", pagination);

                sendJson(exchange, 200, response);

            } catch (SQLException e) {
                System.err.println("[SQL Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /**
     * 更新政策分类（前端标注操作）
     * 更新 POLICY_METADATA 表的 POLICY_ATTRIBUTE 字段
     */
    static class PolicyUpdateHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);

            if (!"POST".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持 POST 方法"));
                return;
            }

            try {
                ensureConnection(); // 检查并重新建立连接

                String body = new String(readAllBytes(exchange.getRequestBody()), StandardCharsets.UTF_8);
                @SuppressWarnings("unchecked")
                Map<String, String> data = (Map<String, String>) parseJsonObject(body);

                String id = data.get("id");
                String type = data.getOrDefault("type", "");
                String operatorName = resolveAuditOperatorName(data);

                if (id == null || id.isEmpty()) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "缺少政策ID"));
                    return;
                }

                Map<String, Object> beforeRow = loadPolicyMetadataRow(conn, id);
                int updated = upsertPolicyMetadataType(conn, id, type, operatorName);

                if (updated > 0) {
                    writeAuditMutation(conn, data, "policy-classification", "update", "POLICY_METADATA", id, id, beforeRow, loadPolicyMetadataRow(conn, id), "政策属性更新");
                    sendJson(exchange, 200, buildMap("success", true, "message", "更新成功"));
                } else {
                    sendJson(exchange, 200, buildMap("success", false, "message", "更新失败"));
                }

            } catch (Exception e) {
                System.err.println("[Update Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /** 政策属性审核通过：只有已选择政策属性的记录才能通过。 */
    static class PolicyApproveHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if (!"POST".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持POST方法"));
                return;
            }
            try {
                ensureConnection();
                String body = new String(readAllBytes(exchange.getRequestBody()), StandardCharsets.UTF_8);
                @SuppressWarnings("unchecked")
                Map<String, String> data = (Map<String, String>) parseJsonObject(body);
                String id = data.get("id");
                String operatorName = resolveAuditOperatorName(data);
                String operatorId = resolveAuditOperatorId(conn, data);
                if (id == null || id.isEmpty()) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "缺少政策ID"));
                    return;
                }

                Map<String, Object> beforeRow = loadPolicyMetadataRow(conn, id);
                String sql = "UPDATE SYSDBA.POLICY_METADATA SET REVIEW_STATUS = '已通过', UPDATED_BY = ?, UPDATED_AT = CURRENT_TIMESTAMP " +
                    "WHERE POLICY_ID = ? AND POLICY_ATTRIBUTE IS NOT NULL AND POLICY_ATTRIBUTE <> ''";
                sql = sql.replace("UPDATED_BY = ?", "REVIEWED_BY_ID = ?, UPDATED_BY = ?");
                PreparedStatement stmt = conn.prepareStatement(sql);
                setNullableBigIntString(stmt, 1, operatorId);
                stmt.setString(2, operatorName);
                stmt.setString(3, id);
                int updated = stmt.executeUpdate();
                stmt.close();
                if (updated > 0) {
                    writeAuditMutation(conn, data, "policy-classification", "approve", "POLICY_METADATA", id, id, beforeRow, loadPolicyMetadataRow(conn, id), "政策属性审核通过");
                    sendJson(exchange, 200, buildMap("success", true, "message", "审核通过"));
                } else {
                    sendJson(exchange, 400, buildMap("success", false, "message", "请先选择政策属性"));
                }
            } catch (Exception e) {
                System.err.println("[Approve Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /** 撤销政策属性审核：保留政策属性，只恢复为未通过状态。 */
    static class PolicyRevokeHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if (!"POST".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持POST方法"));
                return;
            }
            try {
                ensureConnection();
                String body = new String(readAllBytes(exchange.getRequestBody()), StandardCharsets.UTF_8);
                @SuppressWarnings("unchecked")
                Map<String, String> data = (Map<String, String>) parseJsonObject(body);
                String id = data.get("id");
                String operatorName = resolveAuditOperatorName(data);
                if (id == null || id.isEmpty()) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "缺少政策ID"));
                    return;
                }

                Map<String, Object> beforeRow = loadPolicyMetadataRow(conn, id);
                String sql = "UPDATE SYSDBA.POLICY_METADATA SET REVIEW_STATUS = '未通过', UPDATED_BY = ?, UPDATED_AT = CURRENT_TIMESTAMP " +
                    "WHERE POLICY_ID = ? AND REVIEW_STATUS = '已通过'";
                PreparedStatement stmt = conn.prepareStatement(sql);
                stmt.setString(1, operatorName);
                stmt.setString(2, id);
                int updated = stmt.executeUpdate();
                stmt.close();
                if (updated > 0) {
                    writeAuditMutation(conn, data, "policy-classification", "revoke", "POLICY_METADATA", id, id, beforeRow, loadPolicyMetadataRow(conn, id), "政策属性审核撤销");
                    sendJson(exchange, 200, buildMap("success", true, "message", "审核已撤销"));
                } else {
                    sendJson(exchange, 400, buildMap("success", false, "message", "当前政策尚未通过审核"));
                }
            } catch (Exception e) {
                System.err.println("[Revoke Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /** 标记政策备注/错误记录 */
    static class PolicyRemarkHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if (!"POST".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持POST方法"));
                return;
            }
            try {
                ensureConnection();
                String body = new String(readAllBytes(exchange.getRequestBody()), StandardCharsets.UTF_8);
                @SuppressWarnings("unchecked")
                Map<String, String> data = (Map<String, String>) parseJsonObject(body);
                String id = data.get("id");
                String remark = data.get("remark");
                String operatorName = resolveAuditOperatorName(data);
                if (id == null || id.isEmpty()) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "缺少政策ID"));
                    return;
                }
                if (remark == null) {
                    remark = "";
                }

                // 先检查是否存在记录
                String checkSql = "SELECT COUNT(*) AS CNT FROM SYSDBA.POLICY_METADATA WHERE POLICY_ID = ?";
                PreparedStatement checkStmt = conn.prepareStatement(checkSql);
                checkStmt.setString(1, id);
                ResultSet rs = checkStmt.executeQuery();
                boolean exists = rs.next() && rs.getInt("CNT") > 0;
                rs.close();
                checkStmt.close();

                int updated;
                Map<String, Object> beforeRow = loadPolicyMetadataRow(conn, id);
                if (exists) {
                    String sql = "UPDATE SYSDBA.POLICY_METADATA SET REMARK = ?, UPDATED_BY = ?, UPDATED_AT = CURRENT_TIMESTAMP WHERE POLICY_ID = ?";
                    PreparedStatement stmt = conn.prepareStatement(sql);
                    stmt.setString(1, remark);
                    stmt.setString(2, operatorName);
                    stmt.setString(3, id);
                    updated = stmt.executeUpdate();
                    stmt.close();
                } else {
                    if (!policyExists(conn, id)) {
                        sendJson(exchange, 404, buildMap("success", false, "message", "政策主表不存在，无法写入备注；POLICY_ID=" + id));
                        return;
                    }
                    String insertSql = "INSERT INTO SYSDBA.POLICY_METADATA (POLICY_ID, POLICY_ATTRIBUTE, REMARK, REVIEW_STATUS, CREATED_AT, UPDATED_AT, CREATED_BY, UPDATED_BY) " +
                        "VALUES (?, '', ?, '未通过', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, ?)";
                    PreparedStatement stmt = conn.prepareStatement(insertSql);
                    stmt.setString(1, id);
                    stmt.setString(2, remark);
                    stmt.setString(3, operatorName);
                    stmt.setString(4, operatorName);
                    updated = stmt.executeUpdate();
                    stmt.close();
                }

                if (updated > 0) {
                    writeAuditMutation(conn, data, "policy-classification", "remark", "POLICY_METADATA", id, id, beforeRow, loadPolicyMetadataRow(conn, id), "政策备注更新");
                    sendJson(exchange, 200, buildMap("success", true, "message", "标记已保存"));
                } else {
                    sendJson(exchange, 400, buildMap("success", false, "message", "标记保存失败"));
                }
            } catch (Exception e) {
                System.err.println("[Remark Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /**
     * 获取统计数据
     */
    static class StatisticsHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);

            try {
                ensureConnection(); // 检查并重新建立连接
                int total = 0, tagged = 0, untagged = 0, todayNew = 0;

                // 从 POLICY_ORIGINAL_V2 表获取政策总数
                String countSql = "SELECT COUNT(*) AS CNT FROM SYSDBA.POLICY_ORIGINAL_V2";
                PreparedStatement stmt = conn.prepareStatement(countSql);
                ResultSet rs = stmt.executeQuery();
                if (rs.next()) total = rs.getInt("CNT");
                rs.close();
                stmt.close();

                // 已标注数量：以政策原表为基准，只统计存在有效属性的政策，避免元数据孤立记录导致数量超过总数
                try {
                    String taggedSql = "SELECT COUNT(*) AS CNT FROM SYSDBA.POLICY_ORIGINAL_V2 p " +
                        "WHERE EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA m " +
                        "WHERE m.POLICY_ID = p.POLICY_ID AND m.REVIEW_STATUS = '已通过')";
                    stmt = conn.prepareStatement(taggedSql);
                    rs = stmt.executeQuery();
                    if (rs.next()) tagged = rs.getInt("CNT");
                    rs.close();
                    stmt.close();
                } catch (SQLException e) {
                    tagged = 0;
                }

                untagged = Math.max(0, total - tagged);

                // 今日新增
                String todaySql = "SELECT COUNT(*) AS CNT FROM SYSDBA.POLICY_ORIGINAL_V2 WHERE CREATE_AT >= TRUNC(SYSDATE)";
                try {
                    stmt = conn.prepareStatement(todaySql);
                    rs = stmt.executeQuery();
                    if (rs.next()) todayNew = rs.getInt("CNT");
                    rs.close();
                    stmt.close();
                } catch (SQLException e) {
                    todayNew = 0;
                }

                Map<String, Object> response = new LinkedHashMap<>();
                response.put("success", true);

                Map<String, Object> data = new LinkedHashMap<>();
                data.put("totalPolicies", total);
                data.put("taggedCount", tagged);
                data.put("untaggedCount", untagged);
                data.put("todayNew", todayNew);
                data.put("tagRate", total > 0 ? String.format("%.1f", (double) tagged / total * 100) : "0.0");
                response.put("data", data);

                sendJson(exchange, 200, response);

            } catch (SQLException e) {
                System.err.println("[SQL Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /**
     * 获取政策分类列表（从POLICY_TYPE表）
     */
    static class PolicyTypesHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);

            if ("OPTIONS".equals(exchange.getRequestMethod())) {
                sendResponse(exchange, 200, "{\"success\":true}");
                return;
            }

            try {
                ensureConnection();
                Map<String, String> params = parseQueryParams(exchange.getRequestURI().getQuery());
                int page = parseInt(params.getOrDefault("page", "1"), 1);
                int size = parseInt(params.getOrDefault("size", "10"), 10);
                String keyword = params.getOrDefault("keyword", "");
                String policyType = params.getOrDefault("policyType", "");
                String attachmentType = params.getOrDefault("attachmentType", "");
                String markStatus = params.getOrDefault("markStatus", "");

                int offset = (page - 1) * size;

                // 从 POLICY_TYPE_V2 表查询
                StringBuilder where = new StringBuilder(" WHERE 1=1");
                List<String> paramList = new ArrayList<>();

                if (!keyword.isEmpty()) {
                    where.append(" AND (t.POLICY_ID LIKE ? OR p.POLICY_TITLE LIKE ?)");
                    paramList.add("%" + keyword + "%");
                    paramList.add("%" + keyword + "%");
                }

                if (!policyType.isEmpty()) {
                    where.append(" AND t.ATTACH_TYPE LIKE ?");
                    paramList.add("%" + policyType + "%");
                }

                // markStatus 筛选暂时禁用
                // 获取总数
                String countSql = "SELECT COUNT(*) AS TOTAL FROM SYSDBA.POLICY_TYPE_V2" + where;
                PreparedStatement countStmt = conn.prepareStatement(countSql);
                for (int i = 0; i < paramList.size(); i++) {
                    countStmt.setString(i + 1, paramList.get(i));
                }
                ResultSet countRs = countStmt.executeQuery();
                int total = 0;
                if (countRs.next()) {
                    total = countRs.getInt("TOTAL");
                }
                countRs.close();
                countStmt.close();

                // 分页查询（JOIN POLICY_ORIGINAL_V2 表获取政策标题）
                String querySql = "SELECT t.ID, t.POLICY_ID, t.ATTACH_TYPE, t.CREATE_TIME, t.UPDATE_TIME, " +
                    "p.POLICY_TITLE FROM SYSDBA.POLICY_TYPE_V2 t " +
                    "LEFT JOIN SYSDBA.POLICY_ORIGINAL_V2 p ON t.POLICY_ID = p.POLICY_ID " +
                    where + " ORDER BY t.ID DESC LIMIT ? OFFSET ?";
                paramList.add(String.valueOf(size));
                paramList.add(String.valueOf(offset));

                PreparedStatement stmt = conn.prepareStatement(querySql);
                for (int i = 0; i < paramList.size(); i++) {
                    stmt.setString(i + 1, paramList.get(i));
                }

                ResultSet rs = stmt.executeQuery();
                List<Map<String, Object>> dataList = new ArrayList<>();
                int idx = offset + 1;

                while (rs.next()) {
                    Map<String, Object> row = new LinkedHashMap<>();
                    row.put("id", idx++);
                    row.put("uuid", rs.getString("ID"));
                    row.put("policyOriginalId", nullToEmpty(rs.getString("POLICY_ID")));
                    row.put("title", nullToEmpty(rs.getString("POLICY_TITLE")));
                    row.put("policyType", nullToEmpty(rs.getString("ATTACH_TYPE")));
                    row.put("markStatus", "未标注");
                    row.put("createTime", nullToEmpty(rs.getString("CREATE_TIME")));
                    row.put("updateTime", nullToEmpty(rs.getString("UPDATE_TIME")));
                    dataList.add(row);
                }
                rs.close();
                stmt.close();

                Map<String, Object> response = new LinkedHashMap<>();
                response.put("success", true);
                response.put("data", dataList);
                Map<String, Object> pagination = new LinkedHashMap<>();
                pagination.put("page", page);
                pagination.put("size", size);
                pagination.put("total", total);
                pagination.put("pages", (int) Math.ceil((double) total / size));
                response.put("pagination", pagination);

                sendJson(exchange, 200, response);

            } catch (SQLException e) {
                System.err.println("[SQL Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /**
     * 更新政策归类标注
     */
    static class PolicyTypeUpdateHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);

            if (!"POST".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持 POST 方法"));
                return;
            }

            try {
                String body = new String(readAllBytes(exchange.getRequestBody()), StandardCharsets.UTF_8);
                System.out.println("[Update] Received body: " + body);
                @SuppressWarnings("unchecked")
                Map<String, String> data = (Map<String, String>) parseJsonObject(body);
                System.out.println("[Update] Parsed data: " + data);

                String id = data.get("id");
                String policyType = data.getOrDefault("policyType", "");
                String attachmentType = data.getOrDefault("attachmentType", "");
                String markStatus = data.getOrDefault("markStatus", "");

                if (id == null || id.isEmpty()) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "缺少ID"));
                    return;
                }

                // 更新 POLICY_TYPE_V2 表
                String updateSql = "UPDATE SYSDBA.POLICY_TYPE_V2 SET ATTACH_TYPE = ? WHERE ID = ?";
                PreparedStatement stmt = conn.prepareStatement(updateSql);
                stmt.setString(1, policyType);
                stmt.setString(2, id);
                int updated = stmt.executeUpdate();
                stmt.close();

                System.out.println("[Update] ID: " + id + ", Updated rows: " + updated);

                if (updated > 0) {
                    sendJson(exchange, 200, buildMap("success", true, "message", "更新成功"));
                } else {
                    sendJson(exchange, 200, buildMap("success", false, "message", "更新失败，记录不存在或ID不对"));
                }

            } catch (Exception e) {
                System.err.println("[Update Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /** 获取某条政策下已保存的附件类型。 */
    static class AttachmentTypesHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if ("OPTIONS".equals(exchange.getRequestMethod())) {
                sendResponse(exchange, 200, "{\"success\":true}");
                return;
            }
            try {
                ensureConnection();
                Map<String, String> params = parseQueryParams(exchange.getRequestURI().getQuery());
                String policyId = params.get("policyId");
                if (policyId == null || policyId.isEmpty()) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "缺少policyId"));
                    return;
                }

                String sql = "SELECT ATTACH_ID, ATTACH_NAME, ATTACH_TYPE FROM SYSDBA.POLICY_ATTACHMENT " +
                    "WHERE POLICY_ID = ? AND DEL_FLAG = '0'";
                PreparedStatement stmt = conn.prepareStatement(sql);
                stmt.setString(1, policyId);
                ResultSet rs = stmt.executeQuery();
                List<Map<String, Object>> rows = new ArrayList<>();
                while (rs.next()) {
                    Map<String, Object> row = new LinkedHashMap<>();
                    row.put("id", rs.getString("ATTACH_ID"));
                    row.put("attachmentName", nullToEmpty(rs.getString("ATTACH_NAME")));
                    row.put("attachmentType", nullToEmpty(rs.getString("ATTACH_TYPE")));
                    rows.add(row);
                }
                rs.close();
                stmt.close();
                sendJson(exchange, 200, buildMap("success", true, "data", rows));
            } catch (Exception e) {
                System.err.println("[Attachment Types Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /** 按政策ID和附件ID保存附件类型。 */
    static class AttachmentTypeUpdateHandler implements HttpHandler {
        private static final Set<String> ALLOWED_TYPES = new HashSet<>(Arrays.asList(
            "实施细则类", "指导政策类", "申报材料类", "申报通知类", "结果公告类", "通知公告类", "其它"
        ));

        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if (!"POST".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持POST方法"));
                return;
            }
            try {
                ensureConnection();
                String body = new String(readAllBytes(exchange.getRequestBody()), StandardCharsets.UTF_8);
                @SuppressWarnings("unchecked")
                Map<String, String> data = (Map<String, String>) parseJsonObject(body);
                String policyId = data.get("policyId");
                String attachmentId = data.get("attachmentId");
                String attachmentType = data.get("attachmentType");
                String operatorName = resolveAuditOperatorName(data);
                if (policyId == null || policyId.isEmpty() || attachmentId == null || attachmentId.isEmpty()) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "缺少政策ID或附件ID"));
                    return;
                }
                if (!ALLOWED_TYPES.contains(attachmentType)) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "无效的附件类型"));
                    return;
                }

                Map<String, Object> beforeRow = loadPolicyAttachmentRow(conn, attachmentId, policyId);
                PreparedStatement stmt = conn.prepareStatement(
                    "UPDATE SYSDBA.POLICY_ATTACHMENT SET ATTACH_TYPE = ?, ATTACH_REVIEW_STATUS = '未通过', UPDATED_BY = ?, UPDATE_AT = CURRENT_TIMESTAMP " +
                    "WHERE ATTACH_ID = ? AND POLICY_ID = ? AND DEL_FLAG = '0'"
                );
                stmt.setString(1, attachmentType);
                stmt.setString(2, operatorName);
                stmt.setString(3, attachmentId);
                stmt.setString(4, policyId);
                int updated = stmt.executeUpdate();
                stmt.close();
                if (updated > 0) {
                    writeAuditMutation(conn, data, "policy-attachment", "update", "POLICY_ATTACHMENT", attachmentId, policyId, beforeRow, loadPolicyAttachmentRow(conn, attachmentId, policyId), "附件类型更新");
                }
                sendJson(exchange, 200, buildMap("success", updated > 0, "message", updated > 0 ? "附件类型保存成功" : "附件类型保存失败"));
            } catch (Exception e) {
                System.err.println("[Attachment Type Update Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /** 复核通过单个关联文件。 */
    static class AttachmentTypeApproveHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if (!"POST".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持POST方法"));
                return;
            }
            try {
                ensureConnection();
                String body = new String(readAllBytes(exchange.getRequestBody()), StandardCharsets.UTF_8);
                @SuppressWarnings("unchecked")
                Map<String, String> data = (Map<String, String>) parseJsonObject(body);
                String policyId = data.get("policyId");
                String attachmentId = data.get("attachmentId");
                String operatorName = resolveAuditOperatorName(data);
                String operatorId = resolveAuditOperatorId(conn, data);
                if (policyId == null || policyId.isEmpty() || attachmentId == null || attachmentId.isEmpty()) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "缺少政策ID或附件ID"));
                    return;
                }

                Map<String, Object> beforeRow = loadPolicyAttachmentRow(conn, attachmentId, policyId);
                PreparedStatement stmt = conn.prepareStatement(
                    "UPDATE SYSDBA.POLICY_ATTACHMENT SET ATTACH_REVIEW_STATUS = '已通过', UPDATED_BY = ?, UPDATE_AT = CURRENT_TIMESTAMP " +
                    "WHERE ATTACH_ID = ? AND POLICY_ID = ? AND DEL_FLAG = '0' " +
                    "AND ATTACH_TYPE IN (" +
                    "'实施细则类', '指导政策类', '申报材料类', '申报通知类', '结果公告类', '通知公告类', '其它', '其他', '其它类', '其他类', " +
                    "'实施细则', '指导政策', '申报通知', '通知公告', '结果公告')"
                );
                stmt.setString(1, operatorName);
                stmt.setString(2, attachmentId);
                stmt.setString(3, policyId);
                int updated = stmt.executeUpdate();
                stmt.close();
                if (updated > 0) {
                    try (PreparedStatement reviewerStmt = conn.prepareStatement(
                            "UPDATE SYSDBA.POLICY_ATTACHMENT SET REVIEWED_BY_ID = ? WHERE ATTACH_ID = ? AND POLICY_ID = ? AND DEL_FLAG = '0'")) {
                        setNullableBigIntString(reviewerStmt, 1, operatorId);
                        reviewerStmt.setString(2, attachmentId);
                        reviewerStmt.setString(3, policyId);
                        reviewerStmt.executeUpdate();
                    }
                    writeAuditMutation(conn, data, "policy-attachment", "approve", "POLICY_ATTACHMENT", attachmentId, policyId, beforeRow, loadPolicyAttachmentRow(conn, attachmentId, policyId), "附件审核通过");
                    sendJson(exchange, 200, buildMap("success", true, "message", "复核通过"));
                } else {
                    sendJson(exchange, 400, buildMap("success", false, "message", "请先选择有效的文件类型"));
                }
            } catch (Exception e) {
                System.err.println("[Attachment Approve Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /** 撤销单个关联文件的复核。 */
    static class AttachmentTypeRevokeHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if (!"POST".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持POST方法"));
                return;
            }
            try {
                ensureConnection();
                String body = new String(readAllBytes(exchange.getRequestBody()), StandardCharsets.UTF_8);
                @SuppressWarnings("unchecked")
                Map<String, String> data = (Map<String, String>) parseJsonObject(body);
                String policyId = data.get("policyId");
                String attachmentId = data.get("attachmentId");
                String operatorName = resolveAuditOperatorName(data);
                if (policyId == null || policyId.isEmpty() || attachmentId == null || attachmentId.isEmpty()) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "缺少政策ID或附件ID"));
                    return;
                }

                Map<String, Object> beforeRow = loadPolicyAttachmentRow(conn, attachmentId, policyId);
                PreparedStatement stmt = conn.prepareStatement(
                    "UPDATE SYSDBA.POLICY_ATTACHMENT SET ATTACH_REVIEW_STATUS = '未通过', UPDATED_BY = ?, UPDATE_AT = CURRENT_TIMESTAMP " +
                    "WHERE ATTACH_ID = ? AND POLICY_ID = ? AND DEL_FLAG = '0'"
                );
                stmt.setString(1, operatorName);
                stmt.setString(2, attachmentId);
                stmt.setString(3, policyId);
                int updated = stmt.executeUpdate();
                stmt.close();
                if (updated > 0) {
                    writeAuditMutation(conn, data, "policy-attachment", "revoke", "POLICY_ATTACHMENT", attachmentId, policyId, beforeRow, loadPolicyAttachmentRow(conn, attachmentId, policyId), "附件审核撤销");
                    sendJson(exchange, 200, buildMap("success", true, "message", "复核已撤销"));
                } else {
                    sendJson(exchange, 400, buildMap("success", false, "message", "撤销失败，找不到对应记录"));
                }
            } catch (Exception e) {
                System.err.println("[Attachment Revoke Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /**
     * 获取 POLICY_ORIGINAL_V2 表的详情
     */
    static class PolicyOriginalDetailHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);

            if ("OPTIONS".equals(exchange.getRequestMethod())) {
                sendResponse(exchange, 200, "{\"success\":true}");
                return;
            }

            try {
                Map<String, String> params = parseQueryParams(exchange.getRequestURI().getQuery());
                String id = params.get("id");

                if (id == null || id.isEmpty()) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "缺少ID"));
                    return;
                }

                String querySql = "SELECT CAST(p.ID AS VARCHAR(50)) AS POLICY_ID, p.POLICY_TITLE, " +
                    "o.CONTENT, o.ISSUE_DEPARTMENT, o.PUBLISH_DATE, o.POLICY_CODE, o.FILE_TYPE " +
                    "FROM SYSDBA.POLICY p " +
                    "LEFT JOIN SYSDBA.POLICY_ORIGINAL_V2 o ON o.POLICY_ID = CAST(p.ID AS VARCHAR(50)) " +
                    "WHERE p.ID = ?";
                PreparedStatement stmt = conn.prepareStatement(querySql);
                stmt.setString(1, id);
                ResultSet rs = stmt.executeQuery();

                Map<String, Object> response = new LinkedHashMap<>();
                if (rs.next()) {
                    Map<String, Object> data = new LinkedHashMap<>();
                    data.put("id", rs.getString("POLICY_ID"));
                    data.put("policyTitle", nullToEmpty(rs.getString("POLICY_TITLE")));
                    data.put("policyContent", nullToEmpty(rs.getString("CONTENT")));
                    data.put("department", nullToEmpty(rs.getString("ISSUE_DEPARTMENT")));
                    data.put("publishDate", nullToEmpty(rs.getString("PUBLISH_DATE")));
                    data.put("documentNo", nullToEmpty(rs.getString("POLICY_CODE")));
                    data.put("fileType", nullToEmpty(rs.getString("FILE_TYPE")));

                    // 统计该政策的附件数量（从 POLICY_TYPE 表）
                    int attachmentCount = 0;
                    try {
                        PreparedStatement countStmt = conn.prepareStatement(
                            "SELECT COUNT(*) FROM SYSDBA.POLICY_TYPE_V2 WHERE POLICY_ID = ?");
                        countStmt.setString(1, id);
                        ResultSet countRs = countStmt.executeQuery();
                        if (countRs.next()) {
                            attachmentCount = countRs.getInt(1);
                        }
                        countRs.close();
                        countStmt.close();
                    } catch (SQLException e) {
                        System.err.println("[Count Error] " + e.getMessage());
                    }
                    data.put("attachmentCount", attachmentCount);

                    response.put("success", true);
                    response.put("data", data);
                } else {
                    response.put("success", false);
                    response.put("message", "未找到对应记录");
                }
                rs.close();
                stmt.close();

                sendJson(exchange, 200, response);

            } catch (SQLException e) {
                System.err.println("[SQL Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /**
     * 获取政策原文内容（根据FILE_PATH读取文件）
     */
    static class PolicyOriginalContentHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);

            if ("OPTIONS".equals(exchange.getRequestMethod())) {
                sendResponse(exchange, 200, "{\"success\":true}");
                return;
            }

            try {
                ensureConnection(); // 检查并重新建立连接
                Map<String, String> params = parseQueryParams(exchange.getRequestURI().getQuery());
                String id = params.get("id");

                if (id == null || id.isEmpty()) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "缺少ID"));
                    return;
                }

                Map<String, Object> response = new LinkedHashMap<>();
                String filePath = "";
                String policyTitle = "";
                String fileType = "";

                try (PreparedStatement stmt = conn.prepareStatement(
                        "SELECT p.POLICY_TITLE, " +
                        "(SELECT o.FILE_PATH FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID = CAST(p.ID AS VARCHAR(50)) AND ROWNUM = 1) AS FILE_PATH, " +
                        "(SELECT o.FILE_TYPE FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID = CAST(p.ID AS VARCHAR(50)) AND ROWNUM = 1) AS FILE_TYPE " +
                        "FROM SYSDBA.POLICY p WHERE p.ID = ?")) {
                    stmt.setString(1, id);
                    try (ResultSet rs = stmt.executeQuery()) {
                        if (rs.next()) {
                            policyTitle = nullToEmpty(rs.getString("POLICY_TITLE"));
                            filePath = nullToEmpty(rs.getString("FILE_PATH"));
                            fileType = nullToEmpty(rs.getString("FILE_TYPE"));
                        }
                    }
                }

                // 兼容极少数只存在原文表、但 POLICY 主表未命中的历史数据
                if (policyTitle.isEmpty() && filePath.isEmpty()) {
                    try (PreparedStatement stmt = conn.prepareStatement(
                            "SELECT POLICY_TITLE, FILE_PATH, FILE_TYPE FROM SYSDBA.POLICY_ORIGINAL_V2 WHERE POLICY_ID = ?")) {
                        stmt.setString(1, id);
                        try (ResultSet rs = stmt.executeQuery()) {
                            if (rs.next()) {
                                policyTitle = nullToEmpty(rs.getString("POLICY_TITLE"));
                                filePath = nullToEmpty(rs.getString("FILE_PATH"));
                                fileType = nullToEmpty(rs.getString("FILE_TYPE"));
                            }
                        }
                    }
                }

                if (!policyTitle.isEmpty() || !filePath.isEmpty()) {

                    // 读取文件内容
                    String content = "";
                    if (!filePath.isEmpty()) {
                        try {
                            Path file = resolveAttachmentPath(filePath);
                            content = new String(Files.readAllBytes(file), StandardCharsets.UTF_8);
                        } catch (Exception e) {
                            content = "读取文件失败: " + e.getMessage();
                        }
                    } else {
                        content = "无文件路径";
                    }

                    response.put("success", true);
                    response.put("data", buildMap(
                        "id", id,
                        "policyTitle", policyTitle,
                        "filePath", filePath,
                        "fileType", fileType,
                        "content", content
                    ));
                } else {
                    response.put("success", false);
                    response.put("message", "未找到对应记录");
                }

                sendJson(exchange, 200, response);

            } catch (SQLException e) {
                System.err.println("[SQL Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /**
     * 获取政策附件列表
     */
    static class AttachmentListHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);

            if ("OPTIONS".equals(exchange.getRequestMethod())) {
                sendResponse(exchange, 200, "{\"success\":true}");
                return;
            }

            try {
                ensureConnection();
                Map<String, String> params = parseQueryParams(exchange.getRequestURI().getQuery());
                String policyId = params.get("policyId");

                if (policyId == null || policyId.isEmpty()) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "缺少policyId"));
                    return;
                }

                String querySql = "SELECT ATTACH_ID, ATTACH_NAME, ATTACH_SHOW_NAME, FILE_PATH, FILE_SUFFIX, ATTACH_TYPE, ATTACH_REVIEW_STATUS " +
                    "FROM SYSDBA.POLICY_ATTACHMENT WHERE POLICY_ID = ? AND DEL_FLAG = '0'";
                PreparedStatement stmt = conn.prepareStatement(querySql);
                stmt.setString(1, policyId);
                ResultSet rs = stmt.executeQuery();

                List<Map<String, Object>> dataList = new ArrayList<>();
                while (rs.next()) {
                    Map<String, Object> row = new LinkedHashMap<>();
                    row.put("id", nullToEmpty(rs.getString("ATTACH_ID")));
                    row.put("attachmentName", nullToEmpty(rs.getString("ATTACH_NAME")));
                    row.put("attachmentShowName", nullToEmpty(rs.getString("ATTACH_SHOW_NAME")));
                    row.put("filePath", nullToEmpty(rs.getString("FILE_PATH")));
                    row.put("fileSuffix", nullToEmpty(rs.getString("FILE_SUFFIX")));
                    row.put("attachmentType", nullToEmpty(rs.getString("ATTACH_TYPE")));
                    row.put("reviewStatus", nullToEmpty(rs.getString("ATTACH_REVIEW_STATUS")));
                    dataList.add(row);
                }
                rs.close();
                stmt.close();

                Map<String, Object> response = new LinkedHashMap<>();
                response.put("success", true);
                response.put("data", dataList);
                sendJson(exchange, 200, response);

            } catch (SQLException e) {
                System.err.println("[SQL Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /**
     * 获取附件元信息，供下载/预览等二进制接口按附件 ID 解析真实文件。
     */
    static class AttachmentMetaHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);

            if ("OPTIONS".equals(exchange.getRequestMethod())) {
                sendResponse(exchange, 200, "{\"success\":true}");
                return;
            }

            try {
                ensureConnection();
                Map<String, String> params = parseQueryParams(exchange.getRequestURI().getQuery());
                String id = params.get("id");
                String attachmentId = params.get("attachmentId");
                String queryId = id != null ? id : attachmentId;

                if (queryId == null || queryId.isEmpty()) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "缺少ID"));
                    return;
                }

                Map<String, Object> data = loadAttachmentMeta(queryId);
                if (data == null) {
                    sendJson(exchange, 404, buildMap("success", false, "message", "未找到对应附件"));
                    return;
                }

                sendJson(exchange, 200, buildMap("success", true, "data", data));
            } catch (SQLException e) {
                System.err.println("[Attachment Meta Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /**
     * 获取附件原文内容
     */
    static class AttachmentContentHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);

            if ("OPTIONS".equals(exchange.getRequestMethod())) {
                sendResponse(exchange, 200, "{\"success\":true}");
                return;
            }

            try {
                ensureConnection(); // 检查并重新建立连接
                Map<String, String> params = parseQueryParams(exchange.getRequestURI().getQuery());
                String id = params.get("id");
                String attachmentId = params.get("attachmentId");
                String directFilePath = nullToEmpty(params.get("filePath"));
                String directFileSuffix = nullToEmpty(params.get("fileSuffix"));
                String directFileName = nullToEmpty(params.get("fileName"));

                if (!directFilePath.isEmpty()) {
                    Map<String, Object> data = new LinkedHashMap<>();
                    AttachmentContentPreview preview;
                    try {
                        Path file = resolveAttachmentPath(directFilePath);
                        String suffix = directFileSuffix;
                        if (suffix.isEmpty()) {
                            String name = file.getFileName() == null ? "" : file.getFileName().toString();
                            int dot = name.lastIndexOf('.');
                            suffix = dot >= 0 ? name.substring(dot + 1) : "";
                        }
                        preview = extractAttachmentPreviewWithTimeout(file, suffix);
                        data.put("attachmentName", directFileName.isEmpty() && file.getFileName() != null ? file.getFileName().toString() : directFileName);
                        data.put("filePath", directFilePath);
                        data.put("fileSuffix", suffix);
                    } catch (Exception e) {
                        preview = AttachmentContentPreview.text("读取文件失败: " + e.getMessage());
                        data.put("attachmentName", directFileName);
                        data.put("filePath", directFilePath);
                        data.put("fileSuffix", directFileSuffix);
                    }
                    data.put("content", preview.content);
                    data.put("contentHtml", preview.contentHtml);
                    data.put("renderType", preview.renderType);
                    data.put("imageCount", preview.imageCount);
                    sendJson(exchange, 200, buildMap("success", true, "data", data));
                    return;
                }

                String queryId = id != null ? id : attachmentId;

                if (queryId == null || queryId.isEmpty()) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "缺少ID"));
                    return;
                }

                Map<String, Object> response = new LinkedHashMap<>();
                Map<String, Object> data = loadAttachmentMeta(queryId);
                if (data != null) {
                    String filePath = nullToEmpty(String.valueOf(data.getOrDefault("filePath", "")));
                    String fileSuffix = nullToEmpty(String.valueOf(data.getOrDefault("fileSuffix", "")));

                    AttachmentContentPreview preview = AttachmentContentPreview.text("");
                    if (!filePath.isEmpty()) {
                        try {
                            Path file = resolveAttachmentPath(filePath);
                            preview = extractAttachmentPreviewWithTimeout(file, fileSuffix);
                        } catch (Exception e) {
                            preview = AttachmentContentPreview.text("读取文件失败: " + e.getMessage());
                        }
                    } else {
                        preview = AttachmentContentPreview.text("无文件路径");
                    }

                    data = new LinkedHashMap<>(data);
                    data.put("content", preview.content);
                    data.put("contentHtml", preview.contentHtml);
                    data.put("renderType", preview.renderType);
                    data.put("imageCount", preview.imageCount);
                    response.put("success", true);
                    response.put("data", data);
                } else {
                    response.put("success", false);
                    response.put("message", "未找到对应附件");
                }

                sendJson(exchange, 200, response);

            } catch (SQLException e) {
                System.err.println("[SQL Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    private static AttachmentContentPreview extractAttachmentPreviewWithTimeout(Path file, String suffix) {
        Future<AttachmentContentPreview> future = ATTACHMENT_PREVIEW_EXECUTOR.submit(() -> extractAttachmentPreview(file, suffix));
        try {
            return future.get(ATTACHMENT_PREVIEW_TIMEOUT_SECONDS, TimeUnit.SECONDS);
        } catch (TimeoutException e) {
            future.cancel(true);
            return AttachmentContentPreview.text("原文预览生成超时，请下载附件原件查看。");
        } catch (Exception e) {
            future.cancel(true);
            return AttachmentContentPreview.text("读取文件失败: " + e.getMessage());
        }
    }

    private static Map<String, Object> loadAttachmentMeta(String attachmentId) throws SQLException {
        String querySql = "SELECT ATTACH_ID, POLICY_ID, ATTACH_NAME, ATTACH_SHOW_NAME, FILE_PATH, FILE_SUFFIX, ATTACH_TYPE, ATTACH_REVIEW_STATUS " +
            "FROM SYSDBA.POLICY_ATTACHMENT WHERE ATTACH_ID = ? AND DEL_FLAG = '0'";
        try (PreparedStatement stmt = conn.prepareStatement(querySql)) {
            stmt.setString(1, attachmentId);
            try (ResultSet rs = stmt.executeQuery()) {
                if (!rs.next()) {
                    return null;
                }
                Map<String, Object> data = new LinkedHashMap<>();
                data.put("id", nullToEmpty(rs.getString("ATTACH_ID")));
                data.put("policyId", nullToEmpty(rs.getString("POLICY_ID")));
                data.put("attachmentName", nullToEmpty(rs.getString("ATTACH_NAME")));
                data.put("attachmentShowName", nullToEmpty(rs.getString("ATTACH_SHOW_NAME")));
                data.put("filePath", nullToEmpty(rs.getString("FILE_PATH")));
                data.put("fileSuffix", nullToEmpty(rs.getString("FILE_SUFFIX")));
                data.put("attachmentType", nullToEmpty(rs.getString("ATTACH_TYPE")));
                data.put("reviewStatus", nullToEmpty(rs.getString("ATTACH_REVIEW_STATUS")));
                return data;
            }
        }
    }

    /**
     * 获取筛选统计
     */
    static class FilterStatsHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);

            try {
                ensureConnection(); // 检查并重新建立连接
                int total = 0, tagged = 0, untagged = 0, inProgress = 0;

                // 总数 - 从 POLICY_ORIGINAL_V2 获取
                PreparedStatement stmt = conn.prepareStatement("SELECT COUNT(*) AS CNT FROM SYSDBA.POLICY_ORIGINAL_V2");
                ResultSet rs = stmt.executeQuery();
                if (rs.next()) total = rs.getInt("CNT");
                rs.close();
                stmt.close();

                // 已标注：主政策已审核 AND 所有附件都已复核
                try {
                    PreparedStatement tagStmt = conn.prepareStatement(
                        "SELECT COUNT(*) AS CNT FROM SYSDBA.POLICY_ORIGINAL_V2 p " +
                        "WHERE EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA m " +
                            "WHERE m.POLICY_ID = p.POLICY_ID AND m.REVIEW_STATUS = '已通过') " +
                        "AND NOT EXISTS (SELECT 1 FROM SYSDBA.POLICY_ATTACHMENT a " +
                            "WHERE a.POLICY_ID = p.POLICY_ID AND a.DEL_FLAG = '0' " +
                            "AND (a.ATTACH_REVIEW_STATUS IS NULL OR a.ATTACH_REVIEW_STATUS <> '已通过'))"
                    );
                    ResultSet tagRs = tagStmt.executeQuery();
                    if (tagRs.next()) tagged = tagRs.getInt("CNT");
                    tagRs.close();
                    tagStmt.close();
                } catch (SQLException e) {
                    tagged = 0;
                }

                // 未标注：主政策未审核 AND 没有任何附件已复核
                try {
                    PreparedStatement untaggedStmt = conn.prepareStatement(
                        "SELECT COUNT(*) AS CNT FROM SYSDBA.POLICY_ORIGINAL_V2 p " +
                        "WHERE NOT EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA m " +
                            "WHERE m.POLICY_ID = p.POLICY_ID AND m.REVIEW_STATUS = '已通过') " +
                        "AND NOT EXISTS (SELECT 1 FROM SYSDBA.POLICY_ATTACHMENT a " +
                            "WHERE a.POLICY_ID = p.POLICY_ID AND a.DEL_FLAG = '0' " +
                            "AND a.ATTACH_REVIEW_STATUS = '已通过')"
                    );
                    ResultSet untaggedRs = untaggedStmt.executeQuery();
                    if (untaggedRs.next()) untagged = untaggedRs.getInt("CNT");
                    untaggedRs.close();
                    untaggedStmt.close();
                } catch (SQLException e) {
                    untagged = 0;
                }

                // 标注中：total - tagged - untagged
                inProgress = Math.max(0, total - tagged - untagged);

                // 按政策类型统计（从 POLICY_METADATA）
                // 兼容数据库中同时存在的"申报通知"和"通知公告"，合并到"申报通知"下
                Map<String, Integer> byType = new LinkedHashMap<>();
                byType.put("指导政策", 0);
                byType.put("实施细则", 0);
                byType.put("申报通知", 0);
                byType.put("结果公告", 0);
                String byTypeSql = "SELECT TRIM(m.POLICY_ATTRIBUTE) AS ATTR, COUNT(DISTINCT p.POLICY_ID) AS CNT " +
                    "FROM SYSDBA.POLICY_ORIGINAL_V2 p " +
                    "JOIN SYSDBA.POLICY_METADATA m ON m.POLICY_ID = p.POLICY_ID " +
                    "WHERE TRIM(m.POLICY_ATTRIBUTE) IN ('指导政策','实施细则','申报通知','通知公告','结果公告') " +
                    "GROUP BY TRIM(m.POLICY_ATTRIBUTE)";
                try {
                    stmt = conn.prepareStatement(byTypeSql);
                    rs = stmt.executeQuery();
                    while (rs.next()) {
                        String attr = rs.getString("ATTR");
                        int cnt = rs.getInt("CNT");
                        if ("通知公告".equals(attr)) {
                            byType.put("申报通知", byType.get("申报通知") + cnt);
                        } else if (byType.containsKey(attr)) {
                            byType.put(attr, byType.get(attr) + cnt);
                        }
                    }
                    rs.close();
                    stmt.close();
                } catch (SQLException e) {
                    System.err.println("[byType query error] " + e.getMessage());
                }

                Map<String, Object> response = new LinkedHashMap<>();
                response.put("success", true);

                Map<String, Object> data = new LinkedHashMap<>();
                data.put("all", total);
                data.put("tagged", tagged);
                data.put("inProgress", inProgress);
                data.put("untagged", untagged);
                data.put("byType", byType);
                // 添加 remarked 统计（已标记的政策数量）
                int remarked = 0;
                try {
                    PreparedStatement remStmt = conn.prepareStatement(
                        "SELECT COUNT(*) AS CNT FROM SYSDBA.POLICY_METADATA WHERE REMARK IS NOT NULL AND REMARK <> ''"
                    );
                    ResultSet remRs = remStmt.executeQuery();
                    if (remRs.next()) remarked = remRs.getInt("CNT");
                    remRs.close();
                    remStmt.close();
                } catch (SQLException e) {
                    remarked = 0;
                }
                data.put("remarked", remarked);
                response.put("data", data);

                System.out.println("[FilterStats] all=" + total + ", tagged=" + tagged + ", inProgress=" + inProgress + ", untagged=" + untagged + ", remarked=" + remarked + ", byType=" + byType);
                sendJson(exchange, 200, response);

            } catch (SQLException e) {
                System.err.println("[SQL Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /**
     * 健康检查
     */
    static class LoginHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if (!"POST".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持POST方法"));
                return;
            }
            try {
                ensureConnection();
                String body = new String(readAllBytes(exchange.getRequestBody()), StandardCharsets.UTF_8);
                @SuppressWarnings("unchecked")
                Map<String, String> data = (Map<String, String>) parseJsonObject(body);
                String username = data.getOrDefault("username", "").trim();
                String password = data.getOrDefault("password", "");
                if (username.isEmpty() || password.isEmpty() || username.length() > 50 || password.length() > 100) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "请输入有效的用户名和密码"));
                    return;
                }

                String sql = "SELECT ID, USERNAME, PASSWORD, REAL_NAME, EMAIL, PHONE, ROLE, STATUS " +
                    "FROM SYSDBA.SYS_USER WHERE USERNAME = ?";
                PreparedStatement stmt = conn.prepareStatement(sql);
                stmt.setString(1, username);
                ResultSet rs = stmt.executeQuery();
                if (!rs.next() || !password.equals(nullToEmpty(rs.getString("PASSWORD")))) {
                    rs.close();
                    stmt.close();
                    sendJson(exchange, 401, buildMap("success", false, "message", "用户名或密码错误"));
                    return;
                }
                if (rs.getInt("STATUS") != 1) {
                    rs.close();
                    stmt.close();
                    sendJson(exchange, 403, buildMap("success", false, "message", "账号已被禁用，请联系管理员"));
                    return;
                }

                String userId = rs.getString("ID");
                Map<String, Object> user = new LinkedHashMap<>();
                user.put("id", userId);
                user.put("username", nullToEmpty(rs.getString("USERNAME")));
                user.put("realName", nullToEmpty(rs.getString("REAL_NAME")));
                user.put("email", nullToEmpty(rs.getString("EMAIL")));
                user.put("phone", nullToEmpty(rs.getString("PHONE")));
                user.put("role", nullToEmpty(rs.getString("ROLE")));
                rs.close();
                stmt.close();

                PreparedStatement updateStmt = conn.prepareStatement(
                    "UPDATE SYSDBA.SYS_USER SET LAST_LOGIN_TIME = CURRENT_TIMESTAMP WHERE ID = ?"
                );
                updateStmt.setString(1, userId);
                updateStmt.executeUpdate();
                updateStmt.close();
                sendJson(exchange, 200, buildMap("success", true, "message", "登录成功", "data", user));
            } catch (Exception e) {
                System.err.println("[Login Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", "登录服务暂时不可用"));
            }
        }
    }

    /**
     * 政策归类标注数据源。
     *
     * 直接读取 POLICY，并按 POLICY.ID = POLICY_ATTACHMENT.POLICY_ID 关联附件，
     * 不依赖 POLICY_METADATA 或政策属性模块的审核状态。
     */
    static class ClassificationPoliciesHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);

            if ("OPTIONS".equals(exchange.getRequestMethod())) {
                sendResponse(exchange, 200, "{\"success\":true}");
                return;
            }

            try {
                ensureConnection();
                Map<String, String> params = parseQueryParams(exchange.getRequestURI().getQuery());
                int page = Math.max(1, parseInt(params.getOrDefault("page", "1"), 1));
                int size = Math.min(10000, Math.max(1, parseInt(params.getOrDefault("size", "10"), 10)));
                int offset = (page - 1) * size;
                String keyword = params.getOrDefault("keyword", "").trim();
                String department = params.getOrDefault("department", "").trim();
                String applicableScope = params.getOrDefault("scope", "").trim();
                String publishStart = params.getOrDefault("publishStart", "").trim();
                String publishEnd = params.getOrDefault("publishEnd", "").trim();
                String policyType = params.getOrDefault("type", "").trim();
                String markStatus = params.getOrDefault("status", "").trim();
                String remarkStatus = params.getOrDefault("remark", "").trim();
                boolean includeAttachments = !"false".equalsIgnoreCase(
                    params.getOrDefault("includeAttachments", "true")
                );
                boolean excludeFusionSource = "true".equalsIgnoreCase(
                    params.getOrDefault("excludeFusionSource", "false")
                );
                String hiddenPolicyAttributeFilter =
                    " AND NOT EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA hm WHERE hm.POLICY_ID = p.ID AND TRIM(hm.POLICY_ATTRIBUTE) IN ('主政策','主属性'))";
                String hiddenPolicyAttributeFilterStats =
                    " AND NOT EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA hm WHERE hm.POLICY_ID = sp.ID AND TRIM(hm.POLICY_ATTRIBUTE) IN ('主政策','主属性'))";
                String fusionSourceExclusion =
                    " AND NOT EXISTS (SELECT 1 FROM SYSDBA.X1_FUSION_POLICY xf WHERE xf.SOURCE_POLICY_ID IS NOT NULL AND TRIM(xf.SOURCE_POLICY_ID) = CAST(p.ID AS VARCHAR(50)) AND NVL(TRIM(xf.DEL_FLAG),'0') = '0')";
                String fusionSourceExclusionStats =
                    " AND NOT EXISTS (SELECT 1 FROM SYSDBA.X1_FUSION_POLICY xf WHERE xf.SOURCE_POLICY_ID IS NOT NULL AND TRIM(xf.SOURCE_POLICY_ID) = CAST(sp.ID AS VARCHAR(50)) AND NVL(TRIM(xf.DEL_FLAG),'0') = '0')";

                StringBuilder where = new StringBuilder(" WHERE 1=1");
                where.append(hiddenPolicyAttributeFilter);
                if (excludeFusionSource) {
                    where.append(fusionSourceExclusion);
                }
                List<String> queryParams = new ArrayList<>();
                if (!keyword.isEmpty()) {
                    where.append(" AND p.POLICY_TITLE LIKE ?");
                    queryParams.add("%" + keyword + "%");
                }
                if (!department.isEmpty()) {
                    where.append(" AND EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA fm WHERE fm.POLICY_ID = p.ID AND fm.ISSUING_DEPARTMENT LIKE ?)");
                    queryParams.add("%" + department + "%");
                }
                if (!applicableScope.isEmpty()) {
                    where.append(" AND EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA fm WHERE fm.POLICY_ID = p.ID AND fm.APPLICABLE_SCOPE LIKE ?)");
                    queryParams.add("%" + applicableScope + "%");
                }
                if (!publishStart.isEmpty()) {
                    where.append(" AND EXISTS (SELECT 1 FROM SYSDBA.POLICY_ORIGINAL_V2 fo WHERE fo.POLICY_ID = CAST(p.ID AS VARCHAR(50)) AND SUBSTR(TRIM(fo.PUBLISH_DATE), 1, 10) >= ?)");
                    queryParams.add(publishStart);
                }
                if (!publishEnd.isEmpty()) {
                    where.append(" AND EXISTS (SELECT 1 FROM SYSDBA.POLICY_ORIGINAL_V2 fo WHERE fo.POLICY_ID = CAST(p.ID AS VARCHAR(50)) AND SUBSTR(TRIM(fo.PUBLISH_DATE), 1, 10) <= ?)");
                    queryParams.add(publishEnd);
                }
                if (!policyType.isEmpty()) {
                    if ("申报通知".equals(policyType)) {
                        where.append(" AND EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA fm WHERE fm.POLICY_ID = p.ID AND TRIM(fm.POLICY_ATTRIBUTE) IN ('申报通知','通知公告'))");
                    } else {
                        where.append(" AND EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA fm WHERE fm.POLICY_ID = p.ID AND TRIM(fm.POLICY_ATTRIBUTE) = ?)");
                        queryParams.add(policyType);
                    }
                }
                if ("已标注".equals(markStatus)) {
                    // 主政策已审核 AND 所有附件都已复核
                    where.append(" AND EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA fm WHERE fm.POLICY_ID = p.ID AND fm.REVIEW_STATUS = '已通过')");
                    where.append(" AND NOT EXISTS (SELECT 1 FROM SYSDBA.POLICY_ATTACHMENT pa WHERE TRIM(pa.POLICY_ID) = CAST(p.ID AS VARCHAR(50)) AND pa.DEL_FLAG = '0' AND (pa.ATTACH_REVIEW_STATUS IS NULL OR pa.ATTACH_REVIEW_STATUS <> '已通过'))");
                } else if ("未标注".equals(markStatus)) {
                    // 主政策未审核 AND 没有任何附件已复核
                    where.append(" AND NOT EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA fm WHERE fm.POLICY_ID = p.ID AND fm.REVIEW_STATUS = '已通过')");
                    where.append(" AND NOT EXISTS (SELECT 1 FROM SYSDBA.POLICY_ATTACHMENT pa WHERE TRIM(pa.POLICY_ID) = CAST(p.ID AS VARCHAR(50)) AND pa.DEL_FLAG = '0' AND pa.ATTACH_REVIEW_STATUS = '已通过')");
                } else if ("标注中".equals(markStatus)) {
                    // 标注中：不是完全已标注，也不是完全未标注
                    // 即：(主政策未审核 OR 有附件未复核) AND (主政策已审核 OR 有附件已复核)
                    where.append(" AND (NOT EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA fm WHERE fm.POLICY_ID = p.ID AND fm.REVIEW_STATUS = '已通过')");
                    where.append(" OR EXISTS (SELECT 1 FROM SYSDBA.POLICY_ATTACHMENT pa WHERE TRIM(pa.POLICY_ID) = CAST(p.ID AS VARCHAR(50)) AND pa.DEL_FLAG = '0' AND (pa.ATTACH_REVIEW_STATUS IS NULL OR pa.ATTACH_REVIEW_STATUS <> '已通过')))");
                    where.append(" AND (EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA fm WHERE fm.POLICY_ID = p.ID AND fm.REVIEW_STATUS = '已通过')");
                    where.append(" OR EXISTS (SELECT 1 FROM SYSDBA.POLICY_ATTACHMENT pa WHERE TRIM(pa.POLICY_ID) = CAST(p.ID AS VARCHAR(50)) AND pa.DEL_FLAG = '0' AND pa.ATTACH_REVIEW_STATUS = '已通过'))");
                }
                if ("已标记".equals(remarkStatus)) {
                    // 已标记：REMARK 字段不为空
                    where.append(" AND EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA fm WHERE fm.POLICY_ID = p.ID AND fm.REMARK IS NOT NULL AND fm.REMARK <> '')");
                }

                int total = 0;
                String countSql = "SELECT COUNT(*) AS TOTAL FROM SYSDBA.POLICY p" + where;
                try (PreparedStatement countStmt = conn.prepareStatement(countSql)) {
                    for (int i = 0; i < queryParams.size(); i++) {
                        countStmt.setString(i + 1, queryParams.get(i));
                    }
                    try (ResultSet rs = countStmt.executeQuery()) {
                        if (rs.next()) total = rs.getInt("TOTAL");
                    }
                }

                List<Map<String, Object>> policies = new ArrayList<>();
                Map<String, Map<String, Object>> policiesById = new LinkedHashMap<>();
                String policySql = "SELECT p.ID, p.POLICY_TITLE, p.FILE_NAME, p.CREATED_AT, " +
                    "(SELECT o.POLICY_TITLE FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID = CAST(p.ID AS VARCHAR(50)) AND ROWNUM = 1) AS ORIGINAL_POLICY_TITLE, " +
                    "(SELECT o.POLICY_CODE FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID = CAST(p.ID AS VARCHAR(50)) AND ROWNUM = 1) AS POLICY_CODE, " +
                    "(SELECT o.ISSUE_DEPARTMENT FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID = CAST(p.ID AS VARCHAR(50)) AND ROWNUM = 1) AS ISSUE_DEPARTMENT, " +
                    "(SELECT o.PUBLISH_DATE FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID = CAST(p.ID AS VARCHAR(50)) AND ROWNUM = 1) AS PUBLISH_DATE, " +
                    "(SELECT o.FILE_TYPE FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID = CAST(p.ID AS VARCHAR(50)) AND ROWNUM = 1) AS ORIGINAL_FILE_TYPE, " +
                    "(SELECT o.FILE_PATH FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID = CAST(p.ID AS VARCHAR(50)) AND ROWNUM = 1) AS ORIGINAL_FILE_PATH, " +
                    "(SELECT MAX(TRIM(m.ISSUING_DEPARTMENT)) FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID = p.ID) AS METADATA_DEPARTMENT, " +
                    "(SELECT MAX(TRIM(m.APPLICABLE_SCOPE)) FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID = p.ID) AS APPLICABLE_SCOPE, " +
                    "(SELECT MAX(TRIM(m.POLICY_ATTRIBUTE)) FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID = p.ID) AS POLICY_ATTRIBUTE, " +
                    "(SELECT MAX(TRIM(m.REMARK)) FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID = p.ID) AS POLICY_REMARK, " +
                    "CASE WHEN EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID = p.ID AND m.REVIEW_STATUS = '已通过') " +
                    "THEN '已通过' ELSE (SELECT MAX(TRIM(m.REVIEW_STATUS)) FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID = p.ID) END AS POLICY_REVIEW_STATUS, " +
                    "(SELECT COUNT(*) FROM SYSDBA.POLICY_ATTACHMENT pa WHERE TRIM(pa.POLICY_ID) = CAST(p.ID AS VARCHAR(50)) AND pa.DEL_FLAG = '0') AS ATTACHMENT_COUNT " +
                    "FROM SYSDBA.POLICY p " +
                    "LEFT JOIN (SELECT DISTINCT TRIM(POLICY_ID) AS PID FROM SYSDBA.POLICY_ATTACHMENT WHERE DEL_FLAG = '0') a " +
                    "ON a.PID = CAST(p.ID AS VARCHAR(50)) " + where +
                    " ORDER BY CASE WHEN a.PID IS NOT NULL THEN 0 ELSE 1 END, p.CREATED_AT DESC, p.ID DESC LIMIT ? OFFSET ?";
                try (PreparedStatement policyStmt = conn.prepareStatement(policySql)) {
                    int paramIndex = 1;
                    for (String value : queryParams) policyStmt.setString(paramIndex++, value);
                    policyStmt.setInt(paramIndex++, size);
                    policyStmt.setInt(paramIndex, offset);
                    try (ResultSet rs = policyStmt.executeQuery()) {
                        while (rs.next()) {
                            String policyId = String.valueOf(rs.getLong("ID"));
                            Map<String, Object> policy = new LinkedHashMap<>();
                            policy.put("id", policyId);
                            policy.put("policyId", policyId);
                            policy.put("title", nullToEmpty(rs.getString("POLICY_TITLE")));
                            policy.put("policyOriginalTitle", nullToEmpty(rs.getString("ORIGINAL_POLICY_TITLE")));
                            policy.put("fileName", nullToEmpty(rs.getString("FILE_NAME")));
                            policy.put("createdAt", nullToEmpty(rs.getString("CREATED_AT")));
                            policy.put("documentNo", nullToEmpty(rs.getString("POLICY_CODE")));
                            String metadataDepartment = nullToEmpty(rs.getString("METADATA_DEPARTMENT"));
                            String originalDepartment = nullToEmpty(rs.getString("ISSUE_DEPARTMENT"));
                            policy.put("issuingDepartment", metadataDepartment);
                            policy.put("department", metadataDepartment.isEmpty() ? originalDepartment : metadataDepartment);
                            policy.put("applicableScope", nullToEmpty(rs.getString("APPLICABLE_SCOPE")));
                            policy.put("publishDate", nullToEmpty(rs.getString("PUBLISH_DATE")));
                            policy.put("fileType", nullToEmpty(rs.getString("ORIGINAL_FILE_TYPE")));
                            policy.put("filePath", nullToEmpty(rs.getString("ORIGINAL_FILE_PATH")));
                            policy.put("policyAttribute", normalizeFusionDocumentType(rs.getString("POLICY_ATTRIBUTE")));
                            policy.put("remark", nullToEmpty(rs.getString("POLICY_REMARK")));
                            policy.put("reviewStatus", nullToEmpty(rs.getString("POLICY_REVIEW_STATUS")));
                            policy.put("attachmentCount", rs.getInt("ATTACHMENT_COUNT"));
                            policy.put("attachments", new ArrayList<Map<String, Object>>());
                            policies.add(policy);
                            policiesById.put(policyId, policy);
                        }
                    }
                }

                if (includeAttachments && !policiesById.isEmpty()) {
                    String placeholders = repeatPlaceholders(policiesById.size());
                    String attachmentSql = "SELECT ATTACH_ID, POLICY_ID, ATTACH_NAME, ATTACH_SHOW_NAME, " +
                        "FILE_PATH, FILE_SUFFIX, ATTACH_TYPE, ATTACH_REVIEW_STATUS " +
                        "FROM SYSDBA.POLICY_ATTACHMENT WHERE DEL_FLAG = '0' AND TRIM(POLICY_ID) IN (" + placeholders + ") " +
                        "ORDER BY CREATE_AT";
                    try (PreparedStatement attachmentStmt = conn.prepareStatement(attachmentSql)) {
                        int paramIndex = 1;
                        for (String policyId : policiesById.keySet()) attachmentStmt.setString(paramIndex++, policyId);
                        try (ResultSet rs = attachmentStmt.executeQuery()) {
                            while (rs.next()) {
                                String attachmentPolicyId = nullToEmpty(rs.getString("POLICY_ID")).trim();
                                Map<String, Object> policy = policiesById.get(attachmentPolicyId);
                                if (policy == null) continue;
                                Map<String, Object> attachment = new LinkedHashMap<>();
                                attachment.put("id", nullToEmpty(rs.getString("ATTACH_ID")));
                                attachment.put("policyId", attachmentPolicyId);
                                attachment.put("attachmentName", nullToEmpty(rs.getString("ATTACH_NAME")));
                                attachment.put("attachmentShowName", nullToEmpty(rs.getString("ATTACH_SHOW_NAME")));
                                attachment.put("filePath", nullToEmpty(rs.getString("FILE_PATH")));
                                attachment.put("fileSuffix", nullToEmpty(rs.getString("FILE_SUFFIX")));
                                attachment.put("attachmentType", nullToEmpty(rs.getString("ATTACH_TYPE")));
                                attachment.put("reviewStatus", nullToEmpty(rs.getString("ATTACH_REVIEW_STATUS")));
                                @SuppressWarnings("unchecked")
                                List<Map<String, Object>> attachments =
                                    (List<Map<String, Object>>) policy.get("attachments");
                                attachments.add(attachment);
                            }
                        }
                    }
                }

                Map<String, Object> pagination = new LinkedHashMap<>();
                pagination.put("page", page);
                pagination.put("size", size);
                pagination.put("total", total);
                pagination.put("pages", (int) Math.ceil((double) total / size));

                Map<String, Object> filterStats = new LinkedHashMap<>();
                String statsExclusionFilter = excludeFusionSource ? fusionSourceExclusionStats : "";
                int allCount = 0;
                int taggedCount = 0;
                int inProgressCount = 0;
                try (PreparedStatement stmt = conn.prepareStatement(
                        "SELECT COUNT(*) AS TOTAL FROM SYSDBA.POLICY sp WHERE 1=1" + hiddenPolicyAttributeFilterStats + statsExclusionFilter);
                     ResultSet rs = stmt.executeQuery()) {
                    if (rs.next()) allCount = rs.getInt("TOTAL");
                }
                // 已标注：主政策已审核 AND 所有附件都已复核
                try (PreparedStatement stmt = conn.prepareStatement(
                        "SELECT COUNT(*) AS TOTAL FROM SYSDBA.POLICY sp WHERE " +
                        "EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA sm WHERE sm.POLICY_ID = sp.ID AND sm.REVIEW_STATUS = '已通过') " +
                        "AND NOT EXISTS (SELECT 1 FROM SYSDBA.POLICY_ATTACHMENT pa WHERE TRIM(pa.POLICY_ID) = CAST(sp.ID AS VARCHAR(50)) AND pa.DEL_FLAG = '0' AND (pa.ATTACH_REVIEW_STATUS IS NULL OR pa.ATTACH_REVIEW_STATUS <> '已通过'))" +
                        hiddenPolicyAttributeFilterStats + statsExclusionFilter);
                     ResultSet rs = stmt.executeQuery()) {
                    if (rs.next()) taggedCount = rs.getInt("TOTAL");
                }
                // 未标注：主政策未审核 AND 没有任何附件已复核
                int untaggedCount = 0;
                try (PreparedStatement stmt = conn.prepareStatement(
                        "SELECT COUNT(*) AS TOTAL FROM SYSDBA.POLICY sp WHERE " +
                        "NOT EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA sm WHERE sm.POLICY_ID = sp.ID AND sm.REVIEW_STATUS = '已通过') " +
                        "AND NOT EXISTS (SELECT 1 FROM SYSDBA.POLICY_ATTACHMENT pa WHERE TRIM(pa.POLICY_ID) = CAST(sp.ID AS VARCHAR(50)) AND pa.DEL_FLAG = '0' AND pa.ATTACH_REVIEW_STATUS = '已通过')" +
                        hiddenPolicyAttributeFilterStats + statsExclusionFilter);
                     ResultSet rs = stmt.executeQuery()) {
                    if (rs.next()) untaggedCount = rs.getInt("TOTAL");
                }
                // 标注中：不是完全已标注，也不是完全未标注
                inProgressCount = Math.max(0, allCount - taggedCount - untaggedCount);
                // 已标记：REMARK 字段不为空，且不统计隐藏的主政策属性
                int remarkedCount = 0;
                try (PreparedStatement stmt = conn.prepareStatement(
                        "SELECT COUNT(*) AS TOTAL FROM SYSDBA.POLICY sp WHERE " +
                        "EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA sm WHERE sm.POLICY_ID = sp.ID AND sm.REMARK IS NOT NULL AND sm.REMARK <> '') " +
                        "AND NOT EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA hm WHERE hm.POLICY_ID = sp.ID AND TRIM(hm.POLICY_ATTRIBUTE) IN ('主政策','主属性'))" +
                        statsExclusionFilter);
                     ResultSet rs = stmt.executeQuery()) {
                    if (rs.next()) remarkedCount = rs.getInt("TOTAL");
                }
                Map<String, Object> typeStats = new LinkedHashMap<>();
                typeStats.put("指导政策", 0);
                typeStats.put("实施细则", 0);
                typeStats.put("申报通知", 0);
                typeStats.put("结果公告", 0);
                String typeStatsSql = "SELECT TRIM(sm.POLICY_ATTRIBUTE) AS POLICY_ATTRIBUTE, COUNT(DISTINCT sp.ID) AS TOTAL " +
                    "FROM SYSDBA.POLICY sp JOIN SYSDBA.POLICY_METADATA sm ON sm.POLICY_ID = sp.ID " +
                    "WHERE TRIM(sm.POLICY_ATTRIBUTE) IN ('指导政策','实施细则','申报通知','通知公告','结果公告') " +
                    statsExclusionFilter +
                    " GROUP BY TRIM(sm.POLICY_ATTRIBUTE)";
                try (PreparedStatement stmt = conn.prepareStatement(typeStatsSql);
                     ResultSet rs = stmt.executeQuery()) {
                    while (rs.next()) {
                        String attr = nullToEmpty(rs.getString("POLICY_ATTRIBUTE"));
                        int typeCount = rs.getInt("TOTAL");
                        // 将数据库中的"通知公告"合并到"申报通知"
                        if ("通知公告".equals(attr)) {
                            typeStats.put("申报通知", (int) typeStats.get("申报通知") + typeCount);
                        } else if (typeStats.containsKey(attr)) {
                            typeStats.put(attr, typeCount);
                        }
                    }
                }
                filterStats.put("all", allCount);
                filterStats.put("tagged", taggedCount);
                filterStats.put("inProgress", inProgressCount);
                filterStats.put("untagged", untaggedCount);
                filterStats.put("remarked", remarkedCount);
                filterStats.put("byType", typeStats);
                sendJson(exchange, 200, buildMap(
                    "success", true,
                    "data", policies,
                    "pagination", pagination,
                    "filterStats", filterStats
                ));
            } catch (SQLException e) {
                System.err.println("[Classification Policies SQL Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /**
     * 永久删除政策及数据库关联记录。
     * 仅删除数据库数据，不删除 FILE_PATH 指向的磁盘文件。
     */
    static class ClassificationPolicyDeleteHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);

            if ("OPTIONS".equals(exchange.getRequestMethod())) {
                sendResponse(exchange, 200, "{\"success\":true}");
                return;
            }
            if (!"POST".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持 POST 方法"));
                return;
            }

            String body = new String(readAllBytes(exchange.getRequestBody()), StandardCharsets.UTF_8);
            Map<String, String> data = parseJsonObject(body);
            String policyId = data.getOrDefault("id", "").trim();
            if (policyId.isEmpty() || !policyId.matches("\\d+")) {
                sendJson(exchange, 400, buildMap("success", false, "message", "政策ID无效"));
                return;
            }

            Properties props = new Properties();
            props.setProperty("user", DB_USER);
            props.setProperty("password", DB_PASSWORD);
            props.setProperty("localTimezone", "Asia/Shanghai");
            props.setProperty("connectTimeout", "5000");
            props.setProperty("socketTimeout", "30000");

            Connection deleteConn = null;
            try {
                deleteConn = DriverManager.getConnection(JDBC_URL, props);
                deleteConn.setAutoCommit(false);

                int attachmentRows = executePolicyDelete(deleteConn,
                    "DELETE FROM SYSDBA.POLICY_ATTACHMENT WHERE TRIM(POLICY_ID) = ?", policyId);
                int metadataRows = executePolicyDelete(deleteConn,
                    "DELETE FROM SYSDBA.POLICY_METADATA WHERE POLICY_ID = ?", policyId);
                int originalRows = executePolicyDelete(deleteConn,
                    "DELETE FROM SYSDBA.POLICY_ORIGINAL_V2 WHERE POLICY_ID = ?", policyId);
                int policyRows = executePolicyDelete(deleteConn,
                    "DELETE FROM SYSDBA.POLICY WHERE ID = ?", policyId);

                if (policyRows == 0) {
                    deleteConn.rollback();
                    sendJson(exchange, 404, buildMap("success", false, "message", "政策不存在或已被删除"));
                    return;
                }

                deleteConn.commit();
                Map<String, Object> deleted = new LinkedHashMap<>();
                deleted.put("policy", policyRows);
                deleted.put("attachments", attachmentRows);
                deleted.put("metadata", metadataRows);
                deleted.put("original", originalRows);
                sendJson(exchange, 200, buildMap(
                    "success", true,
                    "message", "政策及关联数据库记录已删除",
                    "deleted", deleted
                ));
            } catch (SQLException e) {
                if (deleteConn != null) {
                    try { deleteConn.rollback(); } catch (SQLException ignored) {}
                }
                System.err.println("[Classification Policy Delete Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", "删除失败，数据库事务已回滚: " + e.getMessage()));
            } finally {
                if (deleteConn != null) {
                    try { deleteConn.close(); } catch (SQLException ignored) {}
                }
            }
        }

        private int executePolicyDelete(Connection connection, String sql, String policyId) throws SQLException {
            try (PreparedStatement stmt = connection.prepareStatement(sql)) {
                stmt.setString(1, policyId);
                return stmt.executeUpdate();
            }
        }
    }

    static class FusionClassificationHandler implements HttpHandler {
        private final String defaultReviewStatus;
        private final boolean defaultRequireSourcePolicyId;

        FusionClassificationHandler(String defaultReviewStatus, boolean defaultRequireSourcePolicyId) {
            this.defaultReviewStatus = nullToEmpty(defaultReviewStatus).trim();
            this.defaultRequireSourcePolicyId = defaultRequireSourcePolicyId;
        }

        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if ("OPTIONS".equals(exchange.getRequestMethod())) {
                sendResponse(exchange, 200, "{\"success\":true}");
                return;
            }
            if (!"GET".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持 GET 方法"));
                return;
            }
            try {
                ensureConnection();
                Map<String, String> params = parseQueryParams(exchange.getRequestURI().getQuery());
                int page = Math.max(1, parseInt(params.getOrDefault("page", "1"), 1));
                int size = Math.min(100, Math.max(1, parseInt(params.getOrDefault("size", "10"), 10)));
                int offset = (page - 1) * size;
                String keyword = params.getOrDefault("keyword", "").trim();
                String structureReviewStatus = params.getOrDefault("structureReviewStatus", "").trim();
                String classificationStatus = params.getOrDefault("classificationStatus", "").trim();
                String reviewStatus = firstNonEmpty(
                    params.getOrDefault("mainReviewStatus", "").trim(),
                    params.getOrDefault("reviewStatus", "").trim(),
                    params.getOrDefault("markReviewStatus", "").trim()
                );
                if (reviewStatus.isEmpty()) {
                    reviewStatus = defaultReviewStatus;
                }
                String sortBy = params.getOrDefault("sortBy", "").trim();
                String sortOrder = params.getOrDefault("sortOrder", "").trim();
                boolean skipFilterStats = "1".equals(params.getOrDefault("skipFilterStats", "").trim())
                    || "true".equalsIgnoreCase(params.getOrDefault("skipFilterStats", "").trim());
                boolean compactList = "1".equals(params.getOrDefault("compactList", "").trim())
                    || "true".equalsIgnoreCase(params.getOrDefault("compactList", "").trim());
                String deleteStatus = params.getOrDefault("deleteStatus", "").trim();
                String requireSourcePolicyIdRaw = params.getOrDefault("requireSourcePolicyId", "").trim();
                boolean requireSourcePolicyId = "1".equals(requireSourcePolicyIdRaw)
                    || "true".equalsIgnoreCase(requireSourcePolicyIdRaw);
                if (requireSourcePolicyIdRaw.isEmpty()) {
                    requireSourcePolicyId = defaultRequireSourcePolicyId;
                }
                String fusionFilter = params.getOrDefault("fusionFilter", "").trim();
                String applicationStatus = params.getOrDefault("applicationStatus", "").trim();
                String sceneUsage = params.getOrDefault("sceneUsage", "").trim();
                String industryField = params.getOrDefault("industryField", "").trim();
                String supportType = params.getOrDefault("supportType", "").trim();
                boolean includeHidden = "1".equals(params.getOrDefault("includeHidden", "").trim())
                    || "true".equalsIgnoreCase(params.getOrDefault("includeHidden", "").trim())
                    || "all".equalsIgnoreCase(params.getOrDefault("frontVisible", "").trim());
                String applicableScope = firstNonEmpty(
                    params.getOrDefault("applicableScope", "").trim(),
                    params.getOrDefault("scope", "").trim()
                );
                boolean requiresAdvancedFiltering = isSupportedApplicationStatus(applicationStatus)
                    || !applicableScope.isEmpty();

                StringBuilder commonWhere = new StringBuilder(" WHERE 1=1");
                List<String> commonValues = new ArrayList<>();
                if (!includeHidden) {
                    commonWhere.append(" AND TRIM(NVL(f.FRONT_VISIBLE,'1')) <> '0'");
                }
                appendMainReviewStatusCondition(commonWhere, commonValues, reviewStatus);
                if (requireSourcePolicyId) {
                    commonWhere.append(" AND f.SOURCE_POLICY_ID IS NOT NULL AND TRIM(f.SOURCE_POLICY_ID) <> ''");
                }
                if (!keyword.isEmpty()) {
                    commonWhere.append(" AND (f.DIRECTORY_TITLE LIKE ? OR f.MAIN_FILE_TITLE LIKE ? OR EXISTS " +
                        "(SELECT 1 FROM SYSDBA.X1_POLICY_DOCUMENT kd WHERE kd.FUSION_POLICY_ID=f.ID AND kd.DEL_FLAG = TRIM(NVL(f.DEL_FLAG,'0')) AND kd.POLICY_TITLE LIKE ?))");
                    String like = "%" + keyword + "%";
                    commonValues.add(like); commonValues.add(like); commonValues.add(like);
                }
                if (!sceneUsage.isEmpty()) {
                    commonWhere.append(" AND (EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA sm WHERE TRIM(sm.POLICY_ID)=TRIM(f.SOURCE_POLICY_ID) AND sm.SCENE_USAGE LIKE ?) OR EXISTS " +
                        "(SELECT 1 FROM SYSDBA.X1_POLICY_DOCUMENT sd JOIN SYSDBA.POLICY_METADATA sm2 ON TRIM(sm2.POLICY_ID)=TRIM(sd.SOURCE_POLICY_ID) WHERE sd.FUSION_POLICY_ID=f.ID AND sd.DEL_FLAG = TRIM(NVL(f.DEL_FLAG,'0')) AND sm2.SCENE_USAGE LIKE ?))");
                    String like = "%" + sceneUsage + "%";
                    commonValues.add(like); commonValues.add(like);
                }
                if (!industryField.isEmpty()) {
                    commonWhere.append(" AND (EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA im WHERE TRIM(im.POLICY_ID)=TRIM(f.SOURCE_POLICY_ID) AND im.INDUSTRY_FIELD LIKE ?) OR EXISTS " +
                        "(SELECT 1 FROM SYSDBA.X1_POLICY_DOCUMENT idoc JOIN SYSDBA.POLICY_METADATA im2 ON TRIM(im2.POLICY_ID)=TRIM(idoc.SOURCE_POLICY_ID) WHERE idoc.FUSION_POLICY_ID=f.ID AND idoc.DEL_FLAG = TRIM(NVL(f.DEL_FLAG,'0')) AND im2.INDUSTRY_FIELD LIKE ?))");
                    String like = "%" + industryField + "%";
                    commonValues.add(like); commonValues.add(like);
                }
                if (!supportType.isEmpty()) {
                    commonWhere.append(" AND (EXISTS (SELECT 1 FROM SYSDBA.SUPPORT_METHOD st WHERE TRIM(st.POLICY_ID)=TRIM(f.SOURCE_POLICY_ID) AND st.SUPPORT_TYPE LIKE ?) OR EXISTS " +
                        "(SELECT 1 FROM SYSDBA.X1_POLICY_DOCUMENT std JOIN SYSDBA.SUPPORT_METHOD st2 ON TRIM(st2.POLICY_ID)=TRIM(std.SOURCE_POLICY_ID) WHERE std.FUSION_POLICY_ID=f.ID AND std.DEL_FLAG = TRIM(NVL(f.DEL_FLAG,'0')) AND st2.SUPPORT_TYPE LIKE ?))");
                    String like = "%" + supportType + "%";
                    commonValues.add(like); commonValues.add(like);
                }
                if ("none".equals(fusionFilter)) {
                    commonWhere.append(" AND NOT EXISTS (SELECT 1 FROM SYSDBA.X1_POLICY_DOCUMENT d WHERE d.FUSION_POLICY_ID=f.ID AND d.DEL_FLAG = TRIM(NVL(f.DEL_FLAG,'0')))");
                } else if ("pending".equals(fusionFilter)) {
                    commonWhere.append(" AND EXISTS (SELECT 1 FROM SYSDBA.X1_POLICY_DOCUMENT d WHERE d.FUSION_POLICY_ID=f.ID AND d.DEL_FLAG = TRIM(NVL(f.DEL_FLAG,'0')))");
                    commonWhere.append(" AND NOT EXISTS (SELECT 1 FROM SYSDBA.X1_POLICY_DOCUMENT d JOIN SYSDBA.APPLICATION_CONDITION ac ON ac.POLICY_ID = d.SOURCE_POLICY_ID WHERE d.FUSION_POLICY_ID=f.ID AND d.DEL_FLAG = TRIM(NVL(f.DEL_FLAG,'0')))");
                } else if ("done".equals(fusionFilter)) {
                    commonWhere.append(" AND EXISTS (SELECT 1 FROM SYSDBA.X1_POLICY_DOCUMENT d JOIN SYSDBA.APPLICATION_CONDITION ac ON ac.POLICY_ID = d.SOURCE_POLICY_ID WHERE d.FUSION_POLICY_ID=f.ID AND d.DEL_FLAG = TRIM(NVL(f.DEL_FLAG,'0')))");
                }

                StringBuilder baseWhere = new StringBuilder(commonWhere);
                List<String> baseValues = new ArrayList<>(commonValues);
                appendDeleteStatusCondition(baseWhere, deleteStatus);
                String documentCountExpr = fusionDocumentCountExpr("f");
                String classifiedCountExpr = fusionClassifiedCountExpr("f");
                String documentReviewedCountExpr = fusionDocumentReviewedCountExpr("f");
                String attachmentCountExpr = fusionAttachmentCountExpr("f");
                String attachmentReviewedCountExpr = fusionAttachmentReviewedCountExpr("f");
                StringBuilder where = new StringBuilder(baseWhere);
                List<String> values = new ArrayList<>(baseValues);
                StringBuilder filterStatsWhere = new StringBuilder(commonWhere);
                List<String> filterStatsValues = new ArrayList<>(commonValues);
                appendDeleteStatusCondition(filterStatsWhere, "active");
                appendStructureReviewStatusCondition(where, values, structureReviewStatus);
                appendClassificationStatusCondition(where, classificationStatus, documentReviewedCountExpr, attachmentCountExpr, attachmentReviewedCountExpr);

                int total = 0;
                List<Map<String, Object>> rows;
                if (requiresAdvancedFiltering) {
                    List<Map<String, Object>> allRows = queryFusionClassificationRows(where.toString(), values, null, null, sortBy, sortOrder, true);
                    List<Map<String, Object>> filteredRows = applyAdvancedListFilters(allRows, applicationStatus, sceneUsage, applicableScope);
                    total = filteredRows.size();
                    rows = paginateRows(filteredRows, offset, size);
                } else {
                    total = countFusionClassificationRows(where.toString(), values);
                    rows = queryFusionClassificationRows(where.toString(), values, size, offset, sortBy, sortOrder, !compactList);
                }

                Map<String, Object> stats = new LinkedHashMap<>();
                String visibleFusionCondition = includeHidden ? "" : " AND TRIM(NVL(FRONT_VISIBLE,'1')) <> '0'";
                String visibleDocumentCondition = includeHidden ? "" :
                    " AND EXISTS (SELECT 1 FROM SYSDBA.X1_FUSION_POLICY vf " +
                    "WHERE vf.ID = X1_POLICY_DOCUMENT.FUSION_POLICY_ID " +
                    "AND TRIM(NVL(vf.DEL_FLAG,'0'))='0' " +
                    "AND TRIM(NVL(vf.FRONT_VISIBLE,'1')) <> '0')";
                try (Statement stmt = conn.createStatement(); ResultSet rs = stmt.executeQuery(
                        "SELECT (SELECT COUNT(*) FROM SYSDBA.X1_FUSION_POLICY WHERE DEL_FLAG='0'" + visibleFusionCondition + ") FUSIONS," +
                        "(SELECT COUNT(*) FROM SYSDBA.X1_POLICY_DOCUMENT WHERE DEL_FLAG='0'" + visibleDocumentCondition + ") DOCUMENTS," +
                        "(SELECT COUNT(*) FROM SYSDBA.X1_POLICY_DOCUMENT WHERE DEL_FLAG='0' AND POLICY_ATTRIBUTE IS NOT NULL" + visibleDocumentCondition + ") CLASSIFIED FROM DUAL")) {
                    if (rs.next()) {
                        stats.put("fusionPolicies", rs.getInt("FUSIONS"));
                        stats.put("documents", rs.getInt("DOCUMENTS"));
                        stats.put("classified", rs.getInt("CLASSIFIED"));
                    }
                }
                Map<String, Object> pagination = new LinkedHashMap<>();
                pagination.put("page", page); pagination.put("size", size); pagination.put("total", total);
                Map<String, Object> response = buildMap("success", true, "data", rows, "pagination", pagination, "statistics", stats);
                if (!skipFilterStats) {
                    Map<String, Object> filterStats = queryFusionClassificationFilterStats(filterStatsWhere.toString(), filterStatsValues);
                    response.put("filterStats", filterStats);
                }
                sendJson(exchange, 200, response);
            } catch (SQLException e) {
                System.err.println("[Fusion Classification List Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            } catch (Exception e) {
                System.err.println("[Fusion Classification List Unexpected] " + e.getClass().getName() + ": " + e.getMessage());
                e.printStackTrace();
                try { sendJson(exchange, 500, buildMap("success", false, "message", "内部错误: " + e.getMessage())); } catch (Exception ignored) {}
            }
        }

        private int countFusionClassificationRows(String whereSql, List<String> values) throws SQLException {
            try (PreparedStatement stmt = conn.prepareStatement("SELECT COUNT(DISTINCT f.ID) FROM SYSDBA.X1_FUSION_POLICY f" + whereSql)) {
                int i = 1;
                for (String value : values) stmt.setString(i++, value);
                try (ResultSet rs = stmt.executeQuery()) {
                    return rs.next() ? rs.getInt(1) : 0;
                }
            }
        }

        private boolean isDocumentCountSort(String sortBy, String sortOrder) {
            String normalizedSortBy = nullToEmpty(sortBy).trim();
            String normalizedOrder = nullToEmpty(sortOrder).trim();
            boolean supportedSortBy = "documentCount".equalsIgnoreCase(normalizedSortBy) || "document_count".equalsIgnoreCase(normalizedSortBy);
            boolean supportedOrder = "ascending".equalsIgnoreCase(normalizedOrder)
                || "asc".equalsIgnoreCase(normalizedOrder)
                || "descending".equalsIgnoreCase(normalizedOrder)
                || "desc".equalsIgnoreCase(normalizedOrder);
            return supportedSortBy && supportedOrder;
        }

        private String buildFusionClassificationOrderBy(String sortBy, String sortOrder, String documentCountOrderExpr) {
            if (isDocumentCountSort(sortBy, sortOrder)) {
                return " ORDER BY " + documentCountOrderExpr + " " + resolveDocumentCountSortDirection(sortOrder) + ",f.SORT_NO,f.ID";
            }
            return " ORDER BY f.SORT_NO,f.ID";
        }

        private String resolveDocumentCountSortDirection(String sortOrder) {
            String normalizedOrder = nullToEmpty(sortOrder).trim();
            return ("ascending".equalsIgnoreCase(normalizedOrder) || "asc".equalsIgnoreCase(normalizedOrder)) ? "ASC" : "DESC";
        }

        private List<Map<String, Object>> queryFusionClassificationRows(String whereSql, List<String> values, Integer limit, Integer offset, String sortBy, String sortOrder, boolean includeListDetails) throws SQLException {
            List<Map<String, Object>> rows = new ArrayList<>();
            Map<String, Map<String, Object>> byId = new LinkedHashMap<>();
            String documentMetricsJoinSql = " LEFT JOIN (" + fusionDocumentMetricsAggregateSql() + ") dm ON dm.FUSION_POLICY_ID=f.ID AND dm.DEL_FLAG = TRIM(NVL(f.DEL_FLAG,'0'))";
            String attachmentMetricsJoinSql = " LEFT JOIN (" + fusionAttachmentMetricsAggregateSql() + ") am ON am.FUSION_POLICY_ID=f.ID AND am.DEL_FLAG = TRIM(NVL(f.DEL_FLAG,'0'))";
            String documentCountSelectExpr = "NVL(dm.DOCUMENT_COUNT,0)";
            String orderBySql = buildFusionClassificationOrderBy(sortBy, sortOrder, documentCountSelectExpr);
            String baseColumnSql = "f.ID,f.SOURCE_POLICY_ID,f.DEL_FLAG,f.DIRECTORY_TITLE,f.DIRECTORY_PATH,f.MAIN_FILE_TITLE,f.MAIN_FILE_PATH,f.STATUS,f.SORT_NO,f.UPDATED_AT," +
                "f.MARK_REVIEW_STATUS,f.MARK_REVIEWED_AT,f.MARK_REVIEWED_BY,f.MARK_REVIEW_REMARK," +
                "f.STRUCTURE_REVIEW_STATUS,f.STRUCTURE_REVIEWED_AT,f.STRUCTURE_REVIEWED_BY,f.STRUCTURE_REVIEW_REMARK";
            String listSql = "SELECT " + baseColumnSql + "," +
                documentCountSelectExpr + " DOCUMENT_COUNT," +
                "NVL(dm.CLASSIFIED_COUNT,0) CLASSIFIED_COUNT," +
                "NVL(dm.DOCUMENT_REVIEWED_COUNT,0) DOCUMENT_REVIEWED_COUNT," +
                "NVL(am.ATTACHMENT_COUNT,0) ATTACHMENT_COUNT," +
                "NVL(am.ATTACHMENT_REVIEWED_COUNT,0) ATTACHMENT_REVIEWED_COUNT," +
                "(SELECT m.POLICY_ORIGINAL_TITLE FROM SYSDBA.POLICY_METADATA m WHERE TRIM(m.POLICY_ID)=TRIM(f.SOURCE_POLICY_ID) AND ROWNUM=1) POLICY_ORIGINAL_TITLE," +
                "CASE WHEN EXISTS (SELECT 1 FROM SYSDBA.X1_POLICY_DOCUMENT d JOIN SYSDBA.APPLICATION_CONDITION ac ON ac.POLICY_ID = d.SOURCE_POLICY_ID WHERE d.FUSION_POLICY_ID=f.ID AND d.DEL_FLAG = TRIM(NVL(f.DEL_FLAG,'0'))) THEN 1 ELSE 0 END HAS_FUSION_RESULT " +
                "FROM SYSDBA.X1_FUSION_POLICY f" + documentMetricsJoinSql + attachmentMetricsJoinSql + whereSql + orderBySql;
            if (limit != null && offset != null) {
                listSql += " LIMIT ? OFFSET ?";
            }

            try (PreparedStatement stmt = conn.prepareStatement(listSql)) {
                int i = 1;
                for (String value : values) stmt.setString(i++, value);
                if (limit != null && offset != null) {
                    stmt.setInt(i++, limit);
                    stmt.setInt(i, offset);
                }
                try (ResultSet rs = stmt.executeQuery()) {
                    while (rs.next()) {
                        String id = rs.getString("ID");
                        Map<String, Object> row = new LinkedHashMap<>();
                        row.put("id", id);
                        row.put("sourcePolicyId", nullToEmpty(rs.getString("SOURCE_POLICY_ID")));
                        Map<String, Object> policyInfo = new LinkedHashMap<>();
                        policyInfo.put("policyOriginalTitle", nullToEmpty(rs.getString("POLICY_ORIGINAL_TITLE")));
                        row.put("policyInfo", policyInfo);
                        row.put("delFlag", nullToEmpty(rs.getString("DEL_FLAG")));
                        row.put("deleted", "1".equals(nullToEmpty(rs.getString("DEL_FLAG")).trim()));
                        row.put("directoryTitle", nullToEmpty(rs.getString("DIRECTORY_TITLE")));
                        row.put("directoryPath", nullToEmpty(rs.getString("DIRECTORY_PATH")));
                        row.put("mainFileTitle", nullToEmpty(rs.getString("MAIN_FILE_TITLE")));
                        row.put("mainFilePath", nullToEmpty(rs.getString("MAIN_FILE_PATH")));
                        row.put("status", nullToEmpty(rs.getString("STATUS")));
                        row.put("sortNo", rs.getInt("SORT_NO"));
                        row.put("updatedAt", nullToEmpty(rs.getString("UPDATED_AT")));
                        row.put("reviewStatus", nullToEmpty(rs.getString("MARK_REVIEW_STATUS")));
                        row.put("reviewedAt", nullToEmpty(rs.getString("MARK_REVIEWED_AT")));
                        row.put("reviewedBy", nullToEmpty(rs.getString("MARK_REVIEWED_BY")));
                        row.put("reviewRemark", nullToEmpty(rs.getString("MARK_REVIEW_REMARK")));
                        row.put("structureReviewStatus", nullToEmpty(rs.getString("STRUCTURE_REVIEW_STATUS")));
                        row.put("structureReviewedAt", nullToEmpty(rs.getString("STRUCTURE_REVIEWED_AT")));
                        row.put("structureReviewedBy", nullToEmpty(rs.getString("STRUCTURE_REVIEWED_BY")));
                        row.put("structureReviewRemark", nullToEmpty(rs.getString("STRUCTURE_REVIEW_REMARK")));
                        row.put("documentCount", rs.getInt("DOCUMENT_COUNT"));
                        row.put("classifiedCount", rs.getInt("CLASSIFIED_COUNT"));
                        row.put("documentReviewedCount", rs.getInt("DOCUMENT_REVIEWED_COUNT"));
                        row.put("attachmentCount", rs.getInt("ATTACHMENT_COUNT"));
                        row.put("attachmentReviewedCount", rs.getInt("ATTACHMENT_REVIEWED_COUNT"));
                        row.put("hasFusionResult", rs.getInt("HAS_FUSION_RESULT") == 1);
                        // 关联文件的结构化字段不等于融合结果。未存在专属融合结构化记录时，列表不得展示场景用途、适用范围、申报时间或最高奖励。
                        row.put("hasFusionStructuredData", false);
                        row.put("documents", new ArrayList<Map<String, Object>>());
                        initializeFusionHierarchyFields(row);
                        rows.add(row);
                        byId.put(id, row);
                    }
                }
            }

            if (!byId.isEmpty()) {
                try {
                    fillFusionHierarchySummary(conn, byId);
                } catch (SQLException hierarchyError) {
                    System.err.println("[Fusion Classification Hierarchy Summary Error] " + hierarchyError.getMessage());
                }
            }

            if (includeListDetails && !byId.isEmpty()) {
                loadFusionDocuments(byId);
                Map<String, String> sourcePolicyIds = resolveConfiguredListSourcePolicyIds(rows);
                fillListSummaryFields(byId, sourcePolicyIds);
            }
            return rows;
        }

        private List<Map<String, Object>> applyAdvancedListFilters(List<Map<String, Object>> rows, String applicationStatus, String sceneUsage, String applicableScope) {
            List<Map<String, Object>> filteredRows = new ArrayList<>();
            for (Map<String, Object> row : rows) {
                if (!matchesApplicationStatus(row, applicationStatus)) continue;
                if (!matchesMultiValueField(nullToEmpty((String) row.get("sceneUsage")), sceneUsage)) continue;
                if (!matchesMultiValueField(nullToEmpty((String) row.get("applicableScope")), applicableScope)) continue;
                filteredRows.add(row);
            }
            return filteredRows;
        }

        private String fusionDocumentCountExpr(String alias) {
            return "(SELECT COUNT(DISTINCT TRIM(d.SOURCE_POLICY_ID)) FROM SYSDBA.X1_POLICY_DOCUMENT d WHERE " +
                fusionSourceDocumentBaseWhere("d", alias) + ")";
        }

        private String fusionDocumentMetricsAggregateSql() {
            return "SELECT d.FUSION_POLICY_ID,d.DEL_FLAG," +
                "COUNT(DISTINCT TRIM(d.SOURCE_POLICY_ID)) DOCUMENT_COUNT," +
                "COUNT(DISTINCT CASE WHEN (" +
                "(d.POLICY_ATTRIBUTE IS NOT NULL AND TRIM(d.POLICY_ATTRIBUTE) <> '') " +
                "OR NVL(pm.HAS_TYPE,0) = 1" +
                ") THEN TRIM(d.SOURCE_POLICY_ID) END) CLASSIFIED_COUNT," +
                "COUNT(DISTINCT CASE WHEN NVL(pm.IS_REVIEWED,0) = 1 THEN TRIM(d.SOURCE_POLICY_ID) END) DOCUMENT_REVIEWED_COUNT " +
                "FROM SYSDBA.X1_POLICY_DOCUMENT d " +
                "LEFT JOIN (" +
                "SELECT TRIM(POLICY_ID) AS POLICY_ID," +
                "MAX(CASE WHEN POLICY_ATTRIBUTE IS NOT NULL AND TRIM(POLICY_ATTRIBUTE) <> '' THEN 1 ELSE 0 END) AS HAS_TYPE," +
                "MAX(CASE WHEN REVIEW_STATUS = '已通过' THEN 1 ELSE 0 END) AS IS_REVIEWED " +
                "FROM SYSDBA.POLICY_METADATA GROUP BY TRIM(POLICY_ID)" +
                ") pm ON pm.POLICY_ID = TRIM(d.SOURCE_POLICY_ID) " +
                "WHERE " + fusionSourceDocumentStandaloneBaseWhere("d") +
                " GROUP BY d.FUSION_POLICY_ID, d.DEL_FLAG";
        }

        private String fusionAttachmentMetricsAggregateSql() {
            return "SELECT docs.FUSION_POLICY_ID,docs.DEL_FLAG," +
                "COUNT(DISTINCT pa.ATTACH_ID) ATTACHMENT_COUNT," +
                "COUNT(DISTINCT CASE WHEN pa.ATTACH_REVIEW_STATUS='已通过' THEN pa.ATTACH_ID END) ATTACHMENT_REVIEWED_COUNT " +
                "FROM (" +
                "SELECT DISTINCT d.FUSION_POLICY_ID, TRIM(d.SOURCE_POLICY_ID) AS SOURCE_POLICY_ID, d.DEL_FLAG " +
                "FROM SYSDBA.X1_POLICY_DOCUMENT d WHERE " + fusionSourceDocumentStandaloneBaseWhere("d") +
                ") docs " +
                "JOIN SYSDBA.POLICY_ATTACHMENT pa ON TRIM(pa.POLICY_ID)=docs.SOURCE_POLICY_ID AND pa.DEL_FLAG='0' " +
                "GROUP BY docs.FUSION_POLICY_ID, docs.DEL_FLAG";
        }

        private String fusionClassifiedCountExpr(String alias) {
            return "(SELECT COUNT(DISTINCT TRIM(d.SOURCE_POLICY_ID)) FROM SYSDBA.X1_POLICY_DOCUMENT d WHERE " +
                fusionSourceDocumentBaseWhere("d", alias) + " " +
                "AND (" +
                "(d.POLICY_ATTRIBUTE IS NOT NULL AND TRIM(d.POLICY_ATTRIBUTE) <> '') " +
                "OR EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID=d.SOURCE_POLICY_ID AND m.POLICY_ATTRIBUTE IS NOT NULL AND TRIM(m.POLICY_ATTRIBUTE) <> '')" +
                "))";
        }

        private String fusionDocumentReviewedCountExpr(String alias) {
            return "(SELECT COUNT(DISTINCT TRIM(d.SOURCE_POLICY_ID)) FROM SYSDBA.X1_POLICY_DOCUMENT d WHERE " +
                fusionSourceDocumentBaseWhere("d", alias) + " " +
                "AND EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID=d.SOURCE_POLICY_ID AND m.REVIEW_STATUS='已通过'))";
        }

        private String fusionAttachmentCountExpr(String alias) {
            return "(SELECT COUNT(DISTINCT pa.ATTACH_ID) FROM SYSDBA.POLICY_ATTACHMENT pa " +
                "JOIN (SELECT DISTINCT TRIM(d.SOURCE_POLICY_ID) AS SOURCE_POLICY_ID FROM SYSDBA.X1_POLICY_DOCUMENT d WHERE " +
                fusionSourceDocumentBaseWhere("d", alias) +
                ") docs ON TRIM(pa.POLICY_ID)=docs.SOURCE_POLICY_ID " +
                "WHERE pa.DEL_FLAG='0')";
        }

        private String fusionAttachmentReviewedCountExpr(String alias) {
            return "(SELECT COUNT(DISTINCT pa.ATTACH_ID) FROM SYSDBA.POLICY_ATTACHMENT pa " +
                "JOIN (SELECT DISTINCT TRIM(d.SOURCE_POLICY_ID) AS SOURCE_POLICY_ID FROM SYSDBA.X1_POLICY_DOCUMENT d WHERE " +
                fusionSourceDocumentBaseWhere("d", alias) +
                ") docs ON TRIM(pa.POLICY_ID)=docs.SOURCE_POLICY_ID " +
                "WHERE pa.DEL_FLAG='0' AND pa.ATTACH_REVIEW_STATUS='已通过')";
        }

        private String fusionSourceDocumentBaseWhere(String docAlias, String fusionAlias) {
            return docAlias + ".FUSION_POLICY_ID=" + fusionAlias + ".ID " +
                "AND " + docAlias + ".DEL_FLAG = TRIM(NVL(" + fusionAlias + ".DEL_FLAG,'0')) " +
                "AND " + fusionSourceDocumentStandaloneBaseWhere(docAlias);
        }

        private String fusionSourceDocumentStandaloneBaseWhere(String docAlias) {
            return docAlias + ".SOURCE_POLICY_ID IS NOT NULL " +
                "AND TRIM(" + docAlias + ".SOURCE_POLICY_ID) <> '' " +
                "AND NOT (" +
                "TRIM(NVL(" + docAlias + ".POLICY_ATTRIBUTE,'')) IN ('实施细则类','指导政策类','申报材料类','申报通知类','结果公告类','通知公告类','其它') " +
                "OR LOWER(NVL(" + docAlias + ".FILE_PATH,'')) LIKE '%/attachments/%'" +
                ")";
        }

        private void appendDeleteStatusCondition(StringBuilder where, String deleteStatus) {
            String normalized = nullToEmpty(deleteStatus).trim();
            if (normalized.isEmpty() || "active".equalsIgnoreCase(normalized) || "未删除".equals(normalized)) {
                where.append(" AND TRIM(NVL(f.DEL_FLAG,'0')) = '0'");
                return;
            }
            if ("deleted".equalsIgnoreCase(normalized) || "已删除".equals(normalized)) {
                where.append(" AND TRIM(NVL(f.DEL_FLAG,'0')) = '1'");
            }
        }

        private void appendMainReviewStatusCondition(StringBuilder where, List<String> values, String reviewStatus) {
            String normalized = nullToEmpty(reviewStatus).trim();
            if (normalized.isEmpty() || "all".equalsIgnoreCase(normalized) || "全部".equals(normalized)) {
                return;
            }
            if ("approved".equalsIgnoreCase(normalized) || FUSION_REVIEW_STATUS_APPROVED.equals(normalized) || "已通过".equals(normalized) || "已审核".equals(normalized)) {
                where.append(" AND TRIM(NVL(f.MARK_REVIEW_STATUS,'')) = ?");
                values.add(FUSION_REVIEW_STATUS_APPROVED);
            } else if ("pending".equalsIgnoreCase(normalized) || FUSION_REVIEW_STATUS_PENDING.equals(normalized) || "待审核".equals(normalized) || "未审核".equals(normalized)) {
                where.append(" AND TRIM(NVL(f.MARK_REVIEW_STATUS,'')) = ?");
                values.add(FUSION_REVIEW_STATUS_PENDING);
            }
        }

        private void appendStructureReviewStatusCondition(StringBuilder where, List<String> values, String structureReviewStatus) {
            String normalized = nullToEmpty(structureReviewStatus).trim();
            if (normalized.isEmpty() || "all".equalsIgnoreCase(normalized) || "全部".equals(normalized)) {
                return;
            }
            if ("approved".equalsIgnoreCase(normalized) || STRUCTURE_REVIEW_STATUS_APPROVED.equals(normalized) || "已通过".equals(normalized) || "已审核".equals(normalized)) {
                where.append(" AND TRIM(NVL(f.STRUCTURE_REVIEW_STATUS,'')) = ?");
                values.add(STRUCTURE_REVIEW_STATUS_APPROVED);
            } else if ("pending".equalsIgnoreCase(normalized) || STRUCTURE_REVIEW_STATUS_PENDING.equals(normalized) || "待审核".equals(normalized) || "未审核".equals(normalized)) {
                where.append(" AND TRIM(NVL(f.STRUCTURE_REVIEW_STATUS,'')) <> ?");
                values.add(STRUCTURE_REVIEW_STATUS_APPROVED);
            }
        }

        private void appendClassificationStatusCondition(StringBuilder where, String classificationStatus, String documentReviewedCountExpr, String attachmentCountExpr, String attachmentReviewedCountExpr) {
            String normalized = nullToEmpty(classificationStatus).trim();
            if (normalized.isEmpty() || "all".equalsIgnoreCase(normalized) || "全部".equals(normalized)) {
                return;
            }
            String totalReviewedExpr = "(" + documentReviewedCountExpr + " + " + attachmentReviewedCountExpr + ")";
            String totalAuditExpr = "(" + fusionDocumentCountExpr("f") + " + " + attachmentCountExpr + ")";
            if ("tagged".equalsIgnoreCase(normalized) || "已标注".equals(normalized)) {
                where.append(" AND ").append(totalAuditExpr).append(" > 0");
                where.append(" AND ").append(totalReviewedExpr).append(" >= ").append(totalAuditExpr);
            } else if ("inProgress".equalsIgnoreCase(normalized) || "标注中".equals(normalized)) {
                where.append(" AND ").append(totalAuditExpr).append(" > 0");
                where.append(" AND ").append(totalReviewedExpr).append(" < ").append(totalAuditExpr);
            } else if ("untagged".equalsIgnoreCase(normalized) || "未标注".equals(normalized)) {
                where.append(" AND ").append(totalAuditExpr).append(" = 0");
            }
        }

        private int countFusionClassificationRowsWithCondition(String whereSql, List<String> values, String extraCondition) throws SQLException {
            StringBuilder sql = new StringBuilder("SELECT COUNT(*) FROM SYSDBA.X1_FUSION_POLICY f").append(whereSql);
            if (extraCondition != null && !extraCondition.trim().isEmpty()) {
                sql.append(" AND ").append(extraCondition);
            }
            try (PreparedStatement stmt = conn.prepareStatement(sql.toString())) {
                int i = 1;
                for (String value : values) stmt.setString(i++, value);
                try (ResultSet rs = stmt.executeQuery()) {
                    return rs.next() ? rs.getInt(1) : 0;
                }
            }
        }

        private Map<String, Object> queryFusionClassificationFilterStats(String baseWhereSql, List<String> baseValues) throws SQLException {
            Map<String, Object> filterStats = new LinkedHashMap<>();
            String sql = "SELECT " +
                "COUNT(*) AS ALL_COUNT," +
                "SUM(CASE WHEN TOTAL_AUDIT > 0 AND TOTAL_REVIEWED >= TOTAL_AUDIT THEN 1 ELSE 0 END) AS ANNOTATION_TAGGED," +
                "SUM(CASE WHEN TOTAL_AUDIT > 0 AND TOTAL_REVIEWED < TOTAL_AUDIT THEN 1 ELSE 0 END) AS ANNOTATION_IN_PROGRESS," +
                "SUM(CASE WHEN TOTAL_AUDIT = 0 THEN 1 ELSE 0 END) AS ANNOTATION_UNTAGGED," +
                "SUM(CASE WHEN REVIEW_STATUS = '" + FUSION_REVIEW_STATUS_APPROVED + "' THEN 1 ELSE 0 END) AS MAIN_REVIEW_APPROVED," +
                "SUM(CASE WHEN REVIEW_STATUS = '" + FUSION_REVIEW_STATUS_PENDING + "' THEN 1 ELSE 0 END) AS MAIN_REVIEW_PENDING," +
                "SUM(CASE WHEN STRUCTURE_REVIEW_STATUS = '" + STRUCTURE_REVIEW_STATUS_APPROVED + "' THEN 1 ELSE 0 END) AS STRUCTURE_REVIEW_APPROVED," +
                "SUM(CASE WHEN NVL(STRUCTURE_REVIEW_STATUS,'') <> '" + STRUCTURE_REVIEW_STATUS_APPROVED + "' THEN 1 ELSE 0 END) AS STRUCTURE_REVIEW_PENDING " +
                "FROM (" +
                "SELECT DISTINCT f.ID," +
                "TRIM(NVL(f.MARK_REVIEW_STATUS,'')) AS REVIEW_STATUS," +
                "TRIM(NVL(f.STRUCTURE_REVIEW_STATUS,'')) AS STRUCTURE_REVIEW_STATUS," +
                "NVL(dm.DOCUMENT_COUNT,0) AS DOCUMENT_COUNT," +
                "NVL(dm.DOCUMENT_REVIEWED_COUNT,0) AS DOCUMENT_REVIEWED_COUNT," +
                "NVL(am.ATTACHMENT_COUNT,0) AS ATTACHMENT_COUNT," +
                "NVL(am.ATTACHMENT_REVIEWED_COUNT,0) AS ATTACHMENT_REVIEWED_COUNT," +
                "(NVL(dm.DOCUMENT_COUNT,0) + NVL(am.ATTACHMENT_COUNT,0)) AS TOTAL_AUDIT," +
                "(NVL(dm.DOCUMENT_REVIEWED_COUNT,0) + NVL(am.ATTACHMENT_REVIEWED_COUNT,0)) AS TOTAL_REVIEWED " +
                "FROM SYSDBA.X1_FUSION_POLICY f " +
                "LEFT JOIN (" + fusionDocumentMetricsAggregateSql() + ") dm ON dm.FUSION_POLICY_ID = f.ID AND dm.DEL_FLAG = TRIM(NVL(f.DEL_FLAG,'0')) " +
                "LEFT JOIN (" + fusionAttachmentMetricsAggregateSql() + ") am ON am.FUSION_POLICY_ID = f.ID AND am.DEL_FLAG = TRIM(NVL(f.DEL_FLAG,'0')) " +
                baseWhereSql +
                ") stats";
            try (PreparedStatement stmt = conn.prepareStatement(sql)) {
                int i = 1;
                for (String value : baseValues) stmt.setString(i++, value);
                try (ResultSet rs = stmt.executeQuery()) {
                    if (rs.next()) {
                        filterStats.put("all", rs.getInt("ALL_COUNT"));
                        filterStats.put("annotationTagged", rs.getInt("ANNOTATION_TAGGED"));
                        filterStats.put("annotationInProgress", rs.getInt("ANNOTATION_IN_PROGRESS"));
                        filterStats.put("annotationUntagged", rs.getInt("ANNOTATION_UNTAGGED"));
                        filterStats.put("mainReviewApproved", rs.getInt("MAIN_REVIEW_APPROVED"));
                        filterStats.put("mainReviewPending", rs.getInt("MAIN_REVIEW_PENDING"));
                        filterStats.put("structureReviewApproved", rs.getInt("STRUCTURE_REVIEW_APPROVED"));
                        filterStats.put("structureReviewPending", rs.getInt("STRUCTURE_REVIEW_PENDING"));
                    }
                }
            }
            return filterStats;
        }

        private boolean matchesApplicationStatus(Map<String, Object> row, String applicationStatus) {
            if (!isSupportedApplicationStatus(applicationStatus)) return true;
            LocalDate now = LocalDate.now();
            String applicationStartTime = nullToEmpty((String) row.get("applicationStartTime")).trim();
            String applicationEndTime = nullToEmpty((String) row.get("applicationEndTime")).trim();
            LocalDate startDate = parseLocalDateValue(applicationStartTime);
            LocalDate endDate = parseLocalDateValue(applicationEndTime);
            if ("0".equals(applicationStatus)) {
                return startDate != null && now.isBefore(startDate);
            }
            if ("1".equals(applicationStatus)) {
                if (applicationStartTime.isEmpty() && applicationEndTime.isEmpty()) {
                    return true;
                }
                return startDate != null && endDate != null && !now.isBefore(startDate) && !now.isAfter(endDate);
            }
            if ("2".equals(applicationStatus)) {
                return endDate != null && now.isAfter(endDate);
            }
            return true;
        }

        private boolean matchesMultiValueField(String fieldValue, String filterValue) {
            String normalizedFilter = nullToEmpty(filterValue).trim();
            if (normalizedFilter.isEmpty()) return true;
            String normalizedField = nullToEmpty(fieldValue).trim();
            if (normalizedField.isEmpty()) return false;
            for (String part : normalizedField.split("[；;]")) {
                String token = nullToEmpty(part).trim();
                if (!token.isEmpty() && token.equals(normalizedFilter)) return true;
            }
            return normalizedField.contains(normalizedFilter);
        }

        private List<Map<String, Object>> paginateRows(List<Map<String, Object>> rows, int offset, int size) {
            if (rows.isEmpty() || offset >= rows.size()) return new ArrayList<>();
            int endIndex = Math.min(rows.size(), offset + size);
            return new ArrayList<>(rows.subList(offset, endIndex));
        }

        private boolean isSupportedApplicationStatus(String applicationStatus) {
            return "0".equals(applicationStatus) || "1".equals(applicationStatus) || "2".equals(applicationStatus);
        }

        private void loadFusionDocuments(Map<String, Map<String, Object>> byId) throws SQLException {
            String placeholders = repeatPlaceholders(byId.size());
            String docSql = "SELECT d.ID,d.FUSION_POLICY_ID,d.SOURCE_POLICY_ID," +
                "d.POLICY_ATTRIBUTE AS RAW_POLICY_ATTRIBUTE,d.FILE_PATH AS RAW_FILE_PATH,d.FILE_SUFFIX AS RAW_FILE_SUFFIX," +
                "(SELECT MAX(TRIM(m.POLICY_ATTRIBUTE)) FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID=d.SOURCE_POLICY_ID) AS METADATA_POLICY_ATTRIBUTE," +
                "COALESCE((SELECT MAX(TRIM(m.POLICY_ATTRIBUTE)) FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID=d.SOURCE_POLICY_ID), d.POLICY_ATTRIBUTE) AS POLICY_ATTRIBUTE," +
                "CASE WHEN EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID=d.SOURCE_POLICY_ID AND m.REVIEW_STATUS='已通过') " +
                "THEN '已通过' ELSE (SELECT MAX(TRIM(m.REVIEW_STATUS)) FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID=d.SOURCE_POLICY_ID) END AS REVIEW_STATUS," +
                "COALESCE((SELECT NULLIF(TRIM(p.POLICY_TITLE), '') FROM SYSDBA.POLICY p WHERE CAST(p.ID AS VARCHAR(50))=d.SOURCE_POLICY_ID AND ROWNUM=1), d.POLICY_TITLE) AS POLICY_TITLE," +
                "COALESCE((SELECT p.FILE_NAME FROM SYSDBA.POLICY p WHERE CAST(p.ID AS VARCHAR(50))=d.SOURCE_POLICY_ID AND ROWNUM=1), d.FILE_NAME) AS FILE_NAME," +
                "COALESCE((SELECT o.FILE_PATH FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=d.SOURCE_POLICY_ID AND ROWNUM=1), d.FILE_PATH) AS FILE_PATH," +
                "d.FILE_SUFFIX,d.DISPLAY_ORDER," +
                "COALESCE((SELECT o.PUBLISH_DATE FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=d.SOURCE_POLICY_ID AND ROWNUM=1), " +
                "(SELECT b.PUBLISH_DATE FROM SYSDBA.X1_POLICY_BASE_INFO b WHERE b.POLICY_DOCUMENT_ID=d.ID AND ROWNUM=1)) AS PUBLISH_DATE," +
                "COALESCE((SELECT o.POLICY_CODE FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=d.SOURCE_POLICY_ID AND ROWNUM=1), " +
                "(SELECT b.POLICY_DOCUMENT_NO FROM SYSDBA.X1_POLICY_BASE_INFO b WHERE b.POLICY_DOCUMENT_ID=d.ID AND ROWNUM=1)) AS POLICY_DOCUMENT_NO," +
                "COALESCE((SELECT MAX(TRIM(m.ISSUING_DEPARTMENT)) FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID=d.SOURCE_POLICY_ID), " +
                "(SELECT o.ISSUE_DEPARTMENT FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=d.SOURCE_POLICY_ID AND ROWNUM=1), " +
                "(SELECT b.ISSUING_DEPARTMENT FROM SYSDBA.X1_POLICY_BASE_INFO b WHERE b.POLICY_DOCUMENT_ID=d.ID AND ROWNUM=1)) AS ISSUING_DEPARTMENT," +
                "(SELECT b.POLICY_LEVEL FROM SYSDBA.X1_POLICY_BASE_INFO b WHERE b.POLICY_DOCUMENT_ID=d.ID AND ROWNUM=1) POLICY_LEVEL," +
                "(SELECT b.ORIGINAL_URL FROM SYSDBA.X1_POLICY_BASE_INFO b WHERE b.POLICY_DOCUMENT_ID=d.ID AND ROWNUM=1) ORIGINAL_URL " +
                "FROM SYSDBA.X1_POLICY_DOCUMENT d WHERE d.DEL_FLAG='0' AND d.SOURCE_POLICY_ID IS NOT NULL AND TRIM(d.SOURCE_POLICY_ID) <> '' AND d.FUSION_POLICY_ID IN (" + placeholders + ") ORDER BY d.FUSION_POLICY_ID,d.DISPLAY_ORDER,d.ID";
            try (PreparedStatement stmt = conn.prepareStatement(docSql)) {
                int i = 1;
                for (String id : byId.keySet()) stmt.setString(i++, id);
                Map<String, Set<String>> dedupKeysByFusionId = new HashMap<>();
                try (ResultSet rs = stmt.executeQuery()) {
                    while (rs.next()) {
                        String fusionPolicyId = rs.getString("FUSION_POLICY_ID");
                        Map<String, Object> parent = byId.get(fusionPolicyId);
                        if (parent == null) continue;
                        String rawPolicyAttribute = nullToEmpty(rs.getString("RAW_POLICY_ATTRIBUTE"));
                        String rawFilePath = nullToEmpty(rs.getString("RAW_FILE_PATH"));
                        String rawFileSuffix = nullToEmpty(rs.getString("RAW_FILE_SUFFIX"));
                        if (isAttachmentLikeFusionDocument(rawPolicyAttribute, rawFilePath, rawFileSuffix)) {
                            continue;
                        }
                        Map<String, Object> doc = new LinkedHashMap<>();
                        String sourcePolicyId = nullToEmpty(rs.getString("SOURCE_POLICY_ID"));
                        String displayFilePath = nullToEmpty(rs.getString("FILE_PATH"));
                        String docId = rs.getString("ID");
                        String dedupKey = buildFusionDocumentDedupKey(sourcePolicyId, rawFilePath, displayFilePath, docId);
                        Set<String> dedupKeys = dedupKeysByFusionId.computeIfAbsent(fusionPolicyId, key -> new LinkedHashSet<>());
                        if (!dedupKeys.add(dedupKey)) {
                            continue;
                        }
                        String metadataPolicyAttribute = nullToEmpty(rs.getString("METADATA_POLICY_ATTRIBUTE"));
                        doc.put("id", docId);
                        doc.put("sourcePolicyId", sourcePolicyId);
                        doc.put("policyAttribute", normalizeFusionDocumentType(rs.getString("POLICY_ATTRIBUTE")));
                        doc.put("policyAttributePersisted", !metadataPolicyAttribute.isEmpty());
                        doc.put("reviewStatus", nullToEmpty(rs.getString("REVIEW_STATUS")));
                        doc.put("policyTitle", nullToEmpty(rs.getString("POLICY_TITLE")));
                        doc.put("fileName", nullToEmpty(rs.getString("FILE_NAME")));
                        doc.put("filePath", displayFilePath);
                        doc.put("fileSuffix", nullToEmpty(rs.getString("FILE_SUFFIX")));
                        doc.put("displayOrder", nullToEmpty(rs.getString("DISPLAY_ORDER")));
                        doc.put("publishDate", nullToEmpty(rs.getString("PUBLISH_DATE")));
                        doc.put("documentNo", nullToEmpty(rs.getString("POLICY_DOCUMENT_NO")));
                        doc.put("issuingDepartment", nullToEmpty(rs.getString("ISSUING_DEPARTMENT")));
                        doc.put("policyLevel", nullToEmpty(rs.getString("POLICY_LEVEL")));
                        doc.put("originalUrl", nullToEmpty(rs.getString("ORIGINAL_URL")));
                        @SuppressWarnings("unchecked")
                        List<Map<String, Object>> docs = (List<Map<String, Object>>) parent.get("documents");
                        docs.add(doc);
                    }
                }
            }
        }

        private Map<String, String> resolveListSourcePolicyIds(List<Map<String, Object>> rows) throws SQLException {
            Map<String, String> resolved = new LinkedHashMap<>();
            Map<String, List<String>> pendingLookupCandidates = new LinkedHashMap<>();
            Set<String> lookupKeys = new LinkedHashSet<>();

            for (Map<String, Object> row : rows) {
                String rowId = nullToEmpty((String) row.get("id"));
                if (rowId.isEmpty()) continue;
                String sourcePolicyId = nullToEmpty((String) row.get("sourcePolicyId"));
                if (!sourcePolicyId.isEmpty()) {
                    resolved.put(rowId, sourcePolicyId);
                    continue;
                }

                @SuppressWarnings("unchecked")
                List<Map<String, Object>> docs = (List<Map<String, Object>>) row.get("documents");
                String mainFileTitle = nullToEmpty((String) row.get("mainFileTitle"));
                String mainFilePath = nullToEmpty((String) row.get("mainFilePath"));
                String mainFileName = "";
                if (!mainFilePath.isEmpty()) {
                    try { mainFileName = java.nio.file.Paths.get(mainFilePath).getFileName().toString(); } catch (Exception ignored) {}
                }

                if (docs != null && !docs.isEmpty()) {
                    for (Map<String, Object> doc : docs) {
                        String docSourcePolicyId = nullToEmpty((String) doc.get("sourcePolicyId"));
                        if (docSourcePolicyId.isEmpty()) continue;
                        String title = nullToEmpty((String) doc.get("policyTitle"));
                        String fileName = nullToEmpty((String) doc.get("fileName"));
                        if ((!mainFileTitle.isEmpty() && mainFileTitle.equals(title))
                            || (!mainFileName.isEmpty() && mainFileName.equals(fileName))) {
                            sourcePolicyId = docSourcePolicyId;
                            break;
                        }
                    }
                }
                if (!sourcePolicyId.isEmpty()) {
                    resolved.put(rowId, sourcePolicyId);
                    continue;
                }

                List<String> candidates = buildListPolicyLookupCandidates(mainFileTitle, mainFileName);
                pendingLookupCandidates.put(rowId, candidates);
                lookupKeys.addAll(candidates);
            }

            Map<String, String> policyLookup = findListPolicyIdsByLookupKeys(lookupKeys);
            for (Map<String, Object> row : rows) {
                String rowId = nullToEmpty((String) row.get("id"));
                if (rowId.isEmpty() || resolved.containsKey(rowId)) continue;

                String sourcePolicyId = "";
                List<String> candidates = pendingLookupCandidates.getOrDefault(rowId, Collections.emptyList());
                for (String candidate : candidates) {
                    sourcePolicyId = nullToEmpty(policyLookup.get(candidate));
                    if (!sourcePolicyId.isEmpty()) break;
                }

                if (sourcePolicyId.isEmpty()) {
                    @SuppressWarnings("unchecked")
                    List<Map<String, Object>> docs = (List<Map<String, Object>>) row.get("documents");
                    if (docs != null) {
                        for (Map<String, Object> doc : docs) {
                            String docSourcePolicyId = nullToEmpty((String) doc.get("sourcePolicyId"));
                            if (!docSourcePolicyId.isEmpty()) {
                                sourcePolicyId = docSourcePolicyId;
                                break;
                            }
                        }
                    }
                }
                resolved.put(rowId, sourcePolicyId);
            }
            return resolved;
        }

        private Map<String, String> resolveConfiguredListSourcePolicyIds(List<Map<String, Object>> rows) {
            Map<String, String> resolved = new LinkedHashMap<>();
            for (Map<String, Object> row : rows) {
                String rowId = nullToEmpty((String) row.get("id"));
                if (rowId.isEmpty()) continue;
                String sourcePolicyId = nullToEmpty((String) row.get("sourcePolicyId"));
                if (!sourcePolicyId.isEmpty()) resolved.put(rowId, sourcePolicyId);
            }
            return resolved;
        }

        private List<String> buildListPolicyLookupCandidates(String mainFileTitle, String mainFileName) {
            Set<String> candidates = new LinkedHashSet<>();
            addListPolicyLookupCandidate(candidates, mainFileTitle);
            addListPolicyLookupCandidate(candidates, removeFileSuffixForList(mainFileTitle));
            addListPolicyLookupCandidate(candidates, mainFileName);
            addListPolicyLookupCandidate(candidates, removeFileSuffixForList(mainFileName));
            return new ArrayList<>(candidates);
        }

        private void addListPolicyLookupCandidate(Set<String> candidates, String value) {
            String normalized = nullToEmpty(value).trim();
            if (!normalized.isEmpty()) candidates.add(normalized);
        }

        private Map<String, String> findListPolicyIdsByLookupKeys(Set<String> lookupKeys) {
            if (lookupKeys.isEmpty()) return Collections.emptyMap();
            String placeholders = repeatPlaceholders(lookupKeys.size());
            String sql = "SELECT ID,TRIM(POLICY_TITLE) POLICY_TITLE,TRIM(FILE_NAME) FILE_NAME FROM SYSDBA.POLICY " +
                "WHERE TRIM(POLICY_TITLE) IN (" + placeholders + ") OR TRIM(FILE_NAME) IN (" + placeholders + ") ORDER BY ID DESC";
            Map<String, String> matchedPolicyIds = new HashMap<>();
            try (PreparedStatement stmt = conn.prepareStatement(sql)) {
                int i = 1;
                for (String key : lookupKeys) stmt.setString(i++, key);
                for (String key : lookupKeys) stmt.setString(i++, key);
                try (ResultSet rs = stmt.executeQuery()) {
                    while (rs.next()) {
                        String policyId = nullToEmpty(rs.getString("ID"));
                        String policyTitle = nullToEmpty(rs.getString("POLICY_TITLE"));
                        String fileName = nullToEmpty(rs.getString("FILE_NAME"));
                        if (!policyTitle.isEmpty()) matchedPolicyIds.putIfAbsent(policyTitle, policyId);
                        if (!fileName.isEmpty()) matchedPolicyIds.putIfAbsent(fileName, policyId);
                    }
                }
            } catch (SQLException e) {
                System.err.println("[FusionClassification List] POLICY 批量主文件匹配查询失败: " + e.getMessage());
                return Collections.emptyMap();
            }
            return matchedPolicyIds;
        }

        private void fillListSummaryFields(Map<String, Map<String, Object>> byId, Map<String, String> sourcePolicyIds) {
            Set<String> policyIds = new LinkedHashSet<>();
            for (String sourcePolicyId : sourcePolicyIds.values()) {
                String normalized = nullToEmpty(sourcePolicyId);
                if (!normalized.isEmpty()) policyIds.add(normalized);
            }
            if (policyIds.isEmpty()) return;

            Map<String, Map<String, String>> policyMetadata = loadPolicyMetadataBatch(policyIds);
            Map<String, Map<String, String>> applicationTimes = loadApplicationTimeBatch(policyIds);
            Map<String, String> originalDepartments = loadSingleValueBatch(
                "SELECT POLICY_ID,ISSUE_DEPARTMENT FROM SYSDBA.POLICY_ORIGINAL_V2 WHERE POLICY_ID IN (%s)",
                "ISSUE_DEPARTMENT",
                policyIds
            );
            Map<String, String> maxAmounts = loadSingleValueBatch(
                "SELECT POLICY_ID,MAX_SUPPORT_AMOUNT FROM SYSDBA.SUPPORT_METHOD WHERE POLICY_ID IN (%s)",
                "MAX_SUPPORT_AMOUNT",
                policyIds
            );
            Map<String, String> supportTypes = loadMultiValueBatch(
                "SELECT POLICY_ID,SUPPORT_TYPE FROM SYSDBA.SUPPORT_METHOD WHERE POLICY_ID IN (%s)",
                "SUPPORT_TYPE",
                policyIds
            );

            for (Map.Entry<String, String> entry : sourcePolicyIds.entrySet()) {
                String rowId = entry.getKey();
                String sourcePolicyId = nullToEmpty(entry.getValue());
                if (sourcePolicyId.isEmpty()) continue;
                Map<String, Object> row = byId.get(rowId);
                if (row == null) continue;

                Map<String, String> metadata = policyMetadata.get(sourcePolicyId);
                boolean hasStructuredData = false;
                if (metadata != null) {
                    hasStructuredData = true;
                    putRowValueIfPresent(row, "sceneUsage", metadata.get("sceneUsage"));
                    putRowValueIfPresent(row, "applicableScope", metadata.get("applicableScope"));
                    putRowValueIfPresent(row, "industryField", metadata.get("industryField"));
                    putRowValueIfPresent(row, "policyTitle", metadata.get("policyTitle"));
                    putRowValueIfPresent(row, "issuingDepartment", metadata.get("issuingDepartment"));
                }
                putRowValueIfPresent(row, "issuingDepartment", originalDepartments.get(sourcePolicyId));
                Map<String, String> applicationTime = applicationTimes.get(sourcePolicyId);
                if (applicationTime != null) {
                    hasStructuredData = true;
                    putRowValueIfPresent(row, "applicationStartTime", applicationTime.get("applicationStartTime"));
                    putRowValueIfPresent(row, "applicationEndTime", applicationTime.get("applicationEndTime"));
                }
                if (maxAmounts.containsKey(sourcePolicyId)) {
                    hasStructuredData = true;
                }
                putRowValueIfPresent(row, "maxAmount", maxAmounts.get(sourcePolicyId));
                if (supportTypes.containsKey(sourcePolicyId)) {
                    hasStructuredData = true;
                }
                putRowValueIfPresent(row, "supportType", supportTypes.get(sourcePolicyId));
                row.put("hasFusionStructuredData", hasStructuredData);
            }
        }

        private Map<String, Map<String, String>> loadPolicyMetadataBatch(Set<String> policyIds) {
            if (policyIds.isEmpty()) return Collections.emptyMap();
            String placeholders = repeatPlaceholders(policyIds.size());
            String sql = "SELECT POLICY_ID,SCENE_USAGE,APPLICABLE_SCOPE,INDUSTRY_FIELD,POLICY_TITLE_MAIN,ISSUING_DEPARTMENT FROM SYSDBA.POLICY_METADATA WHERE POLICY_ID IN (" + placeholders + ")";
            Map<String, Map<String, String>> metadataByPolicyId = new HashMap<>();
            try (PreparedStatement stmt = conn.prepareStatement(sql)) {
                int i = 1;
                for (String policyId : policyIds) stmt.setString(i++, policyId);
                try (ResultSet rs = stmt.executeQuery()) {
                    while (rs.next()) {
                        String policyId = nullToEmpty(rs.getString("POLICY_ID"));
                        if (policyId.isEmpty() || metadataByPolicyId.containsKey(policyId)) continue;
                        Map<String, String> metadata = new HashMap<>();
                        metadata.put("sceneUsage", nullToEmpty(rs.getString("SCENE_USAGE")));
                        metadata.put("applicableScope", nullToEmpty(rs.getString("APPLICABLE_SCOPE")));
                        metadata.put("industryField", nullToEmpty(rs.getString("INDUSTRY_FIELD")));
                        metadata.put("policyTitle", nullToEmpty(rs.getString("POLICY_TITLE_MAIN")));
                        metadata.put("issuingDepartment", nullToEmpty(rs.getString("ISSUING_DEPARTMENT")));
                        metadataByPolicyId.put(policyId, metadata);
                    }
                }
            } catch (SQLException e) {
                System.err.println("[FusionList] POLICY_METADATA批量查询失败: " + e.getMessage());
                return Collections.emptyMap();
            }
            return metadataByPolicyId;
        }

        private Map<String, String> loadSingleValueBatch(String sqlTemplate, String valueColumn, Set<String> policyIds) {
            if (policyIds.isEmpty()) return Collections.emptyMap();
            String placeholders = repeatPlaceholders(policyIds.size());
            String sql = String.format(sqlTemplate, placeholders);
            Map<String, String> valuesByPolicyId = new HashMap<>();
            try (PreparedStatement stmt = conn.prepareStatement(sql)) {
                int i = 1;
                for (String policyId : policyIds) stmt.setString(i++, policyId);
                try (ResultSet rs = stmt.executeQuery()) {
                    while (rs.next()) {
                        String policyId = nullToEmpty(rs.getString("POLICY_ID"));
                        if (policyId.isEmpty()) continue;
                        String value = nullToEmpty(rs.getString(valueColumn));
                        String existing = nullToEmpty(valuesByPolicyId.get(policyId));
                        if (!valuesByPolicyId.containsKey(policyId) || (existing.isEmpty() && !value.isEmpty())) {
                            valuesByPolicyId.put(policyId, value);
                        }
                    }
                }
            } catch (SQLException e) {
                System.err.println("[FusionList] 批量查询字段 " + valueColumn + " 失败: " + e.getMessage());
                return Collections.emptyMap();
            }
            return valuesByPolicyId;
        }

        private Map<String, String> loadMultiValueBatch(String sqlTemplate, String valueColumn, Set<String> policyIds) {
            if (policyIds.isEmpty()) return Collections.emptyMap();
            String placeholders = repeatPlaceholders(policyIds.size());
            String sql = String.format(sqlTemplate, placeholders);
            Map<String, LinkedHashSet<String>> valuesByPolicyId = new HashMap<>();
            try (PreparedStatement stmt = conn.prepareStatement(sql)) {
                int i = 1;
                for (String policyId : policyIds) stmt.setString(i++, policyId);
                try (ResultSet rs = stmt.executeQuery()) {
                    while (rs.next()) {
                        String policyId = nullToEmpty(rs.getString("POLICY_ID"));
                        String value = nullToEmpty(rs.getString(valueColumn)).trim();
                        if (policyId.isEmpty() || value.isEmpty()) continue;
                        valuesByPolicyId.computeIfAbsent(policyId, k -> new LinkedHashSet<>()).add(value);
                    }
                }
            } catch (SQLException e) {
                System.err.println("[FusionList] 鎵归噺澶氬€兼煡璇㈠け璐? " + e.getMessage());
                return Collections.emptyMap();
            }
            Map<String, String> joinedValues = new HashMap<>();
            for (Map.Entry<String, LinkedHashSet<String>> entry : valuesByPolicyId.entrySet()) {
                joinedValues.put(entry.getKey(), String.join("；", entry.getValue()));
            }
            return joinedValues;
        }

        private Map<String, Map<String, String>> loadApplicationTimeBatch(Set<String> policyIds) {
            if (policyIds.isEmpty()) return Collections.emptyMap();
            String placeholders = repeatPlaceholders(policyIds.size());
            String sql = "SELECT POLICY_ID,APPLICATION_START_TIME,APPLICATION_END_TIME,APPLICATION_TIME " +
                "FROM SYSDBA.APPLICATION_CONDITION WHERE POLICY_ID IN (" + placeholders + ")";
            Map<String, Map<String, String>> valuesByPolicyId = new HashMap<>();
            try (PreparedStatement stmt = conn.prepareStatement(sql)) {
                int i = 1;
                for (String policyId : policyIds) stmt.setString(i++, policyId);
                try (ResultSet rs = stmt.executeQuery()) {
                    while (rs.next()) {
                        String policyId = nullToEmpty(rs.getString("POLICY_ID"));
                        if (policyId.isEmpty()) continue;
                        String applicationStartTime = nullToEmpty(rs.getString("APPLICATION_START_TIME")).trim();
                        String applicationEndTime = nullToEmpty(rs.getString("APPLICATION_END_TIME")).trim();
                        Map<String, String> existing = valuesByPolicyId.get(policyId);
                        if (existing == null || (
                            nullToEmpty(existing.get("applicationStartTime")).trim().isEmpty() &&
                            nullToEmpty(existing.get("applicationEndTime")).trim().isEmpty()
                        )) {
                            Map<String, String> item = new LinkedHashMap<>();
                            item.put("applicationStartTime", applicationStartTime);
                            item.put("applicationEndTime", applicationEndTime);
                            valuesByPolicyId.put(policyId, item);
                        }
                    }
                }
            } catch (SQLException e) {
                System.err.println("[FusionList] 批量查询申报时间失败: " + e.getMessage());
                return Collections.emptyMap();
            }
            return valuesByPolicyId;
        }

        private void putRowValueIfPresent(Map<String, Object> row, String key, String value) {
            String normalized = nullToEmpty(value);
            if (!normalized.isEmpty()) row.put(key, normalized);
        }

        private String removeFileSuffixForList(String value) {
            if (value == null) return "";
            String trimmed = value.trim();
            int dot = trimmed.lastIndexOf('.');
            return dot > 0 ? trimmed.substring(0, dot) : trimmed;
        }
    }

    private static void resetFusionReviewStatus(Connection connection, String fusionPolicyId) throws SQLException {
        if (connection == null || fusionPolicyId == null || fusionPolicyId.trim().isEmpty()) return;
        try (PreparedStatement stmt = connection.prepareStatement(
            "UPDATE SYSDBA.X1_FUSION_POLICY SET MARK_REVIEW_STATUS=?,MARK_REVIEWED_AT=NULL,MARK_REVIEWED_BY=NULL,REVIEWED_BY_ID=NULL,MARK_REVIEW_REMARK=NULL,UPDATED_AT=CURRENT_TIMESTAMP " +
            "WHERE ID=? AND DEL_FLAG='0'")) {
            stmt.setString(1, FUSION_REVIEW_STATUS_PENDING);
            stmt.setString(2, fusionPolicyId.trim());
            stmt.executeUpdate();
        }
        try (PreparedStatement stmt = connection.prepareStatement(
            "UPDATE SYSDBA.X1_POLICY_DOCUMENT SET REVIEWED_BY_ID=NULL " +
            "WHERE FUSION_POLICY_ID=? AND DEL_FLAG='0'")) {
            stmt.setString(1, fusionPolicyId.trim());
            stmt.executeUpdate();
        }
    }

    private static void resetFusionStructureReviewStatus(Connection connection, String fusionPolicyId) throws SQLException {
        if (connection == null || fusionPolicyId == null || fusionPolicyId.trim().isEmpty()) return;
        try (PreparedStatement stmt = connection.prepareStatement(
            "UPDATE SYSDBA.X1_FUSION_POLICY SET STRUCTURE_REVIEW_STATUS=?,STRUCTURE_REVIEWED_AT=NULL,STRUCTURE_REVIEWED_BY=NULL,REVIEWED_BY_ID=NULL,STRUCTURE_REVIEW_REMARK=NULL,UPDATED_AT=CURRENT_TIMESTAMP " +
            "WHERE ID=? AND DEL_FLAG='0'")) {
            stmt.setString(1, STRUCTURE_REVIEW_STATUS_PENDING);
            stmt.setString(2, fusionPolicyId.trim());
            stmt.executeUpdate();
        }
    }

    private static void initializeFusionHierarchyFields(Map<String, Object> row) {
        if (row == null) return;
        row.put("parentPolicyId", "");
        row.put("parentPolicyTitle", "");
        row.put("parentPolicyReviewStatus", "");
        row.put("parentPolicyReviewedAt", "");
        row.put("parentPolicy", new LinkedHashMap<String, Object>());
        row.put("directParentPolicyCount", 0);
        row.put("directParentPolicies", new ArrayList<Map<String, Object>>());
        row.put("ancestorPolicyCount", 0);
        row.put("ancestorPolicies", new ArrayList<Map<String, Object>>());
        row.put("childPolicyCount", 0);
        row.put("childPolicies", new ArrayList<Map<String, Object>>());
        row.put("descendantPolicyCount", 0);
        row.put("descendantPolicies", new ArrayList<Map<String, Object>>());
    }

    private static String resolveFusionPolicyDisplayTitle(String mainFileTitle, String directoryTitle) {
        String normalizedMainTitle = nullToEmpty(mainFileTitle).trim();
        if (!normalizedMainTitle.isEmpty()) return normalizedMainTitle;
        return nullToEmpty(directoryTitle).trim();
    }

    private static String joinFusionPolicyTitles(List<Map<String, Object>> policies) {
        if (policies == null || policies.isEmpty()) return "";
        List<String> titles = new ArrayList<>();
        for (Map<String, Object> policy : policies) {
            if (policy == null) continue;
            String title = nullToEmpty(String.valueOf(policy.getOrDefault("title", ""))).trim();
            if (!title.isEmpty() && !titles.contains(title)) {
                titles.add(title);
            }
        }
        return String.join("、", titles);
    }

    private static void fillFusionHierarchySummary(Connection connection, Map<String, Map<String, Object>> byId) throws SQLException {
        if (connection == null || byId == null || byId.isEmpty()) return;
        for (Map<String, Object> row : byId.values()) {
            initializeFusionHierarchyFields(row);
        }

        String placeholders = repeatPlaceholders(byId.size());
        try (PreparedStatement stmt = connection.prepareStatement(
                "SELECT r.CHILD_FUSION_POLICY_ID, p.ID AS PARENT_ID, p.MAIN_FILE_TITLE, p.DIRECTORY_TITLE, " +
                "p.MARK_REVIEW_STATUS, p.MARK_REVIEWED_AT " +
                "FROM SYSDBA.X1_FUSION_POLICY_RELATION r " +
                "JOIN SYSDBA.X1_FUSION_POLICY p ON p.ID = r.PARENT_FUSION_POLICY_ID AND p.DEL_FLAG='0' " +
                "WHERE r.DEL_FLAG='0' AND r.CHILD_FUSION_POLICY_ID IN (" + placeholders + ") " +
                "ORDER BY r.CHILD_FUSION_POLICY_ID, p.SORT_NO, p.ID")) {
            int paramIndex = 1;
            for (String fusionPolicyId : byId.keySet()) {
                stmt.setString(paramIndex++, fusionPolicyId);
            }
            try (ResultSet rs = stmt.executeQuery()) {
                while (rs.next()) {
                    String childFusionPolicyId = nullToEmpty(rs.getString("CHILD_FUSION_POLICY_ID")).trim();
                    Map<String, Object> row = byId.get(childFusionPolicyId);
                    if (row == null) continue;
                    String parentId = nullToEmpty(rs.getString("PARENT_ID")).trim();
                    String parentTitle = resolveFusionPolicyDisplayTitle(rs.getString("MAIN_FILE_TITLE"), rs.getString("DIRECTORY_TITLE"));
                    String parentReviewStatus = nullToEmpty(rs.getString("MARK_REVIEW_STATUS")).trim();
                    String parentReviewedAt = nullToEmpty(rs.getString("MARK_REVIEWED_AT")).trim();
                    Map<String, Object> parentPolicy = new LinkedHashMap<>();
                    parentPolicy.put("id", parentId);
                    parentPolicy.put("title", parentTitle);
                    parentPolicy.put("mainFileTitle", nullToEmpty(rs.getString("MAIN_FILE_TITLE")));
                    parentPolicy.put("directoryTitle", nullToEmpty(rs.getString("DIRECTORY_TITLE")));
                    parentPolicy.put("reviewStatus", parentReviewStatus);
                    parentPolicy.put("reviewedAt", parentReviewedAt);
                    @SuppressWarnings("unchecked")
                    List<Map<String, Object>> directParents = (List<Map<String, Object>>) row.get("directParentPolicies");
                    if (directParents == null) {
                        directParents = new ArrayList<>();
                        row.put("directParentPolicies", directParents);
                    }
                    directParents.add(parentPolicy);
                }
            }
        }

        for (Map<String, Object> row : byId.values()) {
            @SuppressWarnings("unchecked")
            List<Map<String, Object>> directParents = (List<Map<String, Object>>) row.get("directParentPolicies");
            if (directParents == null) {
                directParents = new ArrayList<>();
                row.put("directParentPolicies", directParents);
            }
            row.put("directParentPolicyCount", directParents.size());
            String parentTitles = joinFusionPolicyTitles(directParents);
            row.put("parentPolicyTitle", parentTitles);
            if (!directParents.isEmpty()) {
                Map<String, Object> firstParent = directParents.get(0);
                row.put("parentPolicy", firstParent);
                row.put("parentPolicyId", nullToEmpty(String.valueOf(firstParent.getOrDefault("id", ""))).trim());
                row.put("parentPolicyReviewStatus", nullToEmpty(String.valueOf(firstParent.getOrDefault("reviewStatus", ""))).trim());
                row.put("parentPolicyReviewedAt", nullToEmpty(String.valueOf(firstParent.getOrDefault("reviewedAt", ""))).trim());
            }
        }

        try (PreparedStatement stmt = connection.prepareStatement(
                "SELECT r.PARENT_FUSION_POLICY_ID, COUNT(*) AS CHILD_COUNT " +
                "FROM SYSDBA.X1_FUSION_POLICY_RELATION r " +
                "JOIN SYSDBA.X1_FUSION_POLICY c ON c.ID = r.CHILD_FUSION_POLICY_ID AND c.DEL_FLAG='0' " +
                "WHERE r.DEL_FLAG='0' AND r.PARENT_FUSION_POLICY_ID IN (" + placeholders + ") " +
                "GROUP BY r.PARENT_FUSION_POLICY_ID")) {
            int paramIndex = 1;
            for (String fusionPolicyId : byId.keySet()) {
                stmt.setString(paramIndex++, fusionPolicyId);
            }
            try (ResultSet rs = stmt.executeQuery()) {
                while (rs.next()) {
                    String parentFusionPolicyId = nullToEmpty(rs.getString("PARENT_FUSION_POLICY_ID")).trim();
                    Map<String, Object> row = byId.get(parentFusionPolicyId);
                    if (row != null) {
                        row.put("childPolicyCount", rs.getInt("CHILD_COUNT"));
                    }
                }
            }
        }
    }

    private static List<Map<String, Object>> queryFusionDirectParentPolicies(Connection connection, String childFusionPolicyId) throws SQLException {
        List<Map<String, Object>> parents = new ArrayList<>();
        if (connection == null || childFusionPolicyId == null || childFusionPolicyId.trim().isEmpty()) return parents;
        try (PreparedStatement stmt = connection.prepareStatement(
                "SELECT p.ID, p.MAIN_FILE_TITLE, p.DIRECTORY_TITLE, p.MARK_REVIEW_STATUS, p.MARK_REVIEWED_AT " +
                "FROM SYSDBA.X1_FUSION_POLICY_RELATION r " +
                "JOIN SYSDBA.X1_FUSION_POLICY p ON p.ID = r.PARENT_FUSION_POLICY_ID AND p.DEL_FLAG='0' " +
                "WHERE r.DEL_FLAG='0' AND r.CHILD_FUSION_POLICY_ID=? " +
                "ORDER BY p.SORT_NO, p.ID")) {
            stmt.setString(1, childFusionPolicyId.trim());
            try (ResultSet rs = stmt.executeQuery()) {
                while (rs.next()) {
                    Map<String, Object> parentPolicy = new LinkedHashMap<>();
                    parentPolicy.put("id", nullToEmpty(rs.getString("ID")).trim());
                    parentPolicy.put("title", resolveFusionPolicyDisplayTitle(rs.getString("MAIN_FILE_TITLE"), rs.getString("DIRECTORY_TITLE")));
                    parentPolicy.put("mainFileTitle", nullToEmpty(rs.getString("MAIN_FILE_TITLE")));
                    parentPolicy.put("directoryTitle", nullToEmpty(rs.getString("DIRECTORY_TITLE")));
                    parentPolicy.put("reviewStatus", nullToEmpty(rs.getString("MARK_REVIEW_STATUS")).trim());
                    parentPolicy.put("reviewedAt", nullToEmpty(rs.getString("MARK_REVIEWED_AT")).trim());
                    parentPolicy.put("depth", 1);
                    parentPolicy.put("relationType", "DIRECT_PARENT");
                    parents.add(parentPolicy);
                }
            }
        }
        return parents;
    }

    private static Map<String, Object> queryFusionParentPolicy(Connection connection, String childFusionPolicyId) throws SQLException {
        List<Map<String, Object>> parents = queryFusionDirectParentPolicies(connection, childFusionPolicyId);
        return parents.isEmpty() ? new LinkedHashMap<>() : new LinkedHashMap<>(parents.get(0));
    }

    private static List<Map<String, Object>> queryFusionChildPolicies(Connection connection, String parentFusionPolicyId) throws SQLException {
        List<Map<String, Object>> children = new ArrayList<>();
        if (connection == null || parentFusionPolicyId == null || parentFusionPolicyId.trim().isEmpty()) return children;
        String documentCountSql =
            "SELECT d.FUSION_POLICY_ID, COUNT(DISTINCT TRIM(d.SOURCE_POLICY_ID)) AS DOCUMENT_COUNT " +
            "FROM SYSDBA.X1_POLICY_DOCUMENT d " +
            "WHERE d.DEL_FLAG='0' " +
            "AND d.SOURCE_POLICY_ID IS NOT NULL " +
            "AND TRIM(d.SOURCE_POLICY_ID) <> '' " +
            "AND NOT (" +
            "TRIM(NVL(d.POLICY_ATTRIBUTE,'')) IN ('实施细则类','指导政策类','申报材料类','申报通知类','结果公告类','通知公告类','其它') " +
            "OR LOWER(NVL(d.FILE_PATH,'')) LIKE '%/attachments/%'" +
            ") GROUP BY d.FUSION_POLICY_ID";
        try (PreparedStatement stmt = connection.prepareStatement(
                "SELECT c.ID, c.MAIN_FILE_TITLE, c.DIRECTORY_TITLE, c.MARK_REVIEW_STATUS, c.MARK_REVIEWED_AT, " +
                "NVL(dm.DOCUMENT_COUNT,0) AS DOCUMENT_COUNT " +
                "FROM SYSDBA.X1_FUSION_POLICY_RELATION r " +
                "JOIN SYSDBA.X1_FUSION_POLICY c ON c.ID = r.CHILD_FUSION_POLICY_ID AND c.DEL_FLAG='0' " +
                "LEFT JOIN (" + documentCountSql + ") dm ON dm.FUSION_POLICY_ID = c.ID " +
                "WHERE r.DEL_FLAG='0' AND r.PARENT_FUSION_POLICY_ID=? " +
                "ORDER BY c.SORT_NO, c.ID")) {
            stmt.setString(1, parentFusionPolicyId.trim());
            try (ResultSet rs = stmt.executeQuery()) {
                while (rs.next()) {
                    Map<String, Object> child = new LinkedHashMap<>();
                    child.put("id", nullToEmpty(rs.getString("ID")).trim());
                    child.put("title", resolveFusionPolicyDisplayTitle(rs.getString("MAIN_FILE_TITLE"), rs.getString("DIRECTORY_TITLE")));
                    child.put("mainFileTitle", nullToEmpty(rs.getString("MAIN_FILE_TITLE")));
                    child.put("directoryTitle", nullToEmpty(rs.getString("DIRECTORY_TITLE")));
                    child.put("reviewStatus", nullToEmpty(rs.getString("MARK_REVIEW_STATUS")).trim());
                    child.put("reviewedAt", nullToEmpty(rs.getString("MARK_REVIEWED_AT")).trim());
                    child.put("documentCount", rs.getInt("DOCUMENT_COUNT"));
                    child.put("depth", 1);
                    child.put("relationType", "DIRECT_CHILD");
                    children.add(child);
                }
            }
        }
        return children;
    }

    private static Map<String, Map<String, Object>> queryFusionPolicySummaryMap(Connection connection, Collection<String> fusionPolicyIds) throws SQLException {
        Map<String, Map<String, Object>> result = new LinkedHashMap<>();
        if (connection == null || fusionPolicyIds == null || fusionPolicyIds.isEmpty()) return result;
        List<String> normalizedIds = new ArrayList<>();
        for (String fusionPolicyId : fusionPolicyIds) {
            String normalizedId = nullToEmpty(fusionPolicyId).trim();
            if (!normalizedId.isEmpty() && !normalizedIds.contains(normalizedId)) {
                normalizedIds.add(normalizedId);
            }
        }
        if (normalizedIds.isEmpty()) return result;

        String documentCountSql =
            "SELECT d.FUSION_POLICY_ID, COUNT(DISTINCT TRIM(d.SOURCE_POLICY_ID)) AS DOCUMENT_COUNT " +
            "FROM SYSDBA.X1_POLICY_DOCUMENT d " +
            "WHERE d.DEL_FLAG='0' " +
            "AND d.SOURCE_POLICY_ID IS NOT NULL " +
            "AND TRIM(d.SOURCE_POLICY_ID) <> '' " +
            "AND NOT (" +
            "TRIM(NVL(d.POLICY_ATTRIBUTE,'')) IN ('实施细则类','指导政策类','申报材料类','申报通知类','结果公告类','通知公告类','其它') " +
            "OR LOWER(NVL(d.FILE_PATH,'')) LIKE '%/attachments/%'" +
            ") GROUP BY d.FUSION_POLICY_ID";
        String placeholders = repeatPlaceholders(normalizedIds.size());
        try (PreparedStatement stmt = connection.prepareStatement(
                "SELECT p.ID, p.MAIN_FILE_TITLE, p.DIRECTORY_TITLE, p.MARK_REVIEW_STATUS, p.MARK_REVIEWED_AT, " +
                "NVL(dm.DOCUMENT_COUNT,0) AS DOCUMENT_COUNT " +
                "FROM SYSDBA.X1_FUSION_POLICY p " +
                "LEFT JOIN (" + documentCountSql + ") dm ON dm.FUSION_POLICY_ID = p.ID " +
                "WHERE p.DEL_FLAG='0' AND p.ID IN (" + placeholders + ")")) {
            int paramIndex = 1;
            for (String fusionPolicyId : normalizedIds) {
                stmt.setString(paramIndex++, fusionPolicyId);
            }
            try (ResultSet rs = stmt.executeQuery()) {
                while (rs.next()) {
                    String fusionPolicyId = nullToEmpty(rs.getString("ID")).trim();
                    if (fusionPolicyId.isEmpty()) continue;
                    Map<String, Object> row = new LinkedHashMap<>();
                    row.put("id", fusionPolicyId);
                    row.put("title", resolveFusionPolicyDisplayTitle(rs.getString("MAIN_FILE_TITLE"), rs.getString("DIRECTORY_TITLE")));
                    row.put("mainFileTitle", nullToEmpty(rs.getString("MAIN_FILE_TITLE")));
                    row.put("directoryTitle", nullToEmpty(rs.getString("DIRECTORY_TITLE")));
                    row.put("reviewStatus", nullToEmpty(rs.getString("MARK_REVIEW_STATUS")).trim());
                    row.put("reviewedAt", nullToEmpty(rs.getString("MARK_REVIEWED_AT")).trim());
                    row.put("documentCount", rs.getInt("DOCUMENT_COUNT"));
                    result.put(fusionPolicyId, row);
                }
            }
        }
        return result;
    }

    private static List<Map<String, Object>> queryFusionAncestorPolicies(Connection connection, String childFusionPolicyId) throws SQLException {
        List<Map<String, Object>> ancestors = new ArrayList<>();
        if (connection == null || childFusionPolicyId == null || childFusionPolicyId.trim().isEmpty()) return ancestors;
        Map<String, Set<String>> parentsByChild = new LinkedHashMap<>();
        try (PreparedStatement stmt = connection.prepareStatement(
                "SELECT r.CHILD_FUSION_POLICY_ID, r.PARENT_FUSION_POLICY_ID " +
                "FROM SYSDBA.X1_FUSION_POLICY_RELATION r " +
                "JOIN SYSDBA.X1_FUSION_POLICY p ON p.ID = r.PARENT_FUSION_POLICY_ID AND p.DEL_FLAG='0' " +
                "WHERE r.DEL_FLAG='0' " +
                "ORDER BY r.CHILD_FUSION_POLICY_ID, p.SORT_NO, p.ID")) {
            try (ResultSet rs = stmt.executeQuery()) {
                while (rs.next()) {
                    String currentChildId = nullToEmpty(rs.getString("CHILD_FUSION_POLICY_ID")).trim();
                    String parentId = nullToEmpty(rs.getString("PARENT_FUSION_POLICY_ID")).trim();
                    if (currentChildId.isEmpty() || parentId.isEmpty()) continue;
                    parentsByChild.computeIfAbsent(currentChildId, key -> new LinkedHashSet<>()).add(parentId);
                }
            }
        }

        List<String> ancestorIds = new ArrayList<>();
        Map<String, Integer> depthById = new LinkedHashMap<>();
        Set<String> visited = new LinkedHashSet<>();
        Deque<String[]> queue = new ArrayDeque<>();
        for (String parentId : parentsByChild.getOrDefault(childFusionPolicyId.trim(), Collections.emptySet())) {
            if (visited.add(parentId)) {
                queue.addLast(new String[]{parentId, "1"});
            }
        }
        while (!queue.isEmpty()) {
            String[] item = queue.removeFirst();
            String currentId = item[0];
            int depth = Integer.parseInt(item[1]);
            ancestorIds.add(currentId);
            depthById.put(currentId, depth);
            for (String parentId : parentsByChild.getOrDefault(currentId, Collections.emptySet())) {
                if (visited.add(parentId)) {
                    queue.addLast(new String[]{parentId, String.valueOf(depth + 1)});
                }
            }
        }
        if (ancestorIds.isEmpty()) return ancestors;

        Map<String, Map<String, Object>> summaryById = queryFusionPolicySummaryMap(connection, ancestorIds);
        for (String ancestorId : ancestorIds) {
            Map<String, Object> summary = summaryById.get(ancestorId);
            if (summary == null) continue;
            Map<String, Object> row = new LinkedHashMap<>(summary);
            int depth = depthById.getOrDefault(ancestorId, 1);
            row.put("depth", depth);
            row.put("relationType", depth == 1 ? "DIRECT_PARENT" : "ANCESTOR");
            ancestors.add(row);
        }
        return ancestors;
    }

    private static List<Map<String, Object>> queryFusionDescendantPolicies(Connection connection, String rootFusionPolicyId) throws SQLException {
        List<Map<String, Object>> descendants = new ArrayList<>();
        if (connection == null || rootFusionPolicyId == null || rootFusionPolicyId.trim().isEmpty()) return descendants;

        Map<String, Set<String>> childrenByParent = new LinkedHashMap<>();
        try (PreparedStatement stmt = connection.prepareStatement(
                "SELECT r.PARENT_FUSION_POLICY_ID, r.CHILD_FUSION_POLICY_ID " +
                "FROM SYSDBA.X1_FUSION_POLICY_RELATION r " +
                "JOIN SYSDBA.X1_FUSION_POLICY c ON c.ID = r.CHILD_FUSION_POLICY_ID AND c.DEL_FLAG='0' " +
                "WHERE r.DEL_FLAG='0'")) {
            try (ResultSet rs = stmt.executeQuery()) {
                while (rs.next()) {
                    String parentId = nullToEmpty(rs.getString("PARENT_FUSION_POLICY_ID")).trim();
                    String childId = nullToEmpty(rs.getString("CHILD_FUSION_POLICY_ID")).trim();
                    if (parentId.isEmpty() || childId.isEmpty()) continue;
                    childrenByParent.computeIfAbsent(parentId, key -> new LinkedHashSet<>()).add(childId);
                }
            }
        }

        List<String> descendantIds = new ArrayList<>();
        Map<String, Integer> depthById = new LinkedHashMap<>();
        Set<String> visited = new LinkedHashSet<>();
        Deque<String[]> queue = new ArrayDeque<>();
        for (String childId : childrenByParent.getOrDefault(rootFusionPolicyId.trim(), Collections.emptySet())) {
            if (visited.add(childId)) {
                queue.addLast(new String[]{childId, "1"});
            }
        }
        while (!queue.isEmpty()) {
            String[] item = queue.removeFirst();
            String currentId = item[0];
            int depth = Integer.parseInt(item[1]);
            descendantIds.add(currentId);
            depthById.put(currentId, depth);
            for (String childId : childrenByParent.getOrDefault(currentId, Collections.emptySet())) {
                if (visited.add(childId)) {
                    queue.addLast(new String[]{childId, String.valueOf(depth + 1)});
                }
            }
        }
        if (descendantIds.isEmpty()) return descendants;

        Map<String, Map<String, Object>> summaryById = queryFusionPolicySummaryMap(connection, descendantIds);
        for (String descendantId : descendantIds) {
            Map<String, Object> summary = summaryById.get(descendantId);
            if (summary == null) continue;
            Map<String, Object> row = new LinkedHashMap<>(summary);
            int depth = depthById.getOrDefault(descendantId, 1);
            row.put("depth", depth);
            row.put("relationType", depth == 1 ? "DIRECT_CHILD" : "DESCENDANT");
            descendants.add(row);
        }
        return descendants;
    }

    private static List<String> queryActiveParentFusionPolicyIds(Connection connection, String childFusionPolicyId) throws SQLException {
        List<String> parentIds = new ArrayList<>();
        if (connection == null || childFusionPolicyId == null || childFusionPolicyId.trim().isEmpty()) return parentIds;
        try (PreparedStatement stmt = connection.prepareStatement(
                "SELECT PARENT_FUSION_POLICY_ID FROM SYSDBA.X1_FUSION_POLICY_RELATION " +
                "WHERE CHILD_FUSION_POLICY_ID=? AND DEL_FLAG='0' ORDER BY ID")) {
            stmt.setString(1, childFusionPolicyId.trim());
            try (ResultSet rs = stmt.executeQuery()) {
                while (rs.next()) {
                    String parentId = nullToEmpty(rs.getString("PARENT_FUSION_POLICY_ID")).trim();
                    if (!parentId.isEmpty() && !parentIds.contains(parentId)) {
                        parentIds.add(parentId);
                    }
                }
            }
        }
        return parentIds;
    }

    private static boolean hasActiveFusionParentRelation(Connection connection, String childFusionPolicyId, String parentFusionPolicyId) throws SQLException {
        if (connection == null || childFusionPolicyId == null || parentFusionPolicyId == null) return false;
        String normalizedChildId = childFusionPolicyId.trim();
        String normalizedParentId = parentFusionPolicyId.trim();
        if (normalizedChildId.isEmpty() || normalizedParentId.isEmpty()) return false;
        try (PreparedStatement stmt = connection.prepareStatement(
                "SELECT COUNT(*) FROM SYSDBA.X1_FUSION_POLICY_RELATION " +
                "WHERE CHILD_FUSION_POLICY_ID=? AND PARENT_FUSION_POLICY_ID=? AND DEL_FLAG='0'")) {
            stmt.setString(1, normalizedChildId);
            stmt.setString(2, normalizedParentId);
            try (ResultSet rs = stmt.executeQuery()) {
                return rs.next() && rs.getInt(1) > 0;
            }
        }
    }

    private static boolean fusionPolicyExists(Connection connection, String fusionPolicyId) throws SQLException {
        if (connection == null || fusionPolicyId == null || fusionPolicyId.trim().isEmpty()) return false;
        try (PreparedStatement stmt = connection.prepareStatement(
                "SELECT COUNT(*) FROM SYSDBA.X1_FUSION_POLICY WHERE ID=? AND DEL_FLAG='0'")) {
            stmt.setString(1, fusionPolicyId.trim());
            try (ResultSet rs = stmt.executeQuery()) {
                return rs.next() && rs.getInt(1) > 0;
            }
        }
    }

    private static boolean ensureActiveFusionPolicy(Connection connection, HttpExchange exchange, String fusionPolicyId) throws IOException, SQLException {
        if (fusionPolicyExists(connection, fusionPolicyId)) return true;
        sendJson(exchange,404,buildMap("success",false,"message","主政策不存在或已弃用"));
        return false;
    }

    private static long nextFusionPolicyRelationId(Connection connection) throws SQLException {
        try (Statement stmt = connection.createStatement();
             ResultSet rs = stmt.executeQuery("SELECT NVL(MAX(ID),0)+1 FROM SYSDBA.X1_FUSION_POLICY_RELATION")) {
            rs.next();
            return rs.getLong(1);
        }
    }

    private static String normalizeOperatorName(String operator) {
        String normalized = nullToEmpty(operator).trim();
        return normalized.isEmpty() ? DEFAULT_AUDIT_OPERATOR : normalized;
    }

    private static int markFusionParentRelationDeleted(Connection connection, String childFusionPolicyId, String parentFusionPolicyId, String operator) throws SQLException {
        if (connection == null || childFusionPolicyId == null || childFusionPolicyId.trim().isEmpty()) return 0;
        String normalizedParentId = nullToEmpty(parentFusionPolicyId).trim();
        if (normalizedParentId.isEmpty()) {
            try (PreparedStatement stmt = connection.prepareStatement(
                    "UPDATE SYSDBA.X1_FUSION_POLICY_RELATION SET DEL_FLAG='1', UPDATED_BY=?, UPDATED_AT=CURRENT_TIMESTAMP " +
                    "WHERE CHILD_FUSION_POLICY_ID=? AND DEL_FLAG='0'")) {
                stmt.setString(1, normalizeOperatorName(operator));
                stmt.setString(2, childFusionPolicyId.trim());
                return stmt.executeUpdate();
            }
        }
        try (PreparedStatement stmt = connection.prepareStatement(
                "UPDATE SYSDBA.X1_FUSION_POLICY_RELATION SET DEL_FLAG='1', UPDATED_BY=?, UPDATED_AT=CURRENT_TIMESTAMP " +
                "WHERE CHILD_FUSION_POLICY_ID=? AND PARENT_FUSION_POLICY_ID=? AND DEL_FLAG='0'")) {
            stmt.setString(1, normalizeOperatorName(operator));
            stmt.setString(2, childFusionPolicyId.trim());
            stmt.setString(3, normalizedParentId);
            return stmt.executeUpdate();
        }
    }

    private static void markFusionPolicyHierarchyDeleted(Connection connection, String fusionPolicyId, String operator) throws SQLException {
        if (connection == null || fusionPolicyId == null || fusionPolicyId.trim().isEmpty()) return;
        try (PreparedStatement stmt = connection.prepareStatement(
                "UPDATE SYSDBA.X1_FUSION_POLICY_RELATION SET DEL_FLAG='1', UPDATED_BY=?, UPDATED_AT=CURRENT_TIMESTAMP " +
                "WHERE DEL_FLAG='0' AND (PARENT_FUSION_POLICY_ID=? OR CHILD_FUSION_POLICY_ID=?)")) {
            stmt.setString(1, normalizeOperatorName(operator));
            String normalizedPolicyId = fusionPolicyId.trim();
            stmt.setString(2, normalizedPolicyId);
            stmt.setString(3, normalizedPolicyId);
            stmt.executeUpdate();
        }
    }

    private static void restoreFusionPolicyHierarchy(Connection connection, String fusionPolicyId, String operator) throws SQLException {
        if (connection == null || fusionPolicyId == null || fusionPolicyId.trim().isEmpty()) return;
        try (PreparedStatement stmt = connection.prepareStatement(
                "UPDATE SYSDBA.X1_FUSION_POLICY_RELATION SET DEL_FLAG='0', UPDATED_BY=?, UPDATED_AT=CURRENT_TIMESTAMP " +
                "WHERE DEL_FLAG='1' AND (PARENT_FUSION_POLICY_ID=? OR CHILD_FUSION_POLICY_ID=?)")) {
            stmt.setString(1, normalizeOperatorName(operator));
            String normalizedPolicyId = fusionPolicyId.trim();
            stmt.setString(2, normalizedPolicyId);
            stmt.setString(3, normalizedPolicyId);
            stmt.executeUpdate();
        }
    }

    private static boolean wouldCreateFusionPolicyCycle(Connection connection, String childFusionPolicyId, String newParentFusionPolicyId) throws SQLException {
        String normalizedChildId = nullToEmpty(childFusionPolicyId).trim();
        String normalizedParentId = nullToEmpty(newParentFusionPolicyId).trim();
        if (normalizedChildId.isEmpty() || normalizedParentId.isEmpty()) return false;
        if (normalizedChildId.equals(normalizedParentId)) return true;

        Map<String, Set<String>> childrenByParent = new LinkedHashMap<>();
        try (PreparedStatement stmt = connection.prepareStatement(
                "SELECT PARENT_FUSION_POLICY_ID, CHILD_FUSION_POLICY_ID " +
                "FROM SYSDBA.X1_FUSION_POLICY_RELATION WHERE DEL_FLAG='0'")) {
            try (ResultSet rs = stmt.executeQuery()) {
                while (rs.next()) {
                    String parentId = nullToEmpty(rs.getString("PARENT_FUSION_POLICY_ID")).trim();
                    String childId = nullToEmpty(rs.getString("CHILD_FUSION_POLICY_ID")).trim();
                    if (parentId.isEmpty() || childId.isEmpty()) continue;
                    childrenByParent.computeIfAbsent(parentId, key -> new LinkedHashSet<>()).add(childId);
                }
            }
        }

        Deque<String> queue = new ArrayDeque<>();
        Set<String> visited = new LinkedHashSet<>();
        queue.add(normalizedChildId);
        visited.add(normalizedChildId);
        while (!queue.isEmpty()) {
            String currentId = queue.removeFirst();
            Set<String> directChildren = childrenByParent.getOrDefault(currentId, Collections.emptySet());
            for (String directChildId : directChildren) {
                if (normalizedParentId.equals(directChildId)) {
                    return true;
                }
                if (visited.add(directChildId)) {
                    queue.addLast(directChildId);
                }
            }
        }
        return false;
    }

    private static String queryFusionPolicyIdByDocumentId(Connection connection, String documentId) throws SQLException {
        if (connection == null || documentId == null || documentId.trim().isEmpty()) return "";
        try (PreparedStatement stmt = connection.prepareStatement(
            "SELECT FUSION_POLICY_ID FROM SYSDBA.X1_POLICY_DOCUMENT WHERE ID=? AND DEL_FLAG='0'")) {
            stmt.setString(1, documentId.trim());
            try (ResultSet rs = stmt.executeQuery()) {
                return rs.next() ? nullToEmpty(rs.getString("FUSION_POLICY_ID")) : "";
            }
        }
    }

    private static String querySourcePolicyIdByDocumentId(Connection connection, String documentId) throws SQLException {
        if (connection == null || documentId == null || documentId.trim().isEmpty()) return "";
        try (PreparedStatement stmt = connection.prepareStatement(
            "SELECT SOURCE_POLICY_ID FROM SYSDBA.X1_POLICY_DOCUMENT WHERE ID=? AND DEL_FLAG='0'")) {
            stmt.setString(1, documentId.trim());
            try (ResultSet rs = stmt.executeQuery()) {
                return rs.next() ? nullToEmpty(rs.getString("SOURCE_POLICY_ID")) : "";
            }
        }
    }

    private static int upsertPolicyMetadataType(Connection connection, String policyId, String type) throws SQLException {
        return upsertPolicyMetadataType(connection, policyId, type, DEFAULT_AUDIT_OPERATOR);
    }

    private static boolean policyExists(Connection connection, String policyId) throws SQLException {
        if (connection == null || policyId == null || policyId.trim().isEmpty()) return false;
        try (PreparedStatement stmt = connection.prepareStatement(
            "SELECT 1 FROM SYSDBA.POLICY WHERE ID = ? AND ROWNUM = 1")) {
            stmt.setString(1, policyId.trim());
            try (ResultSet rs = stmt.executeQuery()) {
                return rs.next();
            }
        }
    }

    private static int upsertPolicyMetadataType(Connection connection, String policyId, String type, String operatorName) throws SQLException {
        if (connection == null || policyId == null || policyId.trim().isEmpty()) return 0;
        String normalizedPolicyId = policyId.trim();
        String normalizedType = nullToEmpty(type).trim();
        String normalizedOperatorName = normalizeOperatorName(operatorName);
        try (PreparedStatement stmt = connection.prepareStatement(
            "UPDATE SYSDBA.POLICY_METADATA SET POLICY_ATTRIBUTE = ?, REVIEW_STATUS = '未通过', UPDATED_BY = ?, UPDATED_AT = CURRENT_TIMESTAMP WHERE POLICY_ID = ?")) {
            stmt.setString(1, normalizedType);
            stmt.setString(2, normalizedOperatorName);
            stmt.setString(3, normalizedPolicyId);
            int updated = stmt.executeUpdate();
            if (updated > 0) return updated;
        }

        if (!policyExists(connection, normalizedPolicyId)) {
            throw new SQLException("POLICY 主表不存在，无法写入 POLICY_METADATA；POLICY_ID=" + normalizedPolicyId);
        }

        try (PreparedStatement stmt = connection.prepareStatement(
            "INSERT INTO SYSDBA.POLICY_METADATA (POLICY_ID, POLICY_ATTRIBUTE, REVIEW_STATUS, CREATED_AT, UPDATED_AT, CREATED_BY, UPDATED_BY) " +
            "VALUES (?, ?, '未通过', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, ?)")) {
            stmt.setString(1, normalizedPolicyId);
            stmt.setString(2, normalizedType);
            stmt.setString(3, normalizedOperatorName);
            stmt.setString(4, normalizedOperatorName);
            return stmt.executeUpdate();
        }
    }

    static class FusionPolicyCreateHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if (!"POST".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持 POST 方法")); return;
            }
            Map<String, String> data = parseJsonObject(new String(readAllBytes(exchange.getRequestBody()), StandardCharsets.UTF_8));
            String title = data.getOrDefault("title", "").trim();
            String directoryPath = data.getOrDefault("directoryPath", "").trim();
            String operatorName = resolveAuditOperatorName(data);
            if (title.isEmpty()) {
                sendJson(exchange, 400, buildMap("success", false, "message", "政策名称不能为空")); return;
            }
            if (directoryPath.isEmpty()) directoryPath = "重点政策融合数据/" + title;
            synchronized (FusionPolicyCreateHandler.class) {
                try (Connection c = createDedicatedConnection()) {
                    c.setAutoCommit(false);
                    long id; int sortNo;
                    try (Statement s = c.createStatement(); ResultSet rs = s.executeQuery("SELECT NVL(MAX(ID),0)+1,NVL(MAX(SORT_NO),0)+1 FROM SYSDBA.X1_FUSION_POLICY")) {
                        rs.next(); id = rs.getLong(1); sortNo = rs.getInt(2);
                    }
                    try (Statement s = c.createStatement()) {
                        s.execute("SET IDENTITY_INSERT SYSDBA.X1_FUSION_POLICY ON");
                    }
                    String sql = "INSERT INTO SYSDBA.X1_FUSION_POLICY (ID,SOURCE_POLICY_ID,DIRECTORY_TITLE,DIRECTORY_PATH,MAIN_FILE_TITLE,MAIN_FILE_PATH,SORT_NO,STATUS,MARK_REVIEW_STATUS,MARK_REVIEWED_AT,MARK_REVIEWED_BY,MARK_REVIEW_REMARK,DEL_FLAG,CREATED_AT,UPDATED_AT) " +
                        "VALUES (?,NULL,?,?,?,?,?,'ACTIVE',?,NULL,NULL,NULL,'0',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)";
                    sql = "INSERT INTO SYSDBA.X1_FUSION_POLICY (ID,SOURCE_POLICY_ID,DIRECTORY_TITLE,DIRECTORY_PATH,MAIN_FILE_TITLE,MAIN_FILE_PATH,SORT_NO,STATUS,MARK_REVIEW_STATUS,MARK_REVIEWED_AT,MARK_REVIEWED_BY,MARK_REVIEW_REMARK,DEL_FLAG,CREATED_BY,UPDATED_BY,CREATED_AT,UPDATED_AT) " +
                        "VALUES (?,NULL,?,?,?,?,?,'ACTIVE',?,NULL,NULL,NULL,'0',?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)";
                    try (PreparedStatement s = c.prepareStatement(sql)) {
                        s.setLong(1,id); s.setString(2,title); s.setString(3,directoryPath);
                        s.setString(4,data.getOrDefault("mainFileTitle", title));
                        s.setString(5,data.getOrDefault("mainFilePath", directoryPath)); s.setInt(6,sortNo);
                        s.setString(7, FUSION_REVIEW_STATUS_PENDING);
                        s.setString(8, operatorName);
                        s.setString(9, operatorName);
                        s.executeUpdate();
                    }
                    try (Statement s = c.createStatement()) {
                        s.execute("SET IDENTITY_INSERT SYSDBA.X1_FUSION_POLICY OFF");
                    }
                    writeAuditMutation(c, data, "fusion-policy", "create", "X1_FUSION_POLICY", String.valueOf(id), String.valueOf(id), null, loadFusionPolicyRow(c, String.valueOf(id)), "融合主政策新增");
                    c.commit();
                    sendJson(exchange, 200, buildMap("success", true, "message", "主政策添加成功", "id", id));
                } catch (SQLException e) {
                    sendJson(exchange, 500, buildMap("success", false, "message", "添加主政策失败: " + e.getMessage()));
                }
            }
        }
    }

    static class FusionDocumentCreateHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if (!"POST".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持 POST 方法")); return;
            }
            Map<String, String> data = parseJsonObject(new String(readAllBytes(exchange.getRequestBody()), StandardCharsets.UTF_8));
            String fusionId = data.getOrDefault("fusionPolicyId", "").trim();
            String title = data.getOrDefault("policyTitle", "").trim();
            String filePath = data.getOrDefault("filePath", "").trim();
            String type = normalizeFusionDocumentType(data.getOrDefault("policyAttribute", ""));
            String operatorName = resolveAuditOperatorName(data);
            if (fusionId.isEmpty() || title.isEmpty() || filePath.isEmpty() || !FUSION_DOCUMENT_TYPES.contains(type)) {
                sendJson(exchange, 400, buildMap("success", false, "message", "主政策、关联政策名称、文件路径和文件类型均为必填项")); return;
            }
            synchronized (FusionDocumentCreateHandler.class) {
                Connection c = null;
                try {
                    c = createDedicatedConnection(); c.setAutoCommit(false);
                    if (!ensureActiveFusionPolicy(c, exchange, fusionId)) {
                        c.rollback();
                        return;
                    }
                    long documentId; long baseId; int displayOrder;
                    try (Statement s = c.createStatement(); ResultSet rs = s.executeQuery("SELECT NVL(MAX(ID),0)+1 FROM SYSDBA.X1_POLICY_DOCUMENT")) { rs.next(); documentId=rs.getLong(1); }
                    try (Statement s = c.createStatement(); ResultSet rs = s.executeQuery("SELECT NVL(MAX(ID),0)+1 FROM SYSDBA.X1_POLICY_BASE_INFO")) { rs.next(); baseId=rs.getLong(1); }
                    try (PreparedStatement s = c.prepareStatement("SELECT NVL(MAX(DISPLAY_ORDER),0)+1 FROM SYSDBA.X1_POLICY_DOCUMENT WHERE FUSION_POLICY_ID=?")) { s.setString(1,fusionId); try(ResultSet rs=s.executeQuery()){rs.next();displayOrder=rs.getInt(1);} }
                    String fileName = data.getOrDefault("fileName", "").trim();
                    if (fileName.isEmpty()) fileName = Paths.get(filePath).getFileName().toString();
                    String suffix = ""; int dot = fileName.lastIndexOf('.'); if (dot >= 0) suffix = fileName.substring(dot + 1);
                    try (PreparedStatement s = c.prepareStatement("INSERT INTO SYSDBA.X1_POLICY_DOCUMENT (ID,FUSION_POLICY_ID,SOURCE_POLICY_ID,POLICY_ATTRIBUTE,POLICY_TITLE,FILE_NAME,FILE_PATH,FILE_SUFFIX,DISPLAY_ORDER,DEL_FLAG,CREATED_BY,UPDATED_BY,CREATED_AT,UPDATED_AT) VALUES (?,?,NULL,?,?,?,?,?,?,'0',?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")) {
                        s.setLong(1,documentId);s.setString(2,fusionId);s.setString(3,type);s.setString(4,title);s.setString(5,fileName);s.setString(6,filePath);s.setString(7,suffix);s.setInt(8,displayOrder);s.setString(9,operatorName);s.setString(10,operatorName);s.executeUpdate();
                    }
                    try (PreparedStatement s = c.prepareStatement("INSERT INTO SYSDBA.X1_POLICY_BASE_INFO (ID,POLICY_DOCUMENT_ID,PUBLISH_DATE,POLICY_DOCUMENT_NO,ISSUING_DEPARTMENT,POLICY_LEVEL,ORIGINAL_URL,CREATED_BY,UPDATED_BY,CREATED_AT,UPDATED_AT) VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")) {
                        s.setLong(1,baseId);s.setLong(2,documentId);
                        String date=data.getOrDefault("publishDate","").trim(); if(date.isEmpty())s.setNull(3,Types.DATE);else s.setDate(3,java.sql.Date.valueOf(date));
                        s.setString(4,data.getOrDefault("documentNo",""));s.setString(5,data.getOrDefault("issuingDepartment",""));s.setString(6,data.getOrDefault("policyLevel",""));s.setString(7,data.getOrDefault("originalUrl",""));s.setString(8,operatorName);s.setString(9,operatorName);s.executeUpdate();
                    }
                    resetFusionReviewStatus(c, fusionId);
                    writeAuditMutation(c, data, "fusion-document", "create", "X1_POLICY_DOCUMENT", String.valueOf(documentId), fusionId, null, loadFusionDocumentRow(c, String.valueOf(documentId)), "融合关联文档新增");
                    writeAuditMutation(c, data, "fusion-document", "create", "X1_POLICY_BASE_INFO", String.valueOf(baseId), String.valueOf(documentId), null, loadFusionBaseInfoRowByDocumentId(c, String.valueOf(documentId)), "融合文档基础信息新增");
                    c.commit(); sendJson(exchange,200,buildMap("success",true,"message","关联政策添加成功","id",documentId));
                } catch (Exception e) {
                    if(c!=null)try{c.rollback();}catch(SQLException ignored){}
                    sendJson(exchange,500,buildMap("success",false,"message","添加关联政策失败: "+e.getMessage()));
                } finally { if(c!=null)try{c.close();}catch(SQLException ignored){} }
            }
        }
    }

    static class FusionDocumentCandidatesHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            Map<String,String> params=parseQueryParams(exchange.getRequestURI().getQuery());
            String fusionId=params.getOrDefault("fusionPolicyId","").trim();
            String keyword=params.getOrDefault("keyword","").trim();
            if(fusionId.isEmpty()){sendJson(exchange,400,buildMap("success",false,"message","缺少主政策ID"));return;}
            try {
                ensureConnection();
                String sql="SELECT d.ID,d.POLICY_TITLE,d.FILE_NAME,d.FILE_PATH,d.POLICY_ATTRIBUTE,f.DIRECTORY_TITLE " +
                    "FROM SYSDBA.X1_POLICY_DOCUMENT d JOIN SYSDBA.X1_FUSION_POLICY f ON f.ID=d.FUSION_POLICY_ID " +
                    "WHERE d.DEL_FLAG='0' AND f.DEL_FLAG='0' AND d.FUSION_POLICY_ID<>? " +
                    "AND NOT EXISTS (SELECT 1 FROM SYSDBA.X1_POLICY_DOCUMENT x WHERE x.FUSION_POLICY_ID=? AND x.DEL_FLAG='0' AND x.FILE_PATH=d.FILE_PATH) " +
                    (keyword.isEmpty()?"":"AND (d.POLICY_TITLE LIKE ? OR d.FILE_NAME LIKE ?) ")+
                    "ORDER BY d.POLICY_TITLE LIMIT 50";
                List<Map<String,Object>> rows=new ArrayList<>();
                try(PreparedStatement s=conn.prepareStatement(sql)){
                    s.setString(1,fusionId);s.setString(2,fusionId);
                    if(!keyword.isEmpty()){s.setString(3,"%"+keyword+"%");s.setString(4,"%"+keyword+"%");}
                    try(ResultSet rs=s.executeQuery()){while(rs.next()){Map<String,Object> row=new LinkedHashMap<>();row.put("id",rs.getString("ID"));row.put("policyTitle",rs.getString("POLICY_TITLE"));row.put("fileName",rs.getString("FILE_NAME"));row.put("filePath",rs.getString("FILE_PATH"));row.put("policyAttribute",normalizeFusionDocumentType(rs.getString("POLICY_ATTRIBUTE")));row.put("sourceFusionTitle",rs.getString("DIRECTORY_TITLE"));rows.add(row);}}
                }
                sendJson(exchange,200,buildMap("success",true,"data",rows));
            }catch(SQLException e){sendJson(exchange,500,buildMap("success",false,"message",e.getMessage()));}
        }
    }

    static class FusionDocumentLinkHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if(!"POST".equals(exchange.getRequestMethod())){sendJson(exchange,405,buildMap("success",false,"message","只支持 POST 方法"));return;}
            Map<String,String> data=parseJsonObject(new String(readAllBytes(exchange.getRequestBody()),StandardCharsets.UTF_8));
            String fusionId=data.getOrDefault("fusionPolicyId","").trim(),sourceId=data.getOrDefault("sourceDocumentId","").trim();
            String operatorName = resolveAuditOperatorName(data);
            if(fusionId.isEmpty()||sourceId.isEmpty()){sendJson(exchange,400,buildMap("success",false,"message","请选择需要关联的政策文件"));return;}
            synchronized(FusionDocumentLinkHandler.class){
                Connection c=null;
                try{
                    c=createDedicatedConnection();c.setAutoCommit(false);
                    if (!ensureActiveFusionPolicy(c, exchange, fusionId)) {
                        c.rollback();
                        return;
                    }
                    long newDocumentId,newBaseId;int displayOrder;
                    try(Statement s=c.createStatement();ResultSet rs=s.executeQuery("SELECT NVL(MAX(ID),0)+1 FROM SYSDBA.X1_POLICY_DOCUMENT")){rs.next();newDocumentId=rs.getLong(1);}
                    try(Statement s=c.createStatement();ResultSet rs=s.executeQuery("SELECT NVL(MAX(ID),0)+1 FROM SYSDBA.X1_POLICY_BASE_INFO")){rs.next();newBaseId=rs.getLong(1);}
                    try(PreparedStatement s=c.prepareStatement("SELECT NVL(MAX(DISPLAY_ORDER),0)+1 FROM SYSDBA.X1_POLICY_DOCUMENT WHERE FUSION_POLICY_ID=?")){s.setString(1,fusionId);try(ResultSet rs=s.executeQuery()){rs.next();displayOrder=rs.getInt(1);}}
                    String copySql="INSERT INTO SYSDBA.X1_POLICY_DOCUMENT (ID,FUSION_POLICY_ID,SOURCE_POLICY_ID,POLICY_ATTRIBUTE,POLICY_TITLE,FILE_NAME,FILE_PATH,FILE_SUFFIX,DISPLAY_ORDER,DEL_FLAG,CREATED_BY,UPDATED_BY,CREATED_AT,UPDATED_AT) " +
                        "SELECT ?,?,SOURCE_POLICY_ID,POLICY_ATTRIBUTE,POLICY_TITLE,FILE_NAME,FILE_PATH,FILE_SUFFIX,?,'0',?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP FROM SYSDBA.X1_POLICY_DOCUMENT s " +
                        "WHERE s.ID=? AND s.DEL_FLAG='0' AND NOT EXISTS (SELECT 1 FROM SYSDBA.X1_POLICY_DOCUMENT x WHERE x.FUSION_POLICY_ID=? AND x.DEL_FLAG='0' AND x.FILE_PATH=s.FILE_PATH)";
                    int copied;
                    try(PreparedStatement s=c.prepareStatement(copySql)){s.setLong(1,newDocumentId);s.setString(2,fusionId);s.setInt(3,displayOrder);s.setString(4,operatorName);s.setString(5,operatorName);s.setString(6,sourceId);s.setString(7,fusionId);copied=s.executeUpdate();}
                    if(copied==0){c.rollback();sendJson(exchange,409,buildMap("success",false,"message","文件不存在或已关联到该主政策"));return;}
                    try(PreparedStatement s=c.prepareStatement("INSERT INTO SYSDBA.X1_POLICY_BASE_INFO (ID,POLICY_DOCUMENT_ID,PUBLISH_DATE,POLICY_DOCUMENT_NO,ISSUING_DEPARTMENT,POLICY_LEVEL,ORIGINAL_URL,CREATED_BY,UPDATED_BY,CREATED_AT,UPDATED_AT) SELECT ?,?,PUBLISH_DATE,POLICY_DOCUMENT_NO,ISSUING_DEPARTMENT,POLICY_LEVEL,ORIGINAL_URL,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP FROM SYSDBA.X1_POLICY_BASE_INFO WHERE POLICY_DOCUMENT_ID=? AND ROWNUM=1")){s.setLong(1,newBaseId);s.setLong(2,newDocumentId);s.setString(3,operatorName);s.setString(4,operatorName);s.setString(5,sourceId);s.executeUpdate();}
                    resetFusionReviewStatus(c, fusionId);
                    writeAuditMutation(c, data, "fusion-document", "link", "X1_POLICY_DOCUMENT", String.valueOf(newDocumentId), fusionId, null, loadFusionDocumentRow(c, String.valueOf(newDocumentId)), "融合关联文档引用新增");
                    writeAuditMutation(c, data, "fusion-document", "link", "X1_POLICY_BASE_INFO", String.valueOf(newBaseId), String.valueOf(newDocumentId), null, loadFusionBaseInfoRowByDocumentId(c, String.valueOf(newDocumentId)), "融合引用文档基础信息新增");
                    c.commit();sendJson(exchange,200,buildMap("success",true,"message","关联政策添加成功","id",newDocumentId));
                }catch(SQLException e){if(c!=null)try{c.rollback();}catch(SQLException ignored){}sendJson(exchange,500,buildMap("success",false,"message","添加关联失败: "+e.getMessage()));}
                finally{if(c!=null)try{c.close();}catch(SQLException ignored){}}
            }
        }
    }

    static class FusionPolicyDeleteHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if(!"POST".equals(exchange.getRequestMethod())){sendJson(exchange,405,buildMap("success",false,"message","只支持 POST 方法"));return;}
            Map<String,String> data=parseJsonObject(new String(readAllBytes(exchange.getRequestBody()),StandardCharsets.UTF_8));String id=data.getOrDefault("id","").trim();
            if(id.isEmpty()){sendJson(exchange,400,buildMap("success",false,"message","缺少主政策ID"));return;}
            Connection c = null;
            try{
                c = createDedicatedConnection();
                c.setAutoCommit(false);
                String operatorName = resolveAuditOperatorName(data);
                Map<String, Object> policyBeforeRow = loadFusionPolicyRow(c, id);
                List<Map<String, Object>> documentBeforeRows = loadFusionDocumentRowsByFusionPolicyId(c, id);
                List<Map<String, Object>> relationBeforeRows = loadFusionPolicyRelationRowsByFusionPolicyId(c, id);

                int deletedDocumentCount;
                try(PreparedStatement s=c.prepareStatement(
                    "UPDATE SYSDBA.X1_POLICY_DOCUMENT SET DEL_FLAG='1',UPDATED_BY=?,UPDATED_AT=CURRENT_TIMESTAMP " +
                    "WHERE FUSION_POLICY_ID=? AND DEL_FLAG='0'")){
                    s.setString(1,operatorName);
                    s.setString(2,id);
                    deletedDocumentCount=s.executeUpdate();
                }

                int deletedPolicyCount;
                try(PreparedStatement s=c.prepareStatement(
                    "UPDATE SYSDBA.X1_FUSION_POLICY SET DEL_FLAG='1',STATUS='DELETED',UPDATED_BY=?,UPDATED_AT=CURRENT_TIMESTAMP " +
                    "WHERE ID=? AND DEL_FLAG='0'")){
                    s.setString(1,operatorName);
                    s.setString(2,id);
                    deletedPolicyCount=s.executeUpdate();
                }

                if(deletedPolicyCount<=0){
                    c.rollback();
                    sendJson(exchange,404,buildMap("success",false,"message","主政策不存在"));
                    return;
                }

                markFusionPolicyHierarchyDeleted(c, id, operatorName);
                writeAuditMutation(c, data, "fusion-policy", "delete", "X1_FUSION_POLICY", id, id, policyBeforeRow, loadFusionPolicyRow(c, id), "融合主政策删除");
                if(!documentBeforeRows.isEmpty()){
                    writeAuditMutation(c, data, "fusion-policy", "delete", "X1_POLICY_DOCUMENT", id, id, documentBeforeRows, loadFusionDocumentRowsByFusionPolicyId(c, id), "融合主政策删除时同步删除关联文档");
                }
                if(!relationBeforeRows.isEmpty()){
                    writeAuditMutation(c, data, "fusion-policy", "delete", "X1_FUSION_POLICY_RELATION", id, id, relationBeforeRows, loadFusionPolicyRelationRowsByFusionPolicyId(c, id), "融合主政策删除时同步删除父子关系");
                }

                c.commit();
                String message = deletedDocumentCount>0
                    ? "主政策已删除，并同步删除 "+deletedDocumentCount+" 条关联政策"
                    : "主政策已删除";
                sendJson(exchange,200,buildMap(
                    "success",true,
                    "message",message,
                    "deletedDocuments",deletedDocumentCount
                ));
            }catch(SQLException e){
                if(c!=null)try{c.rollback();}catch(SQLException ignored){}
                sendJson(exchange,500,buildMap("success",false,"message",e.getMessage()));
            }finally{
                if(c!=null)try{c.close();}catch(SQLException ignored){}
            }
        }
    }

    static class FusionPolicyRestoreHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if(!"POST".equals(exchange.getRequestMethod())){sendJson(exchange,405,buildMap("success",false,"message","只支持 POST 方法"));return;}
            Map<String,String> data=parseJsonObject(new String(readAllBytes(exchange.getRequestBody()),StandardCharsets.UTF_8));String id=data.getOrDefault("id","").trim();
            if(id.isEmpty()){sendJson(exchange,400,buildMap("success",false,"message","缺少主政策ID"));return;}
            Connection c = null;
            try{
                c = createDedicatedConnection();
                c.setAutoCommit(false);
                String operatorName = resolveAuditOperatorName(data);
                Map<String, Object> policyBeforeRow = loadFusionPolicyRow(c, id);
                List<Map<String, Object>> documentBeforeRows = loadFusionDocumentRowsByFusionPolicyId(c, id);
                List<Map<String, Object>> relationBeforeRows = loadFusionPolicyRelationRowsByFusionPolicyId(c, id);

                int restoredDocumentCount;
                try(PreparedStatement s=c.prepareStatement(
                    "UPDATE SYSDBA.X1_POLICY_DOCUMENT SET DEL_FLAG='0',UPDATED_BY=?,UPDATED_AT=CURRENT_TIMESTAMP " +
                    "WHERE FUSION_POLICY_ID=? AND DEL_FLAG='1'")){
                    s.setString(1,operatorName);
                    s.setString(2,id);
                    restoredDocumentCount=s.executeUpdate();
                }

                int restoredPolicyCount;
                try(PreparedStatement s=c.prepareStatement(
                    "UPDATE SYSDBA.X1_FUSION_POLICY SET DEL_FLAG='0',STATUS='ACTIVE',UPDATED_BY=?,UPDATED_AT=CURRENT_TIMESTAMP " +
                    "WHERE ID=? AND DEL_FLAG='1'")){
                    s.setString(1,operatorName);
                    s.setString(2,id);
                    restoredPolicyCount=s.executeUpdate();
                }

                if(restoredPolicyCount<=0){
                    c.rollback();
                    sendJson(exchange,404,buildMap("success",false,"message","主政策不存在或未处于弃用状态"));
                    return;
                }

                restoreFusionPolicyHierarchy(c, id, operatorName);
                writeAuditMutation(c, data, "fusion-policy", "restore", "X1_FUSION_POLICY", id, id, policyBeforeRow, loadFusionPolicyRow(c, id), "融合主政策恢复");
                if(!documentBeforeRows.isEmpty()){
                    writeAuditMutation(c, data, "fusion-policy", "restore", "X1_POLICY_DOCUMENT", id, id, documentBeforeRows, loadFusionDocumentRowsByFusionPolicyId(c, id), "融合主政策恢复时同步恢复关联文档");
                }
                if(!relationBeforeRows.isEmpty()){
                    writeAuditMutation(c, data, "fusion-policy", "restore", "X1_FUSION_POLICY_RELATION", id, id, relationBeforeRows, loadFusionPolicyRelationRowsByFusionPolicyId(c, id), "融合主政策恢复时同步恢复父子关系");
                }

                c.commit();
                String message = restoredDocumentCount>0
                    ? "主政策已取消弃用，并同步恢复 "+restoredDocumentCount+" 条关联政策"
                    : "主政策已取消弃用";
                sendJson(exchange,200,buildMap(
                    "success",true,
                    "message",message,
                    "restoredDocuments",restoredDocumentCount
                ));
            }catch(SQLException e){
                if(c!=null)try{c.rollback();}catch(SQLException ignored){}
                sendJson(exchange,500,buildMap("success",false,"message",e.getMessage()));
            }finally{
                if(c!=null)try{c.close();}catch(SQLException ignored){}
            }
        }
    }

    static class FusionDocumentTypeHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if (!"POST".equals(exchange.getRequestMethod())) { sendJson(exchange,405,buildMap("success",false,"message","只支持 POST 方法"));return; }
            Map<String,String> data=parseJsonObject(new String(readAllBytes(exchange.getRequestBody()),StandardCharsets.UTF_8));
            String id=data.getOrDefault("id","").trim(); String type=normalizeFusionDocumentType(data.getOrDefault("type",""));
            if(id.isEmpty()||!FUSION_DOCUMENT_TYPES.contains(type)){sendJson(exchange,400,buildMap("success",false,"message","政策ID或文件类型无效"));return;}
            Connection c = null;
            try {
                c = createDedicatedConnection();
                c.setAutoCommit(false);
                String operatorName = resolveAuditOperatorName(data);
                String fusionId = queryFusionPolicyIdByDocumentId(c, id);
                String sourcePolicyId = querySourcePolicyIdByDocumentId(c, id);
                boolean canSyncSourceMetadata = !sourcePolicyId.isEmpty() && policyExists(c, sourcePolicyId);
                Map<String, Object> documentBeforeRow = loadFusionDocumentRow(c, id);
                Map<String, Object> metadataBeforeRow = canSyncSourceMetadata ? loadPolicyMetadataRow(c, sourcePolicyId) : null;
                int updated;
                try (PreparedStatement s = c.prepareStatement(
                    "UPDATE SYSDBA.X1_POLICY_DOCUMENT SET POLICY_ATTRIBUTE=?,UPDATED_BY=?,UPDATED_AT=CURRENT_TIMESTAMP WHERE ID=? AND DEL_FLAG='0'")) {
                    s.setString(1, type);
                    s.setString(2, operatorName);
                    s.setString(3, id);
                    updated = s.executeUpdate();
                }
                if (updated <= 0) {
                    c.rollback();
                    sendJson(exchange,404,buildMap("success",false,"message","关联政策不存在"));
                    return;
                }
                if (canSyncSourceMetadata) {
                    upsertPolicyMetadataType(c, sourcePolicyId, type, operatorName);
                }
                if (!fusionId.isEmpty()) {
                    resetFusionReviewStatus(c, fusionId);
                }
                writeAuditMutation(c, data, "fusion-document", "type", "X1_POLICY_DOCUMENT", id, fusionId, documentBeforeRow, loadFusionDocumentRow(c, id), "融合关联文档类型更新");
                if (canSyncSourceMetadata) {
                    writeAuditMutation(c, data, "fusion-document", "type", "POLICY_METADATA", sourcePolicyId, sourcePolicyId, metadataBeforeRow, loadPolicyMetadataRow(c, sourcePolicyId), "融合关联文档类型同步原政策属性");
                }
                c.commit();
                if (!sourcePolicyId.isEmpty() && !canSyncSourceMetadata) {
                    sendJson(exchange,200,buildMap("success",true,"message","文件类型已保存，但源政策主表不存在，未同步回 POLICY_METADATA","warning","source-policy-missing","sourcePolicyId",sourcePolicyId));
                    return;
                }
                sendJson(exchange,200,buildMap("success",true,"message","文件类型已保存"));
            }
            catch(SQLException e){
                if(c!=null) try{c.rollback();}catch(SQLException ignored){}
                sendJson(exchange,500,buildMap("success",false,"message",e.getMessage()));
            } finally {
                if(c!=null) try{c.close();}catch(SQLException ignored){}
            }
        }
    }

    static class FusionPolicyOriginalCandidatesHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            Map<String,String> params=parseQueryParams(exchange.getRequestURI().getQuery());
            String keyword=params.getOrDefault("keyword","").trim();
            try {
                ensureConnection();
                StringBuilder where=new StringBuilder(" WHERE 1=1");
                List<String> queryParams=new ArrayList<>();
                if(!keyword.isEmpty()){
                    where.append(" AND p.POLICY_TITLE LIKE ?");
                    queryParams.add("%"+keyword+"%");
                }
                String sql="SELECT p.ID,p.POLICY_TITLE," +
                    "p.FILE_NAME," +
                    "(SELECT o.POLICY_CODE FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=CAST(p.ID AS VARCHAR(50)) AND ROWNUM=1) AS POLICY_CODE," +
                    "(SELECT o.ISSUE_DEPARTMENT FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=CAST(p.ID AS VARCHAR(50)) AND ROWNUM=1) AS ISSUE_DEPARTMENT," +
                    "(SELECT o.PUBLISH_DATE FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=CAST(p.ID AS VARCHAR(50)) AND ROWNUM=1) AS PUBLISH_DATE," +
                    "(SELECT o.FILE_PATH FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=CAST(p.ID AS VARCHAR(50)) AND ROWNUM=1) AS FILE_PATH," +
                    "(SELECT MAX(TRIM(m.POLICY_ATTRIBUTE)) FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID=p.ID) AS POLICY_ATTRIBUTE " +
                    "FROM SYSDBA.POLICY p"+where+
                    " ORDER BY p.CREATED_AT DESC LIMIT 50";
                List<Map<String,Object>> rows=new ArrayList<>();
                try(PreparedStatement s=conn.prepareStatement(sql)){
                    for(int i=0;i<queryParams.size();i++)s.setString(i+1,queryParams.get(i));
                    try(ResultSet rs=s.executeQuery()){while(rs.next()){Map<String,Object> row=new LinkedHashMap<>();
                        row.put("id",rs.getString("ID"));
                        row.put("policyTitle",nullToEmpty(rs.getString("POLICY_TITLE")));
                        row.put("fileName",nullToEmpty(rs.getString("FILE_NAME")));
                        row.put("filePath",nullToEmpty(rs.getString("FILE_PATH")));
                        row.put("publishDate",nullToEmpty(rs.getString("PUBLISH_DATE")));
                        row.put("issuingDepartment",nullToEmpty(rs.getString("ISSUE_DEPARTMENT")));
                        row.put("documentNo",nullToEmpty(rs.getString("POLICY_CODE")));
                        row.put("policyAttribute",normalizeFusionDocumentType(rs.getString("POLICY_ATTRIBUTE")));
                        rows.add(row);
                    }}
                }
                sendJson(exchange,200,buildMap("success",true,"data",rows));
            }catch(SQLException e){sendJson(exchange,500,buildMap("success",false,"message",e.getMessage()));}
        }
    }

    static class FusionPolicyLinkFromOriginalHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if(!"POST".equals(exchange.getRequestMethod())){sendJson(exchange,405,buildMap("success",false,"message","只支持 POST 方法"));return;}
            Map<String,String> data=parseJsonObject(new String(readAllBytes(exchange.getRequestBody()),StandardCharsets.UTF_8));
            String fusionId=data.getOrDefault("fusionPolicyId","").trim();
            String policyId=data.getOrDefault("policyId","").trim();
            if(fusionId.isEmpty()||policyId.isEmpty()){sendJson(exchange,400,buildMap("success",false,"message","参数不完整"));return;}
            synchronized(FusionPolicyLinkFromOriginalHandler.class){
                Connection c=null;
                try{
                    c=createDedicatedConnection();c.setAutoCommit(false);
                    if (!ensureActiveFusionPolicy(c, exchange, fusionId)) {
                        c.rollback();
                        return;
                    }
                    String operatorName = resolveAuditOperatorName(data);
                    long documentId;int displayOrder;
                    try(Statement s=c.createStatement();ResultSet rs=s.executeQuery("SELECT NVL(MAX(ID),0)+1 FROM SYSDBA.X1_POLICY_DOCUMENT")){rs.next();documentId=rs.getLong(1);}
                    try(PreparedStatement s=c.prepareStatement("SELECT NVL(MAX(DISPLAY_ORDER),0)+1 FROM SYSDBA.X1_POLICY_DOCUMENT WHERE FUSION_POLICY_ID=?")){s.setString(1,fusionId);try(ResultSet rs=s.executeQuery()){rs.next();displayOrder=rs.getInt(1);}}
                    // 统一使用 POLICY 表中的 POLICY_TITLE 作为政策名称
                    String policyTitle="",filePath="",publishDate="",issuingDepartment="",documentNo="",policyType="";
                    try(PreparedStatement s=c.prepareStatement(
                        "SELECT p.POLICY_TITLE," +
                        "(SELECT o.PUBLISH_DATE FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=CAST(p.ID AS VARCHAR(50)) AND ROWNUM=1) AS PUBLISH_DATE," +
                        "(SELECT o.ISSUE_DEPARTMENT FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=CAST(p.ID AS VARCHAR(50)) AND ROWNUM=1) AS ISSUE_DEPARTMENT," +
                        "(SELECT o.POLICY_CODE FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=CAST(p.ID AS VARCHAR(50)) AND ROWNUM=1) AS POLICY_CODE," +
                        "(SELECT o.FILE_PATH FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=CAST(p.ID AS VARCHAR(50)) AND ROWNUM=1) AS FILE_PATH," +
                        "(SELECT MAX(TRIM(m.POLICY_ATTRIBUTE)) FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID=p.ID) AS POLICY_ATTRIBUTE " +
                        "FROM SYSDBA.POLICY p WHERE p.ID=?")){
                        s.setString(1,policyId);
                        try(ResultSet rs=s.executeQuery()){if(rs.next()){
                            policyTitle=nullToEmpty(rs.getString("POLICY_TITLE"));
                            filePath=nullToEmpty(rs.getString("FILE_PATH"));
                            publishDate=nullToEmpty(rs.getString("PUBLISH_DATE"));
                            issuingDepartment=nullToEmpty(rs.getString("ISSUE_DEPARTMENT"));
                            documentNo=nullToEmpty(rs.getString("POLICY_CODE"));
                            policyType=normalizeFusionDocumentType(nullToEmpty(rs.getString("POLICY_ATTRIBUTE")));
                        }}
                    }
                    if(policyTitle.isEmpty()){c.rollback();sendJson(exchange,404,buildMap("success",false,"message","政策记录不存在"));return;}
                    String fileName="";int dot=filePath.lastIndexOf('/');if(dot>=0)fileName=filePath.substring(dot+1);else if(!filePath.isEmpty())fileName=filePath;
                    if(!FUSION_DOCUMENT_TYPES.contains(policyType))policyType=FUSION_DOCUMENT_TYPES.iterator().next();
                    String suffix="";dot=fileName.lastIndexOf('.');if(dot>=0)suffix=fileName.substring(dot+1);
                    try(Statement s=c.createStatement()){s.execute("SET IDENTITY_INSERT SYSDBA.X1_POLICY_DOCUMENT ON");}
                    try(PreparedStatement s=c.prepareStatement("INSERT INTO SYSDBA.X1_POLICY_DOCUMENT (ID,FUSION_POLICY_ID,SOURCE_POLICY_ID,POLICY_ATTRIBUTE,POLICY_TITLE,FILE_NAME,FILE_PATH,FILE_SUFFIX,DISPLAY_ORDER,DEL_FLAG,CREATED_BY,UPDATED_BY,CREATED_AT,UPDATED_AT) VALUES (?,?,?,?,?,?,?,?,?,'0',?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")){
                        s.setLong(1,documentId);s.setString(2,fusionId);s.setString(3,policyId);s.setString(4,policyType);s.setString(5,policyTitle);s.setString(6,fileName);s.setString(7,filePath);s.setString(8,suffix);s.setInt(9,displayOrder);s.setString(10,operatorName);s.setString(11,operatorName);s.executeUpdate();
                    }
                    try(Statement s=c.createStatement()){s.execute("SET IDENTITY_INSERT SYSDBA.X1_POLICY_DOCUMENT OFF");}
                    // 复制 base_info 元数据
                    long baseId;
                    try(Statement s=c.createStatement();ResultSet rs=s.executeQuery("SELECT NVL(MAX(ID),0)+1 FROM SYSDBA.X1_POLICY_BASE_INFO")){rs.next();baseId=rs.getLong(1);}
                    try(Statement s=c.createStatement()){s.execute("SET IDENTITY_INSERT SYSDBA.X1_POLICY_BASE_INFO ON");}
                    try(PreparedStatement s=c.prepareStatement("INSERT INTO SYSDBA.X1_POLICY_BASE_INFO (ID,POLICY_DOCUMENT_ID,PUBLISH_DATE,POLICY_DOCUMENT_NO,ISSUING_DEPARTMENT,POLICY_LEVEL,ORIGINAL_URL,CREATED_BY,UPDATED_BY,CREATED_AT,UPDATED_AT) VALUES (?,?,?,?,?,NULL,NULL,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")){
                        s.setLong(1,baseId);s.setLong(2,documentId);
                        if(publishDate.isEmpty())s.setNull(3,Types.DATE);else s.setDate(3,java.sql.Date.valueOf(publishDate));
                        s.setString(4,documentNo);s.setString(5,issuingDepartment);s.setString(6,operatorName);s.setString(7,operatorName);s.executeUpdate();
                    }
                    try(Statement s=c.createStatement()){s.execute("SET IDENTITY_INSERT SYSDBA.X1_POLICY_BASE_INFO OFF");}
                    resetFusionReviewStatus(c, fusionId);
                    writeAuditMutation(c, data, "fusion-policy", "link-from-original", "X1_POLICY_DOCUMENT", String.valueOf(documentId), fusionId, null, loadFusionDocumentRow(c, String.valueOf(documentId)), "从原始政策引用生成融合关联文档");
                    writeAuditMutation(c, data, "fusion-policy", "link-from-original", "X1_POLICY_BASE_INFO", String.valueOf(baseId), String.valueOf(documentId), null, loadFusionBaseInfoRowByDocumentId(c, String.valueOf(documentId)), "从原始政策复制融合文档基础信息");
                    c.commit();sendJson(exchange,200,buildMap("success",true,"message","关联政策添加成功","id",documentId));
                }catch(Exception e){if(c!=null)try{c.rollback();}catch(SQLException ignored){} sendJson(exchange,500,buildMap("success",false,"message","添加关联失败: "+e.getMessage()));}
                finally{if(c!=null)try{c.close();}catch(SQLException ignored){}}
            }
        }
    }

    static class FusionDocumentContentHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            Map<String,String> params=parseQueryParams(exchange.getRequestURI().getQuery());String id=params.getOrDefault("id","").trim();
            if(id.isEmpty()){sendJson(exchange,400,buildMap("success",false,"message","缺少关联政策ID"));return;}
            try { ensureConnection(); try(PreparedStatement s=conn.prepareStatement("SELECT POLICY_TITLE,FILE_PATH,FILE_SUFFIX FROM SYSDBA.X1_POLICY_DOCUMENT WHERE ID=? AND DEL_FLAG='0'")){s.setString(1,id);try(ResultSet rs=s.executeQuery()){if(!rs.next()){sendJson(exchange,404,buildMap("success",false,"message","关联政策不存在"));return;}String path=rs.getString("FILE_PATH");Path file=resolveAttachmentPath(path);String suffix=nullToEmpty(rs.getString("FILE_SUFFIX"));String content="md".equalsIgnoreCase(suffix)?new String(Files.readAllBytes(file),StandardCharsets.UTF_8):extractAttachmentText(file,suffix);sendJson(exchange,200,buildMap("success",true,"data",buildMap("title",nullToEmpty(rs.getString("POLICY_TITLE")),"filePath",path,"content",content)));}} }
            catch(Exception e){sendJson(exchange,404,buildMap("success",false,"message",e.getMessage()));}
        }
    }

    static class FusionPolicyStatusHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if (!"POST".equals(exchange.getRequestMethod())) { sendJson(exchange,405,buildMap("success",false,"message","只支持 POST 方法"));return; }
            Map<String,String> data=parseJsonObject(new String(readAllBytes(exchange.getRequestBody()),StandardCharsets.UTF_8));
            String id=data.getOrDefault("id","").trim();
            String status=data.getOrDefault("status","").trim();
            if(id.isEmpty()||status.isEmpty()){sendJson(exchange,400,buildMap("success",false,"message","参数不完整"));return;}
            // 前端传"有效"/"无效"，映射为数据库的 ACTIVE/INACTIVE
            String dbStatus;
            if("有效".equals(status)) dbStatus = "ACTIVE";
            else if("无效".equals(status)) dbStatus = "INACTIVE";
            else {sendJson(exchange,400,buildMap("success",false,"message","状态值无效"));return;}
            Connection c = null;
            try{
                c = createDedicatedConnection();
                c.setAutoCommit(false);
                String operatorName = resolveAuditOperatorName(data);
                Map<String, Object> beforeRow = loadFusionPolicyRow(c, id);
                int n;
                try(PreparedStatement s=c.prepareStatement("UPDATE SYSDBA.X1_FUSION_POLICY SET STATUS=?,UPDATED_BY=?,UPDATED_AT=CURRENT_TIMESTAMP WHERE ID=? AND DEL_FLAG='0'")){
                    s.setString(1,dbStatus);
                    s.setString(2,operatorName);
                    s.setString(3,id);
                    n=s.executeUpdate();
                }
                if(n<=0){
                    c.rollback();
                    sendJson(exchange,404,buildMap("success",false,"message","主政策不存在"));
                    return;
                }
                writeAuditMutation(c, data, "fusion-policy", "status", "X1_FUSION_POLICY", id, id, beforeRow, loadFusionPolicyRow(c, id), "融合主政策状态更新");
                c.commit();
                sendJson(exchange,200,buildMap("success",true,"message","状态已更新"));
            } catch(SQLException e){
                if(c!=null)try{c.rollback();}catch(SQLException ignored){}
                sendJson(exchange,500,buildMap("success",false,"message",e.getMessage()));
            } finally {
                if(c!=null)try{c.close();}catch(SQLException ignored){}
            }
        }
    }

    static class FusionPolicyUpdateHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if (!"POST".equals(exchange.getRequestMethod())) { sendJson(exchange,405,buildMap("success",false,"message","只支持 POST 方法"));return; }
            Map<String,String> data=parseJsonObject(new String(readAllBytes(exchange.getRequestBody()),StandardCharsets.UTF_8));
            String id=data.getOrDefault("id","").trim();
            String mainFileTitle=data.getOrDefault("mainFileTitle","").trim();
            String directoryTitle=data.getOrDefault("directoryTitle","").trim();
            if(id.isEmpty()||(mainFileTitle.isEmpty()&&directoryTitle.isEmpty())){sendJson(exchange,400,buildMap("success",false,"message","参数不完整"));return;}
            Connection c = null;
            try{
                c = createDedicatedConnection();
                c.setAutoCommit(false);
                String operatorName = resolveAuditOperatorName(data);
                Map<String, Object> beforeRow = loadFusionPolicyRow(c, id);
                StringBuilder sql=new StringBuilder("UPDATE SYSDBA.X1_FUSION_POLICY SET UPDATED_AT=CURRENT_TIMESTAMP");
                sql.append(",UPDATED_BY=?");
                if(!mainFileTitle.isEmpty()) sql.append(",MAIN_FILE_TITLE=?");
                if(!directoryTitle.isEmpty()) sql.append(",DIRECTORY_TITLE=?");
                sql.append(" WHERE ID=? AND DEL_FLAG='0'");
                int n;
                try(PreparedStatement s=c.prepareStatement(sql.toString())){
                    int idx=1;
                    s.setString(idx++,operatorName);
                    if(!mainFileTitle.isEmpty()){s.setString(idx++,mainFileTitle);}
                    if(!directoryTitle.isEmpty()){s.setString(idx++,directoryTitle);}
                    s.setString(idx,id);
                    n=s.executeUpdate();
                }
                if(n<=0){
                    c.rollback();
                    sendJson(exchange,404,buildMap("success",false,"message","主政策不存在"));
                    return;
                }
                writeAuditMutation(c, data, "fusion-policy", "update", "X1_FUSION_POLICY", id, id, beforeRow, loadFusionPolicyRow(c, id), "融合主政策标题信息更新");
                c.commit();
                sendJson(exchange,200,buildMap("success",true,"message","主政策已更新"));
            }catch(SQLException e){
                if(c!=null)try{c.rollback();}catch(SQLException ignored){}
                sendJson(exchange,500,buildMap("success",false,"message",e.getMessage()));
            } finally {
                if(c!=null)try{c.close();}catch(SQLException ignored){}
            }
        }
    }

    static class FusionPolicyParentUpdateHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if (!"POST".equals(exchange.getRequestMethod())) { sendJson(exchange,405,buildMap("success",false,"message","只支持 POST 方法"));return; }
            Map<String,String> data=parseJsonObject(new String(readAllBytes(exchange.getRequestBody()),StandardCharsets.UTF_8));
            String childFusionPolicyId=data.getOrDefault("id","").trim();
            String parentFusionPolicyId=data.getOrDefault("parentFusionPolicyId","").trim();
            String action = data.getOrDefault("action", "").trim();
            String operator = resolveAuditOperatorName(data);
            boolean removingRelation = "remove".equalsIgnoreCase(action) || parentFusionPolicyId.isEmpty();
            if(childFusionPolicyId.isEmpty()){sendJson(exchange,400,buildMap("success",false,"message","缺少当前主政策ID"));return;}
            if(!parentFusionPolicyId.isEmpty() && childFusionPolicyId.equals(parentFusionPolicyId)){
                sendJson(exchange,400,buildMap("success",false,"message","上级主政策不能选择自己"));
                return;
            }

            synchronized(FusionPolicyParentUpdateHandler.class){
                Connection c = null;
                try{
                    c = createDedicatedConnection();
                    c.setAutoCommit(false);
                    List<Map<String, Object>> beforeRows = loadFusionParentRelations(c, childFusionPolicyId);

                    if(!fusionPolicyExists(c, childFusionPolicyId)){
                        c.rollback();
                        sendJson(exchange,404,buildMap("success",false,"message","当前主政策不存在"));
                        return;
                    }

                    if(!parentFusionPolicyId.isEmpty()){
                        if(!fusionPolicyExists(c, parentFusionPolicyId)){
                            c.rollback();
                            sendJson(exchange,404,buildMap("success",false,"message","上级主政策不存在"));
                            return;
                        }
                        if(wouldCreateFusionPolicyCycle(c, childFusionPolicyId, parentFusionPolicyId)){
                            c.rollback();
                            sendJson(exchange,400,buildMap("success",false,"message","不能把当前主政策挂到自己的下级链路下，这会形成循环关系"));
                            return;
                        }
                    }

                    Set<String> touchedFusionPolicyIds = new LinkedHashSet<>();
                    touchedFusionPolicyIds.add(childFusionPolicyId);
                    if(!parentFusionPolicyId.isEmpty()) touchedFusionPolicyIds.add(parentFusionPolicyId);

                    if(removingRelation){
                        List<String> oldParentFusionPolicyIds = queryActiveParentFusionPolicyIds(c, childFusionPolicyId);
                        touchedFusionPolicyIds.addAll(oldParentFusionPolicyIds);
                        int deleted = markFusionParentRelationDeleted(c, childFusionPolicyId, parentFusionPolicyId, operator);
                        if(deleted <= 0){
                            c.rollback();
                            sendJson(exchange,200,buildMap(
                                "success",true,
                                "message",parentFusionPolicyId.isEmpty() ? "当前未设置上级主政策" : "指定父子关系未发生变化"
                            ));
                            return;
                        }
                    }else{
                        if(hasActiveFusionParentRelation(c, childFusionPolicyId, parentFusionPolicyId)){
                            c.rollback();
                            sendJson(exchange,200,buildMap("success",true,"message","该上级主政策已关联，无需重复设置"));
                            return;
                        }
                        long relationId = nextFusionPolicyRelationId(c);
                        try(PreparedStatement stmt=c.prepareStatement(
                            "INSERT INTO SYSDBA.X1_FUSION_POLICY_RELATION " +
                            "(ID,PARENT_FUSION_POLICY_ID,CHILD_FUSION_POLICY_ID,DEL_FLAG,CREATED_BY,UPDATED_BY,CREATED_AT,UPDATED_AT) " +
                            "VALUES (?,?,?,'0',?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")){
                            stmt.setLong(1, relationId);
                            stmt.setString(2, parentFusionPolicyId);
                            stmt.setString(3, childFusionPolicyId);
                            stmt.setString(4, operator);
                            stmt.setString(5, operator);
                            stmt.executeUpdate();
                        }
                    }
                    for(String fusionPolicyId : touchedFusionPolicyIds){
                        resetFusionReviewStatus(c, fusionPolicyId);
                    }

                    writeAuditMutation(c, data, "fusion-policy-relation", "update-parent", "X1_FUSION_POLICY_RELATION",
                        childFusionPolicyId, childFusionPolicyId, beforeRows, loadFusionParentRelations(c, childFusionPolicyId),
                        removingRelation ? "融合主政策父子关系解除" : "融合主政策父子关系新增");
                    c.commit();
                    sendJson(exchange,200,buildMap(
                        "success",true,
                        "message",removingRelation ? (parentFusionPolicyId.isEmpty() ? "已解除全部直接上级关系" : "已解除直接上级关系") : "上级主政策关联已保存"
                    ));
                }catch(SQLException e){
                    if(c!=null)try{c.rollback();}catch(SQLException ignored){}
                    sendJson(exchange,500,buildMap("success",false,"message",e.getMessage()));
                }finally{
                    if(c!=null)try{c.close();}catch(SQLException ignored){}
                }
            }
        }
    }

    static class FusionPolicyReviewApproveHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if (!"POST".equals(exchange.getRequestMethod())) { sendJson(exchange,405,buildMap("success",false,"message","只支持 POST 方法"));return; }
            Map<String,String> data=parseJsonObject(new String(readAllBytes(exchange.getRequestBody()),StandardCharsets.UTF_8));
            String id=data.getOrDefault("id","").trim();
            String reviewedBy=firstNonEmpty(data.get("reviewedBy"), resolveAuditOperatorName(data));
            String reviewedById=resolveAuditOperatorId(data);
            String remark=data.getOrDefault("remark","").trim();
            if(id.isEmpty()){sendJson(exchange,400,buildMap("success",false,"message","缺少主政策ID"));return;}
            data.put("reviewedBy", reviewedBy);
            data.put("reviewedById", reviewedById);
            try{
                Connection c = createDedicatedConnection();
                c.setAutoCommit(false);
                int totalDocs = 0;
                int typedDocs = 0;
                try(PreparedStatement s=c.prepareStatement(
                    "SELECT COUNT(*) AS TOTAL, SUM(HAS_TYPE) AS TYPED FROM (" +
                    "SELECT TRIM(d.SOURCE_POLICY_ID) AS SOURCE_POLICY_ID, " +
                    "MAX(CASE WHEN d.POLICY_ATTRIBUTE IS NOT NULL AND TRIM(d.POLICY_ATTRIBUTE) <> '' THEN 1 ELSE 0 END) AS DOC_HAS_TYPE, " +
                    "CASE WHEN EXISTS (" +
                    "SELECT 1 FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID=TRIM(d.SOURCE_POLICY_ID) " +
                    "AND m.POLICY_ATTRIBUTE IS NOT NULL AND TRIM(m.POLICY_ATTRIBUTE) <> ''" +
                    ") THEN 1 ELSE 0 END AS META_HAS_TYPE, " +
                    "CASE WHEN (" +
                    "MAX(CASE WHEN d.POLICY_ATTRIBUTE IS NOT NULL AND TRIM(d.POLICY_ATTRIBUTE) <> '' THEN 1 ELSE 0 END)=1 " +
                    "OR EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID=TRIM(d.SOURCE_POLICY_ID) " +
                    "AND m.POLICY_ATTRIBUTE IS NOT NULL AND TRIM(m.POLICY_ATTRIBUTE) <> '')" +
                    ") THEN 1 ELSE 0 END AS HAS_TYPE " +
                    "FROM SYSDBA.X1_POLICY_DOCUMENT d " +
                    "WHERE d.FUSION_POLICY_ID=? AND d.DEL_FLAG='0' " +
                    "AND d.SOURCE_POLICY_ID IS NOT NULL AND TRIM(d.SOURCE_POLICY_ID) <> '' " +
                    "AND NOT (" +
                    "TRIM(NVL(d.POLICY_ATTRIBUTE,'')) IN ('实施细则类','指导政策类','申报材料类','申报通知类','结果公告类','通知公告类','其它') " +
                    "OR LOWER(NVL(d.FILE_PATH,'')) LIKE '%/attachments/%'" +
                    ") " +
                    "GROUP BY TRIM(d.SOURCE_POLICY_ID)" +
                    ") t")){
                    s.setString(1,id);
                    try(ResultSet rs=s.executeQuery()){
                        if(rs.next()){
                            totalDocs = rs.getInt("TOTAL");
                            typedDocs = rs.getInt("TYPED");
                        }
                    }
                }
                if(totalDocs <= 0){
                    sendJson(exchange,400,buildMap("success",false,"message","请先添加关联政策"));
                    return;
                }
                if(typedDocs < totalDocs){
                    sendJson(exchange,400,buildMap("success",false,"message","请先完成全部关联政策的类型标注"));
                    return;
                }
                try {
                    Set<String> sourcePolicyIds = new LinkedHashSet<>();
                    Map<String, Map<String, Object>> metadataBeforeRows = new LinkedHashMap<>();
                    Map<String, List<Map<String, Object>>> attachmentBeforeRows = new LinkedHashMap<>();
                    Map<String, Object> fusionPolicyBeforeRow = loadFusionPolicyRow(c, id);
                    try(PreparedStatement sourceStmt=c.prepareStatement(
                        "SELECT DISTINCT TRIM(SOURCE_POLICY_ID) AS SOURCE_POLICY_ID " +
                        "FROM SYSDBA.X1_POLICY_DOCUMENT WHERE FUSION_POLICY_ID=? AND DEL_FLAG='0' " +
                        "AND SOURCE_POLICY_ID IS NOT NULL AND TRIM(SOURCE_POLICY_ID) <> ''")){
                        sourceStmt.setString(1,id);
                        try(ResultSet sourceRs=sourceStmt.executeQuery()){
                            while(sourceRs.next()){
                                String sourcePolicyId = nullToEmpty(sourceRs.getString("SOURCE_POLICY_ID")).trim();
                                if(!sourcePolicyId.isEmpty()) {
                                    sourcePolicyIds.add(sourcePolicyId);
                                    metadataBeforeRows.put(sourcePolicyId, loadPolicyMetadataRow(c, sourcePolicyId));
                                    attachmentBeforeRows.put(sourcePolicyId, loadPolicyAttachmentRowsByPolicyId(c, sourcePolicyId));
                                }
                            }
                        }
                    }

                    int n;
                    try(PreparedStatement s=c.prepareStatement(
                        // 审核时间取数据库主机时钟，避免会话时区为 UTC 时落库少 8 小时
                        "UPDATE SYSDBA.X1_FUSION_POLICY SET MARK_REVIEW_STATUS=?,MARK_REVIEWED_AT=SYSTIMESTAMP,MARK_REVIEWED_BY=?,REVIEWED_BY_ID=?,MARK_REVIEW_REMARK=?,UPDATED_BY=?,UPDATED_AT=SYSTIMESTAMP " +
                        "WHERE ID=? AND DEL_FLAG='0'")){
                        s.setString(1,FUSION_REVIEW_STATUS_APPROVED);
                        s.setString(2,reviewedBy);
                        setNullableBigIntString(s, 3, reviewedById);
                        s.setString(4,remark);
                        s.setString(5,reviewedBy);
                        s.setString(6,id);
                        n=s.executeUpdate();
                    }

                    if(n <= 0){
                        c.rollback();
                        sendJson(exchange,404,buildMap("success",false,"message","主政策不存在"));
                        return;
                    }

                    try(PreparedStatement s=c.prepareStatement(
                        "UPDATE SYSDBA.X1_POLICY_DOCUMENT SET REVIEWED_BY_ID=? " +
                        "WHERE FUSION_POLICY_ID=? AND DEL_FLAG='0'")){
                        setNullableBigIntString(s, 1, reviewedById);
                        s.setString(2, id);
                        s.executeUpdate();
                    }

                    if(!sourcePolicyIds.isEmpty()){
                        String placeholders = repeatPlaceholders(sourcePolicyIds.size());

                        try(PreparedStatement metadataStmt=c.prepareStatement(
                            "UPDATE SYSDBA.POLICY_METADATA SET REVIEW_STATUS='已通过', UPDATED_BY=?, UPDATED_AT=CURRENT_TIMESTAMP " +
                            "WHERE POLICY_ID IN (" + placeholders + ") AND POLICY_ATTRIBUTE IS NOT NULL AND TRIM(POLICY_ATTRIBUTE) <> ''")){
                            int paramIndex = 1;
                            metadataStmt.setString(paramIndex++, reviewedBy);
                            for(String sourcePolicyId : sourcePolicyIds){
                                metadataStmt.setString(paramIndex++, sourcePolicyId);
                            }
                            metadataStmt.executeUpdate();
                        }

                        try(PreparedStatement attachmentStmt=c.prepareStatement(
                            "UPDATE SYSDBA.POLICY_ATTACHMENT SET ATTACH_REVIEW_STATUS='已通过', UPDATED_BY=?, UPDATE_AT=CURRENT_TIMESTAMP " +
                            "WHERE DEL_FLAG='0' AND TRIM(POLICY_ID) IN (" + placeholders + ") " +
                            "AND ATTACH_TYPE IS NOT NULL AND TRIM(ATTACH_TYPE) <> ''")){
                            int paramIndex = 1;
                            attachmentStmt.setString(paramIndex++, reviewedBy);
                            for(String sourcePolicyId : sourcePolicyIds){
                                attachmentStmt.setString(paramIndex++, sourcePolicyId);
                            }
                            attachmentStmt.executeUpdate();
                        }

                        try(PreparedStatement metadataReviewerStmt=c.prepareStatement(
                            "UPDATE SYSDBA.POLICY_METADATA SET REVIEWED_BY_ID=? " +
                            "WHERE POLICY_ID IN (" + placeholders + ") AND POLICY_ATTRIBUTE IS NOT NULL AND TRIM(POLICY_ATTRIBUTE) <> ''")){
                            int paramIndex = 1;
                            setNullableBigIntString(metadataReviewerStmt, paramIndex++, reviewedById);
                            for(String sourcePolicyId : sourcePolicyIds){
                                metadataReviewerStmt.setString(paramIndex++, sourcePolicyId);
                            }
                            metadataReviewerStmt.executeUpdate();
                        }

                        try(PreparedStatement attachmentReviewerStmt=c.prepareStatement(
                            "UPDATE SYSDBA.POLICY_ATTACHMENT SET REVIEWED_BY_ID=? " +
                            "WHERE DEL_FLAG='0' AND TRIM(POLICY_ID) IN (" + placeholders + ") " +
                            "AND ATTACH_TYPE IS NOT NULL AND TRIM(ATTACH_TYPE) <> ''")){
                            int paramIndex = 1;
                            setNullableBigIntString(attachmentReviewerStmt, paramIndex++, reviewedById);
                            for(String sourcePolicyId : sourcePolicyIds){
                                attachmentReviewerStmt.setString(paramIndex++, sourcePolicyId);
                            }
                            attachmentReviewerStmt.executeUpdate();
                        }
                    }

                    writeAuditMutation(c, data, "fusion-policy", "approve", "X1_FUSION_POLICY", id, id, fusionPolicyBeforeRow, loadFusionPolicyRow(c, id), "融合主政策审核通过");
                    for(String sourcePolicyId : sourcePolicyIds){
                        writeAuditMutation(c, data, "fusion-policy", "approve", "POLICY_METADATA", sourcePolicyId, sourcePolicyId,
                            metadataBeforeRows.get(sourcePolicyId), loadPolicyMetadataRow(c, sourcePolicyId), "融合主政策审核通过同步原政策审核状态");
                        List<Map<String, Object>> beforeAttachmentRows = attachmentBeforeRows.get(sourcePolicyId);
                        List<Map<String, Object>> afterAttachmentRows = loadPolicyAttachmentRowsByPolicyId(c, sourcePolicyId);
                        if ((beforeAttachmentRows != null && !beforeAttachmentRows.isEmpty()) || !afterAttachmentRows.isEmpty()) {
                            writeAuditMutation(c, data, "fusion-policy", "approve", "POLICY_ATTACHMENT", sourcePolicyId, sourcePolicyId,
                                beforeAttachmentRows, afterAttachmentRows, "融合主政策审核通过同步附件审核状态");
                        }
                    }
                    c.commit();
                    sendJson(exchange,200,buildMap("success",true,"message","审核通过"));
                } catch(SQLException approveError){
                    c.rollback();
                    throw approveError;
                } finally {
                    try { c.close(); } catch (SQLException ignored) {}
                }
            }catch(SQLException e){sendJson(exchange,500,buildMap("success",false,"message",e.getMessage()));}
        }
    }

    static class FusionPolicyReviewRevokeHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if (!"POST".equals(exchange.getRequestMethod())) { sendJson(exchange,405,buildMap("success",false,"message","只支持 POST 方法"));return; }
            Map<String,String> data=parseJsonObject(new String(readAllBytes(exchange.getRequestBody()),StandardCharsets.UTF_8));
            String id=data.getOrDefault("id","").trim();
            if(id.isEmpty()){sendJson(exchange,400,buildMap("success",false,"message","缺少主政策ID"));return;}
            Connection c = null;
            try{
                c = createDedicatedConnection();
                c.setAutoCommit(false);
                String operatorName = resolveAuditOperatorName(data);
                Map<String, Object> beforeRow = loadFusionPolicyRow(c, id);
                int n;
                try(PreparedStatement s=c.prepareStatement(
                    "UPDATE SYSDBA.X1_FUSION_POLICY SET MARK_REVIEW_STATUS=?,MARK_REVIEWED_AT=NULL,MARK_REVIEWED_BY=NULL,REVIEWED_BY_ID=NULL,MARK_REVIEW_REMARK=NULL,UPDATED_BY=?,UPDATED_AT=CURRENT_TIMESTAMP " +
                    "WHERE ID=? AND DEL_FLAG='0'")){
                    s.setString(1,FUSION_REVIEW_STATUS_PENDING);
                    s.setString(2,operatorName);
                    s.setString(3,id);
                    n=s.executeUpdate();
                }
                if(n<=0){
                    c.rollback();
                    sendJson(exchange,404,buildMap("success",false,"message","主政策不存在"));
                    return;
                }
                writeAuditMutation(c, data, "fusion-policy", "revoke", "X1_FUSION_POLICY", id, id, beforeRow, loadFusionPolicyRow(c, id), "融合主政策审核撤销");
                c.commit();
                sendJson(exchange,200,buildMap("success",true,"message","审核已撤销"));
            }catch(SQLException e){
                if(c!=null)try{c.rollback();}catch(SQLException ignored){}
                sendJson(exchange,500,buildMap("success",false,"message",e.getMessage()));
            } finally {
                if(c!=null)try{c.close();}catch(SQLException ignored){}
            }
        }
    }

    static class FusionStructureReviewApproveHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if (!"POST".equals(exchange.getRequestMethod())) { sendJson(exchange,405,buildMap("success",false,"message","只支持 POST 方法"));return; }
            Map<String,String> data=parseJsonObject(new String(readAllBytes(exchange.getRequestBody()),StandardCharsets.UTF_8));
            String id=data.getOrDefault("id","").trim();
            if(id.isEmpty()){sendJson(exchange,400,buildMap("success",false,"message","缺少主政策ID"));return;}
            Connection c = null;
            try{
                c = createDedicatedConnection();
                c.setAutoCommit(false);
                String reviewedBy = firstNonEmpty(data.get("reviewedBy"), resolveAuditOperatorName(data));
                String reviewedById = resolveAuditOperatorId(data);
                String remark = data.getOrDefault("remark","").trim();
                data.put("reviewedById", reviewedById);
                Map<String, Object> beforeRow = loadFusionPolicyRow(c, id);
                int n;
                try(PreparedStatement s=c.prepareStatement(
                    "UPDATE SYSDBA.X1_FUSION_POLICY SET STRUCTURE_REVIEW_STATUS=?,STRUCTURE_REVIEWED_AT=SYSTIMESTAMP,STRUCTURE_REVIEWED_BY=?,REVIEWED_BY_ID=?,STRUCTURE_REVIEW_REMARK=?,UPDATED_BY=?,UPDATED_AT=SYSTIMESTAMP " +
                    "WHERE ID=? AND DEL_FLAG='0'")){
                    s.setString(1, STRUCTURE_REVIEW_STATUS_APPROVED);
                    s.setString(2, reviewedBy);
                    setNullableBigIntString(s, 3, reviewedById);
                    s.setString(4, remark);
                    s.setString(5, reviewedBy);
                    s.setString(6, id);
                    n = s.executeUpdate();
                }
                if(n<=0){
                    c.rollback();
                    sendJson(exchange,404,buildMap("success",false,"message","主政策不存在"));
                    return;
                }
                try(PreparedStatement s=c.prepareStatement(
                    "UPDATE SYSDBA.X1_POLICY_DOCUMENT SET REVIEWED_BY_ID=? " +
                    "WHERE FUSION_POLICY_ID=? AND DEL_FLAG='0'")){
                    setNullableBigIntString(s, 1, reviewedById);
                    s.setString(2, id);
                    s.executeUpdate();
                }
                writeAuditMutation(c, data, "fusion-structure", "approve", "X1_FUSION_POLICY", id, id, beforeRow, loadFusionPolicyRow(c, id), "结构化信息审核通过");
                c.commit();
                sendJson(exchange,200,buildMap("success",true,"message","审核通过"));
            }catch(SQLException e){
                if(c!=null)try{c.rollback();}catch(SQLException ignored){}
                sendJson(exchange,500,buildMap("success",false,"message",e.getMessage()));
            } finally {
                if(c!=null)try{c.close();}catch(SQLException ignored){}
            }
        }
    }

    static class FusionStructureReviewRevokeHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if (!"POST".equals(exchange.getRequestMethod())) { sendJson(exchange,405,buildMap("success",false,"message","只支持 POST 方法"));return; }
            Map<String,String> data=parseJsonObject(new String(readAllBytes(exchange.getRequestBody()),StandardCharsets.UTF_8));
            String id=data.getOrDefault("id","").trim();
            if(id.isEmpty()){sendJson(exchange,400,buildMap("success",false,"message","缺少主政策ID"));return;}
            Connection c = null;
            try{
                c = createDedicatedConnection();
                c.setAutoCommit(false);
                String operatorName = resolveAuditOperatorName(data);
                Map<String, Object> beforeRow = loadFusionPolicyRow(c, id);
                int n;
                try(PreparedStatement s=c.prepareStatement(
                    "UPDATE SYSDBA.X1_FUSION_POLICY SET STRUCTURE_REVIEW_STATUS=?,STRUCTURE_REVIEWED_AT=NULL,STRUCTURE_REVIEWED_BY=NULL,STRUCTURE_REVIEW_REMARK=NULL,UPDATED_BY=?,UPDATED_AT=CURRENT_TIMESTAMP " +
                    "WHERE ID=? AND DEL_FLAG='0'")){
                    s.setString(1, STRUCTURE_REVIEW_STATUS_PENDING);
                    s.setString(2, operatorName);
                    s.setString(3, id);
                    n = s.executeUpdate();
                }
                if(n<=0){
                    c.rollback();
                    sendJson(exchange,404,buildMap("success",false,"message","主政策不存在"));
                    return;
                }
                writeAuditMutation(c, data, "fusion-structure", "revoke", "X1_FUSION_POLICY", id, id, beforeRow, loadFusionPolicyRow(c, id), "结构化信息审核撤销");
                c.commit();
                sendJson(exchange,200,buildMap("success",true,"message","审核已撤销"));
            }catch(SQLException e){
                if(c!=null)try{c.rollback();}catch(SQLException ignored){}
                sendJson(exchange,500,buildMap("success",false,"message",e.getMessage()));
            } finally {
                if(c!=null)try{c.close();}catch(SQLException ignored){}
            }
        }
    }

    static class FusionDocumentDeleteHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if (!"POST".equals(exchange.getRequestMethod())) { sendJson(exchange,405,buildMap("success",false,"message","只支持 POST 方法"));return; }
            Map<String,String> data=parseJsonObject(new String(readAllBytes(exchange.getRequestBody()),StandardCharsets.UTF_8));
            String id=data.getOrDefault("id","").trim();
            if(id.isEmpty()){sendJson(exchange,400,buildMap("success",false,"message","缺少关联政策ID"));return;}
            Connection c = null;
            try{
                c = createDedicatedConnection();
                c.setAutoCommit(false);
                String operatorName = resolveAuditOperatorName(data);
                String fusionId = queryFusionPolicyIdByDocumentId(c, id);
                Map<String, Object> beforeRow = loadFusionDocumentRow(c, id);
                int n;
                try(PreparedStatement s=c.prepareStatement("UPDATE SYSDBA.X1_POLICY_DOCUMENT SET DEL_FLAG='1',UPDATED_BY=?,UPDATED_AT=CURRENT_TIMESTAMP WHERE ID=? AND DEL_FLAG='0'")){
                    s.setString(1,operatorName);
                    s.setString(2,id);
                    n=s.executeUpdate();
                }
                if(n<=0){
                    c.rollback();
                    sendJson(exchange,404,buildMap("success",false,"message","关联政策不存在"));
                    return;
                }
                if(!fusionId.isEmpty()) resetFusionReviewStatus(c, fusionId);
                writeAuditMutation(c, data, "fusion-document", "delete", "X1_POLICY_DOCUMENT", id, fusionId, beforeRow, loadFusionDocumentRow(c, id), "融合关联文档删除");
                c.commit();
                sendJson(exchange,200,buildMap("success",true,"message","关联政策已删除"));
            }catch(SQLException e){
                if(c!=null)try{c.rollback();}catch(SQLException ignored){}
                sendJson(exchange,500,buildMap("success",false,"message",e.getMessage()));
            } finally {
                if(c!=null)try{c.close();}catch(SQLException ignored){}
            }
        }
    }

    static class FusionDocumentRestoreHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if (!"POST".equals(exchange.getRequestMethod())) { sendJson(exchange,405,buildMap("success",false,"message","只支持 POST 方法"));return; }
            Map<String,String> data=parseJsonObject(new String(readAllBytes(exchange.getRequestBody()),StandardCharsets.UTF_8));
            String id=data.getOrDefault("id","").trim();
            if(id.isEmpty()){sendJson(exchange,400,buildMap("success",false,"message","缺少关联政策ID"));return;}
            Connection c = null;
            try{
                c = createDedicatedConnection();
                c.setAutoCommit(false);
                String operatorName = resolveAuditOperatorName(data);
                String fusionId = queryFusionPolicyIdByDocumentId(c, id);
                Map<String, Object> beforeRow = loadFusionDocumentRow(c, id);
                int n;
                try(PreparedStatement s=c.prepareStatement("UPDATE SYSDBA.X1_POLICY_DOCUMENT SET DEL_FLAG='0',UPDATED_BY=?,UPDATED_AT=CURRENT_TIMESTAMP WHERE ID=? AND DEL_FLAG='1'")){
                    s.setString(1,operatorName);
                    s.setString(2,id);
                    n=s.executeUpdate();
                }
                if(n<=0){
                    c.rollback();
                    sendJson(exchange,404,buildMap("success",false,"message","关联政策不存在或未处于弃用状态"));
                    return;
                }
                if(!fusionId.isEmpty()) resetFusionReviewStatus(c, fusionId);
                writeAuditMutation(c, data, "fusion-document", "restore", "X1_POLICY_DOCUMENT", id, fusionId, beforeRow, loadFusionDocumentRow(c, id), "融合关联文档恢复");
                c.commit();
                sendJson(exchange,200,buildMap("success",true,"message","关联政策已取消弃用"));
            }catch(SQLException e){
                if(c!=null)try{c.rollback();}catch(SQLException ignored){}
                sendJson(exchange,500,buildMap("success",false,"message",e.getMessage()));
            } finally {
                if(c!=null)try{c.close();}catch(SQLException ignored){}
            }
        }
    }

    static class FusionDocumentMoveHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if (!"POST".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持 POST 方法"));
                return;
            }

            Map<String, String> data = parseJsonObject(new String(readAllBytes(exchange.getRequestBody()), StandardCharsets.UTF_8));
            String id = data.getOrDefault("id", "").trim();
            String targetFusionPolicyId = data.getOrDefault("targetFusionPolicyId", "").trim();
            if (id.isEmpty() || targetFusionPolicyId.isEmpty()) {
                sendJson(exchange, 400, buildMap("success", false, "message", "缺少关联政策ID或目标主政策ID"));
                return;
            }

            synchronized (FusionDocumentMoveHandler.class) {
                Connection c = null;
                try {
                    c = createDedicatedConnection();
                    c.setAutoCommit(false);
                    String operatorName = resolveAuditOperatorName(data);
                    Map<String, Object> beforeRow = loadFusionDocumentRow(c, id);

                    String sourceFusionPolicyId = "";
                    String sourcePolicyId = "";
                    String filePath = "";
                    String policyTitle = "";
                    try (PreparedStatement stmt = c.prepareStatement(
                            "SELECT FUSION_POLICY_ID,SOURCE_POLICY_ID,FILE_PATH,POLICY_TITLE " +
                            "FROM SYSDBA.X1_POLICY_DOCUMENT WHERE ID=? AND DEL_FLAG='0'")) {
                        stmt.setString(1, id);
                        try (ResultSet rs = stmt.executeQuery()) {
                            if (!rs.next()) {
                                c.rollback();
                                sendJson(exchange, 404, buildMap("success", false, "message", "关联政策不存在"));
                                return;
                            }
                            sourceFusionPolicyId = nullToEmpty(rs.getString("FUSION_POLICY_ID")).trim();
                            sourcePolicyId = nullToEmpty(rs.getString("SOURCE_POLICY_ID")).trim();
                            filePath = nullToEmpty(rs.getString("FILE_PATH")).trim();
                            policyTitle = nullToEmpty(rs.getString("POLICY_TITLE")).trim();
                        }
                    }

                    if (sourceFusionPolicyId.isEmpty()) {
                        c.rollback();
                        sendJson(exchange, 400, buildMap("success", false, "message", "当前关联政策未绑定主政策，不能迁移"));
                        return;
                    }
                    if (sourceFusionPolicyId.equals(targetFusionPolicyId)) {
                        c.rollback();
                        sendJson(exchange, 400, buildMap("success", false, "message", "目标主政策不能与当前主政策相同"));
                        return;
                    }

                    try (PreparedStatement stmt = c.prepareStatement(
                            "SELECT COUNT(*) FROM SYSDBA.X1_FUSION_POLICY WHERE ID=? AND DEL_FLAG='0'")) {
                        stmt.setString(1, targetFusionPolicyId);
                        try (ResultSet rs = stmt.executeQuery()) {
                            if (!rs.next() || rs.getInt(1) <= 0) {
                                c.rollback();
                                sendJson(exchange, 404, buildMap("success", false, "message", "目标主政策不存在"));
                                return;
                            }
                        }
                    }

                    if (!sourcePolicyId.isEmpty()) {
                        try (PreparedStatement stmt = c.prepareStatement(
                                "SELECT COUNT(*) FROM SYSDBA.X1_POLICY_DOCUMENT " +
                                "WHERE FUSION_POLICY_ID=? AND DEL_FLAG='0' AND ID<>? " +
                                "AND TRIM(NVL(SOURCE_POLICY_ID,''))=?")) {
                            stmt.setString(1, targetFusionPolicyId);
                            stmt.setString(2, id);
                            stmt.setString(3, sourcePolicyId);
                            try (ResultSet rs = stmt.executeQuery()) {
                                if (rs.next() && rs.getInt(1) > 0) {
                                    c.rollback();
                                    sendJson(exchange, 409, buildMap("success", false, "message", "目标主政策已存在同一关联政策，不能重复迁移"));
                                    return;
                                }
                            }
                        }
                    }

                    if (!filePath.isEmpty()) {
                        try (PreparedStatement stmt = c.prepareStatement(
                                "SELECT COUNT(*) FROM SYSDBA.X1_POLICY_DOCUMENT " +
                                "WHERE FUSION_POLICY_ID=? AND DEL_FLAG='0' AND ID<>? " +
                                "AND LOWER(TRIM(NVL(FILE_PATH,'')))=LOWER(?)")) {
                            stmt.setString(1, targetFusionPolicyId);
                            stmt.setString(2, id);
                            stmt.setString(3, filePath);
                            try (ResultSet rs = stmt.executeQuery()) {
                                if (rs.next() && rs.getInt(1) > 0) {
                                    c.rollback();
                                    sendJson(exchange, 409, buildMap("success", false, "message", "目标主政策已存在相同文件，不能重复迁移"));
                                    return;
                                }
                            }
                        }
                    }

                    int targetDisplayOrder = 1;
                    try (PreparedStatement stmt = c.prepareStatement(
                            "SELECT NVL(MAX(DISPLAY_ORDER),0)+1 FROM SYSDBA.X1_POLICY_DOCUMENT WHERE FUSION_POLICY_ID=? AND DEL_FLAG='0'")) {
                        stmt.setString(1, targetFusionPolicyId);
                        try (ResultSet rs = stmt.executeQuery()) {
                            if (rs.next()) targetDisplayOrder = rs.getInt(1);
                        }
                    }

                    int updated;
                    try (PreparedStatement stmt = c.prepareStatement(
                            "UPDATE SYSDBA.X1_POLICY_DOCUMENT SET FUSION_POLICY_ID=?,DISPLAY_ORDER=?,UPDATED_BY=?,UPDATED_AT=CURRENT_TIMESTAMP " +
                            "WHERE ID=? AND DEL_FLAG='0'")) {
                        stmt.setString(1, targetFusionPolicyId);
                        stmt.setInt(2, targetDisplayOrder);
                        stmt.setString(3, operatorName);
                        stmt.setString(4, id);
                        updated = stmt.executeUpdate();
                    }
                    if (updated <= 0) {
                        c.rollback();
                        sendJson(exchange, 404, buildMap("success", false, "message", "关联政策不存在或迁移失败"));
                        return;
                    }

                    resetFusionReviewStatus(c, sourceFusionPolicyId);
                    resetFusionReviewStatus(c, targetFusionPolicyId);
                    writeAuditMutation(c, data, "fusion-document", "move", "X1_POLICY_DOCUMENT", id, targetFusionPolicyId, beforeRow, loadFusionDocumentRow(c, id), "融合关联文档迁移到其他主政策");
                    c.commit();
                    sendJson(exchange, 200, buildMap(
                        "success", true,
                        "message", "关联政策迁移成功",
                        "data", buildMap(
                            "id", id,
                            "policyTitle", policyTitle,
                            "sourceFusionPolicyId", sourceFusionPolicyId,
                            "targetFusionPolicyId", targetFusionPolicyId
                        )
                    ));
                } catch (SQLException e) {
                    if (c != null) try { c.rollback(); } catch (SQLException ignored) {}
                    sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
                } finally {
                    if (c != null) try { c.close(); } catch (SQLException ignored) {}
                }
            }
        }
    }

    static class FusionDocumentUpdateHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if (!"POST".equals(exchange.getRequestMethod())) { sendJson(exchange,405,buildMap("success",false,"message","只支持 POST 方法"));return; }
            Map<String,String> data=parseJsonObject(new String(readAllBytes(exchange.getRequestBody()),StandardCharsets.UTF_8));
            String id=data.getOrDefault("id","").trim();
            String policyTitle=data.getOrDefault("policyTitle","").trim();
            String fileName=data.getOrDefault("fileName","").trim();
            if(id.isEmpty()){sendJson(exchange,400,buildMap("success",false,"message","缺少文档ID"));return;}
            if(policyTitle.isEmpty()&&fileName.isEmpty()){sendJson(exchange,400,buildMap("success",false,"message","没有需要更新的字段"));return;}
            Connection c = null;
            try{
                c = createDedicatedConnection();
                c.setAutoCommit(false);
                String operatorName = resolveAuditOperatorName(data);
                Map<String, Object> beforeRow = loadFusionDocumentRow(c, id);
                StringBuilder sql=new StringBuilder("UPDATE SYSDBA.X1_POLICY_DOCUMENT SET UPDATED_BY=?,UPDATED_AT=CURRENT_TIMESTAMP");
                List<String> params=new ArrayList<>();
                params.add(operatorName);
                if(!policyTitle.isEmpty()){sql.append(",POLICY_TITLE=?");params.add(policyTitle);}
                if(!fileName.isEmpty()){sql.append(",FILE_NAME=?");params.add(fileName);}
                sql.append(" WHERE ID=? AND DEL_FLAG='0'");
                params.add(id);
                String fusionId = queryFusionPolicyIdByDocumentId(c, id);
                int n;
                try(PreparedStatement s=c.prepareStatement(sql.toString())){
                    for(int i=0;i<params.size();i++)s.setString(i+1,params.get(i));
                    n=s.executeUpdate();
                }
                if(n<=0){
                    c.rollback();
                    sendJson(exchange,404,buildMap("success",false,"message","文档不存在"));
                    return;
                }
                if(!fusionId.isEmpty()) resetFusionReviewStatus(c, fusionId);
                writeAuditMutation(c, data, "fusion-document", "update", "X1_POLICY_DOCUMENT", id, fusionId, beforeRow, loadFusionDocumentRow(c, id), "融合关联文档信息更新");
                c.commit();
                sendJson(exchange,200,buildMap("success",true,"message","文档已更新"));
            } catch(SQLException e){
                if(c!=null)try{c.rollback();}catch(SQLException ignored){}
                sendJson(exchange,500,buildMap("success",false,"message",e.getMessage()));
            } finally {
                if(c!=null)try{c.close();}catch(SQLException ignored){}
            }
        }
    }

    static class FusionFuseHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if ("OPTIONS".equals(exchange.getRequestMethod())) { sendResponse(exchange,200,"{\"success\":true}"); return; }
            if (!"POST".equals(exchange.getRequestMethod())) { sendJson(exchange,405,buildMap("success",false,"message","只支持 POST 方法")); return; }
            Map<String,String> data=parseJsonObject(new String(readAllBytes(exchange.getRequestBody()),StandardCharsets.UTF_8));
            String fusionPolicyId=data.getOrDefault("fusionPolicyId","").trim();
            if(fusionPolicyId.isEmpty()){sendJson(exchange,400,buildMap("success",false,"message","缺少融合政策ID"));return;}
            try{
                ensureConnection();
                // 1. 获取融合政策基本信息
                Map<String,Object> result = new LinkedHashMap<>();
                try(PreparedStatement s=conn.prepareStatement("SELECT ID,SOURCE_POLICY_ID,DIRECTORY_TITLE,DIRECTORY_PATH,MAIN_FILE_TITLE,MAIN_FILE_PATH,STATUS,SORT_NO,UPDATED_AT FROM SYSDBA.X1_FUSION_POLICY WHERE ID=? AND DEL_FLAG='0'")){
                    s.setString(1,fusionPolicyId);
                    try(ResultSet rs=s.executeQuery()){
                        if(!rs.next()){sendJson(exchange,404,buildMap("success",false,"message","融合政策不存在"));return;}
                        result.put("id",rs.getString("ID"));
                        result.put("directoryTitle",nullToEmpty(rs.getString("DIRECTORY_TITLE")));
                        result.put("mainFileTitle",nullToEmpty(rs.getString("MAIN_FILE_TITLE")));
                        result.put("status",nullToEmpty(rs.getString("STATUS")));
                    }
                }
                // 2. 查询关联子文档
                List<Map<String,Object>> docList=new ArrayList<>();
                try(PreparedStatement s=conn.prepareStatement("SELECT ID,SOURCE_POLICY_ID,POLICY_ATTRIBUTE,POLICY_TITLE,FILE_NAME,FILE_PATH FROM SYSDBA.X1_POLICY_DOCUMENT WHERE FUSION_POLICY_ID=? AND DEL_FLAG='0' ORDER BY DISPLAY_ORDER,ID")){
                    s.setString(1,fusionPolicyId);
                    try(ResultSet rs=s.executeQuery()){
                        while(rs.next()){
                            Map<String,Object> doc=new LinkedHashMap<>();
                            String sourceId=nullToEmpty(rs.getString("SOURCE_POLICY_ID"));
                            doc.put("id",rs.getString("ID"));
                            doc.put("policyTitle",nullToEmpty(rs.getString("POLICY_TITLE")));
                            doc.put("fileName",nullToEmpty(rs.getString("FILE_NAME")));
                            doc.put("policyAttribute",normalizeFusionDocumentType(rs.getString("POLICY_ATTRIBUTE")));
                            doc.put("sourcePolicyId",sourceId);
                            // 3. 如果有源政策ID，查 APPLICATION_CONDITION
                            List<Map<String,Object>> conditions=new ArrayList<>();
                            if(!sourceId.isEmpty()&&sourceId.matches("\\d+")){
                                try(PreparedStatement cs=conn.prepareStatement(
                                    "SELECT ID,APPLICABLE_TARGET,APPLICATION_REQUIREMENT,PROMPT_CONTENT," +
                                    "APPLICATION_START_TIME,APPLICATION_END_TIME,APPLICATION_TIME,ANNOUNCEMENT_DATE " +
                                    "FROM SYSDBA.APPLICATION_CONDITION WHERE POLICY_ID=?")){
                                    cs.setString(1,sourceId);
                                    try(ResultSet crs=cs.executeQuery()){
                                        while(crs.next()){
                                            Map<String,Object> cond=new LinkedHashMap<>();
                                            String condId=crs.getString("ID");
                                            String applicationRequirement = nullToEmpty(crs.getString("APPLICATION_REQUIREMENT"));
                                            String applicationInstruction = nullToEmpty(crs.getString("PROMPT_CONTENT"));
                                            cond.put("id",condId);
                                            cond.put("applicableTarget",nullToEmpty(crs.getString("APPLICABLE_TARGET")));
                                            cond.put("applicationInstruction",applicationInstruction);
                                            cond.put("applicationRequirement",applicationRequirement);
                                            cond.put("applicationStartTime",nullToEmpty(crs.getString("APPLICATION_START_TIME")));
                                            cond.put("applicationEndTime",nullToEmpty(crs.getString("APPLICATION_END_TIME")));
                                            // 4. 查 CONDITION_CLUSTER
                                            try(PreparedStatement cls=conn.prepareStatement("SELECT * FROM SYSDBA.CONDITION_CLUSTER WHERE CONDITION_ID=?")){
                                                cls.setString(1,condId);
                                                try(ResultSet clrs=cls.executeQuery()){
                                                    if(clrs.next()){
                                                        Map<String,Object> cluster=new LinkedHashMap<>();
                                                        cluster.put("basicUnitName",nullToEmpty(clrs.getString("BASIC_UNIT_NAME")));
                                                        cluster.put("basicCreditCode",nullToEmpty(clrs.getString("BASIC_CREDIT_CODE")));
                                                        cluster.put("basicRegisterDate",nullToEmpty(clrs.getString("BASIC_REGISTER_DATE")));
                                                        cluster.put("basicEnterpriseNature",nullToEmpty(clrs.getString("BASIC_ENTERPRISE_NATURE")));
                                                        cluster.put("basicEnterpriseTypeValue",nullToEmpty(clrs.getString("BASIC_ENTERPRISE_TYPE_VALUE")));
                                                        cluster.put("basicLegalRepresentative",nullToEmpty(clrs.getString("BASIC_LEGAL_REPRESENTATIVE")));
                                                        cluster.put("basicInstitutionNature",nullToEmpty(clrs.getString("BASIC_INSTITUTION_NATURE")));
                                                        cluster.put("regAddressValue",nullToEmpty(clrs.getString("REG_ADDRESS_VALUE")));
                                                        cluster.put("regRegisterDate",nullToEmpty(clrs.getString("REG_REGISTER_DATE")));
                                                        cluster.put("regActualAddress",nullToEmpty(clrs.getString("REG_ACTUAL_ADDRESS")));
                                                        cluster.put("regTerritorialInfo",nullToEmpty(clrs.getString("REG_TERRITORIAL_INFO")));
                                                        cluster.put("regTaxTerritory",nullToEmpty(clrs.getString("REG_TAX_TERRITORY")));
                                                        cluster.put("bizSubjectQualification",nullToEmpty(clrs.getString("BIZ_SUBJECT_QUALIFICATION")));
                                                        cluster.put("bizUnitType",nullToEmpty(clrs.getString("BIZ_UNIT_TYPE")));
                                                        cluster.put("bizOperationStatus",nullToEmpty(clrs.getString("BIZ_OPERATION_STATUS")));
                                                        cluster.put("bizScopeValue",nullToEmpty(clrs.getString("BIZ_SCOPE_VALUE")));
                                                        // 5. 查各细分表
                                                        String[] detailTables={"CORE_TEAM","INNOVATION_DEVELOPMENT","INNOVATION_INVESTMENT","INNOVATION_OUTPUT","INNOVATION_IMPACT","MAIN_PRODUCT","MAIN_TECHNOLOGY"};
                                                        for(String tbl:detailTables){
                                                            try(PreparedStatement ds=conn.prepareStatement("SELECT * FROM SYSDBA."+tbl+" WHERE CONDITION_ID=?")){
                                                                ds.setString(1,condId);
                                                                try(ResultSet drs=ds.executeQuery()){
                                                                    if(drs.next()){
                                                                        Map<String,Object> detail=new LinkedHashMap<>();
                                                                        int colCount=drs.getMetaData().getColumnCount();
                                                                        for(int ci=2;ci<=colCount;ci++){
                                                                            String colName=drs.getMetaData().getColumnLabel(ci);
                                                                            String val=drs.getString(ci);
                                                                            if(val!=null&&!val.trim().isEmpty()){
                                                                                detail.put(toCamelCase(colName),val.trim());
                                                                            }
                                                                        }
                                                                        if(!detail.isEmpty()) cluster.put(toCamelCase(tbl),detail);
                                                                    }
                                                                }
                                                            }
                                                        }
                                                        cond.put("cluster",cluster);
                                                    }
                                                }
                                            }
                                            conditions.add(cond);
                                        }
                                    }
                                }
                            }
                            doc.put("conditions",conditions);
                            docList.add(doc);
                        }
                    }
                }
                result.put("documents",docList);
                sendJson(exchange,200,buildMap("success",true,"data",result));
            }catch(SQLException e){
                sendJson(exchange,500,buildMap("success",false,"message",e.getMessage()));
            }
        }

        private String toCamelCase(String s){
            if(s==null||s.isEmpty()) return s;
            StringBuilder sb=new StringBuilder();
            boolean upper=false;
            for(int i=0;i<s.length();i++){
                char c=s.charAt(i);
                if(c=='_'){upper=true;continue;}
                sb.append(upper?Character.toUpperCase(c):Character.toLowerCase(c));
                upper=false;
            }
            return sb.toString();
        }
    }

    // ==================== 融合政策详情接口（查询主政策结构化数据） ====================

    static class FusionDetailHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if ("OPTIONS".equals(exchange.getRequestMethod())) { sendResponse(exchange,200,"{\"success\":true}"); return; }
            if (!"GET".equals(exchange.getRequestMethod())) { sendJson(exchange,405,buildMap("success",false,"message","只支持 GET 方法")); return; }
            Map<String,String> params=parseQueryParams(exchange.getRequestURI().getQuery());
            String fusionPolicyId=params.getOrDefault("fusionPolicyId","").trim();
            String documentDeleteStatus=params.getOrDefault("documentDeleteStatus","").trim();
            if(fusionPolicyId.isEmpty()){sendJson(exchange,400,buildMap("success",false,"message","缺少融合政策ID"));return;}
            try{
                ensureConnection();
                long fusionDetailStageStartedAt = System.currentTimeMillis();
                Map<String,Object> result=new LinkedHashMap<>();

                // 1) 融合政策基本信息
                String configuredSourcePolicyId="";
                String mainFileTitle="";
                String mainFilePath="";
                try(PreparedStatement s=conn.prepareStatement(
                    "SELECT ID,SOURCE_POLICY_ID,DIRECTORY_TITLE,MAIN_FILE_TITLE,MAIN_FILE_PATH,STATUS," +
                    "MARK_REVIEW_STATUS,MARK_REVIEWED_AT,MARK_REVIEWED_BY,MARK_REVIEW_REMARK," +
                    "STRUCTURE_REVIEW_STATUS,STRUCTURE_REVIEWED_AT,STRUCTURE_REVIEWED_BY,STRUCTURE_REVIEW_REMARK," +
                    "RULE_TABLE_JSON " +
                    "FROM SYSDBA.X1_FUSION_POLICY WHERE ID=?")){
                    s.setString(1,fusionPolicyId);
                    try(ResultSet rs=s.executeQuery()){
                        if(!rs.next()){sendJson(exchange,404,buildMap("success",false,"message","融合政策不存在"));return;}
                        result.put("id",rs.getString("ID"));
                        configuredSourcePolicyId=nullToEmpty(rs.getString("SOURCE_POLICY_ID")).trim();
                        mainFileTitle=nullToEmpty(rs.getString("MAIN_FILE_TITLE")).trim();
                        mainFilePath=nullToEmpty(rs.getString("MAIN_FILE_PATH")).trim();
                        result.put("sourcePolicyId",configuredSourcePolicyId);
                        result.put("directoryTitle",nullToEmpty(rs.getString("DIRECTORY_TITLE")));
                        result.put("mainFileTitle",mainFileTitle);
                        result.put("status",nullToEmpty(rs.getString("STATUS")));
                        result.put("reviewStatus",nullToEmpty(rs.getString("MARK_REVIEW_STATUS")));
                        result.put("reviewedAt",nullToEmpty(rs.getString("MARK_REVIEWED_AT")));
                        result.put("reviewedBy",nullToEmpty(rs.getString("MARK_REVIEWED_BY")));
                        result.put("reviewRemark",nullToEmpty(rs.getString("MARK_REVIEW_REMARK")));
                        result.put("structureReviewStatus",nullToEmpty(rs.getString("STRUCTURE_REVIEW_STATUS")));
                        result.put("structureReviewedAt",nullToEmpty(rs.getString("STRUCTURE_REVIEWED_AT")));
                        result.put("structureReviewedBy",nullToEmpty(rs.getString("STRUCTURE_REVIEWED_BY")));
                        result.put("structureReviewRemark",nullToEmpty(rs.getString("STRUCTURE_REVIEW_REMARK")));
                        String ruleTableJsonRaw = rs.getString("RULE_TABLE_JSON");
                        if(ruleTableJsonRaw != null && !ruleTableJsonRaw.trim().isEmpty()){
                            try{
                                result.put("ruleTableJson", decodeLiteralUnicodeEscapesInJsonValue(parseNestedJson(ruleTableJsonRaw)));
                            }catch(Exception parseErr){
                                result.put("ruleTableJsonRaw", ruleTableJsonRaw);
                            }
                        }
                        initializeFusionHierarchyFields(result);
                    }
                }
                logFusionDetailStage(fusionPolicyId,"融合政策基础信息",fusionDetailStageStartedAt);
                fusionDetailStageStartedAt = System.currentTimeMillis();

                try {
                    List<Map<String,Object>> directParentPolicies = queryFusionDirectParentPolicies(conn, fusionPolicyId);
                    List<Map<String,Object>> ancestorPolicies = queryFusionAncestorPolicies(conn, fusionPolicyId);
                    Map<String,Object> parentPolicy = queryFusionParentPolicy(conn, fusionPolicyId);
                    List<Map<String,Object>> childPolicies = queryFusionChildPolicies(conn, fusionPolicyId);
                    List<Map<String,Object>> descendantPolicies = queryFusionDescendantPolicies(conn, fusionPolicyId);
                    result.put("directParentPolicies", directParentPolicies);
                    result.put("directParentPolicyCount", directParentPolicies.size());
                    result.put("ancestorPolicies", ancestorPolicies);
                    result.put("ancestorPolicyCount", ancestorPolicies.size());
                    result.put("parentPolicy", parentPolicy);
                    result.put("parentPolicyId", nullToEmpty(String.valueOf(parentPolicy.getOrDefault("id", ""))).trim());
                    result.put("parentPolicyTitle", joinFusionPolicyTitles(directParentPolicies));
                    result.put("parentPolicyReviewStatus", nullToEmpty(String.valueOf(parentPolicy.getOrDefault("reviewStatus", ""))).trim());
                    result.put("parentPolicyReviewedAt", nullToEmpty(String.valueOf(parentPolicy.getOrDefault("reviewedAt", ""))).trim());
                    result.put("childPolicies", childPolicies);
                    result.put("childPolicyCount", childPolicies.size());
                    result.put("descendantPolicies", descendantPolicies);
                    result.put("descendantPolicyCount", descendantPolicies.size());
                } catch (SQLException hierarchyError) {
                    System.err.println("[Fusion Detail Hierarchy Error] " + hierarchyError.getMessage());
                }

                // 2) 关联子文档
                List<Map<String,Object>> documents=new ArrayList<>();
                String documentDeleteWhere = buildFusionDetailDocumentDeleteWhere(documentDeleteStatus);
                try(PreparedStatement s=conn.prepareStatement(
                    "SELECT d.ID,d.SOURCE_POLICY_ID,d.DEL_FLAG," +
                    "d.POLICY_ATTRIBUTE AS RAW_POLICY_ATTRIBUTE,d.FILE_PATH AS RAW_FILE_PATH,d.FILE_SUFFIX AS RAW_FILE_SUFFIX," +
                    "(SELECT MAX(TRIM(m.POLICY_ATTRIBUTE)) FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID=d.SOURCE_POLICY_ID) AS METADATA_POLICY_ATTRIBUTE," +
                    "COALESCE((SELECT MAX(TRIM(m.POLICY_ATTRIBUTE)) FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID=d.SOURCE_POLICY_ID), d.POLICY_ATTRIBUTE) AS POLICY_ATTRIBUTE," +
                    "CASE WHEN EXISTS (SELECT 1 FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID=d.SOURCE_POLICY_ID AND m.REVIEW_STATUS='已通过') " +
                    "THEN '已通过' ELSE (SELECT MAX(TRIM(m.REVIEW_STATUS)) FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID=d.SOURCE_POLICY_ID) END AS REVIEW_STATUS," +
                    "COALESCE((SELECT NULLIF(TRIM(p.POLICY_TITLE), '') FROM SYSDBA.POLICY p WHERE CAST(p.ID AS VARCHAR(50))=d.SOURCE_POLICY_ID AND ROWNUM=1), d.POLICY_TITLE) AS POLICY_TITLE," +
                    "COALESCE((SELECT p.FILE_NAME FROM SYSDBA.POLICY p WHERE CAST(p.ID AS VARCHAR(50))=d.SOURCE_POLICY_ID AND ROWNUM=1), d.FILE_NAME) AS FILE_NAME," +
                    "COALESCE((SELECT o.FILE_PATH FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=d.SOURCE_POLICY_ID AND ROWNUM=1), d.FILE_PATH) AS FILE_PATH," +
                    "d.FILE_SUFFIX,d.DISPLAY_ORDER," +
                    "COALESCE((SELECT o.PUBLISH_DATE FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=d.SOURCE_POLICY_ID AND ROWNUM=1), " +
                    "(SELECT b.PUBLISH_DATE FROM SYSDBA.X1_POLICY_BASE_INFO b WHERE b.POLICY_DOCUMENT_ID=d.ID AND ROWNUM=1)) AS PUBLISH_DATE," +
                    "COALESCE((SELECT o.POLICY_CODE FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=d.SOURCE_POLICY_ID AND ROWNUM=1), " +
                    "(SELECT b.POLICY_DOCUMENT_NO FROM SYSDBA.X1_POLICY_BASE_INFO b WHERE b.POLICY_DOCUMENT_ID=d.ID AND ROWNUM=1)) AS POLICY_DOCUMENT_NO," +
                    "COALESCE((SELECT MAX(TRIM(m.ISSUING_DEPARTMENT)) FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID=d.SOURCE_POLICY_ID), " +
                    "(SELECT o.ISSUE_DEPARTMENT FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=d.SOURCE_POLICY_ID AND ROWNUM=1), " +
                    "(SELECT b.ISSUING_DEPARTMENT FROM SYSDBA.X1_POLICY_BASE_INFO b WHERE b.POLICY_DOCUMENT_ID=d.ID AND ROWNUM=1)) AS ISSUING_DEPARTMENT," +
                    "(SELECT b.POLICY_LEVEL FROM SYSDBA.X1_POLICY_BASE_INFO b WHERE b.POLICY_DOCUMENT_ID=d.ID AND ROWNUM=1) POLICY_LEVEL," +
                    "(SELECT b.ORIGINAL_URL FROM SYSDBA.X1_POLICY_BASE_INFO b WHERE b.POLICY_DOCUMENT_ID=d.ID AND ROWNUM=1) ORIGINAL_URL " +
                    "FROM SYSDBA.X1_POLICY_DOCUMENT d WHERE d.FUSION_POLICY_ID=? " + documentDeleteWhere + " AND d.SOURCE_POLICY_ID IS NOT NULL AND TRIM(d.SOURCE_POLICY_ID) <> '' ORDER BY d.DISPLAY_ORDER,d.ID")){
                    s.setString(1,fusionPolicyId);
                    try(ResultSet rs=s.executeQuery()){
                        Set<String> dedupKeys=new LinkedHashSet<>();
                        while(rs.next()){
                            String rawPolicyAttribute = nullToEmpty(rs.getString("RAW_POLICY_ATTRIBUTE"));
                            String rawFilePath = nullToEmpty(rs.getString("RAW_FILE_PATH"));
                            String rawFileSuffix = nullToEmpty(rs.getString("RAW_FILE_SUFFIX"));
                            if (isAttachmentLikeFusionDocument(rawPolicyAttribute, rawFilePath, rawFileSuffix)) {
                                continue;
                            }
                            String sourcePolicyId = nullToEmpty(rs.getString("SOURCE_POLICY_ID"));
                            String displayFilePath = nullToEmpty(rs.getString("FILE_PATH"));
                            String docId = rs.getString("ID");
                            String dedupKey = buildFusionDocumentDedupKey(sourcePolicyId, rawFilePath, displayFilePath, docId);
                            if (!dedupKeys.add(dedupKey)) {
                                continue;
                            }
                            String metadataPolicyAttribute = nullToEmpty(rs.getString("METADATA_POLICY_ATTRIBUTE"));
                            Map<String,Object> doc=new LinkedHashMap<>();
                            doc.put("id",docId);
                            doc.put("sourcePolicyId",sourcePolicyId);
                            doc.put("delFlag", nullToEmpty(rs.getString("DEL_FLAG")));
                            doc.put("deleted", "1".equals(nullToEmpty(rs.getString("DEL_FLAG")).trim()));
                            doc.put("policyAttribute",normalizeFusionDocumentType(rs.getString("POLICY_ATTRIBUTE")));
                            doc.put("policyAttributePersisted", !metadataPolicyAttribute.isEmpty());
                            doc.put("reviewStatus",nullToEmpty(rs.getString("REVIEW_STATUS")));
                            doc.put("policyTitle",nullToEmpty(rs.getString("POLICY_TITLE")));
                            doc.put("fileName",nullToEmpty(rs.getString("FILE_NAME")));
                            doc.put("filePath",displayFilePath);
                            doc.put("fileSuffix",nullToEmpty(rs.getString("FILE_SUFFIX")));
                            doc.put("displayOrder",nullToEmpty(rs.getString("DISPLAY_ORDER")));
                            doc.put("publishDate",nullToEmpty(rs.getString("PUBLISH_DATE")));
                            doc.put("documentNo",nullToEmpty(rs.getString("POLICY_DOCUMENT_NO")));
                            doc.put("issuingDepartment",nullToEmpty(rs.getString("ISSUING_DEPARTMENT")));
                            doc.put("policyLevel",nullToEmpty(rs.getString("POLICY_LEVEL")));
                            doc.put("originalUrl",nullToEmpty(rs.getString("ORIGINAL_URL")));
                            documents.add(doc);
                        }
                    }
                }catch(SQLException e){/* 子文档查询失败不影响整体 */}
                populateDocumentAttachments(documents);
                result.put("documents",documents);
                logFusionDetailStage(fusionPolicyId,"关联文件与附件",fusionDetailStageStartedAt);
                fusionDetailStageStartedAt = System.currentTimeMillis();

                // 3) 主政策信息
                // 融合目录下的 SOURCE_POLICY_ID 只用于定位「关联文件」，不是融合后的结构化数据。
                // 不能在没有融合结构化结果时，从任意一条关联文件回退查询并展示它的字段。
                // 当前的融合结构化结果尚无独立存储记录，因此返回空字段；后续只能从专属的融合结构化记录中填充这些数据。
                Map<String,Object> policyInfo=new LinkedHashMap<>();
                String sourcePolicyId=configuredSourcePolicyId;
                result.put("sourcePolicyId",sourcePolicyId);
                result.put("hasFusionStructuredData",false);
                Map<String, String> sceneUsageLookup = Collections.emptyMap();
                Map<String, String> applicableScopeLookup = Collections.emptyMap();
                Map<String, String> industryLookup = Collections.emptyMap();
                Map<String, String> applicableTargetLookup = Collections.emptyMap();
                Map<String, String> supportTypeLookup = Collections.emptyMap();
                Map<String, String> keywordLookup = Collections.emptyMap();
                if(!sourcePolicyId.isEmpty()){
                    sceneUsageLookup = loadPolicyUsageHierarchyLookup();
                    applicableScopeLookup = loadRuleOptionHierarchyLookup("scope_level");
                    industryLookup = loadIndustryHierarchyLookup();
                    applicableTargetLookup = loadRuleOptionHierarchyLookup("applicant_type");
                    supportTypeLookup = loadRuleOptionHierarchyLookup("support_type");
                    keywordLookup = loadKeywordHierarchyLookup();

                    // 3a) POLICY 表 — standalone 导入器直接写入的主记录
                    try(PreparedStatement s=conn.prepareStatement(
                        "SELECT POLICY_TITLE,ABSTRACT,CONTACT_INFO,APPLICATION_VOLUME FROM SYSDBA.POLICY WHERE ID=?")){
                        s.setString(1,sourcePolicyId);
                        try(ResultSet rs=s.executeQuery()){
                            if(rs.next()){
                                policyInfo.put("policyTitle",nullToEmpty(rs.getString("POLICY_TITLE")));
                                policyInfo.put("summary",nullToEmpty(rs.getString("ABSTRACT")));
                                policyInfo.put("contactInfo",nullToEmpty(rs.getString("CONTACT_INFO")));
                                policyInfo.put("applicationVolume",nullToEmpty(rs.getString("APPLICATION_VOLUME")));
                            }
                        }
                    }catch(SQLException e){logFusionDetailQueryError("POLICY",e);}
                    // 3b) POLICY_METADATA 表 — standalone 导入器写入的结构化元数据
                    try(PreparedStatement s=conn.prepareStatement(
                        "SELECT POLICY_ATTRIBUTE,POLICY_ORIGINAL_TITLE,SCENE_USAGE,APPLICABLE_SCOPE,INDUSTRY_FIELD," +
                        "EFFECTIVE_START_DATE,EFFECTIVE_END_DATE,EFFECTIVE_DATE," +
                        "ISSUING_DEPARTMENT,POLICY_TITLE_MAIN,REMARK " +
                        "FROM SYSDBA.POLICY_METADATA WHERE POLICY_ID=?")){
                        s.setString(1,sourcePolicyId);
                        try(ResultSet rs=s.executeQuery()){
                            if(rs.next()){
                                String effectiveStartDate = nullToEmpty(rs.getString("EFFECTIVE_START_DATE"));
                                String effectiveEndDate = nullToEmpty(rs.getString("EFFECTIVE_END_DATE"));
                                policyInfo.put("policyAttribute",normalizeFusionDocumentType(rs.getString("POLICY_ATTRIBUTE")));
                                policyInfo.put("policyOriginalTitle",nullToEmpty(rs.getString("POLICY_ORIGINAL_TITLE")));
                                policyInfo.put("sceneUsage",nullToEmpty(rs.getString("SCENE_USAGE")));
                                policyInfo.put("sceneUsagePath",expandHierarchyText(rs.getString("SCENE_USAGE"),sceneUsageLookup));
                                policyInfo.put("applicableScope",nullToEmpty(rs.getString("APPLICABLE_SCOPE")));
                                policyInfo.put("applicableScopePath",expandHierarchyText(rs.getString("APPLICABLE_SCOPE"),applicableScopeLookup));
                                policyInfo.put("industryField",nullToEmpty(rs.getString("INDUSTRY_FIELD")));
                                policyInfo.put("industryFieldPath",expandHierarchyText(rs.getString("INDUSTRY_FIELD"),industryLookup));
                                policyInfo.put("effectiveStartDate",effectiveStartDate);
                                policyInfo.put("effectiveEndDate",effectiveEndDate);
                                putIfNotEmpty(policyInfo,"issuingDepartment",rs.getString("ISSUING_DEPARTMENT"),true);
                                putIfNotEmpty(policyInfo,"policyTitle",rs.getString("POLICY_TITLE_MAIN"),false);
                                putIfNotEmpty(policyInfo,"policyCode",rs.getString("REMARK"),true);
                            }
                        }
                    }catch(SQLException e){logFusionDetailQueryError("POLICY_METADATA",e);}
                    // 3c) POLICY_ORIGINAL_V2 表 — 兼容原有政策库字段
                    try(PreparedStatement s=conn.prepareStatement(
                        "SELECT POLICY_TITLE,POLICY_CODE,PUBLISH_DATE,ISSUE_DEPARTMENT,FILE_PATH " +
                        "FROM SYSDBA.POLICY_ORIGINAL_V2 WHERE POLICY_ID=? AND ROWNUM=1")){
                        s.setString(1,sourcePolicyId);
                        try(ResultSet rs=s.executeQuery()){
                            if(rs.next()){
                                putIfNotEmpty(policyInfo,"policyTitle",rs.getString("POLICY_TITLE"),false);
                                putIfNotEmpty(policyInfo,"policyCode",rs.getString("POLICY_CODE"),false);
                                putIfNotEmpty(policyInfo,"publishDate",rs.getString("PUBLISH_DATE"),true);
                                putIfNotEmpty(policyInfo,"issuingDepartment",rs.getString("ISSUE_DEPARTMENT"),false);
                                putIfNotEmpty(policyInfo,"filePath",rs.getString("FILE_PATH"),true);
                            }
                        }
                    }catch(SQLException e){logFusionDetailQueryError("POLICY_ORIGINAL_V2",e);}
                }
                result.put("policyInfo",policyInfo);
                logFusionDetailStage(fusionPolicyId,"主政策基础信息与层级路径",fusionDetailStageStartedAt);
                fusionDetailStageStartedAt = System.currentTimeMillis();

                // 3d) 政策关键词 — 从 POLICY_KEYWORD 表查询
                List<Map<String,Object>> keywords=new ArrayList<>();
                if(!sourcePolicyId.isEmpty()){
                    try(PreparedStatement s=conn.prepareStatement(
                        "SELECT ID,KEYWORD FROM SYSDBA.POLICY_KEYWORD WHERE POLICY_ID=?")){
                        s.setString(1,sourcePolicyId);
                        try(ResultSet rs=s.executeQuery()){
                            while(rs.next()){
                                Map<String,Object> kw=new LinkedHashMap<>();
                                String keywordText = nullToEmpty(rs.getString("KEYWORD"));
                                kw.put("id",rs.getString("ID"));
                                kw.put("text",keywordText);
                                kw.put("pathText",expandHierarchyText(keywordText,keywordLookup));
                                keywords.add(kw);
                            }
                        }
                    }catch(SQLException e){logFusionDetailQueryError("POLICY_KEYWORD",e);}
                }
                result.put("keywords",keywords);
                logFusionDetailStage(fusionPolicyId,"政策关键词",fusionDetailStageStartedAt);
                fusionDetailStageStartedAt = System.currentTimeMillis();

                // 4) 申报条件 — 只查主政策（与支持方式/申报材料保持一致）
                List<Map<String,Object>> conditions=new ArrayList<>();
                if(!sourcePolicyId.isEmpty()){
                    try(PreparedStatement s=conn.prepareStatement(
                        "SELECT ID,APPLICABLE_TARGET,APPLICATION_REQUIREMENT,PROMPT_CONTENT," +
                        "APPLICATION_START_TIME,APPLICATION_END_TIME,APPLICATION_TIME,ANNOUNCEMENT_DATE " +
                        "FROM SYSDBA.APPLICATION_CONDITION WHERE POLICY_ID=?")){
                        s.setString(1,sourcePolicyId);
                        try(ResultSet rs=s.executeQuery()){
                            while(rs.next()){
                                Map<String,Object> cond=new LinkedHashMap<>();
                                String applicationRequirement = nullToEmpty(rs.getString("APPLICATION_REQUIREMENT"));
                                String applicationInstruction = nullToEmpty(rs.getString("PROMPT_CONTENT"));
                                String applicableTarget = nullToEmpty(rs.getString("APPLICABLE_TARGET"));
                                cond.put("id",rs.getString("ID"));
                                cond.put("applicableTarget",applicableTarget);
                                cond.put("applicableTargetPath",expandHierarchyText(applicableTarget,applicableTargetLookup));
                                cond.put("applicationInstruction",applicationInstruction);
                                cond.put("applicationRequirement",applicationRequirement);
                                cond.put("applicationStartTime",nullToEmpty(rs.getString("APPLICATION_START_TIME")));
                                cond.put("applicationEndTime",nullToEmpty(rs.getString("APPLICATION_END_TIME")));
                                cond.put("announcementDate",nullToEmpty(rs.getString("ANNOUNCEMENT_DATE")));
                                conditions.add(cond);
                            }
                        }
                    }catch(SQLException e){logFusionDetailQueryError("APPLICATION_CONDITION",e);}
                }
                populateConditionClusters(conditions);
                result.put("conditions",conditions);
                logFusionDetailStage(fusionPolicyId,"申报条件与结构化簇",fusionDetailStageStartedAt);
                fusionDetailStageStartedAt = System.currentTimeMillis();

                // 5) 支持方式
                List<Map<String,Object>> supportMethods=new ArrayList<>();
                if(!sourcePolicyId.isEmpty()){
                    try(PreparedStatement s=conn.prepareStatement(
                        "SELECT ID,SUPPORT_TYPE,SUPPORT_CONTENT_DESC,SUPPORT_AMOUNT,MAX_SUPPORT_AMOUNT FROM SYSDBA.SUPPORT_METHOD WHERE POLICY_ID=?")){
                        s.setString(1,sourcePolicyId);
                        try(ResultSet rs=s.executeQuery()){
                            while(rs.next()){
                                Map<String,Object> sm=new LinkedHashMap<>();
                                String supportType = nullToEmpty(rs.getString("SUPPORT_TYPE"));
                                sm.put("id",rs.getString("ID"));
                                sm.put("supportType",supportType);
                                sm.put("supportTypePath",expandHierarchyText(supportType,supportTypeLookup));
                                sm.put("contentDesc",nullToEmpty(rs.getString("SUPPORT_CONTENT_DESC")));
                                sm.put("amount",nullToEmpty(rs.getString("SUPPORT_AMOUNT")));
                                sm.put("maxAmount",nullToEmpty(rs.getString("MAX_SUPPORT_AMOUNT")));
                                supportMethods.add(sm);
                            }
                        }
                    }catch(SQLException e){logFusionDetailQueryError("SUPPORT_METHOD",e);}
                }
                result.put("supportMethods",supportMethods);

                // 6) 申报材料及方式 — 列名基于 TSV: APPLICATION_MATERIALS, APPLICATION_METHOD, APPLICATION_URL
                List<Map<String,Object>> materials=new ArrayList<>();
                if(!sourcePolicyId.isEmpty()){
                    try(PreparedStatement s=conn.prepareStatement(
                        "SELECT ID,APPLICATION_MATERIALS,APPLICATION_METHOD,APPLICATION_URL FROM SYSDBA.APPLICATION_MATERIAL WHERE POLICY_ID=?")){
                        s.setString(1,sourcePolicyId);
                        try(ResultSet rs=s.executeQuery()){
                            while(rs.next()){
                                Map<String,Object> mat=new LinkedHashMap<>();
                                mat.put("id",rs.getString("ID"));
                                mat.put("materials",nullToEmpty(rs.getString("APPLICATION_MATERIALS")));
                                mat.put("method",nullToEmpty(rs.getString("APPLICATION_METHOD")));
                                mat.put("url",nullToEmpty(rs.getString("APPLICATION_URL")));
                                materials.add(mat);
                            }
                        }
                    }catch(SQLException e){logFusionDetailQueryError("APPLICATION_MATERIAL",e);}
                    // 6b) 附件 — 结构页主政策附件改为展示关联政策下“申报材料类”的附件
                    List<Map<String,Object>> attachments=collectApplicationMaterialAttachments(documents,sourcePolicyId);
                    result.put("attachments",attachments);
                }
                if(!result.containsKey("attachments"))result.put("attachments",new ArrayList<Map<String,Object>>());
                result.put("materials",materials);
                boolean hasFusionStructuredData = !policyInfo.isEmpty()
                    || !keywords.isEmpty()
                    || !conditions.isEmpty()
                    || !supportMethods.isEmpty()
                    || !materials.isEmpty();
                result.put("hasFusionStructuredData",hasFusionStructuredData);
                logFusionDetailStage(fusionPolicyId,"支持方式与申报材料",fusionDetailStageStartedAt);

                sendJson(exchange,200,buildMap("success",true,"data",result));
            }catch(SQLException e){
                sendJson(exchange,500,buildMap("success",false,"message",e.getMessage()));
            }
        }

        private String buildFusionDetailDocumentDeleteWhere(String documentDeleteStatus){
            String normalized = nullToEmpty(documentDeleteStatus).trim();
            if ("deleted".equalsIgnoreCase(normalized) || "已删除".equals(normalized)) {
                return " AND d.DEL_FLAG='1'";
            }
            if ("all".equalsIgnoreCase(normalized) || "全部".equals(normalized)) {
                return "";
            }
            return " AND d.DEL_FLAG='0'";
        }

        private void populateDocumentAttachments(List<Map<String,Object>> documents){
            if(documents==null||documents.isEmpty())return;
            Map<String,List<Map<String,Object>>> documentsBySourcePolicyId=new LinkedHashMap<>();
            for(Map<String,Object> doc:documents){
                doc.put("attachments",new ArrayList<Map<String,Object>>());
                String sourcePolicyId=String.valueOf(doc.getOrDefault("sourcePolicyId","")).trim();
                if(sourcePolicyId.isEmpty())continue;
                documentsBySourcePolicyId.computeIfAbsent(sourcePolicyId,key->new ArrayList<>()).add(doc);
            }
            if(documentsBySourcePolicyId.isEmpty())return;

            String sql="SELECT ATTACH_ID, POLICY_ID, ATTACH_NAME, ATTACH_SHOW_NAME, FILE_PATH, FILE_SUFFIX, ATTACH_TYPE, ATTACH_REVIEW_STATUS " +
                "FROM SYSDBA.POLICY_ATTACHMENT WHERE DEL_FLAG='0' AND TRIM(POLICY_ID) IN ("+repeatPlaceholders(documentsBySourcePolicyId.size())+") " +
                "ORDER BY CREATE_AT, ATTACH_ID";
            try(PreparedStatement s=conn.prepareStatement(sql)){
                int paramIndex=1;
                for(String sourcePolicyId:documentsBySourcePolicyId.keySet()){
                    s.setString(paramIndex++,sourcePolicyId);
                }
                try(ResultSet rs=s.executeQuery()){
                    while(rs.next()){
                        String attachmentPolicyId=nullToEmpty(rs.getString("POLICY_ID")).trim();
                        List<Map<String,Object>> matchedDocuments=documentsBySourcePolicyId.get(attachmentPolicyId);
                        if(matchedDocuments==null||matchedDocuments.isEmpty())continue;

                        Map<String,Object> attachment=new LinkedHashMap<>();
                        attachment.put("id",nullToEmpty(rs.getString("ATTACH_ID")));
                        attachment.put("policyId",attachmentPolicyId);
                        attachment.put("attachmentName",nullToEmpty(rs.getString("ATTACH_NAME")));
                        attachment.put("attachmentShowName",nullToEmpty(rs.getString("ATTACH_SHOW_NAME")));
                        attachment.put("filePath",nullToEmpty(rs.getString("FILE_PATH")));
                        attachment.put("fileSuffix",nullToEmpty(rs.getString("FILE_SUFFIX")));
                        attachment.put("attachmentType",nullToEmpty(rs.getString("ATTACH_TYPE")));
                        attachment.put("reviewStatus",nullToEmpty(rs.getString("ATTACH_REVIEW_STATUS")));

                        for(Map<String,Object> doc:matchedDocuments){
                            @SuppressWarnings("unchecked")
                            List<Map<String,Object>> attachments=(List<Map<String,Object>>)doc.get("attachments");
                            attachments.add(new LinkedHashMap<>(attachment));
                        }
                    }
                }
            }catch(SQLException e){logFusionDetailQueryError("POLICY_ATTACHMENT",e);}
        }

        private List<Map<String,Object>> collectApplicationMaterialAttachments(List<Map<String,Object>> documents,String mainSourcePolicyId){
            List<Map<String,Object>> attachments=new ArrayList<>();
            if(documents==null||documents.isEmpty())return attachments;
            Set<String> seenAttachmentIds=new LinkedHashSet<>();
            String normalizedMainSourcePolicyId=nullToEmpty(mainSourcePolicyId).trim();
            for(Map<String,Object> doc:documents){
                String documentSourcePolicyId=String.valueOf(doc.getOrDefault("sourcePolicyId","")).trim();
                if(!normalizedMainSourcePolicyId.isEmpty()&&normalizedMainSourcePolicyId.equals(documentSourcePolicyId)){
                    continue;
                }
                @SuppressWarnings("unchecked")
                List<Map<String,Object>> docAttachments=(List<Map<String,Object>>)doc.get("attachments");
                if(docAttachments==null||docAttachments.isEmpty())continue;
                for(Map<String,Object> docAttachment:docAttachments){
                    String attachmentId=String.valueOf(docAttachment.getOrDefault("id","")).trim();
                    if(attachmentId.isEmpty()||!isApplicationMaterialAttachmentType(docAttachment.get("attachmentType"))||!seenAttachmentIds.add(attachmentId)){
                        continue;
                    }
                    Map<String,Object> attachment=new LinkedHashMap<>(docAttachment);
                    attachment.put("name",firstNonEmpty(
                        String.valueOf(docAttachment.getOrDefault("attachmentShowName","")),
                        String.valueOf(docAttachment.getOrDefault("attachmentName",""))
                    ));
                    attachment.put("sourcePolicyId",documentSourcePolicyId);
                    attachment.put("policyTitle",nullToEmpty(String.valueOf(doc.getOrDefault("policyTitle",""))));
                    attachments.add(attachment);
                }
            }
            return attachments;
        }

        private boolean isApplicationMaterialAttachmentType(Object attachmentTypeRaw){
            String attachmentType=nullToEmpty(String.valueOf(attachmentTypeRaw)).trim();
            return "申报材料类".equals(attachmentType)||"申报材料".equals(attachmentType);
        }

        private String findPolicyIdByMainFile(String mainFileTitle,String mainFileName){
            if((mainFileTitle==null||mainFileTitle.isEmpty())&&(mainFileName==null||mainFileName.isEmpty()))return null;
            String titleWithoutSuffix=removeFileSuffix(mainFileTitle);
            String fileNameWithoutSuffix=removeFileSuffix(mainFileName);
            String sql="SELECT ID,POLICY_TITLE,FILE_NAME FROM SYSDBA.POLICY WHERE " +
                "TRIM(POLICY_TITLE) IN (?,?,?) OR TRIM(FILE_NAME) IN (?,?,?) ORDER BY ID DESC";
            try(PreparedStatement s=conn.prepareStatement(sql)){
                s.setString(1,mainFileTitle);s.setString(2,titleWithoutSuffix);s.setString(3,fileNameWithoutSuffix);
                s.setString(4,mainFileName);s.setString(5,mainFileTitle);s.setString(6,titleWithoutSuffix);
                try(ResultSet rs=s.executeQuery()){if(rs.next())return rs.getString("ID");}
            }catch(SQLException e){logFusionDetailQueryError("POLICY主文件匹配",e);}
            return null;
        }

        private String removeFileSuffix(String value){
            if(value==null)return "";
            String trimmed=value.trim();
            int dot=trimmed.lastIndexOf('.');
            return dot>0?trimmed.substring(0,dot):trimmed;
        }

        private void populateConditionClusters(List<Map<String,Object>> conditions){
            if(conditions==null||conditions.isEmpty())return;

            List<String> conditionIds=new ArrayList<>();
            Map<String,Map<String,Object>> conditionById=new LinkedHashMap<>();
            for(Map<String,Object> condition:conditions){
                String conditionId=nullToEmpty(String.valueOf(condition.getOrDefault("id",""))).trim();
                if(conditionId.isEmpty()||conditionById.containsKey(conditionId))continue;
                conditionIds.add(conditionId);
                conditionById.put(conditionId,condition);
            }
            if(conditionIds.isEmpty())return;

            Map<String,Map<String,Object>> clusterRows=queryStructuredRows("CONDITION_CLUSTER",conditionIds);
            String[] detailTables={"CORE_TEAM","INNOVATION_DEVELOPMENT","INNOVATION_INVESTMENT","INNOVATION_OUTPUT","INNOVATION_IMPACT","MAIN_PRODUCT","MAIN_TECHNOLOGY"};
            Map<String,Map<String,Map<String,Object>>> detailRowsByTable=new LinkedHashMap<>();
            for(String table:detailTables){
                detailRowsByTable.put(table,queryStructuredRows(table,conditionIds));
            }

            for(String conditionId:conditionIds){
                Map<String,Object> condition=conditionById.get(conditionId);
                if(condition==null)continue;
                Map<String,Object> cluster=new LinkedHashMap<>();
                Map<String,Object> baseCluster=clusterRows.get(conditionId);
                if(baseCluster!=null&&!baseCluster.isEmpty()){
                    cluster.putAll(baseCluster);
                }
                for(String table:detailTables){
                    Map<String,Object> detail=detailRowsByTable.getOrDefault(table,Collections.emptyMap()).get(conditionId);
                    if(detail!=null&&!detail.isEmpty()){
                        cluster.put(toCamelCase(table),detail);
                    }
                }
                if(!cluster.isEmpty()){
                    condition.put("cluster",cluster);
                }
            }
        }

        private Map<String,Object> queryStructuredRow(String table,String conditionId){
            Map<String,Object> row=new LinkedHashMap<>();
            try(PreparedStatement s=conn.prepareStatement("SELECT * FROM SYSDBA."+table+" WHERE CONDITION_ID=?")){
                s.setString(1,conditionId);
                try(ResultSet rs=s.executeQuery()){
                    if(rs.next()){
                        row.putAll(readStructuredRow(rs));
                    }
                }
            }catch(SQLException e){logFusionDetailQueryError(table,e);}
            return row;
        }

        private Map<String,Map<String,Object>> queryStructuredRows(String table,List<String> conditionIds){
            Map<String,Map<String,Object>> rows=new LinkedHashMap<>();
            if(conditionIds==null||conditionIds.isEmpty())return rows;
            String sql="SELECT * FROM SYSDBA."+table+" WHERE CONDITION_ID IN ("+repeatPlaceholders(conditionIds.size())+")";
            try(PreparedStatement s=conn.prepareStatement(sql)){
                int paramIndex=1;
                for(String conditionId:conditionIds){
                    s.setString(paramIndex++,conditionId);
                }
                try(ResultSet rs=s.executeQuery()){
                    while(rs.next()){
                        String conditionId=nullToEmpty(rs.getString("CONDITION_ID")).trim();
                        if(conditionId.isEmpty()||rows.containsKey(conditionId))continue;
                        rows.put(conditionId,readStructuredRow(rs));
                    }
                }
            }catch(SQLException e){logFusionDetailQueryError(table,e);}
            return rows;
        }

        private Map<String,Object> readStructuredRow(ResultSet rs) throws SQLException {
            Map<String,Object> row=new LinkedHashMap<>();
            ResultSetMetaData meta=rs.getMetaData();
            for(int index=1;index<=meta.getColumnCount();index++){
                String column=meta.getColumnLabel(index);
                if("ID".equalsIgnoreCase(column)||"CONDITION_ID".equalsIgnoreCase(column))continue;
                String value=rs.getString(index);
                if(value!=null&&!value.trim().isEmpty()){
                    row.put(toCamelCase(column),value.trim());
                }
            }
            return row;
        }

        private void logFusionDetailStage(String fusionPolicyId,String stage,long stageStartedAt){
            long elapsedMs=Math.max(0L,System.currentTimeMillis()-stageStartedAt);
            System.out.println("[Fusion Detail Timing] fusionPolicyId="+fusionPolicyId+" stage="+stage+" cost="+elapsedMs+"ms");
        }

        private String toCamelCase(String value){
            StringBuilder result=new StringBuilder();
            boolean upperNext=false;
            for(int index=0;index<value.length();index++){
                char current=value.charAt(index);
                if(current=='_'){upperNext=true;continue;}
                result.append(upperNext?Character.toUpperCase(current):Character.toLowerCase(current));
                upperNext=false;
            }
            return result.toString();
        }

        private void putIfNotEmpty(Map<String,Object> target,String key,String value,boolean overwrite){
            String normalized=nullToEmpty(value).trim();
            if(normalized.isEmpty())return;
            if(overwrite||!target.containsKey(key)||String.valueOf(target.get(key)).trim().isEmpty())target.put(key,normalized);
        }

        private void logFusionDetailQueryError(String source,SQLException error){
            System.err.println("[Fusion Detail Query Error]["+source+"] "+error.getMessage());
        }
    }

    // ==================== 融合详情保存接口 ====================

    static class FusionDetailSaveHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if ("OPTIONS".equals(exchange.getRequestMethod())) { sendResponse(exchange,200,"{\"success\":true}"); return; }
            if (!"POST".equals(exchange.getRequestMethod())) { sendJson(exchange,405,buildMap("success",false,"message","只支持 POST 方法")); return; }
            try{
                ensureConnection();
                String rawBody = new String(readAllBytes(exchange.getRequestBody()), StandardCharsets.UTF_8);
                Map<String,Object> data = parseNestedJson(rawBody);
                String fusionPolicyId = stringVal(data.get("fusionPolicyId"));
                if(fusionPolicyId.isEmpty()){
                    Map<String,Object> detailFusion = nestedMap(data,"fusionPolicy");
                    if(detailFusion != null) fusionPolicyId = stringVal(detailFusion.get("id"));
                }
                // 规则表专用保存：严格只更新 RULE_TABLE_JSON，不触发详情、审核或元数据更新。
                if(Boolean.TRUE.equals(data.get("ruleTableOnly"))){
                    if(fusionPolicyId.isEmpty()){
                        sendJson(exchange,400,buildMap("success",false,"message","缺少 fusionPolicyId"));
                        return;
                    }
                    if(!data.containsKey("ruleTableJson")){
                        sendJson(exchange,400,buildMap("success",false,"message","缺少 ruleTableJson"));
                        return;
                    }
                    Object normalizedRuleTableJson = decodeLiteralUnicodeEscapesInJsonValue(data.get("ruleTableJson"));
                    String ruleTableJson = toJson(normalizedRuleTableJson);
                    if(ruleTableJson.isEmpty()){
                        sendJson(exchange,400,buildMap("success",false,"message","ruleTableJson 不能为空"));
                        return;
                    }
                    executeUpdate(
                        "UPDATE SYSDBA.X1_FUSION_POLICY SET RULE_TABLE_JSON=? WHERE ID=?",
                        ruleTableJson,
                        fusionPolicyId
                    );
                    sendJson(exchange,200,buildMap("success",true,"message","规则表保存成功"));
                    return;
                }
                // 兼容两种传参方式：直接传 sourcePolicyId 或通过 detail 内嵌
                String sourcePolicyId = stringVal(data.get("sourcePolicyId"));
                if(sourcePolicyId.isEmpty()){
                    Map<String,Object> pi = nestedMap(data,"policyInfo");
                    if(pi != null) sourcePolicyId = stringVal(pi.get("sourcePolicyId"));
                }
                if(sourcePolicyId.isEmpty()){
                    sendJson(exchange,400,buildMap("success",false,"message","缺少 sourcePolicyId"));
                    return;
                }
                if(data.containsKey("mainFileTitle")){
                    String mainFileTitle = stringVal(data.get("mainFileTitle")).trim();
                    if(mainFileTitle.isEmpty()){
                        sendJson(exchange,400,buildMap("success",false,"message","主政策标题不能为空"));
                        return;
                    }
                    if(fusionPolicyId.isEmpty()){
                        sendJson(exchange,400,buildMap("success",false,"message","缺少 fusionPolicyId"));
                        return;
                    }
                    executeUpdate(
                        "UPDATE SYSDBA.X1_FUSION_POLICY SET MAIN_FILE_TITLE=?, UPDATED_AT=CURRENT_TIMESTAMP WHERE ID=?",
                        mainFileTitle,
                        fusionPolicyId
                    );
                }

                // 保存规则表 JSON（若请求中包含 ruleTableJson）
                if(data.containsKey("ruleTableJson")){
                    String ruleTableJson = toJson(data.get("ruleTableJson"));
                    if(!ruleTableJson.isEmpty() && !fusionPolicyId.isEmpty()){
                        executeUpdate(
                            "UPDATE SYSDBA.X1_FUSION_POLICY SET RULE_TABLE_JSON=?, UPDATED_AT=CURRENT_TIMESTAMP WHERE ID=?",
                            ruleTableJson,
                            fusionPolicyId
                        );
                    }
                }

                // 1) 更新 POLICY_METADATA 表
                Map<String,Object> pi = nestedMap(data,"policyInfo");
                if(pi != null){
                    List<String> metaSets = new ArrayList<>();
                    List<Object> metaVals = new ArrayList<>();
                    String[][] metaFields = {
                        {"policyAttribute","POLICY_ATTRIBUTE"}, {"policyOriginalTitle","POLICY_ORIGINAL_TITLE"},
                        {"sceneUsage","SCENE_USAGE"}, {"applicableScope","APPLICABLE_SCOPE"},
                        {"industryField","INDUSTRY_FIELD"},
                        {"issuingDepartment","ISSUING_DEPARTMENT"},
                    };
                    for(String[] f : metaFields){
                        String v = stringVal(pi.get(f[0]));
                        if(pi.containsKey(f[0])){ metaSets.add(f[1]+"=?"); metaVals.add(v); }
                    }
                    String effectiveStartDate = pi.containsKey("effectiveStartDate") ? stringVal(pi.get("effectiveStartDate")) : "";
                    String effectiveEndDate = pi.containsKey("effectiveEndDate") ? stringVal(pi.get("effectiveEndDate")) : "";
                    String effectiveDateMirror = formatDateRange(effectiveStartDate, effectiveEndDate, "");
                    if(pi.containsKey("effectiveStartDate")){ metaSets.add("EFFECTIVE_START_DATE=?"); metaVals.add(effectiveStartDate); }
                    if(pi.containsKey("effectiveEndDate")){ metaSets.add("EFFECTIVE_END_DATE=?"); metaVals.add(effectiveEndDate); }
                    if(pi.containsKey("effectiveStartDate") || pi.containsKey("effectiveEndDate")){
                        metaSets.add("EFFECTIVE_DATE=?"); metaVals.add(effectiveDateMirror);
                    }
                    String titleMain = stringVal(pi.get("policyTitle"));
                    if(pi.containsKey("policyTitle")){ metaSets.add("POLICY_TITLE_MAIN=?"); metaVals.add(titleMain); }
                    String remark = stringVal(pi.get("policyCode"));
                    if(pi.containsKey("policyCode")){ metaSets.add("REMARK=?"); metaVals.add(remark); }
                    if(!metaSets.isEmpty()){
                        metaVals.add(sourcePolicyId);
                        executeUpdate("UPDATE SYSDBA.POLICY_METADATA SET "+String.join(",",metaSets)+" WHERE POLICY_ID=?",
                            metaVals.toArray());
                    }
                }

                // 2) 更新 POLICY 表
                if(pi != null){
                    List<String> pSets = new ArrayList<>();
                    List<Object> pVals = new ArrayList<>();
                    if(pi.containsKey("policyTitle")){ pSets.add("POLICY_TITLE=?"); pVals.add(stringVal(pi.get("policyTitle"))); }
                    if(pi.containsKey("summary")){ pSets.add("ABSTRACT=?"); pVals.add(stringVal(pi.get("summary"))); }
                    if(pi.containsKey("contactInfo")){ pSets.add("CONTACT_INFO=?"); pVals.add(stringVal(pi.get("contactInfo"))); }
                    if(pi.containsKey("applicationVolume")){ pSets.add("APPLICATION_VOLUME=?"); pVals.add(stringVal(pi.get("applicationVolume"))); }
                    if(!pSets.isEmpty()){
                        pVals.add(sourcePolicyId);
                        executeUpdate("UPDATE SYSDBA.POLICY SET "+String.join(",",pSets)+" WHERE ID=?", pVals.toArray());
                    }
                }

                // 3) 更新 POLICY_KEYWORD（全量替换）
                Object keywordsRaw = data.get("keywords");
                if(keywordsRaw instanceof List){
                    executeUpdate("DELETE FROM SYSDBA.POLICY_KEYWORD WHERE POLICY_ID=?", sourcePolicyId);
                    @SuppressWarnings("unchecked")
                    List<Object> kwList = (List<Object>)keywordsRaw;
                    for(Object kwObj : kwList){
                        String kwText = "";
                        if(kwObj instanceof Map) { kwText = stringVal(((Map<String,Object>)kwObj).get("text")); }
                        else { kwText = stringVal(kwObj); }
                        if(!kwText.isEmpty()){
                            executeUpdate("INSERT INTO SYSDBA.POLICY_KEYWORD(POLICY_ID,KEYWORD) VALUES(?,?)",
                                sourcePolicyId, kwText);
                        }
                    }
                }

                // 4) 更新 APPLICATION_CONDITION
                Object condsRaw = data.get("conditions");
                if(condsRaw instanceof List){
                    @SuppressWarnings("unchecked")
                    List<Object> condList = (List<Object>)condsRaw;
                    for(Object cObj : condList){
                        if(!(cObj instanceof Map)) continue;
                        @SuppressWarnings("unchecked")
                        Map<String,Object> cond = (Map<String,Object>)cObj;
                        String condId = stringVal(cond.get("id"));
                        if(condId.isEmpty()) continue;
                        List<String> cSets = new ArrayList<>();
                        List<Object> cVals = new ArrayList<>();
                        String applicationStartTime = cond.containsKey("applicationStartTime") ? stringVal(cond.get("applicationStartTime")) : "";
                        String applicationEndTime = cond.containsKey("applicationEndTime") ? stringVal(cond.get("applicationEndTime")) : "";
                        String applicationTimeMirror = formatDateRange(applicationStartTime, applicationEndTime, "");
                        if(cond.containsKey("applicableTarget")){ cSets.add("APPLICABLE_TARGET=?"); cVals.add(stringVal(cond.get("applicableTarget"))); }
                        if(cond.containsKey("applicationRequirement")){ cSets.add("APPLICATION_REQUIREMENT=?"); cVals.add(stringVal(cond.get("applicationRequirement"))); }
                        if(cond.containsKey("applicationInstruction")){ cSets.add("PROMPT_CONTENT=?"); cVals.add(stringVal(cond.get("applicationInstruction"))); }
                        if(cond.containsKey("applicationStartTime")){ cSets.add("APPLICATION_START_TIME=?"); cVals.add(applicationStartTime); }
                        if(cond.containsKey("applicationEndTime")){ cSets.add("APPLICATION_END_TIME=?"); cVals.add(applicationEndTime); }
                        if(cond.containsKey("applicationStartTime") || cond.containsKey("applicationEndTime")){
                            cSets.add("APPLICATION_TIME=?"); cVals.add(applicationTimeMirror);
                        }
                        if(cond.containsKey("announcementDate")){ cSets.add("ANNOUNCEMENT_DATE=?"); cVals.add(stringVal(cond.get("announcementDate"))); }
                        if(!cSets.isEmpty()){
                            cVals.add(condId);
                            executeUpdate("UPDATE SYSDBA.APPLICATION_CONDITION SET "+String.join(",",cSets)+" WHERE ID=?", cVals.toArray());
                        }
                        // 更新 CONDITION_CLUSTER
                        Map<String,Object> cluster = nestedMap(cond,"cluster");
                        if(cluster != null && !cluster.isEmpty()){
                            saveConditionCluster(condId, cluster);
                        }
                    }
                }

                // 5) 更新 SUPPORT_METHOD
                Object smsRaw = data.get("supportMethods");
                if(smsRaw instanceof List){
                    @SuppressWarnings("unchecked")
                    List<Object> smList = (List<Object>)smsRaw;
                    for(Object sObj : smList){
                        if(!(sObj instanceof Map)) continue;
                        @SuppressWarnings("unchecked")
                        Map<String,Object> sm = (Map<String,Object>)sObj;
                        String smId = stringVal(sm.get("id"));
                        if(smId.isEmpty()) continue;
                        List<String> sSets = new ArrayList<>();
                        List<Object> sVals = new ArrayList<>();
                        if(sm.containsKey("supportType")){ sSets.add("SUPPORT_TYPE=?"); sVals.add(stringVal(sm.get("supportType"))); }
                        if(sm.containsKey("contentDesc")){ sSets.add("SUPPORT_CONTENT_DESC=?"); sVals.add(stringVal(sm.get("contentDesc"))); }
                        if(sm.containsKey("amount")){ sSets.add("SUPPORT_AMOUNT=?"); sVals.add(stringVal(sm.get("amount"))); }
                        if(sm.containsKey("maxAmount")){ sSets.add("MAX_SUPPORT_AMOUNT=?"); sVals.add(stringVal(sm.get("maxAmount"))); }
                        if(!sSets.isEmpty()){
                            sVals.add(smId);
                            executeUpdate("UPDATE SYSDBA.SUPPORT_METHOD SET "+String.join(",",sSets)+" WHERE ID=?", sVals.toArray());
                        }
                    }
                }

                // 6) 更新 APPLICATION_MATERIAL
                Object matsRaw = data.get("materials");
                if(matsRaw instanceof List){
                    @SuppressWarnings("unchecked")
                    List<Object> matList = (List<Object>)matsRaw;
                    for(Object mObj : matList){
                        if(!(mObj instanceof Map)) continue;
                        @SuppressWarnings("unchecked")
                        Map<String,Object> mat = (Map<String,Object>)mObj;
                        String matId = stringVal(mat.get("id"));
                        if(matId.isEmpty()) continue;
                        List<String> mSets = new ArrayList<>();
                        List<Object> mVals = new ArrayList<>();
                        if(mat.containsKey("materials")){ mSets.add("APPLICATION_MATERIALS=?"); mVals.add(stringVal(mat.get("materials"))); }
                        if(mat.containsKey("method")){ mSets.add("APPLICATION_METHOD=?"); mVals.add(stringVal(mat.get("method"))); }
                        if(mat.containsKey("url")){ mSets.add("APPLICATION_URL=?"); mVals.add(stringVal(mat.get("url"))); }
                        if(!mSets.isEmpty()){
                            mVals.add(matId);
                            executeUpdate("UPDATE SYSDBA.APPLICATION_MATERIAL SET "+String.join(",",mSets)+" WHERE ID=?", mVals.toArray());
                        }
                    }
                }

                if(!fusionPolicyId.isEmpty()){
                    resetFusionStructureReviewStatus(conn, fusionPolicyId);
                }

                sendJson(exchange,200,buildMap("success",true,"message","保存成功"));
            }catch(SQLException e){
                System.err.println("[Fusion Detail Save Error] "+e.getMessage());
                sendJson(exchange,500,buildMap("success",false,"message",e.getMessage()));
            }catch(Exception e){
                System.err.println("[Fusion Detail Save Error] "+e.getMessage());
                sendJson(exchange,500,buildMap("success",false,"message",e.getMessage()));
            }
        }

        private void saveConditionCluster(String conditionId, Map<String,Object> cluster) throws SQLException {
            // 更新 CONDITION_CLUSTER 表的基本字段
            List<String> clusterSets = new ArrayList<>();
            List<Object> clusterVals = new ArrayList<>();
            String[] clusterFields = {"applicableTarget","APPLICABLE_TARGET"};
            // 只更新 cluster 表中可识别的字段
            for(Map.Entry<String,Object> e : cluster.entrySet()){
                String key = e.getKey();
                if(key.equals("coreTeam")||key.equals("innovationDevelopment")||key.equals("innovationInvestment")||
                   key.equals("innovationOutput")||key.equals("innovationImpact")||key.equals("mainProduct")||key.equals("mainTechnology")||
                   key.equals("id")) continue;
                String col = snakeCase(key);
                // 跳过非 CONDITION_CLUSTER 表字段（只更新已知列名）
                String v = stringVal(e.getValue());
                if(!v.isEmpty()){ clusterSets.add(col+"=?"); clusterVals.add(v); }
            }
            if(!clusterSets.isEmpty()){
                clusterVals.add(conditionId);
                try{
                    executeUpdate("UPDATE SYSDBA.CONDITION_CLUSTER SET "+String.join(",",clusterSets)+" WHERE CONDITION_ID=?", clusterVals.toArray());
                }catch(SQLException ignored){}
            }
            // 更新子表
            String[][] detailTables = {
                {"coreTeam","CORE_TEAM"},{"innovationDevelopment","INNOVATION_DEVELOPMENT"},
                {"innovationInvestment","INNOVATION_INVESTMENT"},{"innovationOutput","INNOVATION_OUTPUT"},
                {"innovationImpact","INNOVATION_IMPACT"},{"mainProduct","MAIN_PRODUCT"},{"mainTechnology","MAIN_TECHNOLOGY"}
            };
            for(String[] dt : detailTables){
                Map<String,Object> detail = nestedMap(cluster, dt[0]);
                if(detail == null || detail.isEmpty()) continue;
                List<String> dSets = new ArrayList<>();
                List<Object> dVals = new ArrayList<>();
                for(Map.Entry<String,Object> e : detail.entrySet()){
                    String key = e.getKey();
                    if(key.equals("id")||key.equals("conditionId")) continue;
                    String v = stringVal(e.getValue());
                    if(!v.isEmpty()){ dSets.add(snakeCase(key)+"=?"); dVals.add(v); }
                }
                if(!dSets.isEmpty()){
                    dVals.add(conditionId);
                    try{
                        int rows = executeUpdate("UPDATE SYSDBA."+dt[1]+" SET "+String.join(",",dSets)+" WHERE CONDITION_ID=?", dVals.toArray());
                        if(rows == 0){
                            // 插入
                            String id = UUID.randomUUID().toString();
                            StringBuilder cols = new StringBuilder("ID,CONDITION_ID");
                            StringBuilder phs = new StringBuilder("?,?");
                            List<Object> insVals = new ArrayList<>(); insVals.add(id); insVals.add(conditionId);
                            for(Map.Entry<String,Object> e : detail.entrySet()){
                                String k = e.getKey();
                                if(k.equals("id")||k.equals("conditionId")) continue;
                                String v = stringVal(e.getValue());
                                if(!v.isEmpty()){ cols.append(",").append(snakeCase(k)); phs.append(",?"); insVals.add(v); }
                            }
                            executeUpdate("INSERT INTO SYSDBA."+dt[1]+" ("+cols+") VALUES ("+phs+")", insVals.toArray());
                        }
                    }catch(SQLException ignored){}
                }
            }
        }

        private String snakeCase(String camel){
            StringBuilder sb = new StringBuilder();
            for(int i=0;i<camel.length();i++){
                char c = camel.charAt(i);
                if(Character.isUpperCase(c)){ sb.append('_').append(Character.toUpperCase(c)); }
                else{ sb.append(Character.toUpperCase(c)); }
            }
            return sb.toString();
        }
    }

    static class FusionStatsHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if ("OPTIONS".equals(exchange.getRequestMethod())) { sendResponse(exchange,200,"{\"success\":true}"); return; }
            try {
                ensureConnection();
                List<Map<String,Object>> tables = new ArrayList<>();
                String[] checks = {
                    "CONDITION_CLUSTER", "CORE_TEAM", "INNOVATION_DEVELOPMENT",
                    "INNOVATION_INVESTMENT", "INNOVATION_OUTPUT", "INNOVATION_IMPACT",
                    "MAIN_PRODUCT", "MAIN_TECHNOLOGY", "APPLICATION_CONDITION"
                };
                for (String t : checks) {
                    Map<String,Object> info = new LinkedHashMap<>();
                    info.put("table", t);
                    try (Statement s = conn.createStatement(); ResultSet rs = s.executeQuery("SELECT COUNT(*) AS CNT FROM SYSDBA." + t)) {
                        if (rs.next()) info.put("count", rs.getLong("CNT"));
                    }
                    // 取前3条 CONDITION_ID
                    if (!"APPLICATION_CONDITION".equals(t)) {
                        try (Statement s = conn.createStatement(); ResultSet rs = s.executeQuery("SELECT DISTINCT CONDITION_ID FROM SYSDBA." + t + " WHERE ROWNUM <= 3")) {
                            List<String> ids = new ArrayList<>();
                            while (rs.next()) ids.add(rs.getString("CONDITION_ID"));
                            info.put("sampleIds", ids);
                        }
                    }
                    tables.add(info);
                }
                sendJson(exchange, 200, buildMap("success", true, "data", tables));
            } catch (SQLException e) {
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    static class EnterpriseMatchResultsHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            String method = exchange.getRequestMethod();
            if ("OPTIONS".equals(method)) {
                sendResponse(exchange, 200, "{\"success\":true}");
                return;
            }
            if ("POST".equals(method)) {
                saveResult(exchange);
                return;
            }
            if ("GET".equals(method)) {
                getLatestResult(exchange);
                return;
            }
            sendJson(exchange, 405, buildMap("success", false, "message", "只支持 GET、POST 方法"));
        }

        private void saveResult(HttpExchange exchange) throws IOException {
            Map<String, String> data = parseRequestJson(exchange);
            String runId = nullToEmpty(data.get("runId")).trim();
            String enterpriseId = nullToEmpty(data.get("enterpriseId")).trim();
            String enterpriseName = nullToEmpty(data.get("enterpriseName")).trim();
            String creditCode = nullToEmpty(data.get("creditCode")).trim();
            String responseJson = nullToEmpty(data.get("responseJson")).trim();
            String detailFileName = nullToEmpty(data.get("detailFileName")).trim();
            String schemaVersion = firstNonEmpty(data.get("schemaVersion"), "enterprise-match-detail-v1");
            int policyCount;
            long detailFileSize;
            try {
                policyCount = Integer.parseInt(nullToEmpty(data.get("policyCount")).trim());
                detailFileSize = Long.parseLong(nullToEmpty(data.get("detailFileSize")).trim());
            } catch (NumberFormatException e) {
                sendJson(exchange, 400, buildMap("success", false, "message", "policyCount 和 detailFileSize 必须是非负整数"));
                return;
            }
            if (policyCount < 0 || detailFileSize < 0) {
                sendJson(exchange, 400, buildMap("success", false, "message", "policyCount 和 detailFileSize 必须是非负整数"));
                return;
            }
            if (runId.isEmpty() || enterpriseId.isEmpty() || enterpriseName.isEmpty() || responseJson.isEmpty() || detailFileName.isEmpty()) {
                sendJson(exchange, 400, buildMap(
                    "success", false,
                    "message", "缺少 runId、enterpriseId、enterpriseName、responseJson 或 detailFileName"
                ));
                return;
            }

            Connection connection = null;
            try {
                connection = createDedicatedConnection();
                connection.setAutoCommit(false);
                try (PreparedStatement existing = connection.prepareStatement(
                        "SELECT ENTERPRISE_ID FROM SYSDBA.PEM_ENTERPRISE_MATCH_RUN WHERE RUN_ID=?")) {
                    existing.setString(1, runId);
                    try (ResultSet rs = existing.executeQuery()) {
                        if (rs.next()) {
                            String storedEnterpriseId = nullToEmpty(rs.getString("ENTERPRISE_ID")).trim();
                            connection.rollback();
                            if (enterpriseId.equals(storedEnterpriseId)) {
                                sendJson(exchange, 200, buildMap(
                                    "success", true,
                                    "message", "匹配结果已存在",
                                    "data", buildMap("runId", runId, "idempotent", true)
                                ));
                            } else {
                                sendJson(exchange, 409, buildMap(
                                    "success", false,
                                    "message", "runId 已被其他企业使用"
                                ));
                            }
                            return;
                        }
                    }
                }

                String insertSql =
                    "INSERT INTO SYSDBA.PEM_ENTERPRISE_MATCH_RUN " +
                    "(RUN_ID, ENTERPRISE_ID, ENTERPRISE_NAME, CREDIT_CODE, POLICY_COUNT, RESPONSE_JSON, DETAIL_FILE_NAME, DETAIL_FILE_SIZE, SCHEMA_VERSION, CREATED_AT) " +
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)";
                try (PreparedStatement stmt = connection.prepareStatement(insertSql)) {
                    stmt.setString(1, runId);
                    stmt.setString(2, enterpriseId);
                    stmt.setString(3, enterpriseName);
                    if (creditCode.isEmpty()) stmt.setNull(4, Types.VARCHAR);
                    else stmt.setString(4, creditCode);
                    stmt.setInt(5, policyCount);
                    stmt.setCharacterStream(6, new StringReader(responseJson), responseJson.length());
                    stmt.setString(7, detailFileName);
                    stmt.setLong(8, detailFileSize);
                    stmt.setString(9, schemaVersion);
                    stmt.executeUpdate();
                }
                connection.commit();
                sendJson(exchange, 200, buildMap(
                    "success", true,
                    "message", "匹配结果保存成功",
                    "data", buildMap("runId", runId, "idempotent", false)
                ));
            } catch (SQLException e) {
                if (connection != null) {
                    try { connection.rollback(); } catch (SQLException ignored) {}
                }
                System.err.println("[Enterprise Match Result Save Error] runId=" + runId + ", message=" + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", "匹配结果写入数据库失败"));
            } finally {
                if (connection != null) {
                    try { connection.close(); } catch (SQLException ignored) {}
                }
            }
        }

        @SuppressWarnings("unchecked")
        private void getLatestResult(HttpExchange exchange) throws IOException {
            Map<String, String> params = parseQueryParams(exchange.getRequestURI().getQuery());
            String enterpriseId = nullToEmpty(params.get("enterpriseId")).trim();
            if (enterpriseId.isEmpty()) {
                sendJson(exchange, 400, buildMap("success", false, "message", "缺少 enterpriseId 参数"));
                return;
            }

            String sql =
                "SELECT RUN_ID, POLICY_COUNT, RESPONSE_JSON, CREATED_AT FROM (" +
                "SELECT RUN_ID, POLICY_COUNT, RESPONSE_JSON, CREATED_AT " +
                "FROM SYSDBA.PEM_ENTERPRISE_MATCH_RUN WHERE ENTERPRISE_ID=? " +
                "ORDER BY CREATED_AT DESC, RUN_ID DESC" +
                ") WHERE ROWNUM=1";
            try (Connection connection = createDedicatedConnection();
                 PreparedStatement stmt = connection.prepareStatement(sql)) {
                stmt.setString(1, enterpriseId);
                try (ResultSet rs = stmt.executeQuery()) {
                    if (!rs.next()) {
                        sendJson(exchange, 200, buildMap(
                            "success", true,
                            "data", buildMap("policies", new ArrayList<>()),
                            "meta", buildMap(
                                "found", false,
                                "runId", "",
                                "policyCount", 0,
                                "createdAt", null
                            )
                        ));
                        return;
                    }

                    String responseJson = nullToEmpty(rs.getString("RESPONSE_JSON")).trim();
                    Object parsedResponse = parseJsonValue(responseJson);
                    if (!(parsedResponse instanceof Map) || !(((Map<String, Object>) parsedResponse).get("policies") instanceof List)) {
                        throw new IllegalStateException("数据库中的 RESPONSE_JSON 结构无效");
                    }
                    sendJson(exchange, 200, buildMap(
                        "success", true,
                        "data", parsedResponse,
                        "meta", buildMap(
                            "found", true,
                            "runId", nullToEmpty(rs.getString("RUN_ID")),
                            "policyCount", rs.getInt("POLICY_COUNT"),
                            "createdAt", nullToEmpty(rs.getString("CREATED_AT"))
                        )
                    ));
                }
            } catch (SQLException e) {
                System.err.println("[Enterprise Match Result Query Error] enterpriseId=" + enterpriseId + ", message=" + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", "查询企业匹配结果失败"));
            } catch (RuntimeException e) {
                System.err.println("[Enterprise Match Result Parse Error] enterpriseId=" + enterpriseId + ", message=" + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", "企业匹配结果数据格式错误"));
            }
        }
    }

    static class EnterpriseMatchEnterprisesHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if ("OPTIONS".equals(exchange.getRequestMethod())) {
                sendResponse(exchange, 200, "{\"success\":true}");
                return;
            }
            if (!"GET".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持 GET 方法"));
                return;
            }

            Map<String, String> params = parseQueryParams(exchange.getRequestURI().getQuery());
            int page;
            int size;
            try {
                page = parsePositiveInteger(firstNonEmpty(params.get("page"), params.get("pageNum"), params.get("pageNumber"), "1"), "page");
                size = parsePositiveInteger(firstNonEmpty(params.get("size"), params.get("pageSize"), "20"), "size");
            } catch (IllegalArgumentException e) {
                sendJson(exchange, 400, buildMap("success", false, "message", e.getMessage()));
                return;
            }
            if (size > 100) {
                sendJson(exchange, 400, buildMap("success", false, "message", "size 不能大于 100"));
                return;
            }
            String enterpriseName = nullToEmpty(params.get("enterpriseName")).trim();
            int offset = (page - 1) * size;
            String rankedSql =
                "WITH ranked AS (" +
                "SELECT RUN_ID, ENTERPRISE_ID, ENTERPRISE_NAME, CREDIT_CODE, POLICY_COUNT, CREATED_AT, " +
                "ROW_NUMBER() OVER (PARTITION BY ENTERPRISE_ID ORDER BY CREATED_AT DESC, RUN_ID DESC) AS RN " +
                "FROM SYSDBA.PEM_ENTERPRISE_MATCH_RUN" +
                ") ";
            String nameFilter = enterpriseName.isEmpty() ? "" : " AND ENTERPRISE_NAME LIKE ?";
            String countSql = rankedSql + "SELECT COUNT(*) AS TOTAL FROM ranked WHERE RN=1" + nameFilter;
            String listSql = rankedSql +
                "SELECT RUN_ID, ENTERPRISE_ID, ENTERPRISE_NAME, CREDIT_CODE, POLICY_COUNT, CREATED_AT " +
                "FROM ranked WHERE RN=1" + nameFilter +
                " ORDER BY CREATED_AT DESC, RUN_ID DESC LIMIT ? OFFSET ?";

            try (Connection connection = createDedicatedConnection()) {
                int total = 0;
                try (PreparedStatement countStmt = connection.prepareStatement(countSql)) {
                    if (!enterpriseName.isEmpty()) countStmt.setString(1, "%" + enterpriseName + "%");
                    try (ResultSet rs = countStmt.executeQuery()) {
                        if (rs.next()) total = rs.getInt("TOTAL");
                    }
                }

                List<Map<String, Object>> rows = new ArrayList<>();
                try (PreparedStatement listStmt = connection.prepareStatement(listSql)) {
                    int parameterIndex = 1;
                    if (!enterpriseName.isEmpty()) listStmt.setString(parameterIndex++, "%" + enterpriseName + "%");
                    listStmt.setInt(parameterIndex++, size);
                    listStmt.setInt(parameterIndex, offset);
                    try (ResultSet rs = listStmt.executeQuery()) {
                        while (rs.next()) {
                            rows.add(buildMap(
                                "enterpriseId", nullToEmpty(rs.getString("ENTERPRISE_ID")),
                                "enterpriseName", nullToEmpty(rs.getString("ENTERPRISE_NAME")),
                                "creditCode", nullToEmpty(rs.getString("CREDIT_CODE")),
                                "latestRunId", nullToEmpty(rs.getString("RUN_ID")),
                                "policyCount", rs.getInt("POLICY_COUNT"),
                                "latestMatchedAt", nullToEmpty(rs.getString("CREATED_AT"))
                            ));
                        }
                    }
                }

                sendJson(exchange, 200, buildMap(
                    "success", true,
                    "data", rows,
                    "pagination", buildMap(
                        "page", page,
                        "size", size,
                        "total", total,
                        "pages", (int) Math.ceil((double) total / size)
                    )
                ));
            } catch (SQLException e) {
                System.err.println("[Enterprise Match Enterprise List Error] message=" + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", "查询已有匹配结果企业列表失败"));
            }
        }

        private int parsePositiveInteger(String value, String fieldName) {
            try {
                int parsed = Integer.parseInt(nullToEmpty(value).trim());
                if (parsed < 1) throw new NumberFormatException();
                return parsed;
            } catch (NumberFormatException e) {
                throw new IllegalArgumentException(fieldName + " 必须是正整数");
            }
        }
    }

    static class HealthHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);

            String dbStatus = "ok";
            String dbMessage = "";
            try {
                ensureConnection();
                try (PreparedStatement stmt = conn.prepareStatement("SELECT SYSDATE FROM DUAL");
                     ResultSet rs = stmt.executeQuery()) {
                    if (rs.next()) {
                        dbMessage = "已连接";
                    } else {
                        dbStatus = "error";
                        dbMessage = "查询返回空";
                    }
                }
            } catch (Exception e) {
                dbStatus = "error";
                dbMessage = e.getMessage();
            }

            Map<String, Object> response = new LinkedHashMap<>();
            response.put("status", "ok".equals(dbStatus) ? "ok" : "degraded");
            response.put("service", "JDBC Middleware");
            response.put("database", "DMDB");
            response.put("schema", DB_SCHEMA);
            response.put("databaseConnection", dbStatus);
            response.put("databaseMessage", dbMessage);
            response.put("timestamp", System.currentTimeMillis());

            int httpStatus = "ok".equals(dbStatus) ? 200 : 503;
            sendJson(exchange, httpStatus, response);
        }
    }

    // ==================== 四张基础数据真实表统一 CRUD ====================

    private static boolean isMappedBaseDataType(String type) {
        return "policy_usage".equals(type) || "policy_keyword".equals(type) ||
            "applicable_object".equals(type) || "support_type".equals(type) ||
            "applicable_scope".equals(type);
    }

    private static String buildHierarchicalDisplayExpr(String alias, boolean includeLevel3) {
        return buildHierarchicalDisplayExpr(alias, includeLevel3, "LEVEL3_NAME");
    }

    private static String buildHierarchicalDisplayExpr(String alias, boolean includeLevel3, String level3Column) {
        String level1 = "NVL(TRIM(" + alias + ".LEVEL1_NAME), '')";
        String level2 = "NVL(TRIM(" + alias + ".LEVEL2_NAME), '')";
        StringBuilder expr = new StringBuilder(level1);
        expr.append(" || CASE WHEN ").append(level2).append(" <> '' THEN CASE WHEN ").append(level1).append(" <> '' THEN '-' ELSE '' END || ").append(level2).append(" ELSE '' END");
        if (includeLevel3) {
            String level3 = "NVL(TRIM(" + alias + "." + level3Column + "), '')";
            expr.append(" || CASE WHEN ").append(level3).append(" <> '' THEN CASE WHEN ").append(level1).append(" <> '' OR ").append(level2).append(" <> '' THEN '-' ELSE '' END || ").append(level3).append(" ELSE '' END");
        }
        return expr.toString();
    }

    private static String buildRuleOptionDisplayExpr(String alias, boolean includeLevel3) throws SQLException {
        return buildHierarchicalDisplayExpr(alias, includeLevel3, "LEVELS3_NAME");
    }

    private static String buildPolicyUsageDisplayExpr(String alias) throws SQLException {
        String fallbackExpr = buildHierarchicalDisplayExpr(alias, true);
        return "NVL(NULLIF(TRIM(" + alias + ".OPTION_VALUE), ''), " + fallbackExpr + ")";
    }

    private static String normalizeHierarchyLookupKey(String text) {
        String normalized = nullToEmpty(text).trim();
        if (normalized.isEmpty()) return "";
        normalized = normalized
            .replace('—', '-')
            .replace('–', '-')
            .replace('－', '-')
            .replace('／', '/');
        normalized = normalized.replaceAll("\\s*[-/]+\\s*", "-");
        normalized = normalized.replaceAll("\\s+", " ");
        return normalized.trim();
    }

    private static String joinHierarchyParts(String... parts) {
        List<String> values = new ArrayList<>();
        Set<String> seen = new LinkedHashSet<>();
        if (parts != null) {
            for (String part : parts) {
                String text = nullToEmpty(part).trim();
                if (!text.isEmpty() && seen.add(text)) {
                    values.add(text);
                }
            }
        }
        return String.join("-", values);
    }

    private static void registerHierarchyLookup(Map<String, String> lookup, String rawKey, String fullText) {
        String key = normalizeHierarchyLookupKey(rawKey);
        String value = nullToEmpty(fullText).trim();
        if (key.isEmpty() || value.isEmpty()) return;
        lookup.putIfAbsent(key, value);
    }

    private static List<String> splitHierarchyTokens(String rawText) {
        String text = nullToEmpty(rawText).trim();
        if (text.isEmpty()) return Collections.emptyList();
        String[] pieces = text.split("\\s*[；;，,、\\n]+\\s*");
        List<String> values = new ArrayList<>();
        for (String piece : pieces) {
            String token = nullToEmpty(piece).trim();
            if (!token.isEmpty()) values.add(token);
        }
        return values;
    }

    private static String resolveHierarchyText(String rawText, Map<String, String> lookup) {
        String token = nullToEmpty(rawText).trim();
        if (token.isEmpty()) return "";
        String normalized = normalizeHierarchyLookupKey(token);
        if (!normalized.isEmpty()) {
            String mapped = lookup.get(normalized);
            if (mapped != null && !mapped.isEmpty()) return mapped;
        }
        String direct = lookup.get(token);
        return (direct == null || direct.isEmpty()) ? token : direct;
    }

    private static String expandHierarchyText(String rawText, Map<String, String> lookup) {
        List<String> tokens = splitHierarchyTokens(rawText);
        if (tokens.isEmpty()) return "";
        List<String> expanded = new ArrayList<>();
        for (String token : tokens) {
            String resolved = resolveHierarchyText(token, lookup);
            if (!resolved.isEmpty()) expanded.add(resolved);
        }
        return String.join("；", expanded);
    }

    private static Map<String, String> loadHierarchyLookupWithCache(String cacheKey, HierarchyLookupLoader loader) throws SQLException {
        long nowMillis = System.currentTimeMillis();
        HierarchyLookupCacheEntry cached = HIERARCHY_LOOKUP_CACHE.get(cacheKey);
        if (cached != null && cached.isFresh(nowMillis)) {
            return cached.data;
        }
        synchronized (HIERARCHY_LOOKUP_CACHE) {
            HierarchyLookupCacheEntry refreshed = HIERARCHY_LOOKUP_CACHE.get(cacheKey);
            long reloadAtMillis = System.currentTimeMillis();
            if (refreshed != null && refreshed.isFresh(reloadAtMillis)) {
                return refreshed.data;
            }
            Map<String, String> loaded = loader.load();
            HierarchyLookupCacheEntry entry = new HierarchyLookupCacheEntry(loaded, reloadAtMillis);
            HIERARCHY_LOOKUP_CACHE.put(cacheKey, entry);
            return entry.data;
        }
    }

    private static void invalidateHierarchyLookupCache() {
        HIERARCHY_LOOKUP_CACHE.clear();
    }

    private static Map<String, String> loadPolicyUsageHierarchyLookup() throws SQLException {
        return loadHierarchyLookupWithCache("policy_usage", () -> {
            Map<String, String> lookup = new LinkedHashMap<>();
            try (PreparedStatement stmt = conn.prepareStatement(
                "SELECT LEVEL1_NAME, LEVEL2_NAME, LEVEL3_NAME, OPTION_VALUE FROM SYSDBA.DIM_POLICY_USAGE WHERE STATUS = 1");
                 ResultSet rs = stmt.executeQuery()) {
                while (rs.next()) {
                    String level1 = nullToEmpty(rs.getString("LEVEL1_NAME"));
                    String level2 = nullToEmpty(rs.getString("LEVEL2_NAME"));
                    String level3 = nullToEmpty(rs.getString("LEVEL3_NAME"));
                    String optionValue = nullToEmpty(rs.getString("OPTION_VALUE"));
                    String fullText = joinHierarchyParts(level1, level2, level3);
                    if (fullText.isEmpty()) continue;
                    registerHierarchyLookup(lookup, fullText, fullText);
                    registerHierarchyLookup(lookup, optionValue, fullText);
                    registerHierarchyLookup(lookup, level3, fullText);
                    registerHierarchyLookup(lookup, level2, fullText);
                    registerHierarchyLookup(lookup, level1, fullText);
                }
            }
            return lookup;
        });
    }

    private static Map<String, String> loadRuleOptionHierarchyLookup(String ruleType) throws SQLException {
        if ("support_type".equals(ruleType)) {
            return loadHierarchyLookupWithCache("rule_option:support_type", () -> {
                Map<String, String> lookup = new LinkedHashMap<>();
                try (PreparedStatement stmt = conn.prepareStatement(
                    "SELECT FIRST_CATEGORY, SECOND_CATEGORY, THIRD_CATEGORY FROM SYSDBA.SYS_POLICY_SUPPORT_METHOD_22N WHERE STATUS = 1");
                     ResultSet rs = stmt.executeQuery()) {
                    while (rs.next()) {
                        String level1 = nullToEmpty(rs.getString("FIRST_CATEGORY"));
                        String level2 = nullToEmpty(rs.getString("SECOND_CATEGORY"));
                        String level3 = nullToEmpty(rs.getString("THIRD_CATEGORY"));
                        String fullText = joinHierarchyParts(level1, level2, level3);
                        if (fullText.isEmpty()) continue;
                        registerHierarchyLookup(lookup, fullText, fullText);
                        registerHierarchyLookup(lookup, level3, fullText);
                        registerHierarchyLookup(lookup, level2, fullText);
                        registerHierarchyLookup(lookup, level1, fullText);
                    }
                }
                return lookup;
            });
        }
        return loadHierarchyLookupWithCache("rule_option:" + nullToEmpty(ruleType).trim(), () -> {
            Map<String, String> lookup = new LinkedHashMap<>();
            try (PreparedStatement stmt = conn.prepareStatement(
                "SELECT LEVEL1_NAME, LEVEL2_NAME, LEVELS3_NAME FROM SYSDBA.DIM_RULE_OPTION WHERE RULE_TYPE = ? AND STATUS = 1")) {
                stmt.setString(1, ruleType);
                try (ResultSet rs = stmt.executeQuery()) {
                    while (rs.next()) {
                        String level1 = nullToEmpty(rs.getString("LEVEL1_NAME"));
                        String level2 = nullToEmpty(rs.getString("LEVEL2_NAME"));
                        String level3 = nullToEmpty(rs.getString("LEVELS3_NAME"));
                        String fullText = joinHierarchyParts(level1, level2, level3);
                        if (fullText.isEmpty()) continue;
                        registerHierarchyLookup(lookup, fullText, fullText);
                        registerHierarchyLookup(lookup, level3, fullText);
                        registerHierarchyLookup(lookup, level2, fullText);
                        registerHierarchyLookup(lookup, level1, fullText);
                    }
                }
            }
            return lookup;
        });
    }

    private static Map<String, String> loadKeywordHierarchyLookup() throws SQLException {
        return loadHierarchyLookupWithCache("policy_keyword", () -> {
            Map<String, String> lookup = new LinkedHashMap<>();
            try (PreparedStatement stmt = conn.prepareStatement(
                "SELECT LEVEL1_NAME, LEVEL2_NAME, LEVEL3_NAME FROM SYSDBA.POLICY_KEYWORD_LIBRARY WHERE STATUS = 1");
                 ResultSet rs = stmt.executeQuery()) {
                while (rs.next()) {
                    String level1 = nullToEmpty(rs.getString("LEVEL1_NAME"));
                    String level2 = nullToEmpty(rs.getString("LEVEL2_NAME"));
                    String name = nullToEmpty(rs.getString("LEVEL3_NAME"));
                    String fullText = joinHierarchyParts(level1, level2, name);
                    if (fullText.isEmpty()) continue;
                    registerHierarchyLookup(lookup, fullText, fullText);
                    registerHierarchyLookup(lookup, name, fullText);
                    registerHierarchyLookup(lookup, level2, fullText);
                    registerHierarchyLookup(lookup, level1, fullText);
                }
            }
            return lookup;
        });
    }

    private static Map<String, String> loadIndustryHierarchyLookup() throws SQLException {
        Map<String, String> lookup = new LinkedHashMap<>();
        String sql = "SELECT DISTINCT TRIM(TERRITORY_NAME) AS TERRITORY_NAME, TRIM(TERRITORY_TWO_NAME) AS TERRITORY_TWO_NAME " +
            "FROM SYSDBA.SYS_TERRITORY_DATA_NEW " +
            "WHERE DEL_FLAG = 0 " +
            "AND TERRITORY_NAME IS NOT NULL AND TRIM(TERRITORY_NAME) <> '' " +
            "AND ( (TYPE IS NOT NULL AND LOWER(TRIM(TYPE)) = 'policy') OR (TYPE_NAME IS NOT NULL AND TRIM(TYPE_NAME) = '政策法规') )";
        try (PreparedStatement stmt = conn.prepareStatement(sql);
             ResultSet rs = stmt.executeQuery()) {
            while (rs.next()) {
                String level1 = nullToEmpty(rs.getString("TERRITORY_NAME"));
                String level2 = nullToEmpty(rs.getString("TERRITORY_TWO_NAME"));
                String fullText = joinHierarchyParts(level1, level2);
                if (fullText.isEmpty()) continue;
                registerHierarchyLookup(lookup, fullText, fullText);
                registerHierarchyLookup(lookup, level2, fullText);
                registerHierarchyLookup(lookup, level1, fullText);
            }
        }
        return lookup;
    }

    private static String normalizedBaseDataSql(String type) throws SQLException {
        switch (type) {
            case "policy_usage":
                return "SELECT d.ID, d.LEVEL1_NAME AS LEVEL1, d.LEVEL2_NAME AS LEVEL2, " +
                    "d.LEVEL3_NAME AS LEVEL3, " + buildPolicyUsageDisplayExpr("d") + " AS NAME, d.OPTION_CODE AS CODE, d.DESCRIPTION, " +
                    "CASE WHEN d.STATUS = 1 THEN '启用' ELSE '禁用' END AS STATUS, " +
                    "d.CREATED_BY, d.UPDATED_BY, d.CREATED_AT AS CREATE_TIME, d.UPDATED_AT AS UPDATE_TIME FROM SYSDBA.DIM_POLICY_USAGE d";
            case "policy_keyword":
                return "SELECT d.ID, d.LEVEL1_NAME AS LEVEL1, d.LEVEL2_NAME AS LEVEL2, " +
                    "d.LEVEL3_NAME AS LEVEL3, NVL(NULLIF(TRIM(d.LEVEL3_NAME), ''), NVL(NULLIF(TRIM(d.LEVEL2_NAME), ''), TRIM(d.LEVEL1_NAME))) AS NAME, '' AS CODE, '' AS DESCRIPTION, " +
                    "CASE WHEN d.STATUS = 1 THEN '启用' ELSE '禁用' END AS STATUS, " +
                    "d.CREATED_BY, d.UPDATED_BY, d.CREATED_AT AS CREATE_TIME, d.UPDATED_AT AS UPDATE_TIME FROM SYSDBA.POLICY_KEYWORD_LIBRARY d";
            case "applicable_object":
                return normalizedRuleOptionSql("applicant_type");
            case "support_type":
                return "SELECT d.ID, d.FIRST_CATEGORY AS LEVEL1, d.SECOND_CATEGORY AS LEVEL2, " +
                    "d.THIRD_CATEGORY AS LEVEL3, " + buildSupportMethodDisplayExpr("d") + " AS NAME, '' AS CODE, '' AS DESCRIPTION, " +
                    "CASE WHEN d.STATUS = 1 THEN '启用' ELSE '禁用' END AS STATUS, " +
                    "'' AS CREATED_BY, '' AS UPDATED_BY, d.CREATE_TIME, d.UPDATE_TIME FROM SYSDBA.SYS_POLICY_SUPPORT_METHOD_22N d";
            case "applicable_scope":
                return normalizedRuleOptionSql("scope_level");
            default:
                return null;
        }
    }

    private static String normalizedRuleOptionSql(String ruleType) throws SQLException {
        return "SELECT d.ID, d.LEVEL1_NAME AS LEVEL1, d.LEVEL2_NAME AS LEVEL2, " +
            "d.LEVELS3_NAME AS LEVEL3, " +
            buildRuleOptionDisplayExpr("d", true) + " AS NAME, " +
            "d.OPTION_CODE AS CODE, '' AS DESCRIPTION, " +
            "CASE WHEN d.STATUS = 1 THEN '启用' ELSE '禁用' END AS STATUS, " +
            "d.CREATED_BY, d.UPDATED_BY, d.CREATED_AT AS CREATE_TIME, d.UPDATED_AT AS UPDATE_TIME FROM SYSDBA.DIM_RULE_OPTION d " +
            "WHERE d.RULE_TYPE = '" + ruleType + "'";
    }

    private static String buildSupportMethodDisplayExpr(String alias) {
        String level1 = "NVL(TRIM(" + alias + ".FIRST_CATEGORY), '')";
        String level2 = "NVL(TRIM(" + alias + ".SECOND_CATEGORY), '')";
        String level3 = "NVL(TRIM(" + alias + ".THIRD_CATEGORY), '')";
        return level1 +
            " || CASE WHEN " + level2 + " <> '' THEN CASE WHEN " + level1 + " <> '' THEN '-' ELSE '' END || " + level2 + " ELSE '' END" +
            " || CASE WHEN " + level3 + " <> '' THEN CASE WHEN " + level1 + " <> '' OR " + level2 + " <> '' THEN '-' ELSE '' END || " + level3 + " ELSE '' END";
    }

    private static int statusValue(String status) {
        return "禁用".equals(status) ? 0 : 1;
    }

    private static Map<String, Object> loadBaseDataDictRow(String id) throws SQLException {
        return querySingleRow("SELECT * FROM SYSDBA.BASE_DATA_DICT WHERE ID = ?", id);
    }

    private static Map<String, Object> loadApplicationConditionDictRow(String id) throws SQLException {
        return querySingleRow("SELECT * FROM SYSDBA.APPLICATION_CONDITION_DICT WHERE ID = ?", id);
    }

    private static Map<String, Object> loadCategoryFieldInfoRow(String id) throws SQLException {
        return querySingleRow("SELECT * FROM SYSDBA.CATEGORY_FIELD_INFO WHERE ID = ?", id);
    }

    private static String mappedBaseDataTableName(String type) {
        if ("policy_usage".equals(type)) return "DIM_POLICY_USAGE";
        if ("policy_keyword".equals(type)) return "POLICY_KEYWORD_LIBRARY";
        if ("support_type".equals(type)) return "SYS_POLICY_SUPPORT_METHOD_22N";
        if ("applicable_scope".equals(type) || "applicable_object".equals(type)) return "DIM_RULE_OPTION";
        return "";
    }

    private static Map<String, Object> loadMappedBaseDataRow(String type, String id) throws SQLException {
        String sourceSql = normalizedBaseDataSql(type);
        if (sourceSql == null) return null;
        return querySingleRow("SELECT * FROM (" + sourceSql + ") T WHERE T.ID = ?", id);
    }

    private static List<Map<String, Object>> loadMappedBaseDataRows(String type) throws SQLException {
        String sourceSql = normalizedBaseDataSql(type);
        if (sourceSql == null) return Collections.emptyList();
        return queryRows("SELECT * FROM (" + sourceSql + ") T");
    }

    private static void writeAuditMutation(Map<String, String> data, String defaultModule, String defaultAction,
                                           String targetTable, String targetId, String bizKey,
                                           Object beforeData, Object afterData, String remark) throws SQLException {
        writeAuditMutation(conn, data, defaultModule, defaultAction, targetTable, targetId, bizKey, beforeData, afterData, remark);
    }

    private static void writeAuditMutation(Connection connection, Map<String, String> data, String defaultModule, String defaultAction,
                                           String targetTable, String targetId, String bizKey,
                                           Object beforeData, Object afterData, String remark) throws SQLException {
        insertAuditLog(
            connection,
            resolveAuditTraceId(data),
            resolveAuditModule(data, defaultModule),
            resolveAuditAction(data, defaultAction),
            targetTable,
            targetId,
            bizKey,
            resolveAuditOperatorId(data),
            resolveAuditOperatorName(data),
            beforeData,
            afterData,
            remark
        );
    }

    private static Map<String, Object> loadPolicyMetadataRow(String policyId) throws SQLException {
        return loadPolicyMetadataRow(conn, policyId);
    }

    private static Map<String, Object> loadPolicyMetadataRow(Connection connection, String policyId) throws SQLException {
        return querySingleRow(connection, "SELECT * FROM SYSDBA.POLICY_METADATA WHERE POLICY_ID = ? AND ROWNUM = 1", policyId);
    }

    private static Map<String, Object> loadPolicyAttachmentRow(Connection connection, String attachmentId, String policyId) throws SQLException {
        return querySingleRow(connection,
            "SELECT * FROM SYSDBA.POLICY_ATTACHMENT WHERE ATTACH_ID = ? AND POLICY_ID = ? AND DEL_FLAG = '0' AND ROWNUM = 1",
            attachmentId, policyId);
    }

    private static List<Map<String, Object>> loadPolicyAttachmentRowsByPolicyId(Connection connection, String policyId) throws SQLException {
        return queryRows(connection,
            "SELECT * FROM SYSDBA.POLICY_ATTACHMENT WHERE TRIM(POLICY_ID) = ? AND DEL_FLAG = '0'",
            nullToEmpty(policyId).trim());
    }

    private static Map<String, Object> loadFusionPolicyRow(Connection connection, String fusionPolicyId) throws SQLException {
        return querySingleRow(connection, "SELECT * FROM SYSDBA.X1_FUSION_POLICY WHERE ID = ?", fusionPolicyId);
    }

    private static Map<String, Object> loadFusionDocumentRow(Connection connection, String documentId) throws SQLException {
        return querySingleRow(connection, "SELECT * FROM SYSDBA.X1_POLICY_DOCUMENT WHERE ID = ?", documentId);
    }

    private static List<Map<String, Object>> loadFusionDocumentRowsByFusionPolicyId(Connection connection, String fusionPolicyId) throws SQLException {
        return queryRows(connection,
            "SELECT * FROM SYSDBA.X1_POLICY_DOCUMENT WHERE FUSION_POLICY_ID = ? ORDER BY DISPLAY_ORDER, ID",
            fusionPolicyId);
    }

    private static Map<String, Object> loadFusionBaseInfoRowByDocumentId(Connection connection, String policyDocumentId) throws SQLException {
        return querySingleRow(connection, "SELECT * FROM SYSDBA.X1_POLICY_BASE_INFO WHERE POLICY_DOCUMENT_ID = ? AND ROWNUM = 1", policyDocumentId);
    }

    private static List<Map<String, Object>> loadFusionParentRelations(Connection connection, String childFusionPolicyId) throws SQLException {
        return queryRows(connection,
            "SELECT * FROM SYSDBA.X1_FUSION_POLICY_RELATION WHERE CHILD_FUSION_POLICY_ID = ? ORDER BY ID",
            childFusionPolicyId);
    }

    private static List<Map<String, Object>> loadFusionPolicyRelationRowsByFusionPolicyId(Connection connection, String fusionPolicyId) throws SQLException {
        return queryRows(connection,
            "SELECT * FROM SYSDBA.X1_FUSION_POLICY_RELATION WHERE PARENT_FUSION_POLICY_ID = ? OR CHILD_FUSION_POLICY_ID = ? ORDER BY ID",
            fusionPolicyId, fusionPolicyId);
    }

    private static Map<String, Object> loadFusionTraceCacheRow(Connection connection, String id) throws SQLException {
        return querySingleRow(connection, "SELECT * FROM SYSDBA.FUSION_TRACE_CACHE WHERE ID = ? AND ROWNUM = 1", id);
    }

    static class BaseDataRecordsHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if ("OPTIONS".equals(exchange.getRequestMethod())) {
                sendResponse(exchange, 200, "{\"success\":true}");
                return;
            }

            try {
                ensureConnection();
                Map<String, String> params = parseQueryParams(exchange.getRequestURI().getQuery());
                String type = params.getOrDefault("type", "");
                String sourceSql = normalizedBaseDataSql(type);
                if (sourceSql == null) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "不支持的基础数据类型: " + type));
                    return;
                }

                int page = Math.max(1, parseInt(params.getOrDefault("page", "1"), 1));
                int size = Math.min(100000, Math.max(1, parseInt(params.getOrDefault("size", "10"), 10)));
                int offset = (page - 1) * size;
                String level1 = params.getOrDefault("level1", "").trim();
                String level2 = params.getOrDefault("level2", "").trim();
                String keyword = params.getOrDefault("keyword", "").trim();

                StringBuilder where = new StringBuilder(" WHERE 1=1");
                List<String> values = new ArrayList<>();
                if (!level1.isEmpty()) {
                    where.append(" AND d.LEVEL1 = ?");
                    values.add(level1);
                }
                if (!level2.isEmpty()) {
                    where.append(" AND d.LEVEL2 = ?");
                    values.add(level2);
                }
                if (!keyword.isEmpty()) {
                    where.append(" AND (d.LEVEL1 LIKE ? OR d.LEVEL2 LIKE ? OR d.LEVEL3 LIKE ? OR d.NAME LIKE ? OR d.CODE LIKE ? OR d.DESCRIPTION LIKE ?)");
                    String likeKeyword = "%" + keyword + "%";
                    values.add(likeKeyword);
                    values.add(likeKeyword);
                    values.add(likeKeyword);
                    values.add(likeKeyword);
                    values.add(likeKeyword);
                    values.add(likeKeyword);
                }

                String wrappedSource = "(" + sourceSql + ") d";
                int total = 0;
                try (PreparedStatement stmt = conn.prepareStatement("SELECT COUNT(*) AS TOTAL FROM " + wrappedSource + where)) {
                    bindStrings(stmt, values);
                    try (ResultSet rs = stmt.executeQuery()) {
                        if (rs.next()) total = rs.getInt("TOTAL");
                    }
                }

                List<Map<String, Object>> rows = new ArrayList<>();
                String listSql = "SELECT d.* FROM " + wrappedSource + where +
                    " ORDER BY d.LEVEL1, d.LEVEL2, d.NAME LIMIT ? OFFSET ?";
                try (PreparedStatement stmt = conn.prepareStatement(listSql)) {
                    int index = bindStrings(stmt, values) + 1;
                    stmt.setInt(index++, size);
                    stmt.setInt(index, offset);
                    try (ResultSet rs = stmt.executeQuery()) {
                        while (rs.next()) rows.add(normalizedBaseDataRow(rs, type));
                    }
                }

                Map<String, Object> statistics = new LinkedHashMap<>();
                String statsSql = "SELECT COUNT(*) AS TOTAL, " +
                    "SUM(CASE WHEN d.STATUS = '启用' THEN 1 ELSE 0 END) AS ENABLED, " +
                    "SUM(CASE WHEN d.STATUS = '禁用' THEN 1 ELSE 0 END) AS DISABLED, " +
                    "COUNT(DISTINCT d.LEVEL1) AS CATEGORIES FROM " + wrappedSource;
                try (PreparedStatement stmt = conn.prepareStatement(statsSql);
                     ResultSet rs = stmt.executeQuery()) {
                    if (rs.next()) {
                        statistics.put("total", rs.getInt("TOTAL"));
                        statistics.put("enabled", rs.getInt("ENABLED"));
                        statistics.put("disabled", rs.getInt("DISABLED"));
                        statistics.put("categories", rs.getInt("CATEGORIES"));
                    }
                }

                List<Map<String, Object>> options = new ArrayList<>();
                String optionsSql = "SELECT DISTINCT d.LEVEL1, d.LEVEL2 FROM " + wrappedSource +
                    " WHERE d.LEVEL1 IS NOT NULL ORDER BY d.LEVEL1, d.LEVEL2";
                try (PreparedStatement stmt = conn.prepareStatement(optionsSql);
                     ResultSet rs = stmt.executeQuery()) {
                    while (rs.next()) {
                        options.add(buildMap(
                            "level1", nullToEmpty(rs.getString("LEVEL1")),
                            "level2", nullToEmpty(rs.getString("LEVEL2"))
                        ));
                    }
                }

                Map<String, Object> pagination = new LinkedHashMap<>();
                pagination.put("page", page);
                pagination.put("size", size);
                pagination.put("total", total);
                pagination.put("pages", (int) Math.ceil((double) total / size));
                sendJson(exchange, 200, buildMap(
                    "success", true,
                    "data", rows,
                    "statistics", statistics,
                    "options", options,
                    "pagination", pagination
                ));
            } catch (SQLException e) {
                System.err.println("[Base Data Records Query Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    static class BaseDataRecordCreateHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if (!"POST".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持 POST 方法"));
                return;
            }
            try {
                ensureConnection();
                Map<String, String> data = parseRequestJson(exchange);
                String type = data.getOrDefault("type", "");
                if (!isMappedBaseDataType(type)) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "不支持的基础数据类型: " + type));
                    return;
                }
                String level1 = data.getOrDefault("level1", "").trim();
                String level2 = data.getOrDefault("level2", "").trim();
                String level3 = normalizeBaseDataLevel3(data.getOrDefault("level3", ""), level2);
                String name = data.getOrDefault("name", "").trim();
                String code = data.getOrDefault("code", "").trim();
                String description = data.getOrDefault("description", "").trim();
                int status = statusValue(data.getOrDefault("status", "启用"));
                String operatorName = resolveAuditOperatorName(data);
                String resolvedName = ("policy_keyword".equals(type) || "applicable_object".equals(type) ||
                    "support_type".equals(type) || "applicable_scope".equals(type))
                    ? (!name.isEmpty() ? name : level3)
                    : name;
                boolean allowEmptyTerminal = "policy_keyword".equals(type) || "applicable_object".equals(type) ||
                    "support_type".equals(type) || "applicable_scope".equals(type);
                if (level1.isEmpty() || (!allowEmptyTerminal && resolvedName.isEmpty())) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "一级分类和名称不能为空"));
                    return;
                }
                if ("policy_usage".equals(type) && level2.isEmpty()) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "该模块的二级分类不能为空"));
                    return;
                }

                String id;
                int rows;
                if ("policy_usage".equals(type)) {
                    id = UUID.randomUUID().toString();
                    String sql = "INSERT INTO SYSDBA.DIM_POLICY_USAGE " +
                        "(ID, LEVEL1_NAME, LEVEL2_NAME, LEVEL3_NAME, DESCRIPTION, OPTION_VALUE, OPTION_CODE, SORT_ORDER, STATUS, CREATED_BY, UPDATED_BY, CREATED_AT, UPDATED_AT) " +
                        "VALUES (?, ?, ?, ?, ?, ?, ?, (SELECT COALESCE(MAX(SORT_ORDER),0)+10 FROM SYSDBA.DIM_POLICY_USAGE), ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)";
                    rows = executeUpdate(sql, id, level1, level2, level3, description, name, code, status, operatorName, operatorName);
                } else if ("policy_keyword".equals(type)) {
                    id = UUID.randomUUID().toString();
                    String sql = "INSERT INTO SYSDBA.POLICY_KEYWORD_LIBRARY " +
                        "(ID, LEVEL1_NAME, LEVEL2_NAME, LEVEL3_NAME, STATUS, CREATED_BY, UPDATED_BY, CREATED_AT, UPDATED_AT) " +
                        "VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)";
                    rows = executeUpdate(sql, id, level1, level2, resolvedName, status, operatorName, operatorName);
                } else if ("applicable_scope".equals(type)) {
                    id = UUID.randomUUID().toString().replace("-", "");
                    String sql = "INSERT INTO SYSDBA.DIM_RULE_OPTION " +
                        "(ID, RULE_TYPE, LEVEL1_NAME, LEVEL2_NAME, LEVELS3_NAME, OPTION_CODE, PARENT_ID, SORT_ORDER, STATUS, CREATED_BY, UPDATED_BY, CREATED_AT, UPDATED_AT) " +
                        "VALUES (?, 'scope_level', ?, ?, ?, ?, NULL, (SELECT COALESCE(MAX(SORT_ORDER),0)+10 FROM SYSDBA.DIM_RULE_OPTION WHERE RULE_TYPE='scope_level'), ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)";
                    rows = executeUpdate(sql, id, level1, level2, resolvedName, code, status, operatorName, operatorName);
                } else if ("support_type".equals(type)) {
                    long numericId = nextPolicySupportMethodId();
                    id = String.valueOf(numericId);
                    String sql = "INSERT INTO SYSDBA.SYS_POLICY_SUPPORT_METHOD_22N " +
                        "(ID, FIRST_CATEGORY, SECOND_CATEGORY, THIRD_CATEGORY, STATUS, CREATE_TIME, UPDATE_TIME) " +
                        "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)";
                    rows = executeUpdate(sql, numericId, level1, blankToNull(level2), blankToNull(resolvedName), status);
                } else {
                    id = UUID.randomUUID().toString().replace("-", "");
                    String sql = "INSERT INTO SYSDBA.DIM_RULE_OPTION " +
                        "(ID, RULE_TYPE, LEVEL1_NAME, LEVEL2_NAME, LEVELS3_NAME, OPTION_CODE, PARENT_ID, SORT_ORDER, STATUS, CREATED_BY, UPDATED_BY, CREATED_AT, UPDATED_AT) " +
                        "VALUES (?, 'applicant_type', ?, ?, ?, ?, NULL, (SELECT COALESCE(MAX(SORT_ORDER),0)+10 FROM SYSDBA.DIM_RULE_OPTION WHERE RULE_TYPE='applicant_type'), ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)";
                    rows = executeUpdate(sql, id, level1, level2, resolvedName, code, status, operatorName, operatorName);
                }
                if (rows > 0) {
                    writeAuditMutation(
                        data,
                        "base-data",
                        "create",
                        mappedBaseDataTableName(type),
                        id,
                        type,
                        null,
                        loadMappedBaseDataRow(type, id),
                        "基础数据新增"
                    );
                    invalidateHierarchyLookupCache();
                }
                sendJson(exchange, rows > 0 ? 200 : 500, buildMap(
                    "success", rows > 0,
                    "message", rows > 0 ? "创建成功" : "创建失败",
                    "data", buildMap("id", id)
                ));
            } catch (SQLException e) {
                System.err.println("[Base Data Record Create Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    static class BaseDataRecordUpdateHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if (!"POST".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持 POST 方法"));
                return;
            }
            try {
                ensureConnection();
                Map<String, String> data = parseRequestJson(exchange);
                String id = data.getOrDefault("id", "").trim();
                String type = data.getOrDefault("type", "");
                String level1 = data.getOrDefault("level1", "").trim();
                String level2 = data.getOrDefault("level2", "").trim();
                String level3 = normalizeBaseDataLevel3(data.getOrDefault("level3", ""), level2);
                String name = data.getOrDefault("name", "").trim();
                String code = data.getOrDefault("code", "").trim();
                String description = data.getOrDefault("description", "").trim();
                int status = statusValue(data.getOrDefault("status", "启用"));
                String operatorName = resolveAuditOperatorName(data);
                String resolvedName = ("policy_keyword".equals(type) || "applicable_object".equals(type) ||
                    "support_type".equals(type) || "applicable_scope".equals(type))
                    ? (!name.isEmpty() ? name : level3)
                    : name;
                if (id.isEmpty() || !isMappedBaseDataType(type)) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "ID 或数据类型无效"));
                    return;
                }

                Map<String, Object> beforeRow = loadMappedBaseDataRow(type, id);
                int rows;
                if ("policy_usage".equals(type)) {
                    rows = executeUpdate("UPDATE SYSDBA.DIM_POLICY_USAGE SET LEVEL1_NAME=?, LEVEL2_NAME=?, LEVEL3_NAME=?, " +
                        "DESCRIPTION=?, OPTION_VALUE=?, OPTION_CODE=?, STATUS=?, UPDATED_BY=?, UPDATED_AT=CURRENT_TIMESTAMP WHERE ID=?",
                        level1, level2, level3, description, name, code, status, operatorName, id);
                } else if ("policy_keyword".equals(type)) {
                    rows = executeUpdate("UPDATE SYSDBA.POLICY_KEYWORD_LIBRARY SET LEVEL1_NAME=?, LEVEL2_NAME=?, " +
                        "LEVEL3_NAME=?, STATUS=?, UPDATED_BY=?, UPDATED_AT=CURRENT_TIMESTAMP WHERE ID=?",
                        level1, level2, resolvedName, status, operatorName, id);
                } else if ("applicable_scope".equals(type)) {
                    rows = executeUpdate("UPDATE SYSDBA.DIM_RULE_OPTION SET LEVEL1_NAME=?, LEVEL2_NAME=?, LEVELS3_NAME=?, " +
                        "OPTION_CODE=?, STATUS=?, UPDATED_BY=?, UPDATED_AT=CURRENT_TIMESTAMP WHERE ID=? AND RULE_TYPE='scope_level'",
                        level1, level2, resolvedName, code, status, operatorName, id);
                } else if ("support_type".equals(type)) {
                    rows = executeUpdate("UPDATE SYSDBA.SYS_POLICY_SUPPORT_METHOD_22N SET FIRST_CATEGORY=?, SECOND_CATEGORY=?, THIRD_CATEGORY=?, " +
                        "STATUS=?, UPDATE_TIME=CURRENT_TIMESTAMP WHERE ID=?",
                        level1, blankToNull(level2), blankToNull(resolvedName), status, id);
                } else {
                    rows = executeUpdate("UPDATE SYSDBA.DIM_RULE_OPTION SET LEVEL1_NAME=?, LEVEL2_NAME=?, LEVELS3_NAME=?, " +
                        "OPTION_CODE=?, STATUS=?, UPDATED_BY=?, UPDATED_AT=CURRENT_TIMESTAMP WHERE ID=? AND RULE_TYPE='applicant_type'",
                        level1, level2, resolvedName, code, status, operatorName, id);
                }
                if (rows > 0) {
                    writeAuditMutation(
                        data,
                        "base-data",
                        "update",
                        mappedBaseDataTableName(type),
                        id,
                        type,
                        beforeRow,
                        loadMappedBaseDataRow(type, id),
                        "基础数据更新"
                    );
                    invalidateHierarchyLookupCache();
                }
                sendJson(exchange, rows > 0 ? 200 : 404, buildMap(
                    "success", rows > 0,
                    "message", rows > 0 ? "更新成功" : "更新失败，记录不存在"
                ));
            } catch (SQLException e) {
                System.err.println("[Base Data Record Update Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    static class BaseDataRecordDeleteHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if (!"POST".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持 POST 方法"));
                return;
            }
            try {
                ensureConnection();
                Map<String, String> data = parseRequestJson(exchange);
                String id = data.getOrDefault("id", "").trim();
                String type = data.getOrDefault("type", "");
                if (id.isEmpty() || !isMappedBaseDataType(type)) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "ID 或数据类型无效"));
                    return;
                }
                Map<String, Object> beforeRow = loadMappedBaseDataRow(type, id);
                int rows;
                if ("policy_usage".equals(type)) {
                    rows = executeUpdate("DELETE FROM SYSDBA.DIM_POLICY_USAGE WHERE ID=?", id);
                } else if ("policy_keyword".equals(type)) {
                    rows = executeUpdate("DELETE FROM SYSDBA.POLICY_KEYWORD_LIBRARY WHERE ID=?", id);
                } else if ("applicable_scope".equals(type)) {
                    rows = executeUpdate("DELETE FROM SYSDBA.DIM_RULE_OPTION WHERE ID=? AND RULE_TYPE='scope_level'", id);
                } else if ("support_type".equals(type)) {
                    rows = executeUpdate("DELETE FROM SYSDBA.SYS_POLICY_SUPPORT_METHOD_22N WHERE ID=?", id);
                } else {
                    rows = executeUpdate("DELETE FROM SYSDBA.DIM_RULE_OPTION WHERE ID=? AND RULE_TYPE='applicant_type'", id);
                }
                if (rows > 0) {
                    writeAuditMutation(
                        data,
                        "base-data",
                        "delete",
                        mappedBaseDataTableName(type),
                        id,
                        type,
                        beforeRow,
                        null,
                        "基础数据删除"
                    );
                    invalidateHierarchyLookupCache();
                }
                sendJson(exchange, rows > 0 ? 200 : 404, buildMap(
                    "success", rows > 0,
                    "message", rows > 0 ? "删除成功" : "删除失败，记录不存在"
                ));
            } catch (SQLException e) {
                System.err.println("[Base Data Record Delete Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    // ==================== 基础数据记录清空接口 ====================

    /**
     * 清空指定类型的所有基础数据记录
     * POST /api/baseDataRecords/clear
     * Body: { "type": "policy_keyword" }
     */
    static class BaseDataRecordClearHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if (!"POST".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持 POST 方法"));
                return;
            }
            try {
                ensureConnection();
                Map<String, String> data = parseRequestJson(exchange);
                String type = data.getOrDefault("type", "").trim();

                if (!"policy_keyword".equals(type)) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "不支持的数据类型: " + type));
                    return;
                }

                String tableName = "SYSDBA.POLICY_KEYWORD_LIBRARY";
                String sql = "DELETE FROM " + tableName;

                List<Map<String, Object>> beforeRows = loadMappedBaseDataRows(type);
                int rows = executeUpdate(sql);
                if (rows > 0) {
                    writeAuditMutation(
                        data,
                        "base-data",
                        "clear",
                        mappedBaseDataTableName(type),
                        type,
                        type,
                        beforeRows,
                        buildMap("type", type, "deletedCount", rows),
                        "基础数据批量清空"
                    );
                    invalidateHierarchyLookupCache();
                }

                sendJson(exchange, 200, buildMap(
                    "success", true,
                    "message", "已清空 " + rows + " 条记录"
                ));
            } catch (SQLException e) {
                System.err.println("[Base Data Record Clear Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    private static Map<String, String> parseRequestJson(HttpExchange exchange) throws IOException {
        return parseJsonObject(new String(readAllBytes(exchange.getRequestBody()), StandardCharsets.UTF_8));
    }

    private static int bindStrings(PreparedStatement stmt, List<String> values) throws SQLException {
        for (int i = 0; i < values.size(); i++) stmt.setString(i + 1, values.get(i));
        return values.size();
    }

    private static String normalizeBaseDataLevel3(String level3, String level2) {
        String normalizedLevel3 = nullToEmpty(level3).trim();
        String normalizedLevel2 = nullToEmpty(level2).trim();
        return !normalizedLevel3.isEmpty() && normalizedLevel3.equals(normalizedLevel2) ? "" : normalizedLevel3;
    }

    private static String blankToNull(String value) {
        String text = nullToEmpty(value).trim();
        return text.isEmpty() ? null : text;
    }

    private static Map<String, Object> normalizedBaseDataRow(ResultSet rs, String type) throws SQLException {
        Map<String, Object> row = new LinkedHashMap<>();
        String level2 = nullToEmpty(rs.getString("LEVEL2"));
        String level3 = ("policy_usage".equals(type) || "policy_keyword".equals(type) || "support_type".equals(type) ||
            "applicable_object".equals(type) || "applicable_scope".equals(type))
            ? normalizeBaseDataLevel3(rs.getString("LEVEL3"), level2)
            : "";
        row.put("id", nullToEmpty(rs.getString("ID")));
        row.put("type", type);
        row.put("level1", nullToEmpty(rs.getString("LEVEL1")));
        row.put("level2", level2);
        row.put("level3", level3);
        row.put("name", nullToEmpty(rs.getString("NAME")));
        row.put("code", nullToEmpty(rs.getString("CODE")));
        row.put("description", nullToEmpty(rs.getString("DESCRIPTION")));
        row.put("status", nullToEmpty(rs.getString("STATUS")));
        row.put("createdBy", nullToEmpty(rs.getString("CREATED_BY")));
        row.put("updatedBy", nullToEmpty(rs.getString("UPDATED_BY")));
        row.put("createTime", nullToEmpty(rs.getString("CREATE_TIME")));
        row.put("updateTime", nullToEmpty(rs.getString("UPDATE_TIME")));
        return row;
    }

    private static int executeUpdate(String sql, Object... values) throws SQLException {
        try (PreparedStatement stmt = conn.prepareStatement(sql)) {
            for (int i = 0; i < values.length; i++) bindStatementValue(stmt, i + 1, values[i]);
            return stmt.executeUpdate();
        }
    }

    private static void bindStatementValue(PreparedStatement stmt, int index, Object value) throws SQLException {
        if (value == null) {
            stmt.setObject(index, null);
            return;
        }
        if (value instanceof String) {
            String text = (String) value;
            if (text.length() > 3500) {
                stmt.setCharacterStream(index, new StringReader(text), text.length());
            } else {
                stmt.setString(index, text);
            }
            return;
        }
        stmt.setObject(index, value);
    }

    private static String nextTerritoryId() throws SQLException {
        try (PreparedStatement stmt = conn.prepareStatement("SELECT SYSDBA.SEQ_SYS_TERRITORY_DATA_NEW.NEXTVAL AS ID");
             ResultSet rs = stmt.executeQuery()) {
            if (!rs.next()) throw new SQLException("无法生成地域数据 ID");
            return rs.getString("ID");
        }
    }

    private static long nextPolicySupportMethodId() throws SQLException {
        try (PreparedStatement stmt = conn.prepareStatement("SELECT COALESCE(MAX(ID), 0) + 1 AS ID FROM SYSDBA.SYS_POLICY_SUPPORT_METHOD_22N");
             ResultSet rs = stmt.executeQuery()) {
            if (!rs.next()) throw new SQLException("无法生成扶持类型 ID");
            return rs.getLong("ID");
        }
    }

    // ==================== 基础数据字典接口 (保留兼容) ====================

    /**
     * 获取基础数据字典列表
     * GET /api/baseDataDict?type=xxx&level1=xxx&level2=xxx&keyword=xxx&page=1&size=10
     */
    static class BaseDataDictHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);

            if ("OPTIONS".equals(exchange.getRequestMethod())) {
                sendResponse(exchange, 200, "{\"success\":true}");
                return;
            }

            try {
                ensureConnection();
                Map<String, String> params = parseQueryParams(exchange.getRequestURI().getQuery());
                int page = parseInt(params.getOrDefault("page", "1"), 1);
                int size = parseInt(params.getOrDefault("size", "10"), 10);
                String type = params.getOrDefault("type", "");
                String level1 = params.getOrDefault("level1", "");
                String level2 = params.getOrDefault("level2", "");
                String keyword = params.getOrDefault("keyword", "");

                int offset = (page - 1) * size;

                // 构建 WHERE 条件
                StringBuilder where = new StringBuilder(" WHERE 1=1");
                List<String> paramList = new ArrayList<>();

                if (!type.isEmpty()) {
                    where.append(" AND TYPE = ?");
                    paramList.add(type);
                }
                if (!level1.isEmpty()) {
                    where.append(" AND LEVEL1 = ?");
                    paramList.add(level1);
                }
                if (!level2.isEmpty()) {
                    where.append(" AND LEVEL2 = ?");
                    paramList.add(level2);
                }
                if (!keyword.isEmpty()) {
                    where.append(" AND (NAME LIKE ? OR CODE LIKE ? OR DESCRIPTION LIKE ?)");
                    paramList.add("%" + keyword + "%");
                    paramList.add("%" + keyword + "%");
                    paramList.add("%" + keyword + "%");
                }

                // 获取总数
                String countSql = "SELECT COUNT(*) AS TOTAL FROM SYSDBA.BASE_DATA_DICT" + where;
                PreparedStatement countStmt = conn.prepareStatement(countSql);
                for (int i = 0; i < paramList.size(); i++) {
                    countStmt.setString(i + 1, paramList.get(i));
                }
                ResultSet countRs = countStmt.executeQuery();
                int total = 0;
                if (countRs.next()) {
                    total = countRs.getInt("TOTAL");
                }
                countRs.close();
                countStmt.close();

                // 分页查询
                String querySql = "SELECT * FROM SYSDBA.BASE_DATA_DICT" + where +
                    " ORDER BY CODE ASC LIMIT ? OFFSET ?";
                paramList.add(String.valueOf(size));
                paramList.add(String.valueOf(offset));

                PreparedStatement stmt = conn.prepareStatement(querySql);
                for (int i = 0; i < paramList.size(); i++) {
                    stmt.setString(i + 1, paramList.get(i));
                }

                ResultSet rs = stmt.executeQuery();
                List<Map<String, Object>> dataList = new ArrayList<>();

                while (rs.next()) {
                    Map<String, Object> row = new LinkedHashMap<>();
                    row.put("id", nullToEmpty(rs.getString("ID")));
                    row.put("type", nullToEmpty(rs.getString("TYPE")));
                    row.put("level1", nullToEmpty(rs.getString("LEVEL1")));
                    row.put("level2", nullToEmpty(rs.getString("LEVEL2")));
                    row.put("name", nullToEmpty(rs.getString("NAME")));
                    row.put("code", nullToEmpty(rs.getString("CODE")));
                    row.put("description", nullToEmpty(rs.getString("DESCRIPTION")));
                    row.put("status", nullToEmpty(rs.getString("STATUS")));
                    row.put("createdBy", nullToEmpty(rs.getString("CREATED_BY")));
                    row.put("updatedBy", nullToEmpty(rs.getString("UPDATED_BY")));
                    row.put("createTime", nullToEmpty(rs.getString("CREATE_TIME")));
                    row.put("updateTime", nullToEmpty(rs.getString("UPDATE_TIME")));
                    dataList.add(row);
                }
                rs.close();
                stmt.close();

                Map<String, Object> response = new LinkedHashMap<>();
                response.put("success", true);
                response.put("data", dataList);
                response.put("message", "");
                Map<String, Object> pagination = new LinkedHashMap<>();
                pagination.put("page", page);
                pagination.put("size", size);
                pagination.put("total", total);
                pagination.put("pages", (int) Math.ceil((double) total / size));
                response.put("pagination", pagination);

                sendJson(exchange, 200, response);

            } catch (SQLException e) {
                System.err.println("[SQL Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /**
     * 创建基础数据字典
     * POST /api/baseDataDict/create
     */
    static class BaseDataDictCreateHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);

            if (!"POST".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持 POST 方法"));
                return;
            }

            try {
                ensureConnection();
                String body = new String(readAllBytes(exchange.getRequestBody()), StandardCharsets.UTF_8);
                Map<String, String> data = parseJsonObject(body);

                String type = data.getOrDefault("type", "");
                String level1 = data.getOrDefault("level1", "");
                String level2 = data.getOrDefault("level2", "");
                String name = data.getOrDefault("name", "");
                String code = data.getOrDefault("code", "");
                String description = data.getOrDefault("description", "");
                String status = data.getOrDefault("status", "启用");
                String operatorName = resolveAuditOperatorName(data);

                // 验证必填字段
                if (type.isEmpty() || name.isEmpty()) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "缺少必填字段: type 和 name"));
                    return;
                }

                // 生成 UUID
                String id = UUID.randomUUID().toString();

                String sql = "INSERT INTO SYSDBA.BASE_DATA_DICT " +
                    "(ID, TYPE, LEVEL1, LEVEL2, NAME, CODE, DESCRIPTION, STATUS, CREATED_BY, UPDATED_BY, CREATE_TIME, UPDATE_TIME) " +
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)";

                PreparedStatement stmt = conn.prepareStatement(sql);
                stmt.setString(1, id);
                stmt.setString(2, type);
                stmt.setString(3, level1);
                stmt.setString(4, level2);
                stmt.setString(5, name);
                stmt.setString(6, code);
                stmt.setString(7, description);
                stmt.setString(8, status);
                stmt.setString(9, operatorName);
                stmt.setString(10, operatorName);

                int rows = stmt.executeUpdate();
                stmt.close();
                if (rows > 0) {
                    writeAuditMutation(data, "base-data", "create", "BASE_DATA_DICT", id, type, null, loadBaseDataDictRow(id), "基础字典新增");
                }

                Map<String, Object> response = new LinkedHashMap<>();
                response.put("success", rows > 0);
                response.put("message", rows > 0 ? "创建成功" : "创建失败");
                response.put("data", buildMap("id", id));
                response.put("pagination", new LinkedHashMap<>());

                sendJson(exchange, rows > 0 ? 200 : 500, response);

            } catch (SQLException e) {
                System.err.println("[SQL Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /**
     * 更新基础数据字典
     * POST /api/baseDataDict/update
     */
    static class BaseDataDictUpdateHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);

            if (!"POST".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持 POST 方法"));
                return;
            }

            try {
                ensureConnection();
                String body = new String(readAllBytes(exchange.getRequestBody()), StandardCharsets.UTF_8);
                Map<String, String> data = parseJsonObject(body);

                String id = data.getOrDefault("id", "");
                String type = data.getOrDefault("type", "");
                String level1 = data.getOrDefault("level1", "");
                String level2 = data.getOrDefault("level2", "");
                String name = data.getOrDefault("name", "");
                String code = data.getOrDefault("code", "");
                String description = data.getOrDefault("description", "");
                String status = data.getOrDefault("status", "启用");
                String operatorName = resolveAuditOperatorName(data);

                // 验证必填字段
                if (id.isEmpty()) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "缺少必填字段: id"));
                    return;
                }

                Map<String, Object> beforeRow = loadBaseDataDictRow(id);
                String sql = "UPDATE SYSDBA.BASE_DATA_DICT SET " +
                    "TYPE = ?, LEVEL1 = ?, LEVEL2 = ?, NAME = ?, CODE = ?, " +
                    "DESCRIPTION = ?, STATUS = ?, UPDATED_BY = ?, UPDATE_TIME = CURRENT_TIMESTAMP " +
                    "WHERE ID = ?";

                PreparedStatement stmt = conn.prepareStatement(sql);
                stmt.setString(1, type);
                stmt.setString(2, level1);
                stmt.setString(3, level2);
                stmt.setString(4, name);
                stmt.setString(5, code);
                stmt.setString(6, description);
                stmt.setString(7, status);
                stmt.setString(8, operatorName);
                stmt.setString(9, id);

                int rows = stmt.executeUpdate();
                stmt.close();
                if (rows > 0) {
                    writeAuditMutation(data, "base-data", "update", "BASE_DATA_DICT", id, type, beforeRow, loadBaseDataDictRow(id), "基础字典更新");
                }

                Map<String, Object> response = new LinkedHashMap<>();
                response.put("success", rows > 0);
                response.put("message", rows > 0 ? "更新成功" : "更新失败，记录不存在");
                response.put("data", new ArrayList<>());
                response.put("pagination", new LinkedHashMap<>());

                sendJson(exchange, rows > 0 ? 200 : 404, response);

            } catch (SQLException e) {
                System.err.println("[SQL Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /**
     * 删除基础数据字典
     * POST /api/baseDataDict/delete
     */
    static class BaseDataDictDeleteHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);

            if (!"POST".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持 POST 方法"));
                return;
            }

            try {
                ensureConnection();
                String body = new String(readAllBytes(exchange.getRequestBody()), StandardCharsets.UTF_8);
                Map<String, String> data = parseJsonObject(body);

                String id = data.getOrDefault("id", "");

                // 验证必填字段
                if (id.isEmpty()) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "缺少必填字段: id"));
                    return;
                }

                Map<String, Object> beforeRow = loadBaseDataDictRow(id);
                String sql = "DELETE FROM SYSDBA.BASE_DATA_DICT WHERE ID = ?";

                PreparedStatement stmt = conn.prepareStatement(sql);
                stmt.setString(1, id);

                int rows = stmt.executeUpdate();
                stmt.close();
                if (rows > 0) {
                    writeAuditMutation(data, "base-data", "delete", "BASE_DATA_DICT", id, null, beforeRow, null, "基础字典删除");
                }

                Map<String, Object> response = new LinkedHashMap<>();
                response.put("success", rows > 0);
                response.put("message", rows > 0 ? "删除成功" : "删除失败，记录不存在");
                response.put("data", new ArrayList<>());
                response.put("pagination", new LinkedHashMap<>());

                sendJson(exchange, rows > 0 ? 200 : 404, response);

            } catch (SQLException e) {
                System.err.println("[SQL Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    // ==================== 申报条件字典接口 (合并新增) ====================

    /**
     * 获取申报条件字典列表
     * GET /api/applicationConditionDict?applyType=xxx&keyword=xxx&page=1&size=10
     */
    static class ApplicationConditionDictHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);

            if ("OPTIONS".equals(exchange.getRequestMethod())) {
                sendResponse(exchange, 200, "{\"success\":true}");
                return;
            }

            try {
                ensureConnection();
                Map<String, String> params = parseQueryParams(exchange.getRequestURI().getQuery());
                int page = parseInt(params.getOrDefault("page", "1"), 1);
                int size = parseInt(params.getOrDefault("size", "10"), 10);
                String applyType = params.getOrDefault("applyType", "");
                String level1 = params.getOrDefault("level1", "");
                String level2 = params.getOrDefault("level2", "");
                String level3 = params.getOrDefault("level3", "");
                String keyword = params.getOrDefault("keyword", "");

                int offset = (page - 1) * size;

                // 构建 WHERE 条件
                StringBuilder where = new StringBuilder(" WHERE 1=1");
                List<String> paramList = new ArrayList<>();

                if (!applyType.isEmpty()) {
                    where.append(" AND APPLY_TYPE = ?");
                    paramList.add(applyType);
                }
                if (!level1.isEmpty()) {
                    where.append(" AND LEVEL1 = ?");
                    paramList.add(level1);
                }
                if (!level2.isEmpty()) {
                    where.append(" AND LEVEL2 = ?");
                    paramList.add(level2);
                }
                if (!level3.isEmpty()) {
                    where.append(" AND LEVEL3 = ?");
                    paramList.add(level3);
                }
                if (!keyword.isEmpty()) {
                    where.append(" AND (NAME LIKE ? OR KEYWORDS LIKE ? OR DESCRIPTION LIKE ?)");
                    paramList.add("%" + keyword + "%");
                    paramList.add("%" + keyword + "%");
                    paramList.add("%" + keyword + "%");
                }

                // 获取总数
                String countSql = "SELECT COUNT(*) AS TOTAL FROM SYSDBA.APPLICATION_CONDITION_DICT" + where;
                PreparedStatement countStmt = conn.prepareStatement(countSql);
                for (int i = 0; i < paramList.size(); i++) {
                    countStmt.setString(i + 1, paramList.get(i));
                }
                ResultSet countRs = countStmt.executeQuery();
                int total = 0;
                if (countRs.next()) {
                    total = countRs.getInt("TOTAL");
                }
                countRs.close();
                countStmt.close();

                // 分页查询
                String querySql = "SELECT * FROM SYSDBA.APPLICATION_CONDITION_DICT" + where +
                    " ORDER BY CODE ASC LIMIT ? OFFSET ?";
                paramList.add(String.valueOf(size));
                paramList.add(String.valueOf(offset));

                PreparedStatement stmt = conn.prepareStatement(querySql);
                for (int i = 0; i < paramList.size(); i++) {
                    stmt.setString(i + 1, paramList.get(i));
                }

                ResultSet rs = stmt.executeQuery();
                List<Map<String, Object>> dataList = new ArrayList<>();

                while (rs.next()) {
                    Map<String, Object> row = new LinkedHashMap<>();
                    row.put("id", nullToEmpty(rs.getString("ID")));
                    row.put("code", nullToEmpty(rs.getString("CODE")));
                    row.put("applyType", nullToEmpty(rs.getString("APPLY_TYPE")));
                    row.put("level1", nullToEmpty(rs.getString("LEVEL1")));
                    row.put("level2", nullToEmpty(rs.getString("LEVEL2")));
                    row.put("level3", nullToEmpty(rs.getString("LEVEL3")));
                    row.put("name", nullToEmpty(rs.getString("NAME")));
                    row.put("keywords", nullToEmpty(rs.getString("KEYWORDS")));
                    row.put("description", nullToEmpty(rs.getString("DESCRIPTION")));
                    row.put("status", nullToEmpty(rs.getString("STATUS")));
                    row.put("createdBy", nullToEmpty(rs.getString("CREATED_BY")));
                    row.put("updatedBy", nullToEmpty(rs.getString("UPDATED_BY")));
                    row.put("createTime", nullToEmpty(rs.getString("CREATE_TIME")));
                    row.put("updateTime", nullToEmpty(rs.getString("UPDATE_TIME")));
                    dataList.add(row);
                }
                rs.close();
                stmt.close();

                Map<String, Object> response = new LinkedHashMap<>();
                response.put("success", true);
                response.put("data", dataList);
                response.put("message", "");
                Map<String, Object> pagination = new LinkedHashMap<>();
                pagination.put("page", page);
                pagination.put("size", size);
                pagination.put("total", total);
                pagination.put("pages", (int) Math.ceil((double) total / size));
                response.put("pagination", pagination);

                sendJson(exchange, 200, response);

            } catch (SQLException e) {
                System.err.println("[SQL Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /**
     * 创建申报条件字典
     * POST /api/applicationConditionDict/create
     */
    static class ApplicationConditionDictCreateHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);

            if (!"POST".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持 POST 方法"));
                return;
            }

            try {
                ensureConnection();
                String body = new String(readAllBytes(exchange.getRequestBody()), StandardCharsets.UTF_8);
                Map<String, String> data = parseJsonObject(body);

                String code = data.getOrDefault("code", "");
                String applyType = data.getOrDefault("applyType", "");
                String level1 = data.getOrDefault("level1", "");
                String level2 = data.getOrDefault("level2", "");
                String level3 = data.getOrDefault("level3", "");
                String name = data.getOrDefault("name", "");
                String keywords = data.getOrDefault("keywords", "");
                String description = data.getOrDefault("description", "");
                String status = data.getOrDefault("status", "启用");
                String operatorName = resolveAuditOperatorName(data);

                // 验证必填字段
                if (applyType.isEmpty() || name.isEmpty()) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "缺少必填字段: applyType 和 name"));
                    return;
                }

                // 生成 UUID
                String id = UUID.randomUUID().toString();

                String sql = "INSERT INTO SYSDBA.APPLICATION_CONDITION_DICT " +
                    "(ID, CODE, APPLY_TYPE, LEVEL1, LEVEL2, LEVEL3, NAME, KEYWORDS, DESCRIPTION, STATUS, CREATED_BY, UPDATED_BY, CREATE_TIME, UPDATE_TIME) " +
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)";

                PreparedStatement stmt = conn.prepareStatement(sql);
                stmt.setString(1, id);
                stmt.setString(2, code);
                stmt.setString(3, applyType);
                stmt.setString(4, level1);
                stmt.setString(5, level2);
                stmt.setString(6, level3);
                stmt.setString(7, name);
                stmt.setString(8, keywords);
                stmt.setString(9, description);
                stmt.setString(10, status);
                stmt.setString(11, operatorName);
                stmt.setString(12, operatorName);

                int rows = stmt.executeUpdate();
                stmt.close();
                if (rows > 0) {
                    writeAuditMutation(data, "base-data", "create", "APPLICATION_CONDITION_DICT", id, applyType, null, loadApplicationConditionDictRow(id), "申报条件字典新增");
                }

                Map<String, Object> response = new LinkedHashMap<>();
                response.put("success", rows > 0);
                response.put("message", rows > 0 ? "创建成功" : "创建失败");
                response.put("data", buildMap("id", id));
                response.put("pagination", new LinkedHashMap<>());

                sendJson(exchange, rows > 0 ? 200 : 500, response);

            } catch (SQLException e) {
                System.err.println("[SQL Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /**
     * 更新申报条件字典
     * POST /api/applicationConditionDict/update
     */
    static class ApplicationConditionDictUpdateHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);

            if (!"POST".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持 POST 方法"));
                return;
            }

            try {
                ensureConnection();
                String body = new String(readAllBytes(exchange.getRequestBody()), StandardCharsets.UTF_8);
                Map<String, String> data = parseJsonObject(body);

                String id = data.getOrDefault("id", "");
                String code = data.getOrDefault("code", "");
                String applyType = data.getOrDefault("applyType", "");
                String level1 = data.getOrDefault("level1", "");
                String level2 = data.getOrDefault("level2", "");
                String level3 = data.getOrDefault("level3", "");
                String name = data.getOrDefault("name", "");
                String keywords = data.getOrDefault("keywords", "");
                String description = data.getOrDefault("description", "");
                String status = data.getOrDefault("status", "启用");
                String operatorName = resolveAuditOperatorName(data);

                // 验证必填字段
                if (id.isEmpty()) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "缺少必填字段: id"));
                    return;
                }

                Map<String, Object> beforeRow = loadApplicationConditionDictRow(id);
                String sql = "UPDATE SYSDBA.APPLICATION_CONDITION_DICT SET " +
                    "CODE = ?, APPLY_TYPE = ?, LEVEL1 = ?, LEVEL2 = ?, LEVEL3 = ?, " +
                    "NAME = ?, KEYWORDS = ?, DESCRIPTION = ?, STATUS = ?, UPDATED_BY = ?, UPDATE_TIME = CURRENT_TIMESTAMP " +
                    "WHERE ID = ?";

                PreparedStatement stmt = conn.prepareStatement(sql);
                stmt.setString(1, code);
                stmt.setString(2, applyType);
                stmt.setString(3, level1);
                stmt.setString(4, level2);
                stmt.setString(5, level3);
                stmt.setString(6, name);
                stmt.setString(7, keywords);
                stmt.setString(8, description);
                stmt.setString(9, status);
                stmt.setString(10, operatorName);
                stmt.setString(11, id);

                int rows = stmt.executeUpdate();
                stmt.close();
                if (rows > 0) {
                    writeAuditMutation(data, "base-data", "update", "APPLICATION_CONDITION_DICT", id, applyType, beforeRow, loadApplicationConditionDictRow(id), "申报条件字典更新");
                }

                Map<String, Object> response = new LinkedHashMap<>();
                response.put("success", rows > 0);
                response.put("message", rows > 0 ? "更新成功" : "更新失败，记录不存在");
                response.put("data", new ArrayList<>());
                response.put("pagination", new LinkedHashMap<>());

                sendJson(exchange, rows > 0 ? 200 : 404, response);

            } catch (SQLException e) {
                System.err.println("[SQL Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /**
     * 删除申报条件字典
     * POST /api/applicationConditionDict/delete
     */
    static class ApplicationConditionDictDeleteHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);

            if (!"POST".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持 POST 方法"));
                return;
            }

            try {
                ensureConnection();
                String body = new String(readAllBytes(exchange.getRequestBody()), StandardCharsets.UTF_8);
                Map<String, String> data = parseJsonObject(body);

                String id = data.getOrDefault("id", "");

                // 验证必填字段
                if (id.isEmpty()) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "缺少必填字段: id"));
                    return;
                }

                Map<String, Object> beforeRow = loadApplicationConditionDictRow(id);
                String sql = "DELETE FROM SYSDBA.APPLICATION_CONDITION_DICT WHERE ID = ?";

                PreparedStatement stmt = conn.prepareStatement(sql);
                stmt.setString(1, id);

                int rows = stmt.executeUpdate();
                stmt.close();
                if (rows > 0) {
                    writeAuditMutation(data, "base-data", "delete", "APPLICATION_CONDITION_DICT", id, null, beforeRow, null, "申报条件字典删除");
                }

                Map<String, Object> response = new LinkedHashMap<>();
                response.put("success", rows > 0);
                response.put("message", rows > 0 ? "删除成功" : "删除失败，记录不存在");
                response.put("data", new ArrayList<>());
                response.put("pagination", new LinkedHashMap<>());

                sendJson(exchange, rows > 0 ? 200 : 404, response);

            } catch (SQLException e) {
                System.err.println("[SQL Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    // ==================== 工具方法 ====================

    static class FusionTraceCacheHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if ("OPTIONS".equals(exchange.getRequestMethod())) { exchange.sendResponseHeaders(204, -1); return; }
            if (!"GET".equals(exchange.getRequestMethod())) { sendJson(exchange, 405, buildMap("success", false, "message", "Only GET is supported")); return; }
            try {
                ensureConnection();
                Map<String, String> params = parseQueryParams(exchange.getRequestURI().getQuery());
                String fusionPolicyId = nullToEmpty(params.get("fusionPolicyId")).trim();
                String fieldCode = nullToEmpty(params.get("fieldCode")).trim();
                String valueHash = nullToEmpty(params.get("valueHash")).trim();
                if (fusionPolicyId.isEmpty()) { sendJson(exchange, 400, buildMap("success", false, "message", "fusionPolicyId is required")); return; }
                StringBuilder sql = new StringBuilder("SELECT ID,FUSION_POLICY_ID,FIELD_CODE,FIELD_NAME,FIELD_PATH,RECORD_TYPE,RECORD_ID,FINAL_VALUE,VALUE_HASH,SOURCE_POLICY_ID,SOURCE_POLICY,SOURCE_POLICY_ATTRIBUTE,SOURCE_TYPE,SOURCE_FILE_PATH,TECHNICAL_PATH,EVIDENCE,DESCRIPTION,VALIDATION_STATUS,CONFIRM_STATUS,MATCH_METHOD,CONFIDENCE,CONFIRMED_BY,CONFIRMED_AT,UPDATED_AT FROM SYSDBA.FUSION_TRACE_CACHE WHERE DEL_FLAG='0' AND FUSION_POLICY_ID=?");
                List<String> args = new ArrayList<>();
                args.add(fusionPolicyId);
                if (!fieldCode.isEmpty()) { sql.append(" AND FIELD_CODE=?"); args.add(fieldCode); }
                if (!valueHash.isEmpty()) { sql.append(" AND VALUE_HASH=?"); args.add(valueHash); }
                sql.append(" ORDER BY UPDATED_AT DESC, ID");
                List<Map<String, Object>> rows = new ArrayList<>();
                try (PreparedStatement stmt = conn.prepareStatement(sql.toString())) {
                    for (int i = 0; i < args.size(); i++) stmt.setString(i + 1, args.get(i));
                    try (ResultSet rs = stmt.executeQuery()) {
                        while (rs.next()) rows.add(traceCacheRow(rs));
                    }
                }
                sendJson(exchange, 200, buildMap("success", true, "data", rows));
            } catch (SQLException e) {
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    static class FusionTraceSaveHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if ("OPTIONS".equals(exchange.getRequestMethod())) { exchange.sendResponseHeaders(204, -1); return; }
            if (!"POST".equals(exchange.getRequestMethod())) { sendJson(exchange, 405, buildMap("success", false, "message", "Only POST is supported")); return; }
            Connection c = null;
            try {
                Map<String, String> data = parseJsonObject(new String(readAllBytes(exchange.getRequestBody()), StandardCharsets.UTF_8));
                String fusionPolicyId = nullToEmpty(data.get("fusionPolicyId")).trim();
                String fieldCode = nullToEmpty(data.get("fieldCode")).trim();
                String finalValue = firstNonEmpty(data.get("finalValue"), data.get("value"));
                String valueHash = firstNonEmpty(data.get("valueHash"), sha256(finalValue));
                if (fusionPolicyId.isEmpty() || fieldCode.isEmpty()) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "fusionPolicyId and fieldCode are required"));
                    return;
                }
                c = createDedicatedConnection();
                c.setAutoCommit(false);
                String operatorName = resolveAuditOperatorName(data);
                String existingId = "";
                try (PreparedStatement stmt = c.prepareStatement("SELECT ID FROM SYSDBA.FUSION_TRACE_CACHE WHERE DEL_FLAG='0' AND FUSION_POLICY_ID=? AND FIELD_CODE=? AND VALUE_HASH=? AND ROWNUM=1")) {
                    stmt.setString(1, fusionPolicyId);
                    stmt.setString(2, fieldCode);
                    stmt.setString(3, valueHash);
                    try (ResultSet rs = stmt.executeQuery()) { if (rs.next()) existingId = rs.getString("ID"); }
                }
                String confirmStatus = firstNonEmpty(data.get("confirmStatus"), "人工修正");
                String id = existingId.isEmpty() ? UUID.randomUUID().toString() : existingId;
                Map<String, Object> beforeRow = existingId.isEmpty() ? null : loadFusionTraceCacheRow(c, id);
                if (existingId.isEmpty()) {
                    String sql = "INSERT INTO SYSDBA.FUSION_TRACE_CACHE (ID,FUSION_POLICY_ID,FIELD_CODE,FIELD_NAME,FIELD_PATH,RECORD_TYPE,RECORD_ID,FINAL_VALUE,VALUE_HASH,SOURCE_POLICY_ID,SOURCE_POLICY,SOURCE_POLICY_ATTRIBUTE,SOURCE_TYPE,SOURCE_FILE_PATH,TECHNICAL_PATH,EVIDENCE,DESCRIPTION,VALIDATION_STATUS,CONFIRM_STATUS,MATCH_METHOD,CONFIDENCE,CONFIRMED_BY,CONFIRMED_AT,DEL_FLAG,CREATED_BY,UPDATED_BY,CREATED_AT,UPDATED_AT) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,'0',?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)";
                    try (PreparedStatement stmt = c.prepareStatement(sql)) { fillTraceInsertStatement(stmt, data, id, fusionPolicyId, fieldCode, finalValue, valueHash, confirmStatus, operatorName); stmt.executeUpdate(); }
                } else {
                    String sql = "UPDATE SYSDBA.FUSION_TRACE_CACHE SET FIELD_NAME=?,FIELD_PATH=?,RECORD_TYPE=?,RECORD_ID=?,FINAL_VALUE=?,VALUE_HASH=?,SOURCE_POLICY_ID=?,SOURCE_POLICY=?,SOURCE_POLICY_ATTRIBUTE=?,SOURCE_TYPE=?,SOURCE_FILE_PATH=?,TECHNICAL_PATH=?,EVIDENCE=?,DESCRIPTION=?,VALIDATION_STATUS=?,CONFIRM_STATUS=?,MATCH_METHOD=?,CONFIDENCE=?,CONFIRMED_BY=?,CONFIRMED_AT=CURRENT_TIMESTAMP,UPDATED_BY=?,UPDATED_AT=CURRENT_TIMESTAMP WHERE ID=?";
                    try (PreparedStatement stmt = c.prepareStatement(sql)) { fillTraceUpdateStatement(stmt, data, id, finalValue, valueHash, confirmStatus, operatorName); stmt.executeUpdate(); }
                }
                writeAuditMutation(c, data, "fusion-trace", "save", "FUSION_TRACE_CACHE", id, fusionPolicyId, beforeRow, loadFusionTraceCacheRow(c, id), existingId.isEmpty() ? "融合追溯记录新增" : "融合追溯记录更新");
                c.commit();
                sendJson(exchange, 200, buildMap("success", true, "message", "trace saved", "data", buildMap("id", id)));
            } catch (Exception e) {
                if (c != null) try { c.rollback(); } catch (SQLException ignored) {}
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            } finally {
                if (c != null) try { c.close(); } catch (SQLException ignored) {}
            }
        }
    }

    private static void fillTraceInsertStatement(PreparedStatement stmt, Map<String, String> data, String id, String fusionPolicyId, String fieldCode, String finalValue, String valueHash, String confirmStatus, String operatorName) throws SQLException {
        stmt.setString(1, id); stmt.setString(2, fusionPolicyId); stmt.setString(3, fieldCode);
        stmt.setString(4, nullToEmpty(data.get("fieldName"))); stmt.setString(5, nullToEmpty(data.get("fieldPath")));
        stmt.setString(6, nullToEmpty(data.get("recordType"))); stmt.setString(7, nullToEmpty(data.get("recordId")));
        stmt.setString(8, finalValue); stmt.setString(9, valueHash);
        stmt.setString(10, nullToEmpty(data.get("sourcePolicyId"))); stmt.setString(11, nullToEmpty(data.get("sourcePolicy")));
        stmt.setString(12, nullToEmpty(data.get("sourcePolicyAttribute"))); stmt.setString(13, firstNonEmpty(data.get("sourceType"), "原文直接提取"));
        stmt.setString(14, nullToEmpty(data.get("sourceFilePath"))); stmt.setString(15, firstNonEmpty(data.get("technicalPath"), "POLICY_ORIGINAL_V2.FILE_PATH / POLICY_ATTACHMENT.FILE_PATH"));
        stmt.setString(16, nullToEmpty(data.get("evidence"))); stmt.setString(17, nullToEmpty(data.get("description")));
        stmt.setString(18, firstNonEmpty(data.get("validationStatus"), "待复核")); stmt.setString(19, confirmStatus);
        stmt.setString(20, firstNonEmpty(data.get("matchMethod"), "manual")); setNullableDouble(stmt, 21, data.get("confidence"));
        stmt.setString(22, nullToEmpty(data.get("confirmedBy")));
        stmt.setString(23, operatorName);
        stmt.setString(24, operatorName);
    }

    private static void fillTraceUpdateStatement(PreparedStatement stmt, Map<String, String> data, String id, String finalValue, String valueHash, String confirmStatus, String operatorName) throws SQLException {
        stmt.setString(1, nullToEmpty(data.get("fieldName"))); stmt.setString(2, nullToEmpty(data.get("fieldPath")));
        stmt.setString(3, nullToEmpty(data.get("recordType"))); stmt.setString(4, nullToEmpty(data.get("recordId")));
        stmt.setString(5, finalValue); stmt.setString(6, valueHash);
        stmt.setString(7, nullToEmpty(data.get("sourcePolicyId"))); stmt.setString(8, nullToEmpty(data.get("sourcePolicy")));
        stmt.setString(9, nullToEmpty(data.get("sourcePolicyAttribute"))); stmt.setString(10, firstNonEmpty(data.get("sourceType"), "原文直接提取"));
        stmt.setString(11, nullToEmpty(data.get("sourceFilePath"))); stmt.setString(12, firstNonEmpty(data.get("technicalPath"), "POLICY_ORIGINAL_V2.FILE_PATH / POLICY_ATTACHMENT.FILE_PATH"));
        stmt.setString(13, nullToEmpty(data.get("evidence"))); stmt.setString(14, nullToEmpty(data.get("description")));
        stmt.setString(15, firstNonEmpty(data.get("validationStatus"), "待复核")); stmt.setString(16, confirmStatus);
        stmt.setString(17, firstNonEmpty(data.get("matchMethod"), "manual")); setNullableDouble(stmt, 18, data.get("confidence"));
        stmt.setString(19, nullToEmpty(data.get("confirmedBy"))); stmt.setString(20, operatorName); stmt.setString(21, id);
    }

    private static Map<String, Object> traceCacheRow(ResultSet rs) throws SQLException {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("id", nullToEmpty(rs.getString("ID"))); row.put("fusionPolicyId", nullToEmpty(rs.getString("FUSION_POLICY_ID")));
        row.put("fieldCode", nullToEmpty(rs.getString("FIELD_CODE"))); row.put("fieldName", nullToEmpty(rs.getString("FIELD_NAME")));
        row.put("fieldPath", nullToEmpty(rs.getString("FIELD_PATH"))); row.put("recordType", nullToEmpty(rs.getString("RECORD_TYPE")));
        row.put("recordId", nullToEmpty(rs.getString("RECORD_ID"))); row.put("finalValue", nullToEmpty(rs.getString("FINAL_VALUE")));
        row.put("valueHash", nullToEmpty(rs.getString("VALUE_HASH"))); row.put("sourcePolicyId", nullToEmpty(rs.getString("SOURCE_POLICY_ID")));
        row.put("sourcePolicy", nullToEmpty(rs.getString("SOURCE_POLICY"))); row.put("sourcePolicyAttribute", normalizeFusionDocumentType(rs.getString("SOURCE_POLICY_ATTRIBUTE")));
        row.put("sourceType", nullToEmpty(rs.getString("SOURCE_TYPE"))); row.put("sourceFilePath", nullToEmpty(rs.getString("SOURCE_FILE_PATH")));
        row.put("technicalPath", nullToEmpty(rs.getString("TECHNICAL_PATH"))); row.put("evidence", nullToEmpty(rs.getString("EVIDENCE")));
        row.put("description", nullToEmpty(rs.getString("DESCRIPTION"))); row.put("validationStatus", nullToEmpty(rs.getString("VALIDATION_STATUS")));
        row.put("confirmStatus", nullToEmpty(rs.getString("CONFIRM_STATUS"))); row.put("matchMethod", nullToEmpty(rs.getString("MATCH_METHOD")));
        row.put("confidence", rs.getObject("CONFIDENCE")); row.put("confirmedBy", nullToEmpty(rs.getString("CONFIRMED_BY")));
        row.put("confirmedAt", nullToEmpty(rs.getString("CONFIRMED_AT"))); row.put("updatedAt", nullToEmpty(rs.getString("UPDATED_AT")));
        return row;
    }

    private static void setNullableDouble(PreparedStatement stmt, int index, String value) throws SQLException {
        try { String text = nullToEmpty(value).trim(); if (text.isEmpty()) stmt.setNull(index, Types.DECIMAL); else stmt.setDouble(index, Double.parseDouble(text)); }
        catch (Exception e) { stmt.setNull(index, Types.DECIMAL); }
    }

    private static String sha256(String value) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] hash = digest.digest(String.valueOf(value).getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder();
            for (byte b : hash) sb.append(String.format("%02x", b));
            return sb.toString();
        } catch (Exception e) { return String.valueOf(String.valueOf(value).hashCode()); }
    }

    static class XmsmeCrawlerImportHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);
            if ("OPTIONS".equals(exchange.getRequestMethod())) {
                sendResponse(exchange, 200, "{\"success\":true}");
                return;
            }
            if (!"POST".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持 POST 方法"));
                return;
            }

            String body = new String(readAllBytes(exchange.getRequestBody()), StandardCharsets.UTF_8);
            Object parsed = parseJsonValue(body);
            if (!(parsed instanceof Map)) {
                sendJson(exchange, 400, buildMap("success", false, "message", "请求体格式不正确"));
                return;
            }

            @SuppressWarnings("unchecked")
            Map<String, Object> data = (Map<String, Object>) parsed;
            String title = asCrawlerString(data.get("title")).trim();
            String sourceUrl = asCrawlerString(data.get("sourceUrl")).trim();
            String markdownPath = asCrawlerString(data.get("markdownPath")).trim();
            if (title.isEmpty() || sourceUrl.isEmpty() || markdownPath.isEmpty()) {
                sendJson(exchange, 400, buildMap("success", false, "message", "缺少标题、来源 URL 或 Markdown 路径"));
                return;
            }

            try (Connection c = createDedicatedConnection()) {
                boolean oldAutoCommit = c.getAutoCommit();
                c.setAutoCommit(false);
                try {
                    Long policyId = findCrawlerPolicyIdByUrl(c, sourceUrl);
                    boolean existsByUrl = policyId != null;
                    boolean insertedPolicy = false;

                    if (policyId == null) {
                        policyId = insertCrawlerPolicy(c, data);
                        insertCrawlerOriginal(c, policyId, data);
                        insertCrawlerMetadata(c, policyId, data);
                        insertedPolicy = true;
                    } else if (existsByUrl) {
                        updateCrawlerExistingPolicy(c, policyId, data);
                    }

                    int insertedAttachments = 0;
                    int skippedAttachments = 0;
                    for (Object item : asCrawlerList(data.get("attachments"))) {
                        if (!(item instanceof Map)) continue;
                        @SuppressWarnings("unchecked")
                        Map<String, Object> attachment = (Map<String, Object>) item;
                        String filePath = asCrawlerString(attachment.get("filePath")).trim();
                        String status = asCrawlerString(attachment.get("status")).trim();
                        String attachName = firstCrawlerValue(
                            asCrawlerString(attachment.get("name")),
                            asCrawlerString(attachment.get("showName"))
                        );
                        if (filePath.isEmpty() || !"downloaded".equals(status)) {
                            skippedAttachments++;
                            continue;
                        }
                        if (crawlerAttachmentExists(c, policyId, filePath, attachName)) {
                            skippedAttachments++;
                            continue;
                        }
                        insertCrawlerAttachment(c, policyId, attachment);
                        insertedAttachments++;
                    }

                    c.commit();
                    c.setAutoCommit(oldAutoCommit);

                    Map<String, Object> result = new LinkedHashMap<>();
                    result.put("policyId", policyId);
                    result.put("insertedPolicy", insertedPolicy);
                    result.put("existsByUrl", existsByUrl);
                    result.put("updatedPolicy", !insertedPolicy && existsByUrl);
                    result.put("insertedAttachments", insertedAttachments);
                    result.put("skippedAttachments", skippedAttachments);

                    String message = insertedPolicy
                        ? "入库成功"
                        : (existsByUrl ? "政策已存在，已更新政策信息" : (insertedAttachments > 0 ? "政策已存在，已补充缺失附件" : "政策已存在，无需重复入库"));
                    sendJson(exchange, 200, buildMap("success", true, "message", message, "data", result));
                } catch (Exception e) {
                    c.rollback();
                    throw e;
                } finally {
                    try { c.setAutoCommit(oldAutoCommit); } catch (Exception ignore) {}
                }
            } catch (Exception e) {
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    private static void updateCrawlerExistingPolicy(Connection c, long policyId, Map<String, Object> data) throws SQLException {
        updateCrawlerPolicy(c, policyId, data);
        updateCrawlerOriginal(c, policyId, data);
        upsertCrawlerMetadata(c, policyId, data);
    }

    private static Long findCrawlerPolicyIdByUrl(Connection c, String sourceUrl) throws SQLException {
        if (sourceUrl == null || sourceUrl.trim().isEmpty()) return null;
        try (PreparedStatement ps = c.prepareStatement(
                "SELECT POLICY_ID FROM SYSDBA.POLICY_ORIGINAL_V2 WHERE NVL(DEL_FLAG,'0')='0' AND POLICY_URL=? AND ROWNUM=1")) {
            ps.setString(1, sourceUrl.trim());
            try (ResultSet rs = ps.executeQuery()) {
                if (rs.next()) return Long.parseLong(nullToEmpty(rs.getString(1)).trim());
            }
        }
        return null;
    }

    private static long insertCrawlerPolicy(Connection c, Map<String, Object> data) throws SQLException {
        String title = asCrawlerString(data.get("title")).trim();
        String safeTitle = firstCrawlerValue(asCrawlerString(data.get("safeTitle")), title);
        try (PreparedStatement ps = c.prepareStatement(
                "INSERT INTO SYSDBA.POLICY (FILE_NAME, ABSTRACT, CONTACT_INFO, APPLICATION_VOLUME, POLICY_TITLE, CREATED_AT, UPDATED_AT) " +
                "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                Statement.RETURN_GENERATED_KEYS)) {
            ps.setString(1, fitCrawler(safeTitle + ".md", 255));
            ps.setString(2, "");
            ps.setString(3, "");
            ps.setString(4, "");
            ps.setString(5, fitCrawler(title, 1000));
            ps.executeUpdate();
            try (ResultSet rs = ps.getGeneratedKeys()) {
                if (rs.next()) return rs.getLong(1);
            }
        }
        throw new SQLException("新增 POLICY 后未返回主键");
    }

    private static void updateCrawlerPolicy(Connection c, long policyId, Map<String, Object> data) throws SQLException {
        String title = asCrawlerString(data.get("title")).trim();
        String safeTitle = firstCrawlerValue(asCrawlerString(data.get("safeTitle")), title);
        try (PreparedStatement ps = c.prepareStatement(
                "UPDATE SYSDBA.POLICY SET FILE_NAME=?, POLICY_TITLE=?, UPDATED_AT=CURRENT_TIMESTAMP WHERE ID=?")) {
            ps.setString(1, fitCrawler(safeTitle + ".md", 255));
            ps.setString(2, fitCrawler(title, 1000));
            ps.setLong(3, policyId);
            ps.executeUpdate();
        }
    }

    private static void insertCrawlerOriginal(Connection c, long policyId, Map<String, Object> data) throws SQLException {
        try (PreparedStatement ps = c.prepareStatement(
                "INSERT INTO SYSDBA.POLICY_ORIGINAL_V2 (POLICY_ID, POLICY_TITLE, POLICY_URL, POLICY_CODE, PUBLISH_DATE, FILE_TYPE, ISSUE_DEPARTMENT, FILE_PATH, CONTENT, CREATE_AT, UPDATE_AT, DEL_FLAG) " +
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, '0')")) {
            ps.setString(1, String.valueOf(policyId));
            ps.setString(2, fitCrawler(asCrawlerString(data.get("title")), 500));
            ps.setString(3, fitCrawler(asCrawlerString(data.get("sourceUrl")), 1000));
            ps.setString(4, fitCrawler(asCrawlerString(data.get("policyCode")), 200));
            java.sql.Date publishDate = parseCrawlerDate(asCrawlerString(data.get("publishDate")));
            if (publishDate == null) ps.setNull(5, Types.DATE); else ps.setDate(5, publishDate);
            ps.setString(6, "md");
            ps.setString(7, fitCrawler(asCrawlerString(data.get("department")), 200));
            ps.setString(8, fitCrawler(asCrawlerString(data.get("markdownPath")), 2000));
            ps.setNull(9, Types.CLOB);
            ps.executeUpdate();
        }
    }

    private static void updateCrawlerOriginal(Connection c, long policyId, Map<String, Object> data) throws SQLException {
        try (PreparedStatement ps = c.prepareStatement(
                "UPDATE SYSDBA.POLICY_ORIGINAL_V2 SET POLICY_TITLE=?, POLICY_CODE=?, PUBLISH_DATE=?, ISSUE_DEPARTMENT=?, FILE_PATH=?, UPDATE_AT=CURRENT_TIMESTAMP " +
                "WHERE POLICY_ID=? AND POLICY_URL=? AND NVL(DEL_FLAG,'0')='0'")) {
            ps.setString(1, fitCrawler(asCrawlerString(data.get("title")), 500));
            ps.setString(2, fitCrawler(asCrawlerString(data.get("policyCode")), 200));
            java.sql.Date publishDate = parseCrawlerDate(asCrawlerString(data.get("publishDate")));
            if (publishDate == null) ps.setNull(3, Types.DATE); else ps.setDate(3, publishDate);
            ps.setString(4, fitCrawler(asCrawlerString(data.get("department")), 200));
            ps.setString(5, fitCrawler(asCrawlerString(data.get("markdownPath")), 2000));
            ps.setString(6, String.valueOf(policyId));
            ps.setString(7, fitCrawler(asCrawlerString(data.get("sourceUrl")), 1000));
            ps.executeUpdate();
        }
    }

    private static void insertCrawlerMetadata(Connection c, long policyId, Map<String, Object> data) throws SQLException {
        try (PreparedStatement ps = c.prepareStatement(
                "INSERT INTO SYSDBA.POLICY_METADATA (POLICY_ID, POLICY_ATTRIBUTE, ISSUING_DEPARTMENT, POLICY_TITLE_MAIN, POLICY_ORIGINAL_TITLE, APPLICABLE_SCOPE, REVIEW_STATUS, CREATED_AT, UPDATED_AT) " +
                "VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")) {
            String title = asCrawlerString(data.get("title"));
            ps.setLong(1, policyId);
            ps.setString(2, fitCrawler(firstCrawlerValue(asCrawlerString(data.get("policyAttribute")), "申报通知"), 100));
            ps.setString(3, fitCrawler(asCrawlerString(data.get("department")), 255));
            ps.setString(4, fitCrawler(title, 255));
            ps.setString(5, fitCrawler(title, 255));
            ps.setString(6, fitCrawler(asCrawlerString(data.get("applicableScope")), 100));
            ps.setString(7, "未通过");
            ps.executeUpdate();
        }
    }

    private static void upsertCrawlerMetadata(Connection c, long policyId, Map<String, Object> data) throws SQLException {
        String title = asCrawlerString(data.get("title"));
        try (PreparedStatement ps = c.prepareStatement(
                "UPDATE SYSDBA.POLICY_METADATA SET POLICY_ATTRIBUTE=?, ISSUING_DEPARTMENT=?, POLICY_TITLE_MAIN=?, POLICY_ORIGINAL_TITLE=?, APPLICABLE_SCOPE=?, UPDATED_AT=CURRENT_TIMESTAMP WHERE POLICY_ID=?")) {
            ps.setString(1, fitCrawler(firstCrawlerValue(asCrawlerString(data.get("policyAttribute")), "申报通知"), 100));
            ps.setString(2, fitCrawler(asCrawlerString(data.get("department")), 255));
            ps.setString(3, fitCrawler(title, 255));
            ps.setString(4, fitCrawler(title, 255));
            ps.setString(5, fitCrawler(asCrawlerString(data.get("applicableScope")), 100));
            ps.setLong(6, policyId);
            if (ps.executeUpdate() > 0) return;
        }
        insertCrawlerMetadata(c, policyId, data);
    }

    private static boolean crawlerAttachmentExists(Connection c, long policyId, String filePath, String name) throws SQLException {
        try (PreparedStatement ps = c.prepareStatement(
                "SELECT 1 FROM SYSDBA.POLICY_ATTACHMENT WHERE TRIM(POLICY_ID)=? AND NVL(DEL_FLAG,'0')='0' AND (FILE_PATH=? OR ATTACH_NAME=?)")) {
            ps.setString(1, String.valueOf(policyId));
            ps.setString(2, filePath);
            ps.setString(3, name);
            try (ResultSet rs = ps.executeQuery()) {
                return rs.next();
            }
        }
    }

    private static void insertCrawlerAttachment(Connection c, long policyId, Map<String, Object> attachment) throws SQLException {
        try (PreparedStatement ps = c.prepareStatement(
                "INSERT INTO SYSDBA.POLICY_ATTACHMENT (ATTACH_ID, POLICY_ID, ATTACH_NAME, ATTACH_SHOW_NAME, FILE_PATH, FILE_SUFFIX, CREATE_AT, UPDATE_AT, DEL_FLAG, ATTACH_TYPE, ATTACH_REVIEW_STATUS) " +
                "VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, '0', ?, ?)")) {
            String id = asCrawlerString(attachment.get("id")).trim();
            String name = firstCrawlerValue(asCrawlerString(attachment.get("name")), asCrawlerString(attachment.get("showName")));
            String showName = firstCrawlerValue(asCrawlerString(attachment.get("showName")), name);
            ps.setString(1, id.isEmpty() ? UUID.randomUUID().toString() : fitCrawler(id, 36));
            ps.setLong(2, policyId);
            ps.setString(3, fitCrawler(name, 500));
            ps.setString(4, fitCrawler(showName, 500));
            ps.setString(5, fitCrawler(asCrawlerString(attachment.get("filePath")), 500));
            ps.setString(6, fitCrawler(asCrawlerString(attachment.get("suffix")), 20));
            ps.setString(7, "申报材料类");
            ps.setString(8, "未通过");
            ps.executeUpdate();
        }
    }

    private static String asCrawlerString(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    private static List<Object> asCrawlerList(Object value) {
        if (value instanceof List) {
            @SuppressWarnings("unchecked")
            List<Object> list = (List<Object>) value;
            return list;
        }
        return Collections.emptyList();
    }

    private static String firstCrawlerValue(String... values) {
        if (values == null) return "";
        for (String value : values) {
            if (value != null && !value.trim().isEmpty()) return value.trim();
        }
        return "";
    }

    private static String fitCrawler(String value, int max) {
        String text = value == null ? "" : value;
        return text.length() <= max ? text : text.substring(0, max);
    }

    private static java.sql.Date parseCrawlerDate(String value) {
        try {
            String text = value == null ? "" : value.trim();
            if (text.length() >= 10) text = text.substring(0, 10);
            if (text.isEmpty()) return null;
            return java.sql.Date.valueOf(text);
        } catch (Exception e) {
            return null;
        }
    }

    private static void setCorsHeaders(HttpExchange exchange) {
        exchange.getResponseHeaders().add("Access-Control-Allow-Origin", "*");
        exchange.getResponseHeaders().add("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS");
        exchange.getResponseHeaders().add("Access-Control-Allow-Headers", "Content-Type");
        exchange.getResponseHeaders().add("Content-Type", "application/json; charset=utf-8");
    }

    private static void sendJson(HttpExchange exchange, int statusCode, Object data) throws IOException {
        String json = toJson(data);
        exchange.sendResponseHeaders(statusCode, json.getBytes(StandardCharsets.UTF_8).length);
        OutputStream os = exchange.getResponseBody();
        os.write(json.getBytes(StandardCharsets.UTF_8));
        os.close();
    }

    private static void sendResponse(HttpExchange exchange, int statusCode, String text) throws IOException {
        byte[] bytes = text.getBytes(StandardCharsets.UTF_8);
        exchange.sendResponseHeaders(statusCode, bytes.length);
        OutputStream os = exchange.getResponseBody();
        os.write(bytes);
        os.close();
    }

    private static int parseInt(String value, int defaultValue) {
        try {
            return Integer.parseInt(value);
        } catch (NumberFormatException e) {
            return defaultValue;
        }
    }

    private static String nullToEmpty(String value) {
        return value == null ? "" : value;
    }

    private static String firstNonEmpty(String... values) {
        if (values == null) return "";
        for (String value : values) {
            String normalized = nullToEmpty(value).trim();
            if (!normalized.isEmpty()) return normalized;
        }
        return "";
    }

    private static String formatDateRange(String start, String end, String fallback) {
        String normalizedStart = nullToEmpty(start).trim();
        String normalizedEnd = nullToEmpty(end).trim();
        if (!normalizedStart.isEmpty() && !normalizedEnd.isEmpty()) return normalizedStart + "至" + normalizedEnd;
        if (!normalizedStart.isEmpty()) return normalizedStart;
        if (!normalizedEnd.isEmpty()) return normalizedEnd;
        return nullToEmpty(fallback).trim();
    }

    private static LocalDate parseLocalDateValue(String value) {
        String normalized = nullToEmpty(value).trim();
        if (normalized.isEmpty()) return null;
        normalized = normalized
            .replace('/', '-')
            .replace('.', '-')
            .replace("年", "-")
            .replace("月", "-")
            .replace("日", "");
        int spaceIndex = normalized.indexOf(' ');
        if (spaceIndex > 0) normalized = normalized.substring(0, spaceIndex);
        if (normalized.length() >= 10) normalized = normalized.substring(0, 10);
        try {
            return LocalDate.parse(normalized);
        } catch (Exception e) {
            return null;
        }
    }

    private static Map<String, String> parseQueryParams(String query) {
        Map<String, String> params = new HashMap<>();
        if (query == null || query.isEmpty()) return params;

        for (String pair : query.split("&")) {
            String[] kv = pair.split("=", 2);
            if (kv.length == 2) {
                try {
                    params.put(kv[0], URLDecoder.decode(kv[1], "UTF-8"));
                } catch (Exception e) {
                    params.put(kv[0], kv[1]);
                }
            }
        }
        return params;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, String> parseJsonObject(String json) {
        Map<String, String> map = new HashMap<>();
        if (json == null || json.isEmpty()) return map;

        json = json.trim();
        if (!json.startsWith("{") || !json.endsWith("}")) return map;
        int i = 1;
        while (i < json.length() - 1) {
            while (i < json.length() - 1 && (Character.isWhitespace(json.charAt(i)) || json.charAt(i) == ',')) i++;
            if (i >= json.length() - 1) break;
            String key;
            if (json.charAt(i) == '"') {
                StringParseResult parsedKey = parseJsonStringToken(json, i);
                key = parsedKey.value;
                i = parsedKey.nextIndex;
            } else {
                int start = i;
                while (i < json.length() - 1 && json.charAt(i) != ':') i++;
                key = json.substring(start, i).trim();
            }
            while (i < json.length() - 1 && Character.isWhitespace(json.charAt(i))) i++;
            if (i < json.length() - 1 && json.charAt(i) == ':') i++;
            while (i < json.length() - 1 && Character.isWhitespace(json.charAt(i))) i++;
            String value;
            if (i < json.length() - 1 && json.charAt(i) == '"') {
                StringParseResult parsedValue = parseJsonStringToken(json, i);
                value = parsedValue.value;
                i = parsedValue.nextIndex;
            } else {
                int start = i;
                while (i < json.length() - 1 && json.charAt(i) != ',') i++;
                value = json.substring(start, i).trim();
                if ("null".equals(value)) value = "";
            }
            if (!key.isEmpty()) map.put(key, value);
        }
        return map;
    }

    private static class StringParseResult {
        String value;
        int nextIndex;
        StringParseResult(String value, int nextIndex) { this.value = value; this.nextIndex = nextIndex; }
    }

    private static StringParseResult parseJsonStringToken(String json, int startIndex) {
        StringBuilder sb = new StringBuilder();
        int i = startIndex + 1;
        while (i < json.length()) {
            char ch = json.charAt(i++);
            if (ch == '\\' && i < json.length()) {
                char escaped = json.charAt(i++);
                switch (escaped) {
                    case '"': sb.append('"'); break;
                    case '\\': sb.append('\\'); break;
                    case '/': sb.append('/'); break;
                    case 'b': sb.append('\b'); break;
                    case 'f': sb.append('\f'); break;
                    case 'n': sb.append('\n'); break;
                    case 'r': sb.append('\r'); break;
                    case 't': sb.append('\t'); break;
                    case 'u':
                        if (i + 4 <= json.length()) {
                            try { sb.append((char) Integer.parseInt(json.substring(i, i + 4), 16)); i += 4; }
                            catch (Exception e) { sb.append("\\u"); }
                        }
                        break;
                    default: sb.append(escaped);
                }
            } else if (ch == '"') {
                return new StringParseResult(sb.toString(), i);
            } else {
                sb.append(ch);
            }
        }
        return new StringParseResult(sb.toString(), i);
    }

    /**
     * 规则表经由部分客户端多次 JSON 序列化后，中文有时会以字面量 "\\uXXXX" 保留在字符串值中。
     * 该方法只递归处理规则表的字符串值，不会改变字段名、JSON 结构或正常的中文字符。
     */
    @SuppressWarnings("unchecked")
    private static Object decodeLiteralUnicodeEscapesInJsonValue(Object value) {
        if (value instanceof Map) {
            Map<Object, Object> normalized = new LinkedHashMap<>();
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
                normalized.put(entry.getKey(), decodeLiteralUnicodeEscapesInJsonValue(entry.getValue()));
            }
            return normalized;
        }
        if (value instanceof List) {
            List<Object> normalized = new ArrayList<>();
            for (Object item : (List<?>) value) {
                normalized.add(decodeLiteralUnicodeEscapesInJsonValue(item));
            }
            return normalized;
        }
        if (value instanceof String) {
            return decodeLiteralUnicodeEscapes((String) value);
        }
        return value;
    }

    private static String decodeLiteralUnicodeEscapes(String value) {
        String current = value;
        for (int round = 0; round < 3; round++) {
            StringBuilder normalized = new StringBuilder(current.length());
            boolean changed = false;
            for (int index = 0; index < current.length(); index++) {
                char character = current.charAt(index);
                if (character == '\\') {
                    int unicodeStart = index;
                    while (unicodeStart < current.length() && current.charAt(unicodeStart) == '\\') {
                        unicodeStart++;
                    }
                    if (unicodeStart + 4 < current.length() && current.charAt(unicodeStart) == 'u') {
                        String hexadecimal = current.substring(unicodeStart + 1, unicodeStart + 5);
                        if (isHexadecimal(hexadecimal)) {
                            normalized.append((char) Integer.parseInt(hexadecimal, 16));
                            index = unicodeStart + 4;
                            changed = true;
                            continue;
                        }
                    }
                }
                normalized.append(character);
            }
            String next = normalized.toString();
            if (!changed) return current;
            current = next;
        }
        return current;
    }

    private static boolean isHexadecimal(String value) {
        if (value.length() != 4) return false;
        for (int index = 0; index < value.length(); index++) {
            if (Character.digit(value.charAt(index), 16) < 0) return false;
        }
        return true;
    }

    private static String toJson(Object obj) {
        if (obj instanceof Map) {
            StringBuilder sb = new StringBuilder("{");
            Map<?, ?> map = (Map<?, ?>) obj;
            int i = 0;
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                if (i > 0) sb.append(",");
                sb.append("\"").append(entry.getKey()).append("\":");
                sb.append(toJson(entry.getValue()));
                i++;
            }
            sb.append("}");
            return sb.toString();
        } else if (obj instanceof List) {
            StringBuilder sb = new StringBuilder("[");
            List<?> list = (List<?>) obj;
            for (int i = 0; i < list.size(); i++) {
                if (i > 0) sb.append(",");
                sb.append(toJson(list.get(i)));
            }
            sb.append("]");
            return sb.toString();
        } else if (obj instanceof String) {
            String s = obj.toString()
                .replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t");
            return "\"" + s + "\"";
        } else if (obj instanceof Number || obj instanceof Boolean) {
            return obj.toString();
        } else {
            return obj == null ? "null" : "\"" + obj.toString() + "\"";
        }
    }

    // ==================== 简易嵌套 JSON 解析器 ====================

    private static Object parseJsonValue(String json) {
        if (json == null) return null;
        json = json.trim();
        if (json.startsWith("{")) return parseNestedJson(json);
        if (json.startsWith("[")) {
            List<Object> list = new ArrayList<>();
            // 剥离外层 []
            json = json.substring(1, json.length() - 1).trim();
            if (json.isEmpty()) return list;
            // 手动拆分数组元素（处理嵌套逗号）
            int depth = 0; boolean inStr = false; StringBuilder buf = new StringBuilder();
            for (int i = 0; i < json.length(); i++) {
                char c = json.charAt(i);
                if (c == '"' && !isEscaped(json, i)) { inStr = !inStr; buf.append(c); }
                else if (!inStr && (c == '{' || c == '[')) { depth++; buf.append(c); }
                else if (!inStr && (c == '}' || c == ']')) { depth--; buf.append(c); }
                else if (!inStr && c == ',' && depth == 0) {
                    list.add(parseJsonValue(buf.toString().trim()));
                    buf = new StringBuilder();
                } else { buf.append(c); }
            }
            String last = buf.toString().trim();
            if (!last.isEmpty()) list.add(parseJsonValue(last));
            return list;
        }
        if (json.startsWith("\"")) {
            return parseJsonStringToken(json, 0).value;
        }
        if ("null".equalsIgnoreCase(json)) return null;
        if ("true".equalsIgnoreCase(json)) return Boolean.TRUE;
        if ("false".equalsIgnoreCase(json)) return Boolean.FALSE;
        try {
            if (json.contains(".")) return Double.parseDouble(json);
            return Long.parseLong(json);
        } catch (NumberFormatException e) { return json; }
    }

    private static Map<String, Object> parseNestedJson(String json) {
        Map<String, Object> map = new LinkedHashMap<>();
        if (json == null || json.isEmpty()) return map;
        json = json.trim();
        if (!json.startsWith("{") || !json.endsWith("}")) return map;
        json = json.substring(1, json.length() - 1).trim();
        if (json.isEmpty()) return map;

        // 逐层解析 key-value，处理嵌套
        StringBuilder key = new StringBuilder();
        StringBuilder val = new StringBuilder();
        int depth = 0; boolean inStr = false; boolean inKey = true;
        for (int i = 0; i < json.length(); i++) {
            char c = json.charAt(i);
            if (c == '"' && !isEscaped(json, i)) {
                inStr = !inStr;
                if (inKey) key.append(c);
                else val.append(c);
            } else if (!inStr && (c == '{' || c == '[')) {
                depth++; val.append(c);
            } else if (!inStr && (c == '}' || c == ']')) {
                depth--; val.append(c);
            } else if (!inStr && c == ':' && depth == 0 && inKey) {
                inKey = false;
            } else if (!inStr && c == ',' && depth == 0) {
                String k = key.toString().trim();
                String v = val.toString().trim();
                if (k.startsWith("\"")) k = parseJsonStringToken(k, 0).value;
                map.put(k, parseJsonValue(v));
                key = new StringBuilder(); val = new StringBuilder(); inKey = true;
            } else {
                if (inKey) key.append(c);
                else val.append(c);
            }
        }
        String k = key.toString().trim();
        String v = val.toString().trim();
        if (k.startsWith("\"")) k = parseJsonStringToken(k, 0).value;
        if (!k.isEmpty()) map.put(k, parseJsonValue(v));
        return map;
    }

    private static boolean isEscaped(String text, int index) {
        int slashCount = 0;
        for (int i = index - 1; i >= 0 && text.charAt(i) == '\\'; i--) slashCount++;
        return slashCount % 2 == 1;
    }

    // ==================== 工具方法 ====================

    private static String stringVal(Object obj) {
        if (obj == null) return "";
        if (obj instanceof String) return (String) obj;
        return obj.toString();
    }

    @SuppressWarnings("unchecked")
    private static Map<String,Object> nestedMap(Map<String,Object> parent, String key) {
        Object v = parent.get(key);
        if (v instanceof Map) return (Map<String,Object>) v;
        return null;
    }

    private static Map<String, Object> buildMap(Object... pairs) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i < pairs.length; i += 2) {
            m.put((String) pairs[i], pairs[i + 1]);
        }
        return m;
    }

    private static byte[] readAllBytes(InputStream in) throws IOException {
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        byte[] chunk = new byte[4096];
        int len;
        while ((len = in.read(chunk)) != -1) {
            buffer.write(chunk, 0, len);
        }
        return buffer.toByteArray();
    }

    private static String repeat(char ch, int count) {
        StringBuilder sb = new StringBuilder(count);
        for (int i = 0; i < count; i++) sb.append(ch);
        return sb.toString();
    }

    private static String repeatPlaceholders(int count) {
        StringBuilder sb = new StringBuilder(count * 2);
        for (int i = 0; i < count; i++) {
            if (i > 0) sb.append(',');
            sb.append('?');
        }
        return sb.toString();
    }

    public static void shutdown() {
        try {
            if (conn != null && !conn.isClosed()) {
                conn.close();
                System.out.println("[JDBC] 数据库连接已关闭");
            }
            if (server != null) {
                server.stop(0);
                System.out.println("[HTTP] 服务已停止");
            }
        } catch (SQLException e) {
            e.printStackTrace();
        }
    }

    // ==================== 申报条件字段信息接口 (CATEGORY_FIELD_INFO) ====================

    /**
     * 获取申报条件字段信息列表
     * GET /api/categoryFieldInfo?level1=&level2=&keyword=&page=1&size=10
     */
    static class CategoryFieldInfoHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);

            if ("OPTIONS".equals(exchange.getRequestMethod())) {
                sendResponse(exchange, 200, "{\"success\":true}");
                return;
            }

            try {
                ensureConnection();
                ensureCategoryFieldInfoStatusColumn();
                Map<String, String> params = parseQueryParams(exchange.getRequestURI().getQuery());
                int page = parseInt(params.getOrDefault("page", "1"), 1);
                int size = parseInt(params.getOrDefault("size", "10"), 10);
                String level1 = params.getOrDefault("level1", "").trim();
                String level2 = params.getOrDefault("level2", "").trim();
                String keyword = params.getOrDefault("keyword", "").trim();

                int offset = (page - 1) * size;

                // 构建 WHERE 条件
                StringBuilder where = new StringBuilder(" WHERE 1=1");
                List<String> paramList = new ArrayList<>();

                if (!level1.isEmpty()) {
                    where.append(" AND LEVEL1_CATEGORY = ?");
                    paramList.add(level1);
                }
                if (!level2.isEmpty()) {
                    where.append(" AND LEVEL2_CATEGORY = ?");
                    paramList.add(level2);
                }
                if (!keyword.isEmpty()) {
                    where.append(" AND (LEVEL1_CATEGORY LIKE ? OR LEVEL2_CATEGORY LIKE ? OR LEVEL3_CATEGORY LIKE ? OR CORE_FIELDS LIKE ?)");
                    String likeKeyword = "%" + keyword + "%";
                    paramList.add(likeKeyword);
                    paramList.add(likeKeyword);
                    paramList.add(likeKeyword);
                    paramList.add(likeKeyword);
                }
                List<String> filterParams = new ArrayList<>(paramList);

                // 获取总数
                String countSql = "SELECT COUNT(*) AS TOTAL FROM SYSDBA.CATEGORY_FIELD_INFO" + where;
                PreparedStatement countStmt = conn.prepareStatement(countSql);
                for (int i = 0; i < paramList.size(); i++) {
                    countStmt.setString(i + 1, paramList.get(i));
                }
                ResultSet countRs = countStmt.executeQuery();
                int total = 0;
                if (countRs.next()) {
                    total = countRs.getInt("TOTAL");
                }
                countRs.close();
                countStmt.close();

                String querySql = "SELECT ID, LEVEL1_CATEGORY, LEVEL2_CATEGORY, LEVEL3_CATEGORY, CORE_FIELDS, STATUS, CREATED_BY, UPDATED_BY, CREATED_AT, UPDATED_AT " +
                    "FROM SYSDBA.CATEGORY_FIELD_INFO" + where +
                    " ORDER BY LEVEL1_CATEGORY, LEVEL2_CATEGORY, LEVEL3_CATEGORY LIMIT ? OFFSET ?";
                paramList.add(String.valueOf(size));
                paramList.add(String.valueOf(offset));

                PreparedStatement stmt = conn.prepareStatement(querySql);
                for (int i = 0; i < paramList.size(); i++) {
                    stmt.setString(i + 1, paramList.get(i));
                }

                ResultSet rs = stmt.executeQuery();
                List<Map<String, Object>> dataList = new ArrayList<>();

                while (rs.next()) {
                    Map<String, Object> row = new LinkedHashMap<>();
                    row.put("id", nullToEmpty(rs.getString("ID")));
                    row.put("level1Category", nullToEmpty(rs.getString("LEVEL1_CATEGORY")));
                    row.put("level2Category", nullToEmpty(rs.getString("LEVEL2_CATEGORY")));
                    row.put("name", nullToEmpty(rs.getString("LEVEL3_CATEGORY")));
                    row.put("coreField", nullToEmpty(rs.getString("CORE_FIELDS")));
                    row.put("status", rs.getInt("STATUS") == 1 ? "启用" : "禁用");
                    row.put("createdBy", nullToEmpty(rs.getString("CREATED_BY")));
                    row.put("updatedBy", nullToEmpty(rs.getString("UPDATED_BY")));
                    row.put("createTime", nullToEmpty(rs.getString("CREATED_AT")));
                    row.put("updateTime", nullToEmpty(rs.getString("UPDATED_AT")));
                    dataList.add(row);
                }
                rs.close();
                stmt.close();

                // 统计数据
                Map<String, Object> statistics = new LinkedHashMap<>();
                statistics.put("total", total);
                String statusCountSql = "SELECT " +
                    "SUM(CASE WHEN STATUS = 1 THEN 1 ELSE 0 END) AS ENABLED, " +
                    "SUM(CASE WHEN STATUS = 0 THEN 1 ELSE 0 END) AS DISABLED " +
                    "FROM SYSDBA.CATEGORY_FIELD_INFO" + where;
                try (PreparedStatement statusStmt = conn.prepareStatement(statusCountSql)) {
                    for (int i = 0; i < filterParams.size(); i++) {
                        statusStmt.setString(i + 1, filterParams.get(i));
                    }
                    try (ResultSet statusRs = statusStmt.executeQuery()) {
                        if (statusRs.next()) {
                            statistics.put("enabled", statusRs.getInt("ENABLED"));
                            statistics.put("disabled", statusRs.getInt("DISABLED"));
                        }
                    }
                }

                // 计算一级分类去重数量
                String categoryCountSql = "SELECT COUNT(DISTINCT LEVEL1_CATEGORY) AS CNT FROM SYSDBA.CATEGORY_FIELD_INFO";
                try (Statement s = conn.createStatement(); ResultSet rs2 = s.executeQuery(categoryCountSql)) {
                    if (rs2.next()) {
                        statistics.put("categoryCount", rs2.getInt("CNT"));
                    }
                }

                // 获取筛选选项
                List<Map<String, Object>> options = new ArrayList<>();
                String optionsSql = "SELECT DISTINCT LEVEL1_CATEGORY, LEVEL2_CATEGORY FROM SYSDBA.CATEGORY_FIELD_INFO " +
                    "WHERE LEVEL1_CATEGORY IS NOT NULL ORDER BY LEVEL1_CATEGORY, LEVEL2_CATEGORY";
                try (Statement s = conn.createStatement(); ResultSet rs2 = s.executeQuery(optionsSql)) {
                    while (rs2.next()) {
                        options.add(buildMap(
                            "level1", nullToEmpty(rs2.getString("LEVEL1_CATEGORY")),
                            "level2", nullToEmpty(rs2.getString("LEVEL2_CATEGORY"))
                        ));
                    }
                }

                Map<String, Object> response = new LinkedHashMap<>();
                response.put("success", true);
                response.put("data", dataList);
                response.put("statistics", statistics);
                response.put("options", options);
                response.put("message", "");

                Map<String, Object> pagination = new LinkedHashMap<>();
                pagination.put("page", page);
                pagination.put("size", size);
                pagination.put("total", total);
                pagination.put("pages", (int) Math.ceil((double) total / size));
                response.put("pagination", pagination);

                sendJson(exchange, 200, response);

            } catch (SQLException e) {
                System.err.println("[CategoryFieldInfo Query Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /**
     * 创建申报条件字段信息
     * POST /api/categoryFieldInfo/create
     */
    static class CategoryFieldInfoCreateHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);

            if (!"POST".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持 POST 方法"));
                return;
            }

            try {
                ensureConnection();
                ensureCategoryFieldInfoStatusColumn();
                String body = new String(readAllBytes(exchange.getRequestBody()), StandardCharsets.UTF_8);
                Map<String, String> data = parseJsonObject(body);

                String level1Category = data.getOrDefault("level1Category", "").trim();
                String level2Category = data.getOrDefault("level2Category", "").trim();
                String level3Category = data.getOrDefault("name", "").trim();
                String coreField = data.getOrDefault("coreField", "").trim();
                int status = statusValue(data.getOrDefault("status", "启用"));
                String operatorName = resolveAuditOperatorName(data);

                if (level1Category.isEmpty()) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "一级分类和名称不能为空"));
                    return;
                }

                String id = UUID.randomUUID().toString();

                String sql = "INSERT INTO SYSDBA.CATEGORY_FIELD_INFO " +
                    "(ID, LEVEL1_CATEGORY, LEVEL2_CATEGORY, LEVEL3_CATEGORY, CORE_FIELDS, STATUS, CREATED_BY, UPDATED_BY, CREATED_AT, UPDATED_AT) " +
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)";

                PreparedStatement stmt = conn.prepareStatement(sql);
                stmt.setString(1, id);
                stmt.setString(2, level1Category);
                stmt.setString(3, level2Category);
                stmt.setString(4, level3Category);
                stmt.setString(5, coreField);
                stmt.setInt(6, status);
                stmt.setString(7, operatorName);
                stmt.setString(8, operatorName);

                int rows = stmt.executeUpdate();
                stmt.close();
                if (rows > 0) {
                    writeAuditMutation(data, "base-data", "create", "CATEGORY_FIELD_INFO", id, level1Category, null, loadCategoryFieldInfoRow(id), "申报条件字段新增");
                }

                Map<String, Object> response = new LinkedHashMap<>();
                response.put("success", rows > 0);
                response.put("message", rows > 0 ? "创建成功" : "创建失败");
                response.put("data", buildMap("id", id));

                sendJson(exchange, rows > 0 ? 200 : 500, response);

            } catch (SQLException e) {
                System.err.println("[CategoryFieldInfo Create Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /**
     * 更新申报条件字段信息
     * POST /api/categoryFieldInfo/update
     */
    static class CategoryFieldInfoUpdateHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);

            if (!"POST".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持 POST 方法"));
                return;
            }

            try {
                ensureConnection();
                ensureCategoryFieldInfoStatusColumn();
                String body = new String(readAllBytes(exchange.getRequestBody()), StandardCharsets.UTF_8);
                Map<String, String> data = parseJsonObject(body);

                String id = data.getOrDefault("id", "").trim();
                String level1Category = data.getOrDefault("level1Category", "").trim();
                String level2Category = data.getOrDefault("level2Category", "").trim();
                String level3Category = data.getOrDefault("name", "").trim();
                String coreField = data.getOrDefault("coreField", "").trim();
                int status = statusValue(data.getOrDefault("status", "启用"));
                String operatorName = resolveAuditOperatorName(data);

                if (id.isEmpty()) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "缺少必填字段: id"));
                    return;
                }

                Map<String, Object> beforeRow = loadCategoryFieldInfoRow(id);
                String sql = "UPDATE SYSDBA.CATEGORY_FIELD_INFO SET " +
                    "LEVEL1_CATEGORY = ?, LEVEL2_CATEGORY = ?, LEVEL3_CATEGORY = ?, CORE_FIELDS = ?, STATUS = ?, UPDATED_BY = ?, UPDATED_AT = CURRENT_TIMESTAMP " +
                    "WHERE ID = ?";

                PreparedStatement stmt = conn.prepareStatement(sql);
                stmt.setString(1, level1Category);
                stmt.setString(2, level2Category);
                stmt.setString(3, level3Category);
                stmt.setString(4, coreField);
                stmt.setInt(5, status);
                stmt.setString(6, operatorName);
                stmt.setString(7, id);

                int rows = stmt.executeUpdate();
                stmt.close();
                if (rows > 0) {
                    writeAuditMutation(data, "base-data", "update", "CATEGORY_FIELD_INFO", id, level1Category, beforeRow, loadCategoryFieldInfoRow(id), "申报条件字段更新");
                }

                Map<String, Object> response = new LinkedHashMap<>();
                response.put("success", rows > 0);
                response.put("message", rows > 0 ? "更新成功" : "更新失败，记录不存在");

                sendJson(exchange, rows > 0 ? 200 : 404, response);

            } catch (SQLException e) {
                System.err.println("[CategoryFieldInfo Update Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /**
     * 删除申报条件字段信息
     * POST /api/categoryFieldInfo/delete
     */
    static class CategoryFieldInfoDeleteHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            setCorsHeaders(exchange);

            if (!"POST".equals(exchange.getRequestMethod())) {
                sendJson(exchange, 405, buildMap("success", false, "message", "只支持 POST 方法"));
                return;
            }

            try {
                ensureConnection();
                ensureCategoryFieldInfoStatusColumn();
                String body = new String(readAllBytes(exchange.getRequestBody()), StandardCharsets.UTF_8);
                Map<String, String> data = parseJsonObject(body);

                String id = data.getOrDefault("id", "").trim();

                if (id.isEmpty()) {
                    sendJson(exchange, 400, buildMap("success", false, "message", "缺少必填字段: id"));
                    return;
                }

                Map<String, Object> beforeRow = loadCategoryFieldInfoRow(id);
                String sql = "DELETE FROM SYSDBA.CATEGORY_FIELD_INFO WHERE ID = ?";

                PreparedStatement stmt = conn.prepareStatement(sql);
                stmt.setString(1, id);

                int rows = stmt.executeUpdate();
                stmt.close();
                if (rows > 0) {
                    writeAuditMutation(data, "base-data", "delete", "CATEGORY_FIELD_INFO", id, null, beforeRow, null, "申报条件字段删除");
                }

                Map<String, Object> response = new LinkedHashMap<>();
                response.put("success", rows > 0);
                response.put("message", rows > 0 ? "删除成功" : "删除失败，记录不存在");

                sendJson(exchange, rows > 0 ? 200 : 404, response);

            } catch (SQLException e) {
                System.err.println("[CategoryFieldInfo Delete Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    // ==================== 适用范围选项接口 ====================

    /**
     * 获取适用范围选项 (DIM_RULE_OPTION WHERE RULE_TYPE='scope_level')
     * GET /api/rule-options/scope-level
     */
    static class ScopeLevelOptionsHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            try {
                ensureConnection();
                String nameExpr = buildRuleOptionDisplayExpr("d", true);
                String leafExpr = "NVL(NULLIF(TRIM(d.LEVELS3_NAME), ''), NVL(NULLIF(TRIM(d.LEVEL2_NAME), ''), TRIM(d.LEVEL1_NAME)))";
                String sql = "SELECT d.ID, d.LEVEL1_NAME, d.LEVEL2_NAME, d.LEVELS3_NAME, " + leafExpr + " AS LEAF_NAME, " + nameExpr + " AS NAME FROM SYSDBA.DIM_RULE_OPTION d " +
                    "WHERE d.RULE_TYPE = 'scope_level' AND d.STATUS = 1 ORDER BY d.SORT_ORDER";
                List<Map<String, Object>> items = new ArrayList<>();
                try (PreparedStatement stmt = conn.prepareStatement(sql);
                     ResultSet rs = stmt.executeQuery()) {
                    while (rs.next()) {
                        Map<String, Object> item = new LinkedHashMap<>();
                        item.put("id", rs.getString("ID"));
                        item.put("level1", rs.getString("LEVEL1_NAME"));
                        item.put("level2", rs.getString("LEVEL2_NAME"));
                        item.put("level3", rs.getString("LEVELS3_NAME"));
                        item.put("leafName", rs.getString("LEAF_NAME"));
                        item.put("name", rs.getString("NAME"));
                        items.add(item);
                    }
                }
                sendJson(exchange, 200, buildMap("success", true, "data", items));
            } catch (SQLException e) {
                System.err.println("[ScopeLevelOptions Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    // ==================== 行业领域接口 (SYS_TERRITORY_DATA_NEW / SYS_TERRITORY_INDUSTRY_22N) ====================

    /**
     * 获取行业领域选项（从 SYS_TERRITORY_DATA_NEW 中提取政策法规侧维护的行业领域数据）
     * GET /api/territory/industry-names
     */
    static class TerritoryIndustryNamesHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            try {
                ensureConnection();
                // 从 SYS_TERRITORY_DATA_NEW 获取行业领域数据：
                // territory_name = 一级名称（大类），territory_two_name = 二级名称（细类）
                // 当前库里的行业领域记录实际落在 TYPE=policy / TYPE_NAME=政策法规 这一组，
                // 旧实现按“行业/产业”关键字过滤会把数据全部筛空。
                String sql = "SELECT DISTINCT TRIM(TERRITORY_NAME) AS TERRITORY_NAME, TRIM(TERRITORY_TWO_NAME) AS TERRITORY_TWO_NAME " +
                    "FROM SYSDBA.SYS_TERRITORY_DATA_NEW " +
                    "WHERE DEL_FLAG = 0 " +
                    "AND TERRITORY_NAME IS NOT NULL AND TRIM(TERRITORY_NAME) <> '' " +
                    "AND (" +
                    "    (TYPE IS NOT NULL AND LOWER(TRIM(TYPE)) = 'policy') " +
                    "    OR (TYPE_NAME IS NOT NULL AND TRIM(TYPE_NAME) = '政策法规')" +
                    ") " +
                    "ORDER BY TERRITORY_NAME, TERRITORY_TWO_NAME";
                PreparedStatement stmt = conn.prepareStatement(sql);
                ResultSet rs = stmt.executeQuery();
                List<Map<String, Object>> items = new ArrayList<>();
                while (rs.next()) {
                    Map<String, Object> item = new LinkedHashMap<>();
                    String name1 = nullToEmpty(rs.getString("TERRITORY_NAME"));
                    String name2 = nullToEmpty(rs.getString("TERRITORY_TWO_NAME"));
                    item.put("level1", name1);
                    item.put("level2", name2);
                    items.add(item);
                }
                rs.close();
                stmt.close();
                sendJson(exchange, 200, buildMap("success", true, "data", items));
            } catch (SQLException e) {
                System.err.println("[TerritoryIndustryNames Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

    /**
     * 获取标准化行业领域分级（从 SYS_TERRITORY_INDUSTRY_22N 查询三级分类）
     * GET /api/territory/industry-22n
     */
    static class TerritoryIndustry22NHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            try {
                ensureConnection();
                Map<String, String> params = parseQueryParams(exchange.getRequestURI().getQuery());
                String statusParam = nullToEmpty(params.getOrDefault("status", "1")).trim();
                String firstCategory = nullToEmpty(params.get("firstCategory")).trim();
                String secondCategory = nullToEmpty(params.get("secondCategory")).trim();
                String thirdCategory = nullToEmpty(params.get("thirdCategory")).trim();
                String keyword = nullToEmpty(params.get("keyword")).trim();

                Integer statusFilter = null;
                if (!statusParam.isEmpty() && !"all".equalsIgnoreCase(statusParam)) {
                    try {
                        statusFilter = Integer.valueOf(statusParam);
                    } catch (NumberFormatException e) {
                        sendJson(exchange, 400, buildMap("success", false, "message", "status 参数必须是数字或 all"));
                        return;
                    }
                }

                StringBuilder sql = new StringBuilder(
                    "SELECT ID, TRIM(FIRST_CATEGORY) AS FIRST_CATEGORY, TRIM(SECOND_CATEGORY) AS SECOND_CATEGORY, " +
                        "TRIM(THIRD_CATEGORY) AS THIRD_CATEGORY, STATUS, CREATE_TIME, UPDATE_TIME " +
                        "FROM SYSDBA.SYS_TERRITORY_INDUSTRY_22N WHERE 1 = 1"
                );
                List<Object> sqlParams = new ArrayList<>();

                if (statusFilter != null) {
                    sql.append(" AND STATUS = ?");
                    sqlParams.add(statusFilter);
                }
                if (!firstCategory.isEmpty()) {
                    sql.append(" AND TRIM(FIRST_CATEGORY) = ?");
                    sqlParams.add(firstCategory);
                }
                if (!secondCategory.isEmpty()) {
                    sql.append(" AND TRIM(SECOND_CATEGORY) = ?");
                    sqlParams.add(secondCategory);
                }
                if (!thirdCategory.isEmpty()) {
                    sql.append(" AND TRIM(THIRD_CATEGORY) = ?");
                    sqlParams.add(thirdCategory);
                }
                if (!keyword.isEmpty()) {
                    String likeKeyword = "%" + keyword.toLowerCase(Locale.ROOT) + "%";
                    sql.append(" AND (")
                        .append("LOWER(TRIM(FIRST_CATEGORY)) LIKE ? ")
                        .append("OR LOWER(TRIM(SECOND_CATEGORY)) LIKE ? ")
                        .append("OR LOWER(TRIM(THIRD_CATEGORY)) LIKE ?")
                        .append(")");
                    sqlParams.add(likeKeyword);
                    sqlParams.add(likeKeyword);
                    sqlParams.add(likeKeyword);
                }

                sql.append(" ORDER BY FIRST_CATEGORY, SECOND_CATEGORY, THIRD_CATEGORY, ID");

                List<Map<String, Object>> items = new ArrayList<>();
                try (PreparedStatement stmt = conn.prepareStatement(sql.toString())) {
                    for (int i = 0; i < sqlParams.size(); i++) {
                        stmt.setObject(i + 1, sqlParams.get(i));
                    }
                    try (ResultSet rs = stmt.executeQuery()) {
                        while (rs.next()) {
                            Map<String, Object> item = new LinkedHashMap<>();
                            String level1 = nullToEmpty(rs.getString("FIRST_CATEGORY"));
                            String level2 = nullToEmpty(rs.getString("SECOND_CATEGORY"));
                            String level3 = nullToEmpty(rs.getString("THIRD_CATEGORY"));
                            String leafName = !level3.isEmpty() ? level3 : (!level2.isEmpty() ? level2 : level1);
                            item.put("id", rs.getString("ID"));
                            item.put("level1", level1);
                            item.put("level2", level2);
                            item.put("level3", level3);
                            item.put("firstCategory", level1);
                            item.put("secondCategory", level2);
                            item.put("thirdCategory", level3);
                            item.put("leafName", leafName);
                            item.put("pathText", joinHierarchyParts(level1, level2, level3));
                            item.put("status", rs.getInt("STATUS"));
                            item.put("createTime", nullToEmpty(rs.getString("CREATE_TIME")));
                            item.put("updateTime", nullToEmpty(rs.getString("UPDATE_TIME")));
                            items.add(item);
                        }
                    }
                }

                sendJson(exchange, 200, buildMap(
                    "success", true,
                    "data", items,
                    "total", items.size(),
                    "sourceTable", "SYS_TERRITORY_INDUSTRY_22N"
                ));
            } catch (SQLException e) {
                System.err.println("[TerritoryIndustry22N Error] " + e.getMessage());
                sendJson(exchange, 500, buildMap("success", false, "message", e.getMessage()));
            }
        }
    }

}
