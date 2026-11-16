var Files = Java.type("java.nio.file.Files");
var Paths = Java.type("java.nio.file.Paths");
var StandardCharsets = Java.type("java.nio.charset.StandardCharsets");
var Pattern = Java.type("java.util.regex.Pattern");
var Properties = Java.type("java.util.Properties");
var DriverManager = Java.type("java.sql.DriverManager");
var Class = Java.type("java.lang.Class");
var StringBuilder = Java.type("java.lang.StringBuilder");

var DB_URL = Java.type("java.lang.System").getenv().getOrDefault("KEJIJU_JDBC_DB_URL", "jdbc:dm://127.0.0.1:5236");
var DB_USER = "SYSDBA";
var DB_PASSWORD = Java.type("java.lang.System").getenv("KEJIJU_DB_PASSWORD");

function findMd() {
  var stream = Files.list(Paths.get("."));
  try {
    var it = stream.iterator();
    while (it.hasNext()) {
      var p = it.next();
      var name = String(p.getFileName().toString());
      if (name.indexOf("8.4") === 0 && name.lastIndexOf(".md") === name.length - 3) {
        return p;
      }
    }
  } finally {
    stream.close();
  }
  throw new Error("8.4*.md not found");
}

function trim(s) {
  return s == null ? "" : String(s).replace(/^\s+|\s+$/g, "");
}

function parseMd(mdPath) {
  var lines = Files.readAllLines(mdPath, StandardCharsets.UTF_8);
  var mainRe = Pattern.compile("^###\\s+(\\d+)\\.\\s+(.+?)\\s*$");
  var docRe = Pattern.compile("^\\d+\\.\\s+\\[(指导政策|实施细则|申报通知|结果公告)\\]\\s+(.+?)(?:（发布时间：([0-9]{4}-[0-9]{2}-[0-9]{2})）)?\\s*$");
  var docs = [];
  var currentNo = -1;
  var currentTitle = "";
  for (var i = 0; i < lines.size(); i++) {
    var line = String(lines.get(i));
    var main = mainRe.matcher(line);
    if (main.matches()) {
      currentNo = parseInt(String(main.group(1)), 10);
      currentTitle = trim(main.group(2));
      continue;
    }
    var doc = docRe.matcher(line);
    if (doc.matches() && currentNo > 0) {
      docs.push({
        mainNo: currentNo,
        mainTitle: currentTitle,
        type: trim(doc.group(1)),
        title: trim(doc.group(2)),
        lineNo: i + 1
      });
    }
  }
  return docs;
}

function groupByTitle(docs) {
  var grouped = {};
  var order = [];
  for (var i = 0; i < docs.length; i++) {
    var title = docs[i].title;
    if (!grouped[title]) {
      grouped[title] = [];
      order.push(title);
    }
    grouped[title].push(docs[i]);
  }
  return { grouped: grouped, order: order };
}

function distinctPolicyIds(hits) {
  var seen = {};
  var ids = [];
  for (var i = 0; i < hits.length; i++) {
    var id = hits[i].policyId;
    if (id && !seen[id]) {
      seen[id] = true;
      ids.push(id);
    }
  }
  return ids;
}

