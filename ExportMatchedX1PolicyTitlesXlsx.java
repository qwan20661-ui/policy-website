import java.io.ByteArrayOutputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

public class ExportMatchedX1PolicyTitlesXlsx {
    private static final String DUPLICATE_TITLE = "2026年行业龙头企业年会、峰会补助项目";
    private static final String PREFERRED_STRUCTURED_ID = "817c1f6f-08ee-4405-9678-c59c063798bf";

    public static void main(String[] args) throws Exception {
        Class.forName("dm.jdbc.driver.DmDriver");
        Properties props = new Properties();
        props.setProperty("user", "SYSDBA");
        String password = System.getenv("DM_PASSWORD");
        if (password == null || password.trim().isEmpty()) {
            throw new IllegalStateException("DM_PASSWORD environment variable is required");
        }
        props.setProperty("password", password);
        props.setProperty("localTimezone", "Asia/Shanghai");

        List<Row> rows;
        try (Connection conn = DriverManager.getConnection(System.getenv().getOrDefault("KEJIJU_JDBC_DB_URL", "jdbc:dm://127.0.0.1:5236"), props)) {
            rows = selectRows(conn);
        }

        Path outDir = Paths.get("exports");
        Files.createDirectories(outDir);
        Path out = outDir.resolve("X1匹配政策标题清单.xlsx");
        writeXlsx(out, rows);
        System.out.println("rows=" + rows.size());
        System.out.println("xlsx=" + out.toAbsolutePath());
    }

    private static List<Row> selectRows(Connection conn) throws Exception {
        String sql =
            "SELECT f.ID FUSION_POLICY_ID, f.SOURCE_POLICY_ID, f.MAIN_FILE_TITLE, " +
            "       s.ID STRUCTURED_ID, s.SOURCE_MD_PATH, s.POLICY_TITLE " +
            "  FROM SYSDBA.X1_FUSION_POLICY f " +
            "  JOIN SYSDBA.POLICY_MD_STRUCTURED s ON TRIM(s.POLICY_TITLE)=TRIM(f.MAIN_FILE_TITLE) " +
            " WHERE NVL(TRIM(f.DEL_FLAG),'0')='0' " +
            "   AND NVL(TRIM(s.DEL_FLAG),'0')='0' " +
            "   AND f.SOURCE_POLICY_ID IS NOT NULL " +
            " ORDER BY f.ID, s.ID";

        Map<Long, List<Row>> byFusion = new LinkedHashMap<>();
        try (PreparedStatement ps = conn.prepareStatement(sql); ResultSet rs = ps.executeQuery()) {
            while (rs.next()) {
                Row row = new Row();
                row.fusionPolicyId = rs.getString("FUSION_POLICY_ID");
                row.sourcePolicyId = rs.getString("SOURCE_POLICY_ID");
                row.title = rs.getString("MAIN_FILE_TITLE");
                row.structuredId = rs.getString("STRUCTURED_ID");
                row.sourceMdPath = rs.getString("SOURCE_MD_PATH");
                row.structuredTitle = rs.getString("POLICY_TITLE");
                byFusion.computeIfAbsent(Long.valueOf(row.fusionPolicyId), k -> new ArrayList<>()).add(row);
            }
        }

        List<Row> selected = new ArrayList<>();
        for (Map.Entry<Long, List<Row>> entry : byFusion.entrySet()) {
            List<Row> group = entry.getValue();
            if (group.size() == 1) {
                selected.add(group.get(0));
                continue;
            }
            Row preferred = null;
            for (Row row : group) {
                if (DUPLICATE_TITLE.equals(row.title) && PREFERRED_STRUCTURED_ID.equals(row.structuredId)) {
                    preferred = row;
                    break;
                }
            }
            if (preferred == null) {
                throw new IllegalStateException("Unresolved duplicate structured match for fusion_policy_id=" + entry.getKey());
            }
            selected.add(preferred);
        }
        return selected;
    }

