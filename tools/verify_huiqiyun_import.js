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

function writeJson(path, obj) {
  Files.write(Paths.get(path), JSON.stringify(obj, null, 2).getBytes(StandardCharsets.UTF_8));
}

function trim(value) {
  return value == null ? "" : String(value).replace(/^\s+|\s+$/g, "");
}

Class.forName("dm.jdbc.driver.DmDriver");
var props = new Properties();
props.setProperty("user", "SYSDBA");
props.setProperty("password", Java.type("java.lang.System").getenv("KEJIJU_DB_PASSWORD"));
var conn = DriverManager.getConnection(Java.type("java.lang.System").getenv().getOrDefault("KEJIJU_JDBC_DB_URL", "jdbc:dm://127.0.0.1:5236"), props);

function findFusion(title) {
  var ps = conn.prepareStatement(
    "SELECT ID,MARK_REVIEW_STATUS,NVL(FRONT_VISIBLE,'1') AS FRONT_VISIBLE,NVL(DEL_FLAG,'0') AS DEL_FLAG " +
    "FROM SYSDBA.X1_FUSION_POLICY WHERE NVL(DEL_FLAG,'0')='0' AND (TRIM(DIRECTORY_TITLE)=? OR TRIM(MAIN_FILE_TITLE)=?) ORDER BY ID DESC"
  );
  try {
    ps.setString(1, title);
    ps.setString(2, title);
    var rs = ps.executeQuery();
    try {
      if (!rs.next()) return null;
      return {
        id: trim(rs.getString("ID")),
        review: trim(rs.getString("MARK_REVIEW_STATUS")),
        visible: trim(rs.getString("FRONT_VISIBLE")),
        delFlag: trim(rs.getString("DEL_FLAG"))
      };
    } finally {
      rs.close();
    }
  } finally {
    ps.close();
  }
}

function findDocument(fusionId, policyId) {
  var ps = conn.prepareStatement(
    "SELECT ID,POLICY_ATTRIBUTE FROM SYSDBA.X1_POLICY_DOCUMENT WHERE NVL(DEL_FLAG,'0')='0' AND FUSION_POLICY_ID=? AND SOURCE_POLICY_ID=? ORDER BY ID DESC"
  );
  try {
    ps.setString(1, fusionId);
    ps.setString(2, policyId);
    var rs = ps.executeQuery();
    try {
      if (!rs.next()) return null;
      return { id: trim(rs.getString("ID")), type: trim(rs.getString("POLICY_ATTRIBUTE")) };
    } finally {
      rs.close();
    }
  } finally {
    ps.close();
  }
}

function metadata(policyId) {
  var ps = conn.prepareStatement("SELECT POLICY_ATTRIBUTE,REVIEW_STATUS FROM SYSDBA.POLICY_METADATA WHERE POLICY_ID=? AND ROWNUM=1");
  try {
    ps.setString(1, policyId);
    var rs = ps.executeQuery();
    try {
      if (!rs.next()) return null;
      return { type: trim(rs.getString("POLICY_ATTRIBUTE")), review: trim(rs.getString("REVIEW_STATUS")) };
    } finally {
      rs.close();
    }
  } finally {
    ps.close();
  }
}

function scalar(sql) {
  var st = conn.createStatement();
  var rs = st.executeQuery(sql);
  try {
    rs.next();
    return Number(rs.getString(1));
  } finally {
    rs.close();
    st.close();
  }
}

var plan = readJson("tmp/huiqiyun_import_precheck.json");
var rows = plan.confirmedPlanRows;
var stats = {
  plannedRows: rows.length,
  fusionFound: 0,
  fusionMissing: 0,
  fusionHidden: 0,
  fusionNotPending: 0,
  documentFound: 0,
  documentMissing: 0,
  documentTypeMismatch: 0,
  metadataFound: 0,
  metadataMissing: 0,
  metadataTypeMismatch: 0,
  visibleFusionCreatedByBatch: 0,
  documentCreatedByBatch: 0
};
var bad = [];
var fusionCache = {};
var docSeen = {};
var metaSeen = {};

try {
  for (var i = 0; i < rows.length; i++) {
    var row = rows[i];
    var title = trim(row.mainTitle);
    var policyId = trim(row.policyId);
    var type = trim(row.policyAttribute);
    var fusion = fusionCache[title];
    if (fusion === undefined) {
      fusion = findFusion(title);
      fusionCache[title] = fusion;
    }
    if (!fusion) {
      stats.fusionMissing++;
      bad.push({ kind: "missingFusion", mainTitle: title, policyId: policyId });
      continue;
    }
    stats.fusionFound++;
    if (fusion.visible === "0") {
      stats.fusionHidden++;
      bad.push({ kind: "hiddenFusion", fusionId: fusion.id, mainTitle: title });
    }
    if (fusion.review !== "0") {
      stats.fusionNotPending++;
      bad.push({ kind: "fusionNotPending", fusionId: fusion.id, review: fusion.review, mainTitle: title });
    }

    var docKey = fusion.id + "|" + policyId;
    var doc = docSeen[docKey];
    if (doc === undefined) {
      doc = findDocument(fusion.id, policyId);
      docSeen[docKey] = doc;
    }
    if (!doc) {
      stats.documentMissing++;
      bad.push({ kind: "missingDocument", fusionId: fusion.id, mainTitle: title, policyId: policyId });
    } else {
      stats.documentFound++;
      if (doc.type !== type) {
        stats.documentTypeMismatch++;
        bad.push({ kind: "documentTypeMismatch", fusionId: fusion.id, policyId: policyId, expected: type, actual: doc.type });
      }
    }

    var meta = metaSeen[policyId];
    if (meta === undefined) {
      meta = metadata(policyId);
      metaSeen[policyId] = meta;
    }
    if (!meta) {
      stats.metadataMissing++;
      bad.push({ kind: "metadataMissing", policyId: policyId, expected: type });
    } else {
      stats.metadataFound++;
      if (meta.type !== type) {
        stats.metadataTypeMismatch++;
        bad.push({ kind: "metadataTypeMismatch", policyId: policyId, expected: type, actual: meta.type });
      }
    }
  }
  stats.uniqueFusionTitles = Object.keys(fusionCache).length;
  stats.uniqueDocumentKeys = Object.keys(docSeen).length;
  stats.uniqueMetadataPolicies = Object.keys(metaSeen).length;
  stats.visibleFusionCreatedByBatch = scalar("SELECT COUNT(*) FROM SYSDBA.X1_FUSION_POLICY WHERE CREATED_BY='Codex-Huiqiyun' AND NVL(DEL_FLAG,'0')='0' AND TRIM(NVL(FRONT_VISIBLE,'1')) <> '0'");
  stats.documentCreatedByBatch = scalar("SELECT COUNT(*) FROM SYSDBA.X1_POLICY_DOCUMENT WHERE CREATED_BY='Codex-Huiqiyun' AND NVL(DEL_FLAG,'0')='0'");
  var result = { stats: stats, bad: bad.slice(0, 200) };
  writeJson("tmp/huiqiyun_import_verify.json", result);
  print(JSON.stringify(stats, null, 2));
  print("bad_count=" + bad.length);
  print("result=tmp/huiqiyun_import_verify.json");
} finally {
  conn.close();
}
