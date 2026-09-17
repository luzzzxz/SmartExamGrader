param(
    [string]$OutputDirectory = 'd:\作业试卷批改系统',
    [bool]$Sanitize = $true,
    [switch]$SkipRuntime
)

$ErrorActionPreference = 'Stop'

$projectDirectory = Split-Path -Parent $PSCommandPath
$parentDir = Split-Path -Parent $projectDirectory
if (Test-Path -LiteralPath (Join-Path $parentDir 'answer_card_stable_bootstrap.py')) {
    $launcherDirectory = $parentDir
    $runtimeDirectory = Join-Path $parentDir 'runtime\pydeps314'
} else {
    $launcherDirectory = $projectDirectory
    $runtimeDirectory = Join-Path $projectDirectory 'runtime\pydeps314'
}

$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$packageTag = if ($Sanitize) { '脱敏发布备份' } else { '完整私有备份' }
$packageName = "答题批改系统_${packageTag}_$timestamp"
$buildRoot = Join-Path $env:TEMP $packageName
$packageRoot = Join-Path $buildRoot $packageName
$appTarget = Join-Path $packageRoot 'app'
$archivePath = Join-Path $OutputDirectory "$packageName.zip"

Write-Host ">>> 开始打包流程 [$packageName]" -ForegroundColor Cyan
Write-Host ">>> 模式: $(if ($Sanitize) { '安全脱敏模式 (清理密钥/私有域名/隐私/业务数据)' } else { '完整私有模式' })" -ForegroundColor Yellow
Write-Host ">>> 原项目目录 (只读保护，绝不修改原数据): $projectDirectory" -ForegroundColor Green
Write-Host ">>> 输出目录: $OutputDirectory" -ForegroundColor Green

if (-not (Test-Path -LiteralPath $projectDirectory)) {
    throw "项目目录不存在：$projectDirectory"
}
if (-not (Test-Path -LiteralPath $OutputDirectory)) {
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
}
if (Test-Path -LiteralPath $buildRoot) {
    $resolvedTemp = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
    $resolvedBuild = [IO.Path]::GetFullPath($buildRoot)
    if (-not $resolvedBuild.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理非临时目录：$resolvedBuild"
    }
    Remove-Item -LiteralPath $buildRoot -Recurse -Force
}

New-Item -ItemType Directory -Path $appTarget -Force | Out-Null

function Copy-ProjectFile {
    param([string]$RelativePath)
    $source = Join-Path $projectDirectory $RelativePath
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        return
    }
    $destination = Join-Path $appTarget $RelativePath
    $destinationDirectory = Split-Path -Parent $destination
    New-Item -ItemType Directory -Path $destinationDirectory -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination -Force
}

function Copy-ProjectDirectoryClean {
    param(
        [string]$RelativePath,
        [string]$ExcludePattern = '(?i)\.bak|__pycache__|\.deleted|\.disabled|\.orig'
    )
    $source = Join-Path $projectDirectory $RelativePath
    if (-not (Test-Path -LiteralPath $source -PathType Container)) {
        return
    }
    $destination = Join-Path $appTarget $RelativePath
    New-Item -ItemType Directory -Path $destination -Force | Out-Null

    Get-ChildItem -LiteralPath $source -Recurse | ForEach-Object {
        $rel = $_.FullName.Substring($source.Length).TrimStart('\', '/')
        if ($rel -match $ExcludePattern) {
            return
        }
        $targetPath = Join-Path $destination $rel
        if ($_.PSIsContainer) {
            New-Item -ItemType Directory -Path $targetPath -Force | Out-Null
        } else {
            $targetDir = Split-Path -Parent $targetPath
            if (-not (Test-Path -LiteralPath $targetDir)) {
                New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
            }
            Copy-Item -LiteralPath $_.FullName -Destination $targetPath -Force
        }
    }
}

$coreFiles = @(
    'enhanced_answer_card_gui_stats.py',
    'marker_detector.py',
    'marker_utils.py',
    'template_registry.py',
    'template_registry.json',
    'template_capture_wizard.py',
    'paddle_ocr_helper.py',
    'wrongbook_generator.py',
    'knowledge_analysis.py',
    'manual_grading_web_server.py',
    'llm_grading_service.py',
    'llm_grading_config.json',
    'extract_roster_from_cards.py',
    'student_roster_local.py',
    'answer_zone_points.json',
    'student_id_points.json',
    'template_marker_detection.json',
    'ocr_preference_memory.json',
    'enhanced_gui_settings.json',
    'overlay_calibration_presets.json',
    'overlay_calibration_last_exports.json',
    'wrongbook_settings.json',
    'knowledge_bridge_settings.json',
    'manual_web_sync_config.json',
    'manual_web_server_config.json',
    '主程序使用帮助.md',
    'README_项目维护与备份.md',
    'README_当前版_使用与维护.md',
    '软件更新日志.md',
    '项目运行风险检查与优化记录_20260914.md',
    '数据隔离排查与修复_20260916.md',
    '数据隔离历史核查清单_20260916.md',
    '单空答案更正修复与成绩恢复_20260917.md',
    '评分修订与输出联动_20260917.md',
    '备份包说明.md',
    '另一台电脑安装说明.md',
    'requirements-main.txt',
    'requirements-paddleocr.txt',
    'migrate_from_old_version.py',
    'build_project_backup.ps1'
)
$coreFiles | ForEach-Object { Copy-ProjectFile $_ }

# 1. 复制代码子目录
Copy-ProjectDirectoryClean 'core'
Copy-ProjectDirectoryClean 'tests'
Copy-ProjectDirectoryClean 'teacher_mark_assets'
Copy-ProjectDirectoryClean 'deploy'

# 2. 处理 answer_keys
if ($Sanitize) {
    $akSource = Join-Path $projectDirectory 'answer_keys'
    $akTarget = Join-Path $appTarget 'answer_keys'
    New-Item -ItemType Directory -Path $akTarget -Force | Out-Null
    Get-ChildItem -LiteralPath $akSource -File -Filter '*.json' | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $akTarget $_.Name) -Force
    }
} else {
    Copy-ProjectDirectoryClean 'answer_keys'
}

