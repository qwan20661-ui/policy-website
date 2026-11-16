$ErrorActionPreference = 'Stop'

$BaseUrl = 'http://127.0.0.1:33044'
$MdPath = Get-ChildItem -Path . -File -Filter '8.4*.md' | Select-Object -First 1
if (-not $MdPath) {
  throw '未找到 8.4*.md 文件'
}

function Normalize-Title([string]$value) {
  if ($null -eq $value) { return '' }
  return ($value -replace '^\s+|\s+$', '')
}

$lines = Get-Content -Path $MdPath.FullName -Encoding UTF8
$docs = New-Object System.Collections.Generic.List[object]
$mainNo = $null
$mainTitle = ''
for ($i = 0; $i -lt $lines.Count; $i++) {
  $line = $lines[$i]
  if ($line -match '^###\s+(\d+)\.\s+(.+?)\s*$') {
    $mainNo = [int]$Matches[1]
    $mainTitle = Normalize-Title $Matches[2]
    continue
  }
  if ($line -match '^\d+\.\s+\[(指导政策|实施细则|申报通知|结果公告)\]\s+(.+?)(?:（发布时间：([0-9]{4}-[0-9]{2}-[0-9]{2})）)?\s*$') {
    $docs.Add([pscustomobject]@{
      LineNo = $i + 1
      MainNo = $mainNo
      MainTitle = $mainTitle
      Type = Normalize-Title $Matches[1]
      Title = Normalize-Title $Matches[2]
    })
  }
}

$pageSize = 2000
$first = Invoke-RestMethod -Uri "$BaseUrl/api/classification/policies?page=1&size=$pageSize" -Method Get -TimeoutSec 60
if (-not $first.success) {
  throw "原政策列表接口返回失败"
}
$total = [int]$first.pagination.total
$pages = [int][Math]::Ceiling($total / $pageSize)
$allPolicies = New-Object System.Collections.Generic.List[object]
foreach ($item in $first.data) { $allPolicies.Add($item) }
Write-Host "fetched page 1 / $pages, total $total"

for ($page = 2; $page -le $pages; $page++) {
  $resp = Invoke-RestMethod -Uri "$BaseUrl/api/classification/policies?page=$page&size=$pageSize" -Method Get -TimeoutSec 60
  if (-not $resp.success) {
    throw "原政策列表第 $page 页接口返回失败"
  }
  foreach ($item in $resp.data) { $allPolicies.Add($item) }
  Write-Host "fetched page $page / $pages"
}

$dbByTitle = @{}
foreach ($p in $allPolicies) {
  $titles = @()
  $t1 = Normalize-Title $p.title
  $t2 = Normalize-Title $p.policyOriginalTitle
  if ($t1) { $titles += $t1 }
  if ($t2 -and $t2 -ne $t1) { $titles += $t2 }
  foreach ($t in $titles) {
    if (-not $dbByTitle.ContainsKey($t)) {
      $dbByTitle[$t] = New-Object System.Collections.Generic.List[object]
    }
    $dbByTitle[$t].Add($p)
  }
}

$docsByTitle = $docs | Group-Object Title
$uniqueTitleCount = $docsByTitle.Count
$mdDuplicateGroups = @($docsByTitle | Where-Object { $_.Count -gt 1 })
$unmatchedGroups = New-Object System.Collections.Generic.List[object]
$uniqueHitGroups = New-Object System.Collections.Generic.List[object]
$multiPolicyIdGroups = New-Object System.Collections.Generic.List[object]
$exactDuplicateGroups = New-Object System.Collections.Generic.List[object]
$sameTitleDifferentFileGroups = New-Object System.Collections.Generic.List[object]
$totalDbRows = 0