    private static void writeXlsx(Path out, List<Row> dataRows) throws Exception {
        List<String[]> table = new ArrayList<>();
        table.add(new String[] {"序号", "X1主政策ID", "source_policy_id", "政策名称标题", "结构化ID", "结构化来源路径"});
        int i = 1;
        for (Row row : dataRows) {
            table.add(new String[] {
                String.valueOf(i++),
                row.fusionPolicyId,
                row.sourcePolicyId,
                nvl(row.title),
                nvl(row.structuredId),
                nvl(row.sourceMdPath)
            });
        }

        Map<String, Integer> sst = new LinkedHashMap<>();
        int stringRefs = 0;
        for (int r = 0; r < table.size(); r++) {
            String[] row = table.get(r);
            for (int col = 0; col < row.length; col++) {
                if (col == 0 && r > 0) continue;
                String text = row[col] == null ? "" : row[col];
                sst.computeIfAbsent(text, k -> sst.size());
                stringRefs++;
            }
        }

        try (OutputStream os = Files.newOutputStream(out); ZipOutputStream zip = new ZipOutputStream(os, StandardCharsets.UTF_8)) {
            put(zip, "[Content_Types].xml", contentTypes());
            put(zip, "_rels/.rels", rootRels());
            put(zip, "xl/workbook.xml", workbook());
            put(zip, "xl/_rels/workbook.xml.rels", workbookRels());
            put(zip, "xl/styles.xml", styles());
            put(zip, "xl/sharedStrings.xml", sharedStrings(sst, stringRefs));
            put(zip, "xl/worksheets/sheet1.xml", worksheet(table, sst));
        }
    }

