import java.sql.Clob;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.Timestamp;
import java.sql.Types;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Properties;

public class ImportMdStructuredToX1 {
    private static final int EXPECTED_FUSION_POLICY_COUNT = 312;
    private static final int EXPECTED_SOURCE_POLICY_COUNT = 301;
    private static final String DUPLICATE_TITLE = "2026年行业龙头企业年会、峰会补助项目";
    private static final String PREFERRED_STRUCTURED_ID = "817c1f6f-08ee-4405-9678-c59c063798bf";
    private static final DateTimeFormatter DATE_FORMAT = DateTimeFormatter.ISO_LOCAL_DATE;

    public static void main(String[] args) throws Exception {
        Class.forName("dm.jdbc.driver.DmDriver");
        Properties props = new Properties();
        props.setProperty("user", "SYSDBA");
        String password = System.getenv("DM_PASSWORD");
        if (isBlank(password)) {
            throw new IllegalStateException("DM_PASSWORD environment variable is required");
        }
        props.setProperty("password", password);
        props.setProperty("localTimezone", "Asia/Shanghai");

        try (Connection conn = DriverManager.getConnection(System.getenv().getOrDefault("KEJIJU_JDBC_DB_URL", "jdbc:dm://127.0.0.1:5236"), props)) {
            conn.setAutoCommit(false);
            try {
                List<Candidate> candidates = selectCandidates(loadCandidates(conn));
                validateTargets(candidates);
                UpdateStats stats = applyUpdates(conn, candidates);
                conn.commit();
                System.out.println("committed=true");
                System.out.println("source_policy_count=" + candidates.size());
                System.out.println("policy_metadata_rows=" + stats.policyMetadataRows);
                System.out.println("application_condition_rows=" + stats.applicationConditionRows);
                System.out.println("support_method_rows=" + stats.supportMethodRows);
                System.out.println("application_material_rows=" + stats.applicationMaterialRows);
                System.out.println("policy_rows=" + stats.policyRows);
                System.out.println("field_updates=" + stats.fieldUpdates);
                System.out.println("duplicate_title_used_structured_id=" + PREFERRED_STRUCTURED_ID);
            } catch (Exception e) {
                conn.rollback();
                System.out.println("committed=false");
                System.out.println("rolled_back=true");
                throw e;
            } finally {
                conn.setAutoCommit(true);
            }
        }
    }