# 3. 建立会话数据相关目录（脱敏模式下仅保留空目录）
$sessionDirs = @('session_answer_keys', 'session_subjective_scores', 'session_template_configs')
foreach ($sDir in $sessionDirs) {
    $targetDir = Join-Path $appTarget $sDir
    New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
    if (-not $Sanitize) {
        Copy-ProjectDirectoryClean $sDir
    }
}

# 4. 知识点编号表
$knowledgeCatalogCandidates = @(
    'D:\题库系统_v2\exports\知识点编号对应内容.csv',
    'D:\onedrive\知识点编号对应内容.csv',
    (Join-Path $projectDirectory '知识点编号对应内容.csv')
)
foreach ($candidate in $knowledgeCatalogCandidates) {
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        Copy-Item -LiteralPath $candidate -Destination (Join-Path $appTarget '知识点编号对应内容.csv') -Force
        break
    }
}

# 5. 模板与示例试卷
$defaultTemplate = Join-Path $projectDirectory 'incoming\template_with_markers\1.jpg'
if (Test-Path -LiteralPath $defaultTemplate) {
    $defaultTarget = Join-Path $appTarget 'incoming\template_with_markers'
    New-Item -ItemType Directory -Path $defaultTarget -Force | Out-Null
    Copy-Item -LiteralPath $defaultTemplate -Destination (Join-Path $defaultTarget '1.jpg') -Force
}

$templatesSource = Join-Path $projectDirectory 'templates'
$templatesTarget = Join-Path $appTarget 'templates'
New-Item -ItemType Directory -Path $templatesTarget -Force | Out-Null
Get-ChildItem -LiteralPath $templatesSource -Directory | ForEach-Object {
    $destination = Join-Path $templatesTarget $_.Name
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    Get-ChildItem -LiteralPath $_.FullName -File | Where-Object {
        $_.Name -notmatch '(?i)backup|debug|preview|\.bak'
    } | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $destination $_.Name) -Force
    }
}

# 6. 数据库快照与脱敏处理
$databaseSource = Join-Path $projectDirectory 'answer_card_app.db'
$databaseTarget = Join-Path $appTarget 'answer_card_app.db'
if (-not (Test-Path -LiteralPath $databaseSource -PathType Leaf)) {
    throw "主数据库不存在：$databaseSource"
}

$pythonCommand = $null
$pythonPrefixArgs = @()
$pythonCandidates = @(
    "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe",
    'C:\Program Files\Python314\python.exe'
)
foreach ($candidate in $pythonCandidates) {
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        $pythonCommand = $candidate
        break
    }
}
if (-not $pythonCommand) {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        $pythonCommand = $launcher.Source
        $pythonPrefixArgs = @('-3.14')
    }
}
if (-not $pythonCommand) {
    $launcher = Get-Command python -ErrorAction SilentlyContinue
    if ($launcher) {
        $pythonCommand = $launcher.Source
    }
}
if (-not $pythonCommand) {
    throw '创建数据库快照需要 Python 3 环境。'
}

