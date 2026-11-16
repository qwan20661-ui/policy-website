var DriverManager = Java.type("java.sql.DriverManager");
var Class = Java.type("java.lang.Class");
var Properties = Java.type("java.util.Properties");

Class.forName("dm.jdbc.driver.DmDriver");

var props = new Properties();
props.setProperty("user", "SYSDBA");
props.setProperty("password", Java.type("java.lang.System").getenv("KEJIJU_DB_PASSWORD"));

var conn = DriverManager.getConnection(Java.type("java.lang.System").getenv().getOrDefault("KEJIJU_JDBC_DB_URL", "jdbc:dm://127.0.0.1:5236"), props);
conn.setAutoCommit(false);

function scalar(sql) {
  var stmt = conn.createStatement();
  var rs = stmt.executeQuery(sql);
  try {
    return rs.next() ? Number(rs.getString(1)) : 0;
  } finally {
    rs.close();
    stmt.close();
  }
}

function exec(sql) {
  var stmt = conn.createStatement();
  try {
    return stmt.executeUpdate(sql);
  } finally {
    stmt.close();
  }
}

try {
  var columnExists = scalar(
    "SELECT COUNT(*) FROM ALL_TAB_COLUMNS " +
    "WHERE OWNER='SYSDBA' AND TABLE_NAME='X1_FUSION_POLICY' AND COLUMN_NAME='FRONT_VISIBLE'"
  );
  if (columnExists === 0) {
    exec("ALTER TABLE SYSDBA.X1_FUSION_POLICY ADD FRONT_VISIBLE CHAR(1) DEFAULT '1'");
    print("column_added=1");
  } else {
    print("column_added=0");
  }

  var beforeVisibleActive = scalar(
    "SELECT COUNT(*) FROM SYSDBA.X1_FUSION_POLICY " +
    "WHERE TRIM(NVL(DEL_FLAG,'0'))='0' AND TRIM(NVL(FRONT_VISIBLE,'1')) <> '0'"
  );
  var beforePendingVisible = scalar(
    "SELECT COUNT(*) FROM SYSDBA.X1_FUSION_POLICY " +
    "WHERE TRIM(NVL(DEL_FLAG,'0'))='0' " +
    "AND TRIM(NVL(MARK_REVIEW_STATUS,'0'))='0' " +
    "AND TRIM(NVL(FRONT_VISIBLE,'1')) <> '0'"
  );

  var normalized = exec(
    "UPDATE SYSDBA.X1_FUSION_POLICY SET FRONT_VISIBLE='1' " +
    "WHERE FRONT_VISIBLE IS NULL OR TRIM(FRONT_VISIBLE)=''"
  );
  var hidden = exec(
    "UPDATE SYSDBA.X1_FUSION_POLICY SET FRONT_VISIBLE='0', UPDATED_AT=CURRENT_TIMESTAMP " +
    "WHERE TRIM(NVL(DEL_FLAG,'0'))='0' " +
    "AND TRIM(NVL(MARK_REVIEW_STATUS,'0'))='0' " +
    "AND TRIM(NVL(FRONT_VISIBLE,'1')) <> '0'"
  );

  conn.commit();

  var afterVisibleActive = scalar(
    "SELECT COUNT(*) FROM SYSDBA.X1_FUSION_POLICY " +
    "WHERE TRIM(NVL(DEL_FLAG,'0'))='0' AND TRIM(NVL(FRONT_VISIBLE,'1')) <> '0'"
  );
  var afterHiddenPending = scalar(
    "SELECT COUNT(*) FROM SYSDBA.X1_FUSION_POLICY " +
    "WHERE TRIM(NVL(DEL_FLAG,'0'))='0' " +
    "AND TRIM(NVL(MARK_REVIEW_STATUS,'0'))='0' " +
    "AND TRIM(NVL(FRONT_VISIBLE,'1'))='0'"
  );
  var afterPendingVisible = scalar(
    "SELECT COUNT(*) FROM SYSDBA.X1_FUSION_POLICY " +
    "WHERE TRIM(NVL(DEL_FLAG,'0'))='0' " +
    "AND TRIM(NVL(MARK_REVIEW_STATUS,'0'))='0' " +
    "AND TRIM(NVL(FRONT_VISIBLE,'1')) <> '0'"
  );

  print("before_visible_active=" + beforeVisibleActive);
  print("before_pending_visible=" + beforePendingVisible);
  print("normalized_blank_visible=" + normalized);
  print("hidden_pending_now=" + hidden);
  print("after_visible_active=" + afterVisibleActive);
  print("after_hidden_pending=" + afterHiddenPending);
  print("after_pending_visible=" + afterPendingVisible);
} catch (e) {
  conn.rollback();
  throw e;
} finally {
  conn.close();
}
