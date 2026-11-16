var Files = Java.type("java.nio.file.Files");
var Paths = Java.type("java.nio.file.Paths");
var StandardCharsets = Java.type("java.nio.charset.StandardCharsets");
var DriverManager = Java.type("java.sql.DriverManager");
var Class = Java.type("java.lang.Class");
var Properties = Java.type("java.util.Properties");
var Types = Java.type("java.sql.Types");
var Date = Java.type("java.sql.Date");
var System = Java.type("java.lang.System");

var DB_URL = Java.type("java.lang.System").getenv().getOrDefault("KEJIJU_JDBC_DB_URL", "jdbc:dm://127.0.0.1:5236");
var DB_USER = "SYSDBA";
var DB_PASSWORD = Java.type("java.lang.System").getenv("KEJIJU_DB_PASSWORD");
var OPERATOR = "Codex批量导入";
var REVIEW_PENDING = "\u672a\u901a\u8fc7";
var FUSION_PENDING = "0";

function readJson(path) {
  var text = new java.lang.String(Files.readAllBytes(Paths.get(path)), StandardCharsets.UTF_8);
  return Java.asJSONCompatible(JSON.parse(String(text)));
}

function trim(value) {
  return value == null ? "" : String(value).replace(/^\s+|\s+$/g, "");
}

function fit(value, max) {
  var text = trim(value);
  return text.length > max ? text.substring(0, max) : text;
}

function fileNameOf(filePath) {
  var text = trim(filePath);
  if (!text) return "";
  var slash = Math.max(text.lastIndexOf("/"), text.lastIndexOf("\\"));
  return slash >= 0 ? text.substring(slash + 1) : text;
}

function suffixOf(fileName) {
  var dot = trim(fileName).lastIndexOf(".");
  return dot >= 0 ? fileName.substring(dot + 1) : "";
}

