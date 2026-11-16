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

var result = readJson("tmp/huiqiyun_import_result.json");
var ids = [];
var seen = {};
for (var i = 0; i < result.rows.length; i++) {
  var id = trim(result.rows[i].policyId);
  if (id && !seen[id]) { seen[id] = true; ids.push(id); }
}

Class.forName("dm.jdbc.driver.DmDriver");
var props = new Properties();
props.setProperty("user", "SYSDBA");
props.setProperty("password", Java.type("java.lang.System").getenv("KEJIJU_DB_PASSWORD"));
var conn = DriverManager.getConnection(Java.type("java.lang.System").getenv().getOrDefault("KEJIJU_JDBC_DB_URL", "jdbc:dm://127.0.0.1:5236"), props);

function repeat(n) { var arr=[]; for (var i=0;i<n;i++) arr.push("?"); return arr.join(","); }
var found = {};
var byUpdater = {};
try {
  for (var start = 0; start < ids.length; start += 500) {
    var chunk = ids.slice(start, start + 500);
    var ps = conn.prepareStatement("SELECT POLICY_ID,POLICY_ATTRIBUTE,UPDATED_BY FROM SYSDBA.POLICY_METADATA WHERE POLICY_ID IN (" + repeat(chunk.length) + ")");
    for (var j = 0; j < chunk.length; j++) ps.setString(j + 1, chunk[j]);
    var rs = ps.executeQuery();
    while (rs.next()) {
      var pid = trim(rs.getString("POLICY_ID"));
      found[pid] = { type: trim(rs.getString("POLICY_ATTRIBUTE")), updatedBy: trim(rs.getString("UPDATED_BY")) };
      var upd = trim(rs.getString("UPDATED_BY"));
      byUpdater[upd] = (byUpdater[upd] || 0) + 1;
    }
    rs.close();
    ps.close();
  }
  var missing = [];
  var notUpdated = [];
  for (var k = 0; k < ids.length; k++) {
    var pid = ids[k];
    if (!found[pid]) missing.push(pid);
    else if (found[pid].updatedBy !== "Codex-Huiqiyun") notUpdated.push({ policyId: pid, updatedBy: found[pid].updatedBy, type: found[pid].type });
  }
  print("planned_unique_ids=" + ids.length);
  print("metadata_found_unique_ids=" + Object.keys(found).length);
  print("missing=" + JSON.stringify(missing));
  print("not_updated_by_huiqiyun=" + JSON.stringify(notUpdated));
  print("updated_by_counts=" + JSON.stringify(byUpdater));
} finally {
  conn.close();
}
