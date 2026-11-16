var DriverManager = Java.type("java.sql.DriverManager");
var Class = Java.type("java.lang.Class");
var Properties = Java.type("java.util.Properties");
Class.forName("dm.jdbc.driver.DmDriver");
var props = new Properties();
props.setProperty("user", "SYSDBA");
props.setProperty("password", Java.type("java.lang.System").getenv("KEJIJU_DB_PASSWORD"));
var conn = DriverManager.getConnection(Java.type("java.lang.System").getenv().getOrDefault("KEJIJU_JDBC_DB_URL", "jdbc:dm://127.0.0.1:5236"), props);
try {
  var ps = conn.prepareStatement("SELECT ID,DIRECTORY_TITLE,MAIN_FILE_TITLE,STATUS,DEL_FLAG,MARK_REVIEW_STATUS,FRONT_VISIBLE,CREATED_BY,UPDATED_BY FROM SYSDBA.X1_FUSION_POLICY WHERE ID IN (7874,8728) ORDER BY ID");
  var rs = ps.executeQuery();
  while (rs.next()) {
    print(rs.getString("ID") + "\t" + rs.getString("STATUS") + "\t" + rs.getString("DEL_FLAG") + "\t" + rs.getString("MARK_REVIEW_STATUS") + "\t" + rs.getString("FRONT_VISIBLE") + "\t" + rs.getString("CREATED_BY") + "\t" + rs.getString("UPDATED_BY") + "\t" + rs.getString("MAIN_FILE_TITLE"));
  }
  rs.close();
  ps.close();
  var ps2 = conn.prepareStatement("SELECT FUSION_POLICY_ID,COUNT(*) FROM SYSDBA.X1_POLICY_DOCUMENT WHERE FUSION_POLICY_ID IN (7874,8728) AND NVL(DEL_FLAG,'0')='0' GROUP BY FUSION_POLICY_ID ORDER BY FUSION_POLICY_ID");
  var rs2 = ps2.executeQuery();
  while (rs2.next()) print("docs\t" + rs2.getString(1) + "\t" + rs2.getString(2));
  rs2.close();
  ps2.close();
} finally {
  conn.close();
}
