import java.io.BufferedReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DatabaseMetaData;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.Statement;
import java.sql.Types;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public class DmPolicyData11Importer {
    static final String[] TABLE_ORDER = new String[] {
        "POLICY",
        "POLICY_METADATA",
        "POLICY_KEYWORD",
        "APPLICATION_CONDITION",
        "SUPPORT_METHOD",
        "APPLICATION_MATERIAL",
        "APPLICATION_ATTACHMENT",
        "CONDITION_CLUSTER",
        "MAIN_TECHNOLOGY",
        "MAIN_PRODUCT",
        "CORE_TEAM",
        "INNOVATION_INVESTMENT",
        "INNOVATION_OUTPUT",
        "INNOVATION_DEVELOPMENT",
        "INNOVATION_IMPACT"
    };

    static final Map<String, Long> policyIds = new HashMap<>();
    static final Map<String, Long> conditionIds = new HashMap<>();
    static final Map<String, Integer> insertedRows = new LinkedHashMap<>();
    static final Map<String, Integer> skippedRows = new LinkedHashMap<>();
    static final Map<String, Integer> columnMaxLengths = new HashMap<>();
    static final Map<String, Integer> truncatedRows = new LinkedHashMap<>();
    static int insertedPolicies = 0;
    static int skippedPolicies = 0;

    public static void main(String[] args) throws Exception {
        if (args.length < 6) {
            throw new IllegalArgumentException("Usage: java DmPolicyData11Importer <stageDir> <host> <port> <user> <password> <dryRun> [forceInsertPolicies]");
        }
        Path stageDir = Path.of(args[0]);
        String url = "jdbc:dm://" + args[1] + ":" + args[2];
        boolean dryRun = Boolean.parseBoolean(args[5]);
        boolean forceInsertPolicies = args.length >= 7 && Boolean.parseBoolean(args[6]);

        Class.forName("dm.jdbc.driver.DmDriver");
        try (Connection conn = DriverManager.getConnection(url, args[3], args[4])) {
            conn.setAutoCommit(false);
            loadColumnMaxLengths(conn);
            try {
                importPolicies(conn, readRows(stageDir.resolve("POLICY.tsv")), forceInsertPolicies);
                for (String table : TABLE_ORDER) {
                    if (!table.equals("POLICY")) {
                        importTable(conn, table, readRows(stageDir.resolve(table + ".tsv")));
                    }
                }

                if (dryRun) {
                    conn.rollback();
                    System.out.println("DRY_RUN rollback complete");
                } else {
                    conn.commit();
                    System.out.println("COMMIT complete");
                }
                System.out.println("insertedPolicies=" + insertedPolicies + ", skippedPolicies=" + skippedPolicies);
                for (String table : TABLE_ORDER) {
                    if (!table.equals("POLICY")) {
                        System.out.println(table + ": inserted=" + insertedRows.getOrDefault(table, 0) + ", skipped=" + skippedRows.getOrDefault(table, 0));
                    }
                }
                for (Map.Entry<String, Integer> entry : truncatedRows.entrySet()) {
                    System.out.println("TRUNCATED " + entry.getKey() + ": " + entry.getValue());
                }
            } catch (Exception ex) {
                conn.rollback();
                throw ex;
            }
        }
    }

    static void loadColumnMaxLengths(Connection conn) throws Exception {
        DatabaseMetaData meta = conn.getMetaData();
        for (String table : TABLE_ORDER) {
            try (ResultSet rs = meta.getColumns(null, null, table, null)) {
                while (rs.next()) {
                    int dataType = rs.getInt("DATA_TYPE");
                    if (!isTextType(dataType)) continue;
                    String column = rs.getString("COLUMN_NAME");
                    int size = rs.getInt("COLUMN_SIZE");
                    if (size > 0 && size < 1000000) {
                        columnMaxLengths.put(table + "." + column.toUpperCase(), size);
                    }
                }
            }
        }
        StringBuilder inClause = new StringBuilder();
        for (String table : TABLE_ORDER) {
            if (inClause.length() > 0) inClause.append(",");
            inClause.append("'").append(table).append("'");
        }
        String sql = "select TABLE_NAME, COLUMN_NAME, DATA_TYPE, DATA_LENGTH from USER_TAB_COLUMNS where TABLE_NAME in (" + inClause + ")";
        try (Statement st = conn.createStatement(); ResultSet rs = st.executeQuery(sql)) {
            while (rs.next()) {
                String dataType = rs.getString("DATA_TYPE");
                if (!isTextType(dataType)) continue;
                int size = rs.getInt("DATA_LENGTH");
                if (size > 0 && size < 1000000) {
                    String table = rs.getString("TABLE_NAME").toUpperCase();
                    String column = rs.getString("COLUMN_NAME").toUpperCase();
                    columnMaxLengths.put(table + "." + column, size);
                }
            }
        } catch (Exception ex) {
            System.out.println("WARN: failed to read USER_TAB_COLUMNS: " + ex.getMessage());
        }
        System.out.println("Loaded text column limits: " + columnMaxLengths.size());
    }

    static List<Map<String, String>> readRows(Path path) throws Exception {
        List<Map<String, String>> rows = new ArrayList<>();
        try (BufferedReader reader = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
            String header = reader.readLine();
            if (header == null) return rows;
            String[] columns = header.split("\t", -1);
            String line;
            while ((line = reader.readLine()) != null) {
                String[] parts = line.split("\t", -1);
                Map<String, String> row = new LinkedHashMap<>();
                for (int i = 0; i < columns.length; i++) {
                    row.put(columns[i], decode(i < parts.length ? parts[i] : "\\N"));
                }
                rows.add(row);
            }
        }
        return rows;
    }

    static String decode(String value) {
        if (value == null || value.equals("\\N")) return null;
        return new String(Base64.getDecoder().decode(value), StandardCharsets.UTF_8);
    }

    static void importPolicies(Connection conn, List<Map<String, String>> rows, boolean forceInsertPolicies) throws Exception {
        String sql = "insert into POLICY (FILE_NAME, ABSTRACT, CONTACT_INFO, APPLICATION_VOLUME, POLICY_TITLE, CREATED_AT, UPDATED_AT) values (?, ?, ?, ?, ?, current_timestamp, current_timestamp)";
        try (PreparedStatement exists = conn.prepareStatement("select id from POLICY where FILE_NAME=? and POLICY_TITLE=?");
             PreparedStatement ps = conn.prepareStatement(sql, Statement.RETURN_GENERATED_KEYS)) {
            for (Map<String, String> row : rows) {
                String rowKey = row.get("ROW_KEY");
                if (!forceInsertPolicies) {
                    exists.setString(1, row.get("FILE_NAME"));
                    exists.setString(2, row.get("POLICY_TITLE"));
                    try (ResultSet rs = exists.executeQuery()) {
                        if (rs.next()) {
                            policyIds.put(rowKey, rs.getLong(1));
                            skippedPolicies++;
                            System.out.println("SKIP existing policy: " + row.get("POLICY_TITLE") + " | " + row.get("FILE_NAME") + " | id=" + rs.getLong(1));
                            continue;
                        }
                    }
                }
                ps.setString(1, fit("POLICY", "FILE_NAME", row.get("FILE_NAME")));
                ps.setString(2, fit("POLICY", "ABSTRACT", row.get("ABSTRACT")));
                ps.setString(3, fit("POLICY", "CONTACT_INFO", row.get("CONTACT_INFO")));
                ps.setString(4, fit("POLICY", "APPLICATION_VOLUME", row.get("APPLICATION_VOLUME")));
                ps.setString(5, fit("POLICY", "POLICY_TITLE", row.get("POLICY_TITLE")));
                ps.executeUpdate();
                long id = generatedId(ps);
                policyIds.put(rowKey, id);
                insertedPolicies++;
                System.out.println("INSERT policy: " + row.get("POLICY_TITLE") + " | " + row.get("FILE_NAME") + " | id=" + id);
            }
        }
    }

    static void importTable(Connection conn, String table, List<Map<String, String>> rows) throws Exception {
        if (rows.isEmpty()) return;
        List<String> inputColumns = new ArrayList<>(rows.get(0).keySet());
        inputColumns.remove("ROW_KEY");
        List<String> dbColumns = new ArrayList<>();
        if (hasPolicyId(table)) dbColumns.add("POLICY_ID");
        if (hasConditionId(table)) dbColumns.add("CONDITION_ID");
        dbColumns.addAll(inputColumns);

        StringBuilder sql = new StringBuilder("insert into ").append(table).append(" (");
        sql.append(String.join(", ", dbColumns)).append(") values (");
        sql.append("?,".repeat(dbColumns.size()));
        sql.setLength(sql.length() - 1);
        sql.append(")");

        try (PreparedStatement ps = conn.prepareStatement(sql.toString(), Statement.RETURN_GENERATED_KEYS)) {
            for (Map<String, String> row : rows) {
                String rowKey = row.get("ROW_KEY");
                Long policyId = policyIds.get(rowKey);
                if (policyId == null) continue;
                Long conditionId = conditionIds.get(rowKey);
                if (hasConditionId(table) && conditionId == null) continue;

                if (shouldSkipExisting(conn, table, policyId, conditionId, row, rowKey)) {
                    skippedRows.merge(table, 1, Integer::sum);
                    continue;
                }

                int index = 1;
                if (hasPolicyId(table)) ps.setLong(index++, policyId);
                if (hasConditionId(table)) ps.setLong(index++, conditionId);
                for (String column : inputColumns) {
                    setValue(ps, index++, table, column, row.get(column));
                }
                ps.executeUpdate();
                if (table.equals("APPLICATION_CONDITION")) {
                    long id = generatedId(ps);
                    conditionIds.put(rowKey, id);
                }
                insertedRows.merge(table, 1, Integer::sum);
            }
        }
    }

    static boolean shouldSkipExisting(Connection conn, String table, Long policyId, Long conditionId, Map<String, String> row, String rowKey) throws Exception {
        if (table.equals("POLICY_KEYWORD")) {
            try (PreparedStatement ps = conn.prepareStatement("select 1 from POLICY_KEYWORD where POLICY_ID=? and KEYWORD=?")) {
                ps.setLong(1, policyId);
                ps.setString(2, row.get("KEYWORD"));
                try (ResultSet rs = ps.executeQuery()) {
                    return rs.next();
                }
            }
        }
        if (table.equals("APPLICATION_ATTACHMENT")) {
            try (PreparedStatement ps = conn.prepareStatement("select 1 from APPLICATION_ATTACHMENT where POLICY_ID=? and ATTACHMENT_NAME=?")) {
                ps.setLong(1, policyId);
                ps.setString(2, row.get("ATTACHMENT_NAME"));
                try (ResultSet rs = ps.executeQuery()) {
                    return rs.next();
                }
            }
        }
        if (hasUniquePolicyId(table)) {
            try (PreparedStatement ps = conn.prepareStatement("select ID from " + table + " where POLICY_ID=?")) {
                ps.setLong(1, policyId);
                try (ResultSet rs = ps.executeQuery()) {
                    if (rs.next()) {
                        if (table.equals("APPLICATION_CONDITION")) {
                            conditionIds.put(rowKey, rs.getLong(1));
                        }
                        return true;
                    }
                }
            }
        }
        if (hasConditionId(table)) {
            try (PreparedStatement ps = conn.prepareStatement("select 1 from " + table + " where CONDITION_ID=?")) {
                ps.setLong(1, conditionId);
                try (ResultSet rs = ps.executeQuery()) {
                    return rs.next();
                }
            }
        }
        return false;
    }

    static long generatedId(PreparedStatement ps) throws Exception {
        try (ResultSet rs = ps.getGeneratedKeys()) {
            if (rs.next()) {
                return rs.getLong(1);
            }
        }
        throw new IllegalStateException("Database did not return generated key");
    }

    static boolean hasPolicyId(String table) {
        return table.equals("POLICY_METADATA")
            || table.equals("POLICY_KEYWORD")
            || table.equals("APPLICATION_CONDITION")
            || table.equals("SUPPORT_METHOD")
            || table.equals("APPLICATION_MATERIAL")
            || table.equals("APPLICATION_ATTACHMENT");
    }

    static boolean hasUniquePolicyId(String table) {
        return table.equals("POLICY_METADATA")
            || table.equals("APPLICATION_CONDITION")
            || table.equals("SUPPORT_METHOD")
            || table.equals("APPLICATION_MATERIAL");
    }

    static boolean hasConditionId(String table) {
        return table.equals("CONDITION_CLUSTER")
            || table.equals("MAIN_TECHNOLOGY")
            || table.equals("MAIN_PRODUCT")
            || table.equals("CORE_TEAM")
            || table.equals("INNOVATION_INVESTMENT")
            || table.equals("INNOVATION_OUTPUT")
            || table.equals("INNOVATION_DEVELOPMENT")
            || table.equals("INNOVATION_IMPACT");
    }

    static void setValue(PreparedStatement ps, int index, String table, String column, String value) throws Exception {
        if (value == null) {
            ps.setNull(index, isSmallInt(column) ? Types.SMALLINT : Types.VARCHAR);
        } else if (isSmallInt(column)) {
            ps.setShort(index, Short.parseShort(value));
        } else {
            ps.setString(index, fit(table, column, value));
        }
    }

    static String fit(String table, String column, String value) {
        if (value == null) return null;
        String key = table + "." + column.toUpperCase();
        Integer maxLength = columnMaxLengths.get(key);
        if (maxLength == null || value.getBytes(StandardCharsets.UTF_8).length <= maxLength) {
            return value;
        }
        truncatedRows.merge(key, 1, Integer::sum);
        int end = value.length();
        while (end > 0 && value.substring(0, end).getBytes(StandardCharsets.UTF_8).length > maxLength) {
            end--;
        }
        return value.substring(0, end);
    }

    static boolean isTextType(int dataType) {
        return dataType == Types.CHAR
            || dataType == Types.VARCHAR
            || dataType == Types.LONGVARCHAR
            || dataType == Types.NCHAR
            || dataType == Types.NVARCHAR
            || dataType == Types.LONGNVARCHAR
            || dataType == Types.CLOB
            || dataType == Types.NCLOB;
    }

    static boolean isTextType(String dataType) {
        if (dataType == null) return false;
        String normalized = dataType.toUpperCase();
        return normalized.contains("CHAR")
            || normalized.contains("TEXT")
            || normalized.contains("CLOB");
    }

    static boolean isSmallInt(String column) {
        return column.equals("MGMT_RD_SYSTEM_VALUE")
            || column.equals("CREDIT_SAFETY_MAJOR_ACCIDENT")
            || column.equals("CREDIT_BUSINESS_ABNORMAL_LIST")
            || column.equals("CREDIT_BUSINESS_SERIOUS_DISHONESTY_LIST")
            || column.equals("CREDIT_LEGAL_COMPLIANCE_VALUE")
            || column.equals("CREDIT_RESEARCH_CREDIT_VALUE")
            || column.equals("CREDIT_ADMIN_PENALTY_VALUE")
            || column.equals("CREDIT_QUALITY_MAJOR_ACCIDENT");
    }
}
