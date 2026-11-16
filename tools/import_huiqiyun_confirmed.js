var Files = Java.type("java.nio.file.Files");
var Paths = Java.type("java.nio.file.Paths");
var StandardCharsets = Java.type("java.nio.charset.StandardCharsets");
var DriverManager = Java.type("java.sql.DriverManager");
var Class = Java.type("java.lang.Class");
var Properties = Java.type("java.util.Properties");
var Types = Java.type("java.sql.Types");
var Date = Java.type("java.sql.Date");

var DB_URL = Java.type("java.lang.System").getenv().getOrDefault("KEJIJU_JDBC_DB_URL", "jdbc:dm://127.0.0.1:5236");
var DB_USER = "SYSDBA";
var DB_PASSWORD = Java.type("java.lang.System").getenv("KEJIJU_DB_PASSWORD");
var OPERATOR = "Codex-Huiqiyun";
var FUSION_PENDING = "0";
var REVIEW_PENDING = "\u672a\u901a\u8fc7";

function readJson(path) {
  var text = new java.lang.String(Files.readAllBytes(Paths.get(path)), StandardCharsets.UTF_8);
  return Java.asJSONCompatible(JSON.parse(String(text)));
}

function writeJson(path, obj) {
  Files.write(Paths.get(path), JSON.stringify(obj, null, 2).getBytes(StandardCharsets.UTF_8));
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
  return /^\d{4}-\d{2}-\d{2}$/.test(text) ? Date.valueOf(text) : null;
}

function q1(conn, sql) {
  var rs = conn.createStatement().executeQuery(sql);
  try {
    rs.next();
    return Number(rs.getString(1));
  } finally {
    rs.close();
  }
}

function ensureFrontVisibleColumn(conn) {
  var exists = q1(conn, "SELECT COUNT(*) FROM ALL_TAB_COLUMNS WHERE OWNER='SYSDBA' AND TABLE_NAME='X1_FUSION_POLICY' AND COLUMN_NAME='FRONT_VISIBLE'");
  if (exists === 0) {
    var st = conn.createStatement();
    try {
      st.executeUpdate("ALTER TABLE SYSDBA.X1_FUSION_POLICY ADD FRONT_VISIBLE CHAR(1) DEFAULT '1'");
    } finally {
      st.close();
    }
  }
  var st2 = conn.createStatement();
  try {
    st2.executeUpdate("UPDATE SYSDBA.X1_FUSION_POLICY SET FRONT_VISIBLE='1' WHERE FRONT_VISIBLE IS NULL OR TRIM(FRONT_VISIBLE)=''");
  } finally {
    st2.close();
  }
}

function collectNeeded(rows) {
  var titleSeen = {};
  var titles = [];
  var policySeen = {};
  var policyIds = [];
  for (var i = 0; i < rows.length; i++) {
    var title = trim(rows[i].mainTitle);
    if (title && !titleSeen[title]) {
      titles.push(title);
      titleSeen[title] = true;
    }
    var pid = trim(rows[i].policyId);
    if (pid && !policySeen[pid]) {
      policyIds.push(pid);
      policySeen[pid] = true;
    }
  }
  return { titles: titles, policyIds: policyIds };
}

function loadFusionMap(conn) {
  var map = {};
  var hidden = {};
  var rs = conn.createStatement().executeQuery(
    "SELECT ID,DIRECTORY_TITLE,MAIN_FILE_TITLE,NVL(FRONT_VISIBLE,'1') AS FRONT_VISIBLE " +
    "FROM SYSDBA.X1_FUSION_POLICY WHERE NVL(DEL_FLAG,'0')='0' AND NVL(STATUS,'ACTIVE')='ACTIVE' ORDER BY ID DESC"
  );
  try {
    while (rs.next()) {
      var id = trim(rs.getString("ID"));
      var visible = trim(rs.getString("FRONT_VISIBLE"));
      var titles = [trim(rs.getString("DIRECTORY_TITLE")), trim(rs.getString("MAIN_FILE_TITLE"))];
      for (var i = 0; i < titles.length; i++) {
        if (titles[i] && !map[titles[i]]) {
          map[titles[i]] = id;
          hidden[titles[i]] = visible === "0";
        }
      }
    }
  } finally {
    rs.close();
  }
  return { map: map, hidden: hidden };
}