foreach ($g in $docsByTitle) {
  $title = $g.Name
  $hits = @()
  if ($dbByTitle.ContainsKey($title)) {
    $hits = @($dbByTitle[$title])
  }
  $totalDbRows += $hits.Count
  $policyIds = @($hits | ForEach-Object { if ($_.policyId) { [string]$_.policyId } else { [string]$_.id } } | Where-Object { $_ } | Sort-Object -Unique)
  if ($policyIds.Count -eq 0) {
    $unmatchedGroups.Add([pscustomobject]@{ Title = $title; Rows = @($g.Group); Hits = @(); PolicyIds = @() })
  } elseif ($policyIds.Count -eq 1) {
    $uniqueHitGroups.Add([pscustomobject]@{ Title = $title; Rows = @($g.Group); Hits = $hits; PolicyIds = $policyIds })
  } else {
    $multiPolicyIdGroups.Add([pscustomobject]@{ Title = $title; Rows = @($g.Group); Hits = $hits; PolicyIds = $policyIds })
    $sameTitleDifferentFileGroups.Add([pscustomobject]@{ Title = $title; Rows = @($g.Group); Hits = $hits; PolicyIds = $policyIds })
  }

  if ($policyIds.Count -eq 1 -and $g.Count -gt 1) {
    $mainPolicyPairs = @{}
    foreach ($row in $g.Group) {
      $key = "$($row.MainTitle)|$($policyIds[0])"
      if (-not $mainPolicyPairs.ContainsKey($key)) { $mainPolicyPairs[$key] = 0 }
      $mainPolicyPairs[$key]++
    }
    $dupPairCount = @($mainPolicyPairs.GetEnumerator() | Where-Object { $_.Value -gt 1 })
    if ($dupPairCount.Count -gt 0) {
      $exactDuplicateGroups.Add([pscustomobject]@{ Title = $title; Rows = @($g.Group); Hits = $hits; PolicyIds = $policyIds })
    }
  }
}

function Append-Rows($sb, $rows) {
  foreach ($row in $rows) {
    [void]$sb.AppendLine("   - md line $($row.LineNo) [$($row.Type)] #$($row.MainNo) $($row.MainTitle)")
  }
}

function Append-Hits($sb, $hits) {
  if (-not $hits -or $hits.Count -eq 0) {
    [void]$sb.AppendLine('   - DB: 未命中')
    return
  }
  $seen = @{}
  foreach ($hit in $hits) {
    $pid = if ($hit.policyId) { [string]$hit.policyId } else { [string]$hit.id }
    $key = "$pid|$($hit.publishDate)|$($hit.filePath)"
    if ($seen.ContainsKey($key)) { continue }
    $seen[$key] = $true
    $pd = if ($hit.publishDate) { $hit.publishDate } else { '-' }
    $ft = if ($hit.fileType) { $hit.fileType } else { '-' }
    [void]$sb.AppendLine("   - DB POLICY_ID=$pid PUBLISH_DATE=$pd FILE_TYPE=$ft")
    [void]$sb.AppendLine("     FILE_PATH=$($hit.filePath)")
    if ($hit.policyOriginalTitle -and $hit.policyOriginalTitle -ne $hit.title) {
      [void]$sb.AppendLine("     ORIGINAL_TITLE=$($hit.policyOriginalTitle)")
    }
  }
}

function Append-Section($sb, [string]$name, $groups) {
  [void]$sb.AppendLine($name)
  [void]$sb.AppendLine()
  if (-not $groups -or $groups.Count -eq 0) {
    [void]$sb.AppendLine('无。')
    [void]$sb.AppendLine()
    return
  }
  $idx = 1
  foreach ($g in $groups) {
    [void]$sb.AppendLine("$idx. $($g.Title)")
    Append-Rows $sb $g.Rows
    Append-Hits $sb $g.Hits
    [void]$sb.AppendLine()
    $idx++
  }
}

