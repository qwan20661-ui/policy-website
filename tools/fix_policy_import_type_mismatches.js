var Files = Java.type("java.nio.file.Files");
var Paths = Java.type("java.nio.file.Paths");
var StandardCharsets = Java.type("java.nio.charset.StandardCharsets");
var DriverManager = Java.type("java.sql.DriverManager");
var Class = Java.type("java.lang.Class");
var Properties = Java.type("java.util.Properties");

function readJson(path) {
  var text = new java.lang.String(Files.readAllBytes(Paths.get(path)), StandardCharsets.UTF_8);
  return Java.asJSONCompatible(JSON.parse(String(text)));
}
function trim(value) { return value == null ? "" : String(value).replace(/^\s+|\s+$/g, ""); }
var OPERATOR = "Codex批量导入";
var REVIEW_PENDING = "\u672a\u901a\u8fc7";

function findFusion(conn, title) {
  var ps = conn.prepareStatement("SELECT ID FROM SYSDBA.X1_FUSION_POLICY WHERE NVL(DEL_FLAG,'0')='0' AND NVL(STATUS,'ACTIVE')='ACTIVE' AND (TRIM(DIRECTORY_TITLE)=? OR TRIM(MAIN_FILE_TITLE)=?) ORDER BY ID DESC");
  try {
    ps.setString(1, title);
    ps.setString(2, title);
    var rs = ps.executeQuery();
    try { return rs.next() ? trim(rs.getString(1)) : ""; } finally { rs.close(); }
  } finally { ps.close(); }
}

var plan = readJson("tmp/policy_import_allocation_by_main_title.json");
var rows = plan.allocRows;
Class.forName("dm.jdbc.driver.DmDriver");
var props = new Properties();
props.setProperty("user", "SYSDBA");
props.setProperty("password", Java.type("java.lang.System").getenv("KEJIJU_DB_PASSWORD"));
var conn = DriverManager.getConnection(Java.type("java.lang.System").getenv().getOrDefault("KEJIJU_JDBC_DB_URL", "jdbc:dm://127.0.0.1:5236"), props);

var keyInfo = {};
var fusionCache = {};
for (var i = 0; i < rows.length; i++) {
  var mainTitle = trim(rows[i].mainTitle);
  var fusionId = fusionCache[mainTitle];
  if (fusionId === undefined) {
    fusionId = findFusion(conn, mainTitle);
    fusionCache[mainTitle] = fusionId;
  }
  var policyId = trim(rows[i].assignedPolicyId);
  var key = fusionId + "|" + policyId;
  if (!keyInfo[key]) keyInfo[key] = { fusionId: fusionId, policyId: policyId, types: {}, rows: [] };
  keyInfo[key].types[trim(rows[i].type)] = true;
  keyInfo[key].rows.push(rows[i]);
}

var stats = { unambiguousKeys: 0, conflictKeys: 0, documentRowsUpdated: 0, metadataUpdated: 0 };
var conflicts = [];
try {
  conn.setAutoCommit(false);
  var updDoc = conn.prepareStatement("UPDATE SYSDBA.X1_POLICY_DOCUMENT SET POLICY_ATTRIBUTE=?,UPDATED_BY=?,UPDATED_AT=CURRENT_TIMESTAMP WHERE NVL(DEL_FLAG,'0')='0' AND FUSION_POLICY_ID=? AND SOURCE_POLICY_ID=?");
  var updMeta = conn.prepareStatement("UPDATE SYSDBA.POLICY_METADATA SET POLICY_ATTRIBUTE=?,REVIEW_STATUS=?,UPDATED_BY=?,UPDATED_AT=CURRENT_TIMESTAMP WHERE POLICY_ID=?");
  try {
    var keys = Object.keys(keyInfo);
    for (var k = 0; k < keys.length; k++) {
      var info = keyInfo[keys[k]];
      var types = Object.keys(info.types);
      if (types.length !== 1) {
        stats.conflictKeys++;
        conflicts.push({ fusionId: info.fusionId, policyId: info.policyId, types: types, rows: info.rows.map(function(r){ return { lineNo: r.lineNo, mainTitle: r.mainTitle, title: r.title, type: r.type }; }) });
        continue;
      }
      stats.unambiguousKeys++;
      var type = types[0];
      updDoc.setString(1, type);
      updDoc.setString(2, OPERATOR);
      updDoc.setString(3, info.fusionId);
      updDoc.setString(4, info.policyId);
      stats.documentRowsUpdated += updDoc.executeUpdate();
      updMeta.setString(1, type);
      updMeta.setString(2, REVIEW_PENDING);
      updMeta.setString(3, OPERATOR);
      updMeta.setString(4, info.policyId);
      stats.metadataUpdated += updMeta.executeUpdate();
    }
  } finally {
    updDoc.close();
    updMeta.close();
  }
  conn.commit();
} catch (e) {
  try { conn.rollback(); } catch (ignored) {}
  throw e;
} finally {
  conn.close();
}
Files.write(Paths.get("tmp", "policy_import_type_conflicts.json"), String(JSON.stringify({ stats: stats, conflicts: conflicts }, null, 2)).getBytes(StandardCharsets.UTF_8));
print(JSON.stringify(stats, null, 2));
