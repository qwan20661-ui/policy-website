var Files = Java.type("java.nio.file.Files");
var Paths = Java.type("java.nio.file.Paths");
var StandardCharsets = Java.type("java.nio.charset.StandardCharsets");
var DriverManager = Java.type("java.sql.DriverManager");
var Class = Java.type("java.lang.Class");
var Properties = Java.type("java.util.Properties");
var StringBuilder = Java.type("java.lang.StringBuilder");

Class.forName("dm.jdbc.driver.DmDriver");
var props = new Properties();
props.setProperty("user", "SYSDBA");
props.setProperty("password", Java.type("java.lang.System").getenv("KEJIJU_DB_PASSWORD"));
var conn = DriverManager.getConnection(Java.type("java.lang.System").getenv().getOrDefault("KEJIJU_JDBC_DB_URL", "jdbc:dm://127.0.0.1:5236"), props);

function text(value) {
  if (value == null) return "";
  return String(value).replace(/\t/g, " ").replace(/\r?\n/g, " ").replace(/^\s+|\s+$/g, "");
}

function writeQuery(path, sql, columns) {
  var out = new StringBuilder();
  out.append(columns.join("\t")).append("\n");
  var stmt = conn.createStatement();
  stmt.setFetchSize(1000);
  var rs = stmt.executeQuery(sql);
  var count = 0;
  try {
    while (rs.next()) {
      var row = [];
      for (var i = 0; i < columns.length; i++) {
        row.push(text(rs.getString(columns[i])));
      }
      out.append(row.join("\t")).append("\n");
      count++;
    }
  } finally {
    rs.close();
    stmt.close();
  }
  Files.write(Paths.get(path), String(out).getBytes(StandardCharsets.UTF_8));
  print(path + "=" + count);
}

try {
  Files.createDirectories(Paths.get("tmp"));
  writeQuery(
    "tmp/huiqiyun_policy_catalog.tsv",
    "SELECT p.ID, p.POLICY_TITLE, p.FILE_NAME, " +
    "(SELECT o.PUBLISH_DATE FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=CAST(p.ID AS VARCHAR(50)) AND ROWNUM=1) AS PUBLISH_DATE, " +
    "(SELECT o.ISSUE_DEPARTMENT FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=CAST(p.ID AS VARCHAR(50)) AND ROWNUM=1) AS ISSUE_DEPARTMENT, " +
    "(SELECT o.POLICY_CODE FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=CAST(p.ID AS VARCHAR(50)) AND ROWNUM=1) AS POLICY_CODE, " +
    "(SELECT o.FILE_PATH FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=CAST(p.ID AS VARCHAR(50)) AND ROWNUM=1) AS FILE_PATH " +
    "FROM SYSDBA.POLICY p ORDER BY p.ID",
    ["ID", "POLICY_TITLE", "FILE_NAME", "PUBLISH_DATE", "ISSUE_DEPARTMENT", "POLICY_CODE", "FILE_PATH"]
  );

  writeQuery(
    "tmp/huiqiyun_existing_fusion.tsv",
    "SELECT ID, DIRECTORY_TITLE, MAIN_FILE_TITLE, SOURCE_POLICY_ID, MARK_REVIEW_STATUS, FRONT_VISIBLE, DEL_FLAG " +
    "FROM SYSDBA.X1_FUSION_POLICY ORDER BY ID",
    ["ID", "DIRECTORY_TITLE", "MAIN_FILE_TITLE", "SOURCE_POLICY_ID", "MARK_REVIEW_STATUS", "FRONT_VISIBLE", "DEL_FLAG"]
  );

  writeQuery(
    "tmp/huiqiyun_existing_documents.tsv",
    "SELECT ID, FUSION_POLICY_ID, SOURCE_POLICY_ID, POLICY_ATTRIBUTE, DEL_FLAG " +
    "FROM SYSDBA.X1_POLICY_DOCUMENT ORDER BY ID",
    ["ID", "FUSION_POLICY_ID", "SOURCE_POLICY_ID", "POLICY_ATTRIBUTE", "DEL_FLAG"]
  );
} finally {
  conn.close();
}