function parseSqlDate(value) {
  var text = trim(value);
  if (text.length >= 10) text = text.substring(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return null;
  return Date.valueOf(text);
}

function bindString(stmt, index, value) {
  stmt.setString(index, trim(value));
}

function queryOneString(conn, sql, args) {
  var ps = conn.prepareStatement(sql);
  try {
    for (var i = 0; i < args.length; i++) ps.setString(i + 1, String(args[i]));
    var rs = ps.executeQuery();
    try {
      if (rs.next()) return trim(rs.getString(1));
      return "";
    } finally {
      rs.close();
    }
  } finally {
    ps.close();
  }
}

function nextLong(conn, table) {
  return Number(queryOneString(conn, "SELECT NVL(MAX(ID),0)+1 FROM SYSDBA." + table, []));
}

function policyDetails(conn, policyId) {
  var ps = conn.prepareStatement(
    "SELECT p.POLICY_TITLE, p.FILE_NAME, " +
    "(SELECT o.PUBLISH_DATE FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=CAST(p.ID AS VARCHAR(50)) AND ROWNUM=1) AS PUBLISH_DATE, " +
    "(SELECT o.ISSUE_DEPARTMENT FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=CAST(p.ID AS VARCHAR(50)) AND ROWNUM=1) AS ISSUE_DEPARTMENT, " +
    "(SELECT o.POLICY_CODE FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=CAST(p.ID AS VARCHAR(50)) AND ROWNUM=1) AS POLICY_CODE, " +
    "(SELECT o.FILE_PATH FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=CAST(p.ID AS VARCHAR(50)) AND ROWNUM=1) AS FILE_PATH " +
    "FROM SYSDBA.POLICY p WHERE p.ID=?"
  );
  try {
    ps.setString(1, String(policyId));
    var rs = ps.executeQuery();
    try {
      if (!rs.next()) return null;
      return {
        policyTitle: trim(rs.getString("POLICY_TITLE")),
        fileName: trim(rs.getString("FILE_NAME")),
        publishDate: trim(rs.getString("PUBLISH_DATE")),
        issuingDepartment: trim(rs.getString("ISSUE_DEPARTMENT")),
        policyCode: trim(rs.getString("POLICY_CODE")),
        filePath: trim(rs.getString("FILE_PATH"))
      };
    } finally {
      rs.close();
    }
  } finally {
    ps.close();
  }
}

function findFusionPolicy(conn, title) {
  var ps = conn.prepareStatement(
    "SELECT ID FROM SYSDBA.X1_FUSION_POLICY " +
    "WHERE NVL(DEL_FLAG,'0')='0' AND NVL(STATUS,'ACTIVE')='ACTIVE' " +
    "AND (TRIM(DIRECTORY_TITLE)=? OR TRIM(MAIN_FILE_TITLE)=?) " +
    "ORDER BY ID DESC"
  );
  try {
    ps.setString(1, title);
    ps.setString(2, title);
    var rs = ps.executeQuery();
    try {
      return rs.next() ? trim(rs.getString(1)) : "";
    } finally {
      rs.close();
    }
  } finally {
    ps.close();
  }
}

function createFusionPolicy(conn, title) {
  var id = nextLong(conn, "X1_FUSION_POLICY");
  var sortNo = Number(queryOneString(conn, "SELECT NVL(MAX(SORT_NO),0)+1 FROM SYSDBA.X1_FUSION_POLICY", []));
  var directoryPath = "重点政策融合数据/" + title;
  var stmt = conn.createStatement();
  try {
    stmt.execute("SET IDENTITY_INSERT SYSDBA.X1_FUSION_POLICY ON");
  } finally {
    stmt.close();
  }
  var ps = conn.prepareStatement(
    "INSERT INTO SYSDBA.X1_FUSION_POLICY " +
    "(ID,SOURCE_POLICY_ID,DIRECTORY_TITLE,DIRECTORY_PATH,MAIN_FILE_TITLE,MAIN_FILE_PATH,SORT_NO,STATUS,MARK_REVIEW_STATUS,MARK_REVIEWED_AT,MARK_REVIEWED_BY,MARK_REVIEW_REMARK,DEL_FLAG,CREATED_BY,UPDATED_BY,CREATED_AT,UPDATED_AT) " +
    "VALUES (?,NULL,?,?,?,?,?,'ACTIVE',?,NULL,NULL,NULL,'0',?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
  );
  try {
    ps.setLong(1, id);
    ps.setString(2, fit(title, 500));
    ps.setString(3, fit(directoryPath, 1000));
    ps.setString(4, fit(title, 500));
    ps.setString(5, fit(directoryPath, 1000));
    ps.setInt(6, sortNo);
    ps.setString(7, FUSION_PENDING);
    ps.setString(8, OPERATOR);
    ps.setString(9, OPERATOR);
    ps.executeUpdate();
  } finally {
    ps.close();
    var off = conn.createStatement();
    try { off.execute("SET IDENTITY_INSERT SYSDBA.X1_FUSION_POLICY OFF"); } finally { off.close(); }
  }
  return String(id);
}

function existingDocumentId(conn, fusionId, policyId) {
  return queryOneString(conn,
    "SELECT ID FROM SYSDBA.X1_POLICY_DOCUMENT WHERE FUSION_POLICY_ID=? AND SOURCE_POLICY_ID=? AND NVL(DEL_FLAG,'0')='0' ORDER BY ID",
    [fusionId, policyId]
  );
}

function nextDisplayOrder(conn, fusionId) {
  return Number(queryOneString(conn, "SELECT NVL(MAX(DISPLAY_ORDER),0)+1 FROM SYSDBA.X1_POLICY_DOCUMENT WHERE FUSION_POLICY_ID=?", [fusionId]));
}

function insertDocument(conn, fusionId, policyId, type, detail) {
  var documentId = nextLong(conn, "X1_POLICY_DOCUMENT");
  var displayOrder = nextDisplayOrder(conn, fusionId);
  var filePath = detail.filePath;
  var fileName = fileNameOf(filePath);
  if (!fileName) fileName = detail.fileName || (detail.policyTitle + ".md");
  var suffix = suffixOf(fileName);

  var stmt = conn.createStatement();
  try {
    stmt.execute("SET IDENTITY_INSERT SYSDBA.X1_POLICY_DOCUMENT ON");
  } finally {
    stmt.close();
  }
  var ps = conn.prepareStatement(
    "INSERT INTO SYSDBA.X1_POLICY_DOCUMENT " +
    "(ID,FUSION_POLICY_ID,SOURCE_POLICY_ID,POLICY_ATTRIBUTE,POLICY_TITLE,FILE_NAME,FILE_PATH,FILE_SUFFIX,DISPLAY_ORDER,DEL_FLAG,CREATED_BY,UPDATED_BY,CREATED_AT,UPDATED_AT) " +
    "VALUES (?,?,?,?,?,?,?,?,?,'0',?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
  );
  try {
    ps.setLong(1, documentId);
    ps.setString(2, fusionId);
    ps.setString(3, policyId);
    ps.setString(4, type);
    ps.setString(5, fit(detail.policyTitle, 1000));
    ps.setString(6, fit(fileName, 500));
    ps.setString(7, fit(filePath, 2000));
    ps.setString(8, fit(suffix, 50));
    ps.setInt(9, displayOrder);
    ps.setString(10, OPERATOR);
    ps.setString(11, OPERATOR);
    ps.executeUpdate();
  } finally {
    ps.close();
    var off = conn.createStatement();
    try { off.execute("SET IDENTITY_INSERT SYSDBA.X1_POLICY_DOCUMENT OFF"); } finally { off.close(); }
  }

  var baseId = nextLong(conn, "X1_POLICY_BASE_INFO");
  var on = conn.createStatement();
  try {
    on.execute("SET IDENTITY_INSERT SYSDBA.X1_POLICY_BASE_INFO ON");
  } finally {
    on.close();
  }
  var base = conn.prepareStatement(
    "INSERT INTO SYSDBA.X1_POLICY_BASE_INFO " +
    "(ID,POLICY_DOCUMENT_ID,PUBLISH_DATE,POLICY_DOCUMENT_NO,ISSUING_DEPARTMENT,POLICY_LEVEL,ORIGINAL_URL,CREATED_BY,UPDATED_BY,CREATED_AT,UPDATED_AT) " +
    "VALUES (?,?,?,?,?,NULL,NULL,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
  );
  try {
    base.setLong(1, baseId);
    base.setLong(2, documentId);
    var parsedDate = parseSqlDate(detail.publishDate);
    if (parsedDate == null) base.setNull(3, Types.DATE); else base.setDate(3, parsedDate);
    base.setString(4, fit(detail.policyCode, 200));
    base.setString(5, fit(detail.issuingDepartment, 500));
    base.setString(6, OPERATOR);
    base.setString(7, OPERATOR);
    base.executeUpdate();
  } finally {
    base.close();
    var baseOff = conn.createStatement();
    try { baseOff.execute("SET IDENTITY_INSERT SYSDBA.X1_POLICY_BASE_INFO OFF"); } finally { baseOff.close(); }
  }

  return String(documentId);
}

function updateDocumentType(conn, documentId, type) {
  var ps = conn.prepareStatement(
    "UPDATE SYSDBA.X1_POLICY_DOCUMENT SET POLICY_ATTRIBUTE=?, UPDATED_BY=?, UPDATED_AT=CURRENT_TIMESTAMP WHERE ID=?"
  );
  try {
    ps.setString(1, type);
    ps.setString(2, OPERATOR);
    ps.setString(3, documentId);
    ps.executeUpdate();
  } finally {
    ps.close();
  }
}

function upsertMetadata(conn, policyId, type) {
  var upd = conn.prepareStatement(
    "UPDATE SYSDBA.POLICY_METADATA SET POLICY_ATTRIBUTE=?, REVIEW_STATUS=?, UPDATED_BY=?, UPDATED_AT=CURRENT_TIMESTAMP WHERE POLICY_ID=?"
  );
  try {
    upd.setString(1, type);
    upd.setString(2, REVIEW_PENDING);
    upd.setString(3, OPERATOR);
    upd.setString(4, policyId);
    var count = upd.executeUpdate();
    if (count > 0) return "updated";
  } finally {
    upd.close();
  }
  var ins = conn.prepareStatement(
    "INSERT INTO SYSDBA.POLICY_METADATA (POLICY_ID,POLICY_ATTRIBUTE,REVIEW_STATUS,CREATED_AT,UPDATED_AT,CREATED_BY,UPDATED_BY) " +
    "VALUES (?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,?,?)"
  );
  try {
    ins.setString(1, policyId);
    ins.setString(2, type);
    ins.setString(3, REVIEW_PENDING);
    ins.setString(4, OPERATOR);
    ins.setString(5, OPERATOR);
    ins.executeUpdate();
    return "inserted";
  } finally {
    ins.close();
  }
}

function resetFusionReview(conn, fusionId) {
  var ps = conn.prepareStatement(
    "UPDATE SYSDBA.X1_FUSION_POLICY SET MARK_REVIEW_STATUS=?, MARK_REVIEWED_AT=NULL, MARK_REVIEWED_BY=NULL, MARK_REVIEW_REMARK=NULL, UPDATED_BY=?, UPDATED_AT=CURRENT_TIMESTAMP WHERE ID=?"
  );
  try {
    ps.setString(1, FUSION_PENDING);
    ps.setString(2, OPERATOR);
    ps.setString(3, fusionId);
    ps.executeUpdate();
  } finally {
    ps.close();
  }
}

var plan = readJson("tmp/policy_import_allocation_by_main_title.json");
var rows = plan.allocRows;
if (!rows || rows.length !== 1850) {
  throw new Error("Expected 1850 allocated rows, got " + (rows ? rows.length : 0));
}

Class.forName("dm.jdbc.driver.DmDriver");
var props = new Properties();
props.setProperty("user", DB_USER);
props.setProperty("password", DB_PASSWORD);
var conn = DriverManager.getConnection(DB_URL, props);

var stats = {
  plannedRows: rows.length,
  fusionCreated: 0,
  fusionReused: 0,
  documentsInserted: 0,
  documentsExisting: 0,
  documentTypeUpdated: 0,
  metadataInserted: 0,
  metadataUpdated: 0,
  missingPolicy: 0,
  errors: 0
};
var fusionByTitle = {};
var reportRows = [];

try {
  conn.setAutoCommit(false);
  for (var i = 0; i < rows.length; i++) {
    var row = rows[i];
    var mainTitle = trim(row.mainTitle);
    var policyId = trim(row.assignedPolicyId);
    var type = trim(row.type);
    if (!mainTitle || !policyId || !type) {
      stats.errors++;
      reportRows.push({ status: "invalid", lineNo: row.lineNo, mainTitle: mainTitle, policyId: policyId, type: type });
      continue;
    }

    var fusionId = fusionByTitle[mainTitle];
    if (!fusionId) {
      fusionId = findFusionPolicy(conn, mainTitle);
      if (fusionId) stats.fusionReused++;
      else {
        fusionId = createFusionPolicy(conn, mainTitle);
        stats.fusionCreated++;
      }
      fusionByTitle[mainTitle] = fusionId;
    }

    var detail = policyDetails(conn, policyId);
    if (detail == null) {
      stats.missingPolicy++;
      reportRows.push({ status: "missing_policy", lineNo: row.lineNo, mainTitle: mainTitle, policyId: policyId });
      continue;
    }

    var docId = existingDocumentId(conn, fusionId, policyId);
    var status;
    if (docId) {
      stats.documentsExisting++;
      updateDocumentType(conn, docId, type);
      stats.documentTypeUpdated++;
      status = "existing_updated";
    } else {
      docId = insertDocument(conn, fusionId, policyId, type, detail);
      stats.documentsInserted++;
      status = "inserted";
    }

    var metaStatus = upsertMetadata(conn, policyId, type);
    if (metaStatus === "inserted") stats.metadataInserted++;
    else stats.metadataUpdated++;
    resetFusionReview(conn, fusionId);

    reportRows.push({
      status: status,
      fusionId: fusionId,
      documentId: docId,
      policyId: policyId,
      lineNo: row.lineNo,
      mainNo: row.mainNo,
      mainTitle: mainTitle,
      type: type,
      mdTitle: row.title,
      dbTitle: detail.policyTitle,
      publishDate: detail.publishDate,
      filePath: detail.filePath
    });

    if ((i + 1) % 100 === 0) {
      print("processed " + (i + 1) + " / " + rows.length);
      conn.commit();
    }
  }
  conn.commit();
} catch (e) {
  try { conn.rollback(); } catch (ignored) {}
  throw e;
} finally {
  conn.close();
}

var output = {
  importedAt: String(new java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss").format(new java.util.Date())),
  stats: stats,
  fusionPolicyCount: Object.keys(fusionByTitle).length,
  rows: reportRows
};
Files.write(Paths.get("tmp", "policy_import_1850_result.json"), String(JSON.stringify(output, null, 2)).getBytes(StandardCharsets.UTF_8));
print(JSON.stringify(stats, null, 2));