function loadPolicyDetails(conn, ids) {
  var details = {};
  if (!ids.length) return details;
  for (var start = 0; start < ids.length; start += 500) {
    var chunk = ids.slice(start, start + 500);
    var placeholders = [];
    for (var p = 0; p < chunk.length; p++) placeholders.push("?");
    var sql = "SELECT p.ID,p.POLICY_TITLE,p.FILE_NAME," +
      "(SELECT o.PUBLISH_DATE FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=CAST(p.ID AS VARCHAR(50)) AND ROWNUM=1) AS PUBLISH_DATE," +
      "(SELECT o.ISSUE_DEPARTMENT FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=CAST(p.ID AS VARCHAR(50)) AND ROWNUM=1) AS ISSUE_DEPARTMENT," +
      "(SELECT o.POLICY_CODE FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=CAST(p.ID AS VARCHAR(50)) AND ROWNUM=1) AS POLICY_CODE," +
      "(SELECT o.FILE_PATH FROM SYSDBA.POLICY_ORIGINAL_V2 o WHERE o.POLICY_ID=CAST(p.ID AS VARCHAR(50)) AND ROWNUM=1) AS FILE_PATH " +
      "FROM SYSDBA.POLICY p WHERE CAST(p.ID AS VARCHAR(50)) IN (" + placeholders.join(",") + ")";
    var ps = conn.prepareStatement(sql);
    try {
      for (var i = 0; i < chunk.length; i++) ps.setString(i + 1, chunk[i]);
      var rs = ps.executeQuery();
      try {
        while (rs.next()) {
          var id = trim(rs.getString("ID"));
          details[id] = {
            id: id,
            policyTitle: trim(rs.getString("POLICY_TITLE")),
            fileName: trim(rs.getString("FILE_NAME")),
            publishDate: trim(rs.getString("PUBLISH_DATE")),
            issueDepartment: trim(rs.getString("ISSUE_DEPARTMENT")),
            policyCode: trim(rs.getString("POLICY_CODE")),
            filePath: trim(rs.getString("FILE_PATH"))
          };
        }
      } finally {
        rs.close();
      }
    } finally {
      ps.close();
    }
  }
  return details;
}

function loadExistingDocs(conn) {
  var map = {};
  var displayMax = {};
  var rs = conn.createStatement().executeQuery(
    "SELECT ID,FUSION_POLICY_ID,SOURCE_POLICY_ID,NVL(DISPLAY_ORDER,0) AS DISPLAY_ORDER " +
    "FROM SYSDBA.X1_POLICY_DOCUMENT WHERE NVL(DEL_FLAG,'0')='0'"
  );
  try {
    while (rs.next()) {
      var fid = trim(rs.getString("FUSION_POLICY_ID"));
      var pid = trim(rs.getString("SOURCE_POLICY_ID"));
      var id = trim(rs.getString("ID"));
      var display = Number(rs.getString("DISPLAY_ORDER"));
      if (fid && pid && !map[fid + "|" + pid]) map[fid + "|" + pid] = id;
      if (!displayMax[fid] || display > displayMax[fid]) displayMax[fid] = display;
    }
  } finally {
    rs.close();
  }
  return { map: map, displayMax: displayMax };
}

function insertFusionPolicy(ps, id, sortNo, title) {
  var directoryPath = "慧企云申报项目/" + title;
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
}