function queryHits(conn, titles) {
  var result = {};
  for (var init = 0; init < titles.length; init++) {
    result[titles[init]] = [];
  }
  var chunkSize = 300;
  for (var start = 0; start < titles.length; start += chunkSize) {
    var chunk = titles.slice(start, Math.min(start + chunkSize, titles.length));
    var chunkSet = {};
    for (var c = 0; c < chunk.length; c++) {
      chunkSet[chunk[c]] = true;
    }
    var placeholders = [];
    for (var p = 0; p < chunk.length; p++) {
      placeholders.push("?");
    }
    var inList = placeholders.join(",");
    var sql =
      "SELECT CAST(p.ID AS VARCHAR(50)) AS POLICY_ID, p.POLICY_TITLE, " +
      "       o.POLICY_TITLE AS ORIGINAL_TITLE, o.PUBLISH_DATE, o.FILE_PATH, o.FILE_TYPE " +
      "FROM SYSDBA.POLICY p " +
      "LEFT JOIN SYSDBA.POLICY_ORIGINAL_V2 o ON o.POLICY_ID = CAST(p.ID AS VARCHAR(50)) " +
      "WHERE TRIM(p.POLICY_TITLE) IN (" + inList + ") " +
      "   OR TRIM(o.POLICY_TITLE) IN (" + inList + ") " +
      "ORDER BY CAST(p.ID AS VARCHAR(50)), o.PUBLISH_DATE, o.FILE_PATH";
    var ps = conn.prepareStatement(sql);
    try {
      var paramIndex = 1;
      for (var a = 0; a < chunk.length; a++) {
        ps.setString(paramIndex++, chunk[a]);
      }
      for (var b = 0; b < chunk.length; b++) {
        ps.setString(paramIndex++, chunk[b]);
      }
      var rs = ps.executeQuery();
      try {
        while (rs.next()) {
          var hit = {
            policyId: trim(rs.getString("POLICY_ID")),
            policyTitle: trim(rs.getString("POLICY_TITLE")),
            originalTitle: trim(rs.getString("ORIGINAL_TITLE")),
            publishDate: trim(rs.getString("PUBLISH_DATE")),
            filePath: trim(rs.getString("FILE_PATH")),
            fileType: trim(rs.getString("FILE_TYPE"))
          };
          if (chunkSet[hit.policyTitle]) {
            result[hit.policyTitle].push(hit);
          }
          if (hit.originalTitle !== hit.policyTitle && chunkSet[hit.originalTitle]) {
            result[hit.originalTitle].push(hit);
          }
        }
      } finally {
        rs.close();
      }
      print("checked " + Math.min(start + chunk.length, titles.length) + " / " + titles.length);
    } finally {
      ps.close();
    }
  }
  return result;
}

function blank(value) {
  return value && trim(value) ? trim(value) : "-";
}

function writeMdRows(sb, rows) {
  for (var i = 0; i < rows.length; i++) {
    var row = rows[i];
    sb.append("   - md line ").append(row.lineNo)
      .append(" [").append(row.type).append("] #").append(row.mainNo)
      .append(" ").append(row.mainTitle).append("\n");
  }
}

function writeHits(sb, hits) {
  if (!hits || hits.length === 0) {
    sb.append("   - DB: 未命中\n");
    return;
  }
  var seen = {};
  for (var i = 0; i < hits.length; i++) {
    var hit = hits[i];
    var key = hit.policyId + "\u0001" + hit.publishDate + "\u0001" + hit.filePath;
    if (seen[key]) {
      continue;
    }
    seen[key] = true;
    sb.append("   - DB POLICY_ID=").append(hit.policyId)
      .append(" PUBLISH_DATE=").append(blank(hit.publishDate))
      .append(" FILE_TYPE=").append(blank(hit.fileType)).append("\n");
    sb.append("     FILE_PATH=").append(blank(hit.filePath)).append("\n");
    if (hit.originalTitle && hit.originalTitle !== hit.policyTitle) {
      sb.append("     ORIGINAL_TITLE=").append(hit.originalTitle).append("\n");
    }
  }
}

function writeGroupSection(sb, title, groups, hitsByTitle) {
  sb.append(title).append("\n\n");
  var keys = Object.keys(groups);
  if (keys.length === 0) {
    sb.append("无。\n\n");
    return;
  }
  for (var i = 0; i < keys.length; i++) {
    var docTitle = keys[i];
    sb.append(i + 1).append(". ").append(docTitle).append("\n");
    writeMdRows(sb, groups[docTitle]);
    writeHits(sb, hitsByTitle[docTitle]);
    sb.append("\n");
  }
}