# 6.1 在线导出一致性快照（保证主程序运行时也不影响读写，且绝不改变原数据库）
$snapshotScript = Join-Path $env:TEMP 'snapshot_sqlite_helper.py'
$snapshotScriptContent = @(
    'import sqlite3, sys',
    'src, dst = sys.argv[1], sys.argv[2]',
    'with sqlite3.connect(src, timeout=30) as s:',
    '    with sqlite3.connect(dst) as d:',
    '        s.backup(d)'
)
Set-Content -LiteralPath $snapshotScript -Value $snapshotScriptContent -Encoding UTF8
& $pythonCommand @pythonPrefixArgs $snapshotScript $databaseSource $databaseTarget
Remove-Item -LiteralPath $snapshotScript -Force -ErrorAction SilentlyContinue

if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $databaseTarget -PathType Leaf)) {
    throw '主数据库一致性快照创建失败。'
}

# 6.2 脱敏模式下清理临时构建库的敏感业务记录 (保持表结构与索引，清空历史数据并收缩体积)
if ($Sanitize) {
    Write-Host ">>> 正在执行临时数据库脱敏清洗 (保留全量结构，清空历史批改与成绩记录)..." -ForegroundColor Yellow
    $dbSanitizeScript = Join-Path $env:TEMP 'sanitize_db_helper.py'
    $dbSanitizeScriptContent = @(
        'import sqlite3, sys',
        'target_db = sys.argv[1]',
        'conn = sqlite3.connect(target_db)',
        'cur = conn.cursor()',
        'tables_to_clear = ["sessions", "session_results"]',
        'for tbl in tables_to_clear:',
        '    try:',
        '        cur.execute(f"DELETE FROM [{tbl}]")',
        '    except Exception as e:',
        '        print(f"Warning clearing {tbl}: {e}")',
        'try:',
        '    cur.execute("DELETE FROM sqlite_sequence WHERE name IN (\x27sessions\x27, \x27session_results\x27)")',
        'except Exception:',
        '    pass',
        'conn.commit()',
        'cur.execute("VACUUM")',
        'conn.close()',
        'print("DB_SANITIZE_SUCCESS")'
    )
    Set-Content -LiteralPath $dbSanitizeScript -Value $dbSanitizeScriptContent -Encoding UTF8
    & $pythonCommand @pythonPrefixArgs $dbSanitizeScript $databaseTarget
    $sanitizeExit = $LASTEXITCODE
    Remove-Item -LiteralPath $dbSanitizeScript -Force -ErrorAction SilentlyContinue

    if ($sanitizeExit -ne 0) {
        throw '目标数据库脱敏清洗失败。'
    }
}

