import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.Statement;
import java.util.Base64;

public class DamengRuleOptionExporter {
    public static void main(String[] args) throws Exception {
        if (args.length < 4) {
            throw new IllegalArgumentException("Usage: java DamengRuleOptionExporter <host> <port> <user> <password>");
        }

        Class.forName("dm.jdbc.driver.DmDriver");
        String url = "jdbc:dm://" + args[0] + ":" + args[1];
        try (Connection conn = DriverManager.getConnection(url, args[2], args[3]);
             Statement st = conn.createStatement()) {
            export(st, "RULE_OPTIONS",
                "SELECT id, rule_type, level1_name, level2_name, option_value, sort_order " +
                "FROM dim_rule_option WHERE status = 1 ORDER BY rule_type, sort_order",
                6);
            export(st, "CATEGORY_FIELD_INFO",
                "SELECT level1_category, level2_category, level3_category, core_fields " +
                "FROM category_field_info ORDER BY level1_category, level2_category, level3_category",
                4);
            export(st, "INDUSTRY",
                "SELECT id, territory_name, territory_two_name FROM sys_territory_data_new " +
                "WHERE del_flag = 0 AND territory_name IS NOT NULL ORDER BY territory_name, territory_two_name",
                3);
            export(st, "KEYWORDS",
                "SELECT keyword_text FROM policy_keyword_library WHERE status = 1 ORDER BY keyword_category, keyword_text",
                1);
        }
    }

    static void export(Statement st, String section, String sql, int columnCount) throws Exception {
        System.out.println("##" + section);
        try (ResultSet rs = st.executeQuery(sql)) {
            while (rs.next()) {
                StringBuilder line = new StringBuilder();
                for (int i = 1; i <= columnCount; i++) {
                    if (i > 1) {
                        line.append('\t');
                    }
                    line.append(encode(rs.getString(i)));
                }
                System.out.println(line);
            }
        }
    }

    static String encode(String value) {
        if (value == null) {
            return "\\N";
        }
        return Base64.getEncoder().encodeToString(value.getBytes(StandardCharsets.UTF_8));
    }
}