$mdDuplicateRows = @($mdDuplicateGroups | ForEach-Object { $_.Count } | Measure-Object -Sum).Sum
if ($null -eq $mdDuplicateRows) { $mdDuplicateRows = 0 }
$unmatchedRows = @($unmatchedGroups | ForEach-Object { $_.Rows.Count } | Measure-Object -Sum).Sum
if ($null -eq $unmatchedRows) { $unmatchedRows = 0 }
$uniqueRows = @($uniqueHitGroups | ForEach-Object { $_.Rows.Count } | Measure-Object -Sum).Sum
if ($null -eq $uniqueRows) { $uniqueRows = 0 }
$multiRows = @($multiPolicyIdGroups | ForEach-Object { $_.Rows.Count } | Measure-Object -Sum).Sum
if ($null -eq $multiRows) { $multiRows = 0 }
$exactDuplicateRows = @($exactDuplicateGroups | ForEach-Object { $_.Rows.Count } | Measure-Object -Sum).Sum
if ($null -eq $exactDuplicateRows) { $exactDuplicateRows = 0 }

$sb = New-Object System.Text.StringBuilder
[void]$sb.AppendLine('# 8.4 md 政策标题匹配只读预检')
[void]$sb.AppendLine()
[void]$sb.AppendLine("- 源文件: `$($MdPath.FullName)`")
[void]$sb.AppendLine("- 远程只读接口: `$BaseUrl/api/classification/policies`")
[void]$sb.AppendLine("- 原政策库拉取数量: $($allPolicies.Count) / $total")
[void]$sb.AppendLine("- md 关联政策行数: $($docs.Count)")
[void]$sb.AppendLine("- md 去重后标题数: $uniqueTitleCount")
[void]$sb.AppendLine("- md 内部重复标题组数: $($mdDuplicateGroups.Count)")
[void]$sb.AppendLine("- md 内部重复标题涉及行数: $mdDuplicateRows")
[void]$sb.AppendLine("- 数据库未命中标题组数: $($unmatchedGroups.Count)")
[void]$sb.AppendLine("- 数据库未命中 md 行数: $unmatchedRows")
[void]$sb.AppendLine("- 数据库唯一 POLICY_ID 命中标题组数: $($uniqueHitGroups.Count)")
[void]$sb.AppendLine("- 数据库唯一 POLICY_ID 命中 md 行数: $uniqueRows")
[void]$sb.AppendLine("- 数据库多 POLICY_ID 命中标题组数: $($multiPolicyIdGroups.Count)")
[void]$sb.AppendLine("- 数据库多 POLICY_ID 命中 md 行数: $multiRows")
[void]$sb.AppendLine("- 数据库返回匹配行数: $totalDbRows")
[void]$sb.AppendLine("- 同一主政策下同一 POLICY_ID 完全重复组数: $($exactDuplicateGroups.Count)")
[void]$sb.AppendLine("- 同一主政策下同一 POLICY_ID 完全重复涉及 md 行数: $exactDuplicateRows")
[void]$sb.AppendLine()

Append-Section $sb '## 同一主政策下同一 POLICY_ID 完全重复' $exactDuplicateGroups
Append-Section $sb '## 数据库多 POLICY_ID 命中标题' $multiPolicyIdGroups
Append-Section $sb '## 数据库未命中标题' $unmatchedGroups

$mdDupObjects = New-Object System.Collections.Generic.List[object]
foreach ($g in $mdDuplicateGroups) {
  $title = $g.Name
  $hits = if ($dbByTitle.ContainsKey($title)) { @($dbByTitle[$title]) } else { @() }
  $policyIds = @($hits | ForEach-Object { if ($_.policyId) { [string]$_.policyId } else { [string]$_.id } } | Where-Object { $_ } | Sort-Object -Unique)
  $mdDupObjects.Add([pscustomobject]@{ Title = $title; Rows = @($g.Group); Hits = $hits; PolicyIds = $policyIds })
}
Append-Section $sb '## md 内部重复标题完整清单' $mdDupObjects

$out = Join-Path 'tmp' 'policy_title_match_precheck.md'
New-Item -ItemType Directory -Force -Path 'tmp' | Out-Null
[System.IO.File]::WriteAllText((Resolve-Path 'tmp').Path + '\policy_title_match_precheck.md', $sb.ToString(), [System.Text.Encoding]::UTF8)

Write-Host "report: $((Resolve-Path $out).Path)"