    private static List<Candidate> loadCandidates(Connection conn) throws Exception {
        String sql =
            "SELECT f.ID FUSION_POLICY_ID, f.SOURCE_POLICY_ID, f.MAIN_FILE_TITLE, " +
            "       s.ID STRUCTURED_ID, s.PUBLISH_REGION, s.ISSUE_DEPARTMENT, " +
            "       s.DECLARE_START_TIME, s.DECLARE_END_TIME, " +
            "       s.SUPPORT_CONTENT, s.APPLICATION_MATERIALS, s.CONTACT_INFO, " +
            "       (SELECT MIN(m.ID) FROM SYSDBA.POLICY_METADATA m WHERE m.POLICY_ID=f.SOURCE_POLICY_ID) POLICY_METADATA_ID, " +
            "       (SELECT MIN(c.ID) FROM SYSDBA.APPLICATION_CONDITION c WHERE c.POLICY_ID=f.SOURCE_POLICY_ID) APPLICATION_CONDITION_ID, " +
            "       (SELECT MIN(sm.ID) FROM SYSDBA.SUPPORT_METHOD sm WHERE sm.POLICY_ID=f.SOURCE_POLICY_ID) SUPPORT_METHOD_ID, " +
            "       (SELECT MIN(am.ID) FROM SYSDBA.APPLICATION_MATERIAL am WHERE am.POLICY_ID=f.SOURCE_POLICY_ID) APPLICATION_MATERIAL_ID " +
            "  FROM SYSDBA.X1_FUSION_POLICY f " +
            "  JOIN SYSDBA.POLICY_MD_STRUCTURED s ON TRIM(s.POLICY_TITLE)=TRIM(f.MAIN_FILE_TITLE) " +
            " WHERE NVL(TRIM(f.DEL_FLAG),'0')='0' " +
            "   AND NVL(TRIM(s.DEL_FLAG),'0')='0' " +
            "   AND f.SOURCE_POLICY_ID IS NOT NULL " +
            " ORDER BY f.ID, s.ID";

        List<Candidate> rows = new ArrayList<>();
        try (PreparedStatement ps = conn.prepareStatement(sql); ResultSet rs = ps.executeQuery()) {
            while (rs.next()) {
                Candidate c = new Candidate();
                c.fusionPolicyId = rs.getLong("FUSION_POLICY_ID");
                c.sourcePolicyId = rs.getLong("SOURCE_POLICY_ID");
                c.mainFileTitle = rs.getString("MAIN_FILE_TITLE");
                c.structuredId = rs.getString("STRUCTURED_ID");
                c.applicableScope = rs.getString("PUBLISH_REGION");
                c.issuingDepartment = rs.getString("ISSUE_DEPARTMENT");
                c.applicationStartTime = formatDate(rs.getTimestamp("DECLARE_START_TIME"));
                c.applicationEndTime = formatDate(rs.getTimestamp("DECLARE_END_TIME"));
                c.supportContent = clobOrString(rs, "SUPPORT_CONTENT");
                c.applicationMaterials = clobOrString(rs, "APPLICATION_MATERIALS");
                c.contactInfo = clobOrString(rs, "CONTACT_INFO");
                c.policyMetadataId = nullableLong(rs, "POLICY_METADATA_ID");
                c.applicationConditionId = nullableLong(rs, "APPLICATION_CONDITION_ID");
                c.supportMethodId = nullableLong(rs, "SUPPORT_METHOD_ID");
                c.applicationMaterialId = nullableLong(rs, "APPLICATION_MATERIAL_ID");
                rows.add(c);
            }
        }
        return rows;
    }

    private static List<Candidate> selectCandidates(List<Candidate> rows) {
        Map<Long, List<Candidate>> byFusion = new LinkedHashMap<>();
        for (Candidate row : rows) {
            byFusion.computeIfAbsent(row.fusionPolicyId, k -> new ArrayList<>()).add(row);
        }
        if (byFusion.size() != EXPECTED_FUSION_POLICY_COUNT) {
            throw new IllegalStateException("Unexpected matched fusion policy count: " + byFusion.size());
        }

        List<Candidate> selectedByFusion = new ArrayList<>();
        for (Map.Entry<Long, List<Candidate>> entry : byFusion.entrySet()) {
            List<Candidate> group = entry.getValue();
            if (group.size() == 1) {
                selectedByFusion.add(group.get(0));
                continue;
            }
            Candidate preferred = null;
            for (Candidate row : group) {
                if (DUPLICATE_TITLE.equals(row.mainFileTitle) && PREFERRED_STRUCTURED_ID.equals(row.structuredId)) {
                    preferred = row;
                    break;
                }
            }
            if (preferred == null) {
                throw new IllegalStateException("Unresolved duplicate match for fusion_policy_id=" + entry.getKey());
            }
            selectedByFusion.add(preferred);
        }

        Map<Long, Candidate> bySourcePolicy = new LinkedHashMap<>();
        for (Candidate row : selectedByFusion) {
            Candidate existing = bySourcePolicy.get(row.sourcePolicyId);
            if (existing == null || row.fusionPolicyId > existing.fusionPolicyId) {
                bySourcePolicy.put(row.sourcePolicyId, row);
            }
        }
        if (bySourcePolicy.size() != EXPECTED_SOURCE_POLICY_COUNT) {
            throw new IllegalStateException("Unexpected unique source policy count: " + bySourcePolicy.size());
        }
        return new ArrayList<>(bySourcePolicy.values());
    }

