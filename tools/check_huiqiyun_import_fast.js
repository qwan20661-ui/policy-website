var DriverManager = Java.type("java.sql.DriverManager");
var Class = Java.type("java.lang.Class");
var Properties = Java.type("java.util.Properties");

Class.forName("dm.jdbc.driver.DmDriver");
var props = new Properties();
props.setProperty("user", "SYSDBA");
props.setProperty("password", Java.type("java.lang.System").getenv("KEJIJU_DB_PASSWORD"));
var conn = DriverManager.getConnection(Java.type("java.lang.System").getenv().getOrDefault("KEJIJU_JDBC_DB_URL", "jdbc:dm://127.0.0.1:5236"), props);

function q(label, sql) {
  var st = conn.createStatement();
  var rs = st.executeQuery(sql);
  try {
    rs.next();
    print(label + "=" + rs.getString(1));
  } finally {
    rs.close();
    st.close();
  }
}

try {
  q("fusion_created_by_huiqiyun", "SELECT COUNT(*) FROM SYSDBA.X1_FUSION_POLICY WHERE CREATED_BY='Codex-Huiqiyun' AND NVL(DEL_FLAG,'0')='0'");
  q("fusion_visible_created_by_huiqiyun", "SELECT COUNT(*) FROM SYSDBA.X1_FUSION_POLICY WHERE CREATED_BY='Codex-Huiqiyun' AND NVL(DEL_FLAG,'0')='0' AND TRIM(NVL(FRONT_VISIBLE,'1')) <> '0'");
  q("fusion_pending_created_by_huiqiyun", "SELECT COUNT(*) FROM SYSDBA.X1_FUSION_POLICY WHERE CREATED_BY='Codex-Huiqiyun' AND NVL(DEL_FLAG,'0')='0' AND TRIM(NVL(MARK_REVIEW_STATUS,'0'))='0'");
  q("documents_created_by_huiqiyun", "SELECT COUNT(*) FROM SYSDBA.X1_POLICY_DOCUMENT WHERE CREATED_BY='Codex-Huiqiyun' AND NVL(DEL_FLAG,'0')='0'");
  q("documents_updated_by_huiqiyun", "SELECT COUNT(*) FROM SYSDBA.X1_POLICY_DOCUMENT WHERE UPDATED_BY='Codex-Huiqiyun' AND NVL(DEL_FLAG,'0')='0'");
  q("metadata_updated_unique_huiqiyun", "SELECT COUNT(DISTINCT POLICY_ID) FROM SYSDBA.POLICY_METADATA WHERE UPDATED_BY='Codex-Huiqiyun'");
  q("hidden_reused_now_visible", "SELECT COUNT(*) FROM SYSDBA.X1_FUSION_POLICY WHERE ID IN (7874,7165) AND TRIM(NVL(FRONT_VISIBLE,'1')) <> '0'");
  q("frontend_visible_total", "SELECT COUNT(*) FROM SYSDBA.X1_FUSION_POLICY WHERE NVL(DEL_FLAG,'0')='0' AND TRIM(NVL(FRONT_VISIBLE,'1')) <> '0'");
} finally {
  conn.close();
}