# 7. 脱敏模式下清洗配置文件与敏感凭证
if ($Sanitize) {
    Write-Host ">>> 正在执行敏感凭证、域名与学生名册脱敏..." -ForegroundColor Yellow

    # 7.1 大模型配置 (LLM API Key 清零)
    $llmConfigFile = Join-Path $appTarget 'llm_grading_config.json'
    if (Test-Path -LiteralPath $llmConfigFile) {
        $llmJson = Get-Content -LiteralPath $llmConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $llmJson.api_key = ""
        $llmJson.enabled = $false
        $llmJson | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $llmConfigFile -Encoding UTF8
    }

    # 7.2 网页同步配置 (Token 与域名清零)
    $syncConfigFile = Join-Path $appTarget 'manual_web_sync_config.json'
    if (Test-Path -LiteralPath $syncConfigFile) {
        $syncJson = Get-Content -LiteralPath $syncConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $syncJson.api_token = ""
        $syncJson.server_url = ""
        $syncJson.teacher_url = ""
        $syncJson | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $syncConfigFile -Encoding UTF8
    }

    # 7.3 网页服务端配置 (Token 与初始密码清零)
    $serverConfigFile = Join-Path $appTarget 'manual_web_server_config.json'
    if (Test-Path -LiteralPath $serverConfigFile) {
        $serverJson = Get-Content -LiteralPath $serverConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $serverJson.api_token = ""
        $serverJson.session_secret = ""
        $serverJson.teacher_password_hash = ""
        $serverJson.teacher_password_initial = ""
        $serverJson | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $serverConfigFile -Encoding UTF8
    }

    # 7.4 题库桥接配置
    $bridgeConfigFile = Join-Path $appTarget 'knowledge_bridge_settings.json'
    if (Test-Path -LiteralPath $bridgeConfigFile) {
        $bridgeJson = Get-Content -LiteralPath $bridgeConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $bridgeJson.question_bank_url = ""
        $bridgeJson | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $bridgeConfigFile -Encoding UTF8
    }

    # 7.5 代码内私有域名占位符替换 (仅在构建副本中修改)
    $webSyncPy = Join-Path $appTarget 'core\web_sync.py'
    if (Test-Path -LiteralPath $webSyncPy) {
        $c = Get-Content -LiteralPath $webSyncPy -Raw -Encoding UTF8
        $c = [regex]::Replace($c, 'https://[a-zA-Z0-9_\.\-]+\.xyz', 'https://your-manual-grading-server.example.com')
        Set-Content -LiteralPath $webSyncPy -Value $c -Encoding UTF8
    }
    $knowPy = Join-Path $appTarget 'knowledge_analysis.py'
    if (Test-Path -LiteralPath $knowPy) {
        $c = Get-Content -LiteralPath $knowPy -Raw -Encoding UTF8
        $c = [regex]::Replace($c, 'https://[a-zA-Z0-9_\.\-]+\.xyz', 'https://your-question-bank-server.example.com')
        Set-Content -LiteralPath $knowPy -Value $c -Encoding UTF8
    }

    # 7.6 部署脚本 (VPS 真实 IP 与真实域名脱敏)
    $deployScript = Join-Path $appTarget 'deploy\install_manual_grading_web.sh'
    if (Test-Path -LiteralPath $deployScript) {
        $deployContent = Get-Content -LiteralPath $deployScript -Raw -Encoding UTF8
        $deployContent = [regex]::Replace($deployContent, '\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', '192.0.2.1')
        $deployContent = [regex]::Replace($deployContent, '[a-zA-Z0-9_\.\-]+\.xyz', 'your-domain.com')
        Set-Content -LiteralPath $deployScript -Value $deployContent -Encoding UTF8
    }

    # 7.7 学生名册脱敏 (替换为标准空示例模板，彻底移除真实学生名单)
    $rosterFile = Join-Path $appTarget 'student_roster_local.py'
    $cleanRosterLines = @(
        '# -*- coding: utf-8 -*-',
        '"""本地学生名册（由班级管理功能维护）"""',
        '',
        'CLASSES = {',
        '    "示例班级": {',
        '        "0001": "学生A",',
        '        "0002": "学生B"',
        '    }',
        '}'
    )
    Set-Content -LiteralPath $rosterFile -Value $cleanRosterLines -Encoding UTF8

    # 7.8 说明文档中的私有域名脱敏替换 (构建副本)
    Get-ChildItem -LiteralPath $appTarget -Filter '*.md' -Recurse | ForEach-Object {
        $doc = Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8
        $doc = [regex]::Replace($doc, 'https?://[a-zA-Z0-9_\.\-]+\.xyz', 'https://your-domain.com')
        Set-Content -LiteralPath $_.FullName -Value $doc -Encoding UTF8
    }
}

# 8. 启动器与辅助工具配置
$stableBootstrapSource = Join-Path $launcherDirectory 'answer_card_stable_bootstrap.py'
$cvHelperSource = Join-Path $launcherDirectory 'answer_card_cv_helper.py'
if (-not (Test-Path -LiteralPath $stableBootstrapSource)) {
    throw "稳定启动器不存在：$stableBootstrapSource"
}
Copy-Item -LiteralPath $stableBootstrapSource -Destination (Join-Path $packageRoot 'answer_card_stable_bootstrap.py') -Force
if (Test-Path -LiteralPath $cvHelperSource) {
    Copy-Item -LiteralPath $cvHelperSource -Destination (Join-Path $packageRoot 'answer_card_cv_helper.py') -Force
}

$bootstrapPath = Join-Path $packageRoot 'answer_card_stable_bootstrap.py'
$bootstrapText = Get-Content -LiteralPath $bootstrapPath -Raw -Encoding UTF8
$bootstrapText = $bootstrapText.Replace(
    'PROJECT_DIR = Path(r"C:\Users\Administrator\.openclaw\workspace\answer_card_autograder")',
    'PROJECT_DIR = BASE_DIR / "app"'
)
$bootstrapText = $bootstrapText.Replace(
    'PYDEPS_DIR = BASE_DIR / "pydeps314"',
    'PYDEPS_DIR = BASE_DIR / "runtime" / "pydeps314"'
)
Set-Content -LiteralPath $bootstrapPath -Value $bootstrapText -Encoding UTF8