function bindDoc(ps, documentId, fusionId, policyId, type, detail, displayOrder) {
  var filePath = detail.filePath;
  var fileName = fileNameOf(filePath);
  if (!fileName) fileName = detail.fileName || (detail.policyTitle + ".md");
  ps.setLong(1, documentId);
  ps.setString(2, fusionId);
  ps.setString(3, policyId);
  ps.setString(4, type);
  ps.setString(5, fit(detail.policyTitle, 1000));
  ps.setString(6, fit(fileName, 500));
  ps.setString(7, fit(filePath, 2000));
  ps.setString(8, fit(suffixOf(fileName), 50));
  ps.setInt(9, displayOrder);
  ps.setString(10, OPERATOR);
  ps.setString(11, OPERATOR);
}

function bindBase(ps, baseId, documentId, detail) {
  ps.setLong(1, baseId);
  ps.setLong(2, documentId);
  var d = parseSqlDate(detail.publishDate);
  if (d == null) ps.setNull(3, Types.DATE); else ps.setDate(3, d);
  ps.setString(4, fit(detail.policyCode, 200));
  ps.setString(5, fit(detail.issueDepartment, 500));
  ps.setString(6, OPERATOR);
  ps.setString(7, OPERATOR);
}

var plan = readJson("tmp/huiqiyun_import_precheck.json");
var rows = plan.confirmedPlanRows;
if (!rows || rows.length !== 1373) throw new Error("Expected 1373 confirmed rows, got " + (rows ? rows.length : 0));

Class.forName("dm.jdbc.driver.DmDriver");
var props = new Properties();
props.setProperty("user", DB_USER);
props.setProperty("password", DB_PASSWORD);
var conn = DriverManager.getConnection(DB_URL, props);

var stats = {
  plannedRows: rows.length,
  fusionCreated: 0,
  fusionReused: 0,
  fusionRestoredVisible: 0,
  documentsInserted: 0,
  documentsExisting: 0,
  documentTypeUpdated: 0,
  metadataInserted: 0,
  metadataUpdated: 0,
  missingPolicy: 0,
  errors: 0
};
var reportRows = [];

