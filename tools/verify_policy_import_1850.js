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

function findFusion(conn, title) {
  var ps = conn.prepareStatement("SELECT ID,MARK_REVIEW_STATUS,NVL(STRUCTURE_REVIEW_STATUS,'0') AS STRUCTURE_REVIEW_STATUS FROM SYSDBA.X1_FUSION_POLICY WHERE NVL(DEL_FLAG,'0')='0' AND NVL(STATUS,'ACTIVE')='ACTIVE' AND (TRIM(DIRECTORY_TITLE)=? OR TRIM(MAIN_FILE_TITLE)=?) ORDER BY ID DESC");
  try {
    ps.setString(1, title);
    ps.setString(2, title);
    var rs = ps.executeQuery();
    try {
      if (!rs.next()) return null;
      return { id: trim(rs.getString("ID")), review: trim(rs.getString("MARK_REVIEW_STATUS")), structureReview: trim(rs.getString("STRUCTURE_REVIEW_STATUS")) };
    } finally { rs.close(); }
  } finally { ps.close(); }
}

function findDocument(conn, fusionId, policyId) {
  var ps = conn.prepareStatement("SELECT ID,POLICY_ATTRIBUTE FROM SYSDBA.X1_POLICY_DOCUMENT WHERE NVL(DEL_FLAG,'0')='0' AND FUSION_POLICY_ID=? AND SOURCE_POLICY_ID=? ORDER BY ID DESC");
  try {
    ps.setString(1, fusionId);
    ps.setString(2, policyId);
    var rs = ps.executeQuery();
    try {
      if (!rs.next()) return null;
      return { id: trim(rs.getString("ID")), type: trim(rs.getString("POLICY_ATTRIBUTE")) };
    } finally { rs.close(); }
  } finally { ps.close(); }
}

function metadata(conn, policyId) {
  var ps = conn.prepareStatement("SELECT POLICY_ATTRIBUTE,REVIEW_STATUS FROM SYSDBA.POLICY_METADATA WHERE POLICY_ID=? AND ROWNUM=1");
  try {
    ps.setString(1, policyId);
    var rs = ps.executeQuery();
    try {
      if (!rs.next()) return null;
      return { type: trim(rs.getString("POLICY_ATTRIBUTE")), review: trim(rs.getString("REVIEW_STATUS")) };
    } finally { rs.close(); }
  } finally { ps.close(); }
}

var plan = readJson("tmp/policy_import_allocation_by_main_title.json");
var rows = plan.allocRows;
Class.forName("dm.jdbc.driver.DmDriver");
var props = new Properties();
props.setProperty("user", "SYSDBA");
props.setProperty("password", Java.type("java.lang.System").getenv("KEJIJU_DB_PASSWORD"));
var conn = DriverManager.getConnection(Java.type("java.lang.System").getenv().getOrDefault("KEJIJU_JDBC_DB_URL", "jdbc:dm://127.0.0.1:5236"), props);

var stats = {
  plannedRows: rows.length,
  foundFusion: 0,
  missingFusion: 0,
  foundDocument: 0,
  missingDocument: 0,
  documentTypeMismatch: 0,
  metadataFound: 0,
  metadataMissing: 0,
  metadataTypeMismatch: 0
};
var bad = [];
var fusionCache = {};
var docSeen = {};

try {
  for (var i = 0; i < rows.length; i++) {
    var row = rows[i];
    var mainTitle = trim(row.mainTitle);
    var policyId = trim(row.assignedPolicyId);
    var type = trim(row.type);
    var fusion = fusionCache[mainTitle];
    if (fusion === undefined) {
      fusion = findFusion(conn, mainTitle);
      fusionCache[mainTitle] = fusion;
    }
    if (!fusion) {
      stats.missingFusion++;
      bad.push({ reason: "missing_fusion", lineNo: row.lineNo, mainTitle: mainTitle, policyId: policyId });
      continue;
    }
    stats.foundFusion++;
    var doc = findDocument(conn, fusion.id, policyId);
    if (!doc) {
      stats.missingDocument++;
      bad.push({ reason: "missing_document", fusionId: fusion.id, lineNo: row.lineNo, mainTitle: mainTitle, policyId: policyId });
      continue;
    }
    stats.foundDocument++;
    docSeen[fusion.id + "|" + policyId] = true;
    if (doc.type !== type) {
      stats.documentTypeMismatch++;
      bad.push({ reason: "document_type_mismatch", docId: doc.id, policyId: policyId, expected: type, actual: doc.type, lineNo: row.lineNo });
    }
    var meta = metadata(conn, policyId);
    if (!meta) {
      stats.metadataMissing++;
      bad.push({ reason: "metadata_missing", policyId: policyId, lineNo: row.lineNo });
    } else {
      stats.metadataFound++;
      if (meta.type !== type) {
        stats.metadataTypeMismatch++;
        bad.push({ reason: "metadata_type_mismatch", policyId: policyId, expected: type, actual: meta.type, lineNo: row.lineNo });
      }
    }
  }
} finally {
  conn.close();
}

stats.uniqueFusionPolicyCount = Object.keys(fusionCache).length;
stats.uniqueDocumentKeys = Object.keys(docSeen).length;
Files.write(Paths.get("tmp", "policy_import_1850_verify.json"), String(JSON.stringify({ stats: stats, bad: bad }, null, 2)).getBytes(StandardCharsets.UTF_8));
print(JSON.stringify(stats, null, 2));