    private static void validateTargets(List<Candidate> candidates) {
        List<String> missing = new ArrayList<>();
        for (Candidate c : candidates) {
            if (!isBlank(c.applicableScope) && c.policyMetadataId == null) missing.add(c.sourcePolicyId + ":POLICY_METADATA");
            if (!isBlank(c.issuingDepartment) && c.policyMetadataId == null) missing.add(c.sourcePolicyId + ":POLICY_METADATA");
            if (!isBlank(c.applicationStartTime) && c.applicationConditionId == null) missing.add(c.sourcePolicyId + ":APPLICATION_CONDITION");
            if (!isBlank(c.applicationEndTime) && c.applicationConditionId == null) missing.add(c.sourcePolicyId + ":APPLICATION_CONDITION");
            if (!isBlank(c.supportContent) && c.supportMethodId == null) missing.add(c.sourcePolicyId + ":SUPPORT_METHOD");
            if (!isBlank(c.applicationMaterials) && c.applicationMaterialId == null) missing.add(c.sourcePolicyId + ":APPLICATION_MATERIAL");
        }
        if (!missing.isEmpty()) {
            throw new IllegalStateException("Missing target rows: " + missing);
        }
    }

    private static UpdateStats applyUpdates(Connection conn, List<Candidate> candidates) throws Exception {
        UpdateStats stats = new UpdateStats();
        for (Candidate c : candidates) {
            int changed;

            changed = updatePolicyMetadata(conn, c);
            stats.policyMetadataRows += changed > 0 ? 1 : 0;
            stats.fieldUpdates += changed;

            changed = updateApplicationCondition(conn, c);
            stats.applicationConditionRows += changed > 0 ? 1 : 0;
            stats.fieldUpdates += changed;

            changed = updateSingleColumn(conn, "SYSDBA.SUPPORT_METHOD", "SUPPORT_CONTENT_DESC", "ID", c.supportMethodId, c.supportContent, Types.CLOB);
            stats.supportMethodRows += changed > 0 ? 1 : 0;
            stats.fieldUpdates += changed;

            changed = updateSingleColumn(conn, "SYSDBA.APPLICATION_MATERIAL", "APPLICATION_MATERIALS", "ID", c.applicationMaterialId, c.applicationMaterials, Types.CLOB);
            stats.applicationMaterialRows += changed > 0 ? 1 : 0;
            stats.fieldUpdates += changed;

            changed = updateSingleColumn(conn, "SYSDBA.POLICY", "CONTACT_INFO", "ID", c.sourcePolicyId, c.contactInfo, Types.CLOB);
            stats.policyRows += changed > 0 ? 1 : 0;
            stats.fieldUpdates += changed;
        }
        return stats;
    }

    private static int updatePolicyMetadata(Connection conn, Candidate c) throws Exception {
        if (c.policyMetadataId == null) return 0;
        List<String> sets = new ArrayList<>();
        List<Object> vals = new ArrayList<>();
        if (!isBlank(c.applicableScope) && !Objects.equals(fetchString(conn, "SYSDBA.POLICY_METADATA", "APPLICABLE_SCOPE", "ID", c.policyMetadataId), c.applicableScope)) {
            sets.add("APPLICABLE_SCOPE=?");
            vals.add(c.applicableScope);
        }
        if (!isBlank(c.issuingDepartment) && !Objects.equals(fetchString(conn, "SYSDBA.POLICY_METADATA", "ISSUING_DEPARTMENT", "ID", c.policyMetadataId), c.issuingDepartment)) {
            sets.add("ISSUING_DEPARTMENT=?");
            vals.add(c.issuingDepartment);
        }
        return executeUpdate(conn, "SYSDBA.POLICY_METADATA", "ID", c.policyMetadataId, sets, vals);
    }

    private static int updateApplicationCondition(Connection conn, Candidate c) throws Exception {
        if (c.applicationConditionId == null) return 0;
        List<String> sets = new ArrayList<>();
        List<Object> vals = new ArrayList<>();
        if (!isBlank(c.applicationStartTime) && !Objects.equals(fetchString(conn, "SYSDBA.APPLICATION_CONDITION", "APPLICATION_START_TIME", "ID", c.applicationConditionId), c.applicationStartTime)) {
            sets.add("APPLICATION_START_TIME=?");
            vals.add(c.applicationStartTime);
        }
        if (!isBlank(c.applicationEndTime) && !Objects.equals(fetchString(conn, "SYSDBA.APPLICATION_CONDITION", "APPLICATION_END_TIME", "ID", c.applicationConditionId), c.applicationEndTime)) {
            sets.add("APPLICATION_END_TIME=?");
            vals.add(c.applicationEndTime);
        }
        return executeUpdate(conn, "SYSDBA.APPLICATION_CONDITION", "ID", c.applicationConditionId, sets, vals);
    }