try {
  conn.setAutoCommit(false);
  ensureFrontVisibleColumn(conn);
  var needed = collectNeeded(rows);
  var fusionState = loadFusionMap(conn);
  var fusionMap = fusionState.map;
  var fusionHidden = fusionState.hidden;
  var details = loadPolicyDetails(conn, needed.policyIds);
  var existing = loadExistingDocs(conn);
  var nextFusionId = q1(conn, "SELECT NVL(MAX(ID),0)+1 FROM SYSDBA.X1_FUSION_POLICY");
  var nextSortNo = q1(conn, "SELECT NVL(MAX(SORT_NO),0)+1 FROM SYSDBA.X1_FUSION_POLICY");
  var nextDocId = q1(conn, "SELECT NVL(MAX(ID),0)+1 FROM SYSDBA.X1_POLICY_DOCUMENT");
  var nextBaseId = q1(conn, "SELECT NVL(MAX(ID),0)+1 FROM SYSDBA.X1_POLICY_BASE_INFO");

  var st = conn.createStatement();
  st.execute("SET IDENTITY_INSERT SYSDBA.X1_FUSION_POLICY ON");
  st.close();
  var insertFusion = conn.prepareStatement(
    "INSERT INTO SYSDBA.X1_FUSION_POLICY (ID,SOURCE_POLICY_ID,DIRECTORY_TITLE,DIRECTORY_PATH,MAIN_FILE_TITLE,MAIN_FILE_PATH,SORT_NO,STATUS,MARK_REVIEW_STATUS,MARK_REVIEWED_AT,MARK_REVIEWED_BY,MARK_REVIEW_REMARK,DEL_FLAG,FRONT_VISIBLE,CREATED_BY,UPDATED_BY,CREATED_AT,UPDATED_AT) " +
    "VALUES (?,NULL,?,?,?,?,?,'ACTIVE',?,NULL,NULL,NULL,'0','1',?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
  );
  var restoreVisible = conn.prepareStatement("UPDATE SYSDBA.X1_FUSION_POLICY SET FRONT_VISIBLE='1',UPDATED_BY=?,UPDATED_AT=CURRENT_TIMESTAMP WHERE ID=? AND TRIM(NVL(FRONT_VISIBLE,'1'))='0'");
  try {
    for (var t = 0; t < needed.titles.length; t++) {
      var title = trim(needed.titles[t]);
      if (!fusionMap[title]) {
        insertFusionPolicy(insertFusion, nextFusionId, nextSortNo, title);
        fusionMap[title] = String(nextFusionId);
        nextFusionId++;
        nextSortNo++;
        stats.fusionCreated++;
      } else {
        stats.fusionReused++;
        if (fusionHidden[title]) {
          restoreVisible.setString(1, OPERATOR);
          restoreVisible.setString(2, fusionMap[title]);
          if (restoreVisible.executeUpdate() > 0) stats.fusionRestoredVisible++;
          fusionHidden[title] = false;
        }
      }
    }
  } finally {
    insertFusion.close();
    restoreVisible.close();
    var sfOff = conn.createStatement();
    sfOff.execute("SET IDENTITY_INSERT SYSDBA.X1_FUSION_POLICY OFF");
    sfOff.close();
  }

  var insertDoc = conn.prepareStatement(
    "INSERT INTO SYSDBA.X1_POLICY_DOCUMENT (ID,FUSION_POLICY_ID,SOURCE_POLICY_ID,POLICY_ATTRIBUTE,POLICY_TITLE,FILE_NAME,FILE_PATH,FILE_SUFFIX,DISPLAY_ORDER,DEL_FLAG,CREATED_BY,UPDATED_BY,CREATED_AT,UPDATED_AT) " +
    "VALUES (?,?,?,?,?,?,?,?,?,'0',?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
  );
  var insertBase = conn.prepareStatement(
    "INSERT INTO SYSDBA.X1_POLICY_BASE_INFO (ID,POLICY_DOCUMENT_ID,PUBLISH_DATE,POLICY_DOCUMENT_NO,ISSUING_DEPARTMENT,POLICY_LEVEL,ORIGINAL_URL,CREATED_BY,UPDATED_BY,CREATED_AT,UPDATED_AT) " +
    "VALUES (?,?,?,?,?,NULL,NULL,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
  );
  var updateDocType = conn.prepareStatement("UPDATE SYSDBA.X1_POLICY_DOCUMENT SET POLICY_ATTRIBUTE=?,UPDATED_BY=?,UPDATED_AT=CURRENT_TIMESTAMP WHERE ID=?");
  var updateMeta = conn.prepareStatement("UPDATE SYSDBA.POLICY_METADATA SET POLICY_ATTRIBUTE=?,REVIEW_STATUS=?,UPDATED_BY=?,UPDATED_AT=CURRENT_TIMESTAMP WHERE POLICY_ID=?");
  var insertMeta = conn.prepareStatement("INSERT INTO SYSDBA.POLICY_METADATA (POLICY_ID,POLICY_ATTRIBUTE,REVIEW_STATUS,CREATED_AT,UPDATED_AT,CREATED_BY,UPDATED_BY) VALUES (?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,?,?)");
  var resetFusion = conn.prepareStatement("UPDATE SYSDBA.X1_FUSION_POLICY SET MARK_REVIEW_STATUS=?,MARK_REVIEWED_AT=NULL,MARK_REVIEWED_BY=NULL,MARK_REVIEW_REMARK=NULL,FRONT_VISIBLE='1',UPDATED_BY=?,UPDATED_AT=CURRENT_TIMESTAMP WHERE ID=?");

  try {
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      var fusionId = fusionMap[trim(row.mainTitle)];
      var policyId = trim(row.policyId);
      var type = trim(row.policyAttribute);
      var detail = details[policyId];
      if (!fusionId || !policyId || !type || !detail) {
        stats.errors++;
        if (!detail) stats.missingPolicy++;
        reportRows.push({ status: "error", excelRow: row.excelRow, mainTitle: row.mainTitle, policyId: policyId, type: type });
        continue;
      }

      var key = fusionId + "|" + policyId;
      var docId = existing.map[key];
      var status;
      if (docId) {
        updateDocType.setString(1, type);
        updateDocType.setString(2, OPERATOR);
        updateDocType.setString(3, docId);
        updateDocType.executeUpdate();
        stats.documentsExisting++;
        stats.documentTypeUpdated++;
        status = "existing";
      } else {
        var displayOrder = (existing.displayMax[fusionId] || 0) + 1;
        bindDoc(insertDoc, nextDocId, fusionId, policyId, type, detail, displayOrder);
        var docOn = conn.createStatement();
        docOn.execute("SET IDENTITY_INSERT SYSDBA.X1_POLICY_DOCUMENT ON");
        docOn.close();
        insertDoc.executeUpdate();
        var docOffEach = conn.createStatement();
        docOffEach.execute("SET IDENTITY_INSERT SYSDBA.X1_POLICY_DOCUMENT OFF");
        docOffEach.close();
        bindBase(insertBase, nextBaseId, nextDocId, detail);
        var baseOn = conn.createStatement();
        baseOn.execute("SET IDENTITY_INSERT SYSDBA.X1_POLICY_BASE_INFO ON");
        baseOn.close();
        insertBase.executeUpdate();
        var baseOffEach = conn.createStatement();
        baseOffEach.execute("SET IDENTITY_INSERT SYSDBA.X1_POLICY_BASE_INFO OFF");
        baseOffEach.close();
        existing.map[key] = String(nextDocId);
        existing.displayMax[fusionId] = displayOrder;
        docId = String(nextDocId);
        nextDocId++;
        nextBaseId++;
        stats.documentsInserted++;
        status = "inserted";
      }

      updateMeta.setString(1, type);
      updateMeta.setString(2, REVIEW_PENDING);
      updateMeta.setString(3, OPERATOR);
      updateMeta.setString(4, policyId);
      var updated = updateMeta.executeUpdate();
      if (updated > 0) {
        stats.metadataUpdated++;
      } else {
        insertMeta.setString(1, policyId);
        insertMeta.setString(2, type);
        insertMeta.setString(3, REVIEW_PENDING);
        insertMeta.setString(4, OPERATOR);
        insertMeta.setString(5, OPERATOR);
        insertMeta.executeUpdate();
        stats.metadataInserted++;
      }

      resetFusion.setString(1, FUSION_PENDING);
      resetFusion.setString(2, OPERATOR);
      resetFusion.setString(3, fusionId);
      resetFusion.executeUpdate();

      reportRows.push({
        status: status,
        source: row.source,
        excelRow: row.excelRow,
        fusionId: fusionId,
        mainTitle: row.mainTitle,
        documentId: docId,
        policyId: policyId,
        policyTitle: detail.policyTitle,
        policyAttribute: type
      });

      if ((i + 1) % 200 === 0) {
        print("processed=" + (i + 1) + " inserted=" + stats.documentsInserted + " existing=" + stats.documentsExisting);
      }
    }
  } finally {
    insertDoc.close();
    insertBase.close();
    updateDocType.close();
    updateMeta.close();
    insertMeta.close();
    resetFusion.close();
  }

  if (stats.errors > 0) {
    throw new Error("Import has errors: " + stats.errors);
  }
  conn.commit();
  var result = { stats: stats, rows: reportRows };
  writeJson("tmp/huiqiyun_import_result.json", result);
  print(JSON.stringify(stats, null, 2));
  print("result=tmp/huiqiyun_import_result.json");
} catch (e) {
  try { conn.rollback(); } catch (ignored) {}
  throw e;
} finally {
  conn.close();
}
