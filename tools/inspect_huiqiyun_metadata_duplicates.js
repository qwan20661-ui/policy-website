var DriverManager = Java.type("java.sql.DriverManager");
var Class = Java.type("java.lang.Class");
var Properties = Java.type("java.util.Properties");
Class.forName("dm.jdbc.driver.DmDriver");
var props = new Properties();
props.setProperty("user", "SYSDBA");
props.setProperty("password", Java.type("java.lang.System").getenv("KEJIJU_DB_PASSWORD"));
var conn = DriverManager.getConnection(Java.type("java.lang.System").getenv().getOrDefault("KEJIJU_JDBC_DB_URL", "jdbc:dm://127.0.0.1:5236"), props);
try {
  var ps = conn.prepareStatement("SELECT POLICY_ID,POLICY_ATTRIBUTE,REVIEW_STATUS,UPDATED_BY,UPDATED_AT FROM SYSDBA.POLICY_METADATA WHERE POLICY_ID IN ('19170','12380') ORDER BY POLICY_ID,UPDATED_AT DESC");
  var rs = ps.executeQuery();
  while (rs.next()) {
    print(rs.getString("POLICY_ID") + "\t" + rs.getString("POLICY_ATTRIBUTE") + "\t" + rs.getString("REVIEW_STATUS") + "\t" + rs.getString("UPDATED_BY") + "\t" + rs.getString("UPDATED_AT"));
  }
  rs.close();
  ps.close();
} finally {
  conn.close();
}