    private static int updateSingleColumn(Connection conn, String table, String column, String keyColumn, Long keyValue, String newValue, int sqlType) throws Exception {
        if (keyValue == null || isBlank(newValue)) return 0;
        String oldValue = fetchString(conn, table, column, keyColumn, keyValue);
        if (Objects.equals(normalize(oldValue), normalize(newValue))) return 0;
        try (PreparedStatement ps = conn.prepareStatement("UPDATE " + table + " SET " + column + "=? WHERE " + keyColumn + "=?")) {
            if (sqlType == Types.CLOB) {
                ps.setString(1, newValue);
            } else {
                ps.setObject(1, newValue);
            }
            ps.setLong(2, keyValue);
            int rows = ps.executeUpdate();
            if (rows != 1) {
                throw new IllegalStateException("Expected one row update for " + table + "." + column + " id=" + keyValue + ", got " + rows);
            }
            return 1;
        }
    }

    private static int executeUpdate(Connection conn, String table, String keyColumn, Long keyValue, List<String> sets, List<Object> vals) throws Exception {
        if (sets.isEmpty()) return 0;
        String sql = "UPDATE " + table + " SET " + String.join(",", sets) + " WHERE " + keyColumn + "=?";
        try (PreparedStatement ps = conn.prepareStatement(sql)) {
            for (int i = 0; i < vals.size(); i++) {
                ps.setObject(i + 1, vals.get(i));
            }
            ps.setLong(vals.size() + 1, keyValue);
            int rows = ps.executeUpdate();
            if (rows != 1) {
                throw new IllegalStateException("Expected one row update for " + table + " id=" + keyValue + ", got " + rows);
            }
            return sets.size();
        }
    }

    private static String fetchString(Connection conn, String table, String column, String keyColumn, Long keyValue) throws Exception {
        try (PreparedStatement ps = conn.prepareStatement("SELECT " + column + " FROM " + table + " WHERE " + keyColumn + "=?")) {
            ps.setLong(1, keyValue);
            try (ResultSet rs = ps.executeQuery()) {
                if (!rs.next()) return "";
                Object o = rs.getObject(1);
                if (o == null) return "";
                if (o instanceof Clob) {
                    Clob c = (Clob) o;
                    long len = c.length();
                    return len == 0 ? "" : c.getSubString(1, (int) len);
                }
                return String.valueOf(o);
            }
        }
    }

    private static Long nullableLong(ResultSet rs, String col) throws Exception {
        long value = rs.getLong(col);
        return rs.wasNull() ? null : value;
    }

    private static String formatDate(Timestamp ts) {
        if (ts == null) return "";
        return ts.toLocalDateTime().toLocalDate().format(DATE_FORMAT);
    }

    private static String clobOrString(ResultSet rs, String col) throws Exception {
        Object o = rs.getObject(col);
        if (o == null) return "";
        if (o instanceof Clob) {
            Clob c = (Clob) o;
            long len = c.length();
            return len == 0 ? "" : c.getSubString(1, (int) len);
        }
        return String.valueOf(o);
    }

    private static String normalize(String s) {
        return s == null ? "" : s.trim();
    }

    private static boolean isBlank(String s) {
        return s == null || s.trim().isEmpty();
    }

    private static class Candidate {
        long fusionPolicyId;
        long sourcePolicyId;
        String mainFileTitle;
        String structuredId;
        String applicableScope;
        String issuingDepartment;
        String applicationStartTime;
        String applicationEndTime;
        String supportContent;
        String applicationMaterials;
        String contactInfo;
        Long policyMetadataId;
        Long applicationConditionId;
        Long supportMethodId;
        Long applicationMaterialId;
    }

    private static class UpdateStats {
        int policyMetadataRows;
        int applicationConditionRows;
        int supportMethodRows;
        int applicationMaterialRows;
        int policyRows;
        int fieldUpdates;
    }
}