function writeReport(mdPath, docs, groupedInfo, hitsByTitle) {
  var grouped = groupedInfo.grouped;
  var titles = groupedInfo.order;
  var mdDuplicateTitleGroups = 0;
  var mdDuplicateRows = 0;
  var unmatchedTitleGroups = 0;
  var unmatchedRows = 0;
  var uniqueHitTitleGroups = 0;
  var uniqueRows = 0;
  var multiHitTitleGroups = 0;
  var ambiguousRows = 0;
  var totalDbRows = 0;
  var sameMainPolicyIdDuplicateGroups = 0;
  var sameMainPolicyIdDuplicateRows = 0;
  var fullyDuplicateGroups = {};
  var ambiguousGroups = {};
  var unmatchedGroups = {};
  var mdDuplicateGroups = {};

  for (var i = 0; i < titles.length; i++) {
    var title = titles[i];
    var rows = grouped[title];
    var hits = hitsByTitle[title] || [];
    var policyIds = distinctPolicyIds(hits);
    totalDbRows += hits.length;
    if (rows.length > 1) {
      mdDuplicateTitleGroups++;
      mdDuplicateRows += rows.length;
      mdDuplicateGroups[title] = rows;
    }
    if (policyIds.length === 0) {
      unmatchedTitleGroups++;
      unmatchedRows += rows.length;
      unmatchedGroups[title] = rows;
    } else if (policyIds.length === 1) {
      uniqueHitTitleGroups++;
      uniqueRows += rows.length;
    } else {
      multiHitTitleGroups++;
      ambiguousRows += rows.length;
      ambiguousGroups[title] = rows;
    }
    if (policyIds.length === 1 && rows.length > 1) {
      var mainCounts = {};
      for (var j = 0; j < rows.length; j++) {
        var key = rows[j].mainTitle + "\u0001" + policyIds[0];
        mainCounts[key] = (mainCounts[key] || 0) + 1;
      }
      var keys = Object.keys(mainCounts);
      for (var k = 0; k < keys.length; k++) {
        if (mainCounts[keys[k]] > 1) {
          sameMainPolicyIdDuplicateGroups++;
          sameMainPolicyIdDuplicateRows += mainCounts[keys[k]];
          fullyDuplicateGroups[title] = rows;
          break;
        }
      }
    }
  }

  var sb = new StringBuilder();
  sb.append("# 8.4 md 政策标题匹配只读预检\n\n");
  sb.append("- 源文件: `").append(mdPath.toAbsolutePath().toString()).append("`\n");
  sb.append("- md 关联政策行数: ").append(docs.length).append("\n");
  sb.append("- md 去重后标题数: ").append(titles.length).append("\n");
  sb.append("- md 内部重复标题组数: ").append(mdDuplicateTitleGroups).append("\n");
  sb.append("- md 内部重复标题涉及行数: ").append(mdDuplicateRows).append("\n");
  sb.append("- 数据库未命中标题组数: ").append(unmatchedTitleGroups).append("\n");
  sb.append("- 数据库未命中 md 行数: ").append(unmatchedRows).append("\n");
  sb.append("- 数据库唯一 POLICY_ID 命中标题组数: ").append(uniqueHitTitleGroups).append("\n");
  sb.append("- 数据库唯一 POLICY_ID 命中 md 行数: ").append(uniqueRows).append("\n");
  sb.append("- 数据库多 POLICY_ID 命中标题组数: ").append(multiHitTitleGroups).append("\n");
  sb.append("- 数据库多 POLICY_ID 命中 md 行数: ").append(ambiguousRows).append("\n");
  sb.append("- 数据库返回原始行数: ").append(totalDbRows).append("\n");
  sb.append("- 同一主政策下同一 POLICY_ID 完全重复组数: ").append(sameMainPolicyIdDuplicateGroups).append("\n");
  sb.append("- 同一主政策下同一 POLICY_ID 完全重复涉及 md 行数: ").append(sameMainPolicyIdDuplicateRows).append("\n\n");

  writeGroupSection(sb, "## 同一主政策下同一 POLICY_ID 完全重复", fullyDuplicateGroups, hitsByTitle);
  writeGroupSection(sb, "## 数据库多 POLICY_ID 命中标题", ambiguousGroups, hitsByTitle);
  writeGroupSection(sb, "## 数据库未命中标题", unmatchedGroups, hitsByTitle);
  writeGroupSection(sb, "## md 内部重复标题完整清单", mdDuplicateGroups, hitsByTitle);

  var out = Paths.get("tmp", "policy_title_match_precheck.md");
  Files.createDirectories(out.getParent());
  Files.write(out, String(sb.toString()).getBytes(StandardCharsets.UTF_8));
  print(out.toAbsolutePath().toString());
}

var mdPath = findMd();
var docs = parseMd(mdPath);
var groupedInfo = groupByTitle(docs);
Class.forName("dm.jdbc.driver.DmDriver");
var props = new Properties();
props.setProperty("user", DB_USER);
props.setProperty("password", DB_PASSWORD);
var conn = DriverManager.getConnection(DB_URL, props);
try {
  var hitsByTitle = queryHits(conn, groupedInfo.order);
  writeReport(mdPath, docs, groupedInfo, hitsByTitle);
} finally {
  conn.close();
}