$cvHelperPath = Join-Path $packageRoot 'answer_card_cv_helper.py'
if (Test-Path -LiteralPath $cvHelperPath) {
    $cvHelperText = Get-Content -LiteralPath $cvHelperPath -Raw -Encoding UTF8
    $cvHelperText = $cvHelperText.Replace(
        'PROJECT_DIR = Path(r"C:\Users\Administrator\.openclaw\workspace\answer_card_autograder")',
        'PROJECT_DIR = BASE_DIR / "app"'
    )
    $cvHelperText = $cvHelperText.Replace(
        'PYDEPS_DIR = BASE_DIR / "pydeps314"',
        'PYDEPS_DIR = BASE_DIR / "runtime" / "pydeps314"'
    )
    Set-Content -LiteralPath $cvHelperPath -Value $cvHelperText -Encoding UTF8
}

# 启动脚本
$launcherLines = @(
    '@echo off',
    'setlocal EnableDelayedExpansion',
    'set "BASE_DIR=%~dp0"',
    'set "PYTHON_CMD="',
    'set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"',
    'if not exist "%PYTHON_EXE%" set "PYTHON_EXE=C:\Program Files\Python314\python.exe"',
    'if exist "%PYTHON_EXE%" set PYTHON_CMD="%PYTHON_EXE%"',
    'if not defined PYTHON_CMD (',
    '  py -3.14 --version >nul 2>nul',
    '  if not errorlevel 1 set "PYTHON_CMD=py -3.14"',
    ')',
    'if not defined PYTHON_CMD (',
    '  echo ERROR: Python 3.14 not found.',
    '  echo Please install Python 3.14, then run this launcher again.',
    '  pause',
    '  exit /b 1',
    ')',
    'set "PYTHONPATH=%BASE_DIR%runtime\pydeps314;%PYTHONPATH%"',
    '%PYTHON_CMD% "%BASE_DIR%answer_card_stable_bootstrap.py"',
    'set "EXIT_CODE=%ERRORLEVEL%"',
    'if not "!EXIT_CODE!"=="0" (',
    '  echo.',
    '  echo ERROR: app exited with code !EXIT_CODE!.',
    '  pause',
    ')',
    'exit /b !EXIT_CODE!'
)
Set-Content -LiteralPath (Join-Path $packageRoot '启动答题批改系统.bat') -Value $launcherLines -Encoding ASCII

# 复制根目录额外工具与模板
function Copy-OptionalRootFile([string]$fileName, [string]$destinationName = $fileName) {
    $src = Join-Path $projectDirectory $fileName
    if (-not (Test-Path -LiteralPath $src)) {
        $src = Join-Path $launcherDirectory $fileName
    }
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $packageRoot $destinationName) -Force
    }
}

Copy-OptionalRootFile '备份包说明.md' '请先阅读_备份包说明.md'
Copy-OptionalRootFile '另一台电脑安装说明.md' '另一台电脑安装说明.md'
Copy-OptionalRootFile '安装OCR环境.bat' '安装OCR环境.bat'
Copy-OptionalRootFile '从旧版本迁移数据.bat' '从旧版本迁移数据.bat'
Copy-OptionalRootFile '模板试卷红横线检测工具_GUI.py' '模板试卷红横线检测工具_GUI.py'
Copy-OptionalRootFile '选择题套模板工具_GUI_不处理答案.py' '选择题套模板工具_GUI_不处理答案.py'
Copy-OptionalRootFile '直批模板.dotx' '直批模板.dotx'
Copy-OptionalRootFile 'CHANGELOG.md' 'CHANGELOG.md'
Copy-OptionalRootFile '软件更新日志.md' '软件更新日志.md'

if ($Sanitize) {
    Get-ChildItem -LiteralPath $packageRoot -Filter '*.md' | ForEach-Object {
        $doc = Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8
        $doc = [regex]::Replace($doc, 'https?://[a-zA-Z0-9_\.\-]+\.xyz', 'https://your-domain.com')
        Set-Content -LiteralPath $_.FullName -Value $doc -Encoding UTF8
    }
}

