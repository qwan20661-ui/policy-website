var DriverManager = Java.type("java.sql.DriverManager");
var Class = Java.type("java.lang.Class");
var Properties = Java.type("java.util.Properties");
Class.forName("dm.jdbc.driver.DmDriver");
var props = new Properties();
props.setProperty("user", "SYSDBA");
props.setProperty("password", Java.type("java.lang.System").getenv("KEJIJU_DB_PASSWORD"));
var conn = DriverManager.getConnection(Java.type("java.lang.System").getenv().getOrDefault("KEJIJU_JDBC_DB_URL", "jdbc:dm://127.0.0.1:5236"), props);
function q(sql) {
  var rs = conn.createStatement().executeQuery(sql);
  try { rs.next(); print(rs.getString(1)); } finally { rs.close(); }
}
try {
  print("fusion_created_by_codex");
  q("SELECT COUNT(*) FROM SYSDBA.X1_FUSION_POLICY WHERE CREATED_BY='Codex批量导入' AND NVL(DEL_FLAG,'0')='0'");
  print("documents_created_by_codex");
  q("SELECT COUNT(*) FROM SYSDBA.X1_POLICY_DOCUMENT WHERE CREATED_BY='Codex批量导入' AND NVL(DEL_FLAG,'0')='0'");
  print("distinct_fusion_for_codex_docs");
  q("SELECT COUNT(DISTINCT FUSION_POLICY_ID) FROM SYSDBA.X1_POLICY_DOCUMENT WHERE CREATED_BY='Codex批量导入' AND NVL(DEL_FLAG,'0')='0'");
  print("max_doc_id_codex");
  q("SELECT NVL(MAX(ID),0) FROM SYSDBA.X1_POLICY_DOCUMENT WHERE CREATED_BY='Codex批量导入'");
} finally {
  conn.close();
}