    private static String worksheet(List<String[]> table, Map<String, Integer> sst) {
        StringBuilder sb = new StringBuilder();
        sb.append("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>");
        sb.append("<worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" ");
        sb.append("xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\">");
        sb.append("<dimension ref=\"A1:F").append(table.size()).append("\"/>");
        sb.append("<sheetViews><sheetView workbookViewId=\"0\"><pane ySplit=\"1\" topLeftCell=\"A2\" activePane=\"bottomLeft\" state=\"frozen\"/></sheetView></sheetViews>");
        sb.append("<sheetFormatPr defaultRowHeight=\"15\"/>");
        sb.append("<cols>");
        sb.append("<col min=\"1\" max=\"1\" width=\"8\" customWidth=\"1\"/>");
        sb.append("<col min=\"2\" max=\"3\" width=\"16\" customWidth=\"1\"/>");
        sb.append("<col min=\"4\" max=\"4\" width=\"70\" customWidth=\"1\"/>");
        sb.append("<col min=\"5\" max=\"5\" width=\"40\" customWidth=\"1\"/>");
        sb.append("<col min=\"6\" max=\"6\" width=\"90\" customWidth=\"1\"/>");
        sb.append("</cols><sheetData>");
        for (int r = 0; r < table.size(); r++) {
            int rowNum = r + 1;
            String style = rowNum == 1 ? " s=\"1\"" : "";
            sb.append("<row r=\"").append(rowNum).append("\">");
            String[] row = table.get(r);
            for (int c = 0; c < row.length; c++) {
                String ref = col(c + 1) + rowNum;
                if (c == 0 && rowNum > 1) {
                    sb.append("<c r=\"").append(ref).append("\"").append(style).append("><v>").append(xml(row[c])).append("</v></c>");
                } else {
                    int idx = sst.get(row[c] == null ? "" : row[c]);
                    sb.append("<c r=\"").append(ref).append("\" t=\"s\"").append(style).append("><v>").append(idx).append("</v></c>");
                }
            }
            sb.append("</row>");
        }
        sb.append("</sheetData>");
        sb.append("<autoFilter ref=\"A1:F").append(table.size()).append("\"/>");
        sb.append("<pageMargins left=\"0.7\" right=\"0.7\" top=\"0.75\" bottom=\"0.75\" header=\"0.3\" footer=\"0.3\"/>");
        sb.append("</worksheet>");
        return sb.toString();
    }

    private static String sharedStrings(Map<String, Integer> sst, int stringRefs) {
        StringBuilder sb = new StringBuilder();
        sb.append("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>");
        sb.append("<sst xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" count=\"")
            .append(stringRefs).append("\" uniqueCount=\"").append(sst.size()).append("\">");
        for (String text : sst.keySet()) {
            sb.append("<si><t");
            if (!text.equals(text.trim())) sb.append(" xml:space=\"preserve\"");
            sb.append(">").append(xml(text)).append("</t></si>");
        }
        sb.append("</sst>");
        return sb.toString();
    }

    private static String contentTypes() {
        return "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>" +
            "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">" +
            "<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/>" +
            "<Default Extension=\"xml\" ContentType=\"application/xml\"/>" +
            "<Override PartName=\"/xl/workbook.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml\"/>" +
            "<Override PartName=\"/xl/worksheets/sheet1.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml\"/>" +
            "<Override PartName=\"/xl/styles.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml\"/>" +
            "<Override PartName=\"/xl/sharedStrings.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml\"/>" +
            "</Types>";
    }

    private static String rootRels() {
        return "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>" +
            "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">" +
            "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"xl/workbook.xml\"/>" +
            "</Relationships>";
    }

    private static String workbook() {
        return "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>" +
            "<workbook xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\">" +
            "<sheets><sheet name=\"政策标题清单\" sheetId=\"1\" r:id=\"rId1\"/></sheets>" +
            "</workbook>";
    }

    private static String workbookRels() {
        return "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>" +
            "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">" +
            "<Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet\" Target=\"worksheets/sheet1.xml\"/>" +
            "<Relationship Id=\"rId2\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles\" Target=\"styles.xml\"/>" +
            "<Relationship Id=\"rId3\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings\" Target=\"sharedStrings.xml\"/>" +
            "</Relationships>";
    }

    private static String styles() {
        return "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>" +
            "<styleSheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\">" +
            "<fonts count=\"2\"><font><sz val=\"11\"/><name val=\"Calibri\"/></font><font><b/><sz val=\"11\"/><name val=\"Calibri\"/></font></fonts>" +
            "<fills count=\"2\"><fill><patternFill patternType=\"none\"/></fill><fill><patternFill patternType=\"gray125\"/></fill></fills>" +
            "<borders count=\"1\"><border><left/><right/><top/><bottom/><diagonal/></border></borders>" +
            "<cellStyleXfs count=\"1\"><xf numFmtId=\"0\" fontId=\"0\" fillId=\"0\" borderId=\"0\"/></cellStyleXfs>" +
            "<cellXfs count=\"2\"><xf numFmtId=\"0\" fontId=\"0\" fillId=\"0\" borderId=\"0\" xfId=\"0\"/><xf numFmtId=\"0\" fontId=\"1\" fillId=\"0\" borderId=\"0\" xfId=\"0\" applyFont=\"1\"/></cellXfs>" +
            "<cellStyles count=\"1\"><cellStyle name=\"Normal\" xfId=\"0\" builtinId=\"0\"/></cellStyles>" +
            "</styleSheet>";
    }

    private static void put(ZipOutputStream zip, String name, String content) throws Exception {
        zip.putNextEntry(new ZipEntry(name));
        byte[] bytes = content.getBytes(StandardCharsets.UTF_8);
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        bos.write(bytes);
        bos.writeTo(zip);
        zip.closeEntry();
    }

    private static String col(int n) {
        StringBuilder sb = new StringBuilder();
        while (n > 0) {
            int rem = (n - 1) % 26;
            sb.insert(0, (char) ('A' + rem));
            n = (n - 1) / 26;
        }
        return sb.toString();
    }

    private static String xml(String s) {
        if (s == null) return "";
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;");
    }

    private static String nvl(String s) {
        return s == null ? "" : s;
    }

    private static class Row {
        String fusionPolicyId;
        String sourcePolicyId;
        String title;
        String structuredId;
        String sourceMdPath;
        String structuredTitle;
    }
}