# 9. 复制离线运行依赖库 runtime/pydeps314
if (-not $SkipRuntime) {
    if (-not (Test-Path -LiteralPath $runtimeDirectory)) {
        throw "常规依赖目录不存在：$runtimeDirectory"
    }
    Write-Host ">>> 正在复制 Python 3.14 常规依赖库..." -ForegroundColor Cyan
    $runtimeTarget = Join-Path $packageRoot 'runtime\pydeps314'
    New-Item -ItemType Directory -Path $runtimeTarget -Force | Out-Null
    
    Get-ChildItem -LiteralPath $runtimeDirectory | ForEach-Object {
        if ($_.Name -eq '__pycache__') { return }
        Copy-Item -Path $_.FullName -Destination $runtimeTarget -Recurse -Force
    }
}

# 10. 脱敏安全核验 (在打包前进行严格扫描)
if ($Sanitize) {
    Write-Host ">>> 正在执行最终脱敏扫描审计..." -ForegroundColor Cyan
    $leakFound = $false

    $checkFiles = Get-ChildItem -LiteralPath $packageRoot -Recurse -File | Where-Object {
        $_.Extension -match '(?i)\.(json|py|sh|bat|md|txt)' -and $_.Length -lt 20MB
    }
    foreach ($f in $checkFiles) {
        $content = Get-Content -LiteralPath $f.FullName -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
        if ($content -match 'sk-(?:ws-)?[A-Za-z0-9_\-\.]{20,}') {
            Write-Host "ERROR: 发现泄漏的 API KEY: $($f.FullName)" -ForegroundColor Red
            $leakFound = $true
        }
        if ($content -match '\b(?:14[0-9]|15[0-9])\.\d{1,3}\.\d{1,3}\.\d{1,3}\b' -and $f.Name -notmatch '\.example') {
            Write-Host "ERROR: 发现泄漏的公网 IP: $($f.FullName)" -ForegroundColor Red
            $leakFound = $true
        }
        if ($content -match 'https?://[a-zA-Z0-9\-]+\.xyz') {
            Write-Host "ERROR: 发现泄漏的私有域名: $($f.FullName)" -ForegroundColor Red
            $leakFound = $true
        }
    }
    if ($leakFound) {
        throw '脱敏扫描未通过，存在敏感信息泄漏！打包中止。'
    } else {
        Write-Host ">>> 脱敏扫描全部通过：无 API 密钥、无 VPS 凭证、无敏感 IP 域名泄漏。" -ForegroundColor Green
    }
}

# 11. 生成校验清单
Write-Host ">>> 正在计算文件校验指纹清单 (SHA256)..." -ForegroundColor Cyan
$manifestPath = Join-Path $packageRoot 'PACKAGE_MANIFEST_SHA256.txt'
$manifestLines = @(
    "Package: $packageName",
    "Created: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
    "Source: $projectDirectory",
    "Sanitized: $Sanitize",
    "Runtime included: $(-not $SkipRuntime)",
    '',
    'SHA256  RelativePath'
)
Get-ChildItem -LiteralPath $packageRoot -Recurse -File | Where-Object {
    $_.FullName -ne $manifestPath
} | Sort-Object FullName | ForEach-Object {
    $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
    $relative = $_.FullName.Substring($packageRoot.Length + 1)
    $manifestLines += "$hash  $relative"
}
Set-Content -LiteralPath $manifestPath -Value $manifestLines -Encoding UTF8

# 12. 压缩归档
Write-Host ">>> 正在压缩生成最终备份包：$archivePath" -ForegroundColor Cyan
if (Test-Path -LiteralPath $archivePath) {
    Remove-Item -LiteralPath $archivePath -Force
}
Compress-Archive -LiteralPath $packageRoot -DestinationPath $archivePath -CompressionLevel Optimal -Force
if (-not (Test-Path -LiteralPath $archivePath)) {
    throw '压缩包创建失败。'
}

$archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash
$archiveSize = [math]::Round((Get-Item -LiteralPath $archivePath).Length / 1MB, 1)

# 清理临时构建目录
$resolvedTemp = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
$resolvedBuild = [IO.Path]::GetFullPath($buildRoot)
if ($resolvedBuild.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase)) {
    Remove-Item -LiteralPath $buildRoot -Recurse -Force
}

Write-Host "==========================================" -ForegroundColor Green
Write-Host "打包成功完成！" -ForegroundColor Green
Write-Host "归档文件: $archivePath" -ForegroundColor Green
Write-Host "文件大小: ${archiveSize} MB" -ForegroundColor Green
Write-Host "SHA256  : $archiveHash" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Green

Write-Output "ARCHIVE=$archivePath"
Write-Output "SIZE_MB=$archiveSize"
Write-Output "SHA256=$archiveHash"
