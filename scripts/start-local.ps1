<#
.SYNOPSIS
    本地一键启动：后端（FastAPI/uvicorn）+ 可选前端（Vite），并把真实执行需要的
    SAGE_* 环境变量按本机路径配好。

.DESCRIPTION
    应用不读取 .env 文件（只读进程环境变量），因此这里显式设置环境变量再启动。
    默认配置与 `docs/handoff/benchmark-cases.md` 里的评测口径一致：
    规则链 best66 × d3ad0ne、词表 81 条、掩码阶梯与混合掩码、自适应切片开启。

.EXAMPLE
    .\scripts\start-local.ps1                      # 只起后端（http://127.0.0.1:8000）
    .\scripts\start-local.ps1 -WithFrontend        # 同时起前端（http://127.0.0.1:5173）
    .\scripts\start-local.ps1 -NoSlicing           # 关掉自适应切片（对照旧行为）
#>
[CmdletBinding()]
param(
    [string]$HashcatPath = "F:\SA\tools\hashcat-7.1.2\hashcat.exe",
    [string]$JohnRun = "F:\SA\tools\john\john-1.9.0-jumbo-1-win64\run",
    [string]$Wordlist = ".\data\wordlists\base-words.txt",
    [string]$SeedWordlists = ".\data\wordlists\zh-base.txt",
    [string]$RuleFiles = "best66.rule,d3ad0ne.rule",
    [string]$Scheduler = "heuristic_bandit",
    [int]$Port = 8000,
    [switch]$WithFrontend,
    [switch]$NoSlicing,
    [switch]$Stop
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"

function Stop-Listening([int]$p) {
    $conns = Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue
    foreach ($pid_ in ($conns | Select-Object -ExpandProperty OwningProcess -Unique)) {
        Write-Host "停止端口 $p 上的进程 $pid_"
        Stop-Process -Id $pid_ -Force -ErrorAction SilentlyContinue
    }
}

if ($Stop) {
    Stop-Listening $Port
    Stop-Listening 5173
    return
}

if (-not (Test-Path $python)) {
    throw "找不到虚拟环境 Python：$python（先按 README『本地启动』创建 .venv 并安装依赖）"
}

$rulesDir = Join-Path (Split-Path -Parent $HashcatPath) "rules"
$resolvedRules = ($RuleFiles.Split(",") | ForEach-Object { Join-Path $rulesDir $_.Trim() }) -join ","
# 词表类路径统一解析成绝对路径：后端进程的工作目录不一定是仓库根目录
$resolvedWordlist = [System.IO.Path]::GetFullPath((Join-Path $repo $Wordlist))
$resolvedSeeds = ($SeedWordlists.Split(",") | ForEach-Object {
        [System.IO.Path]::GetFullPath((Join-Path $repo $_.Trim()))
    }) -join ","

$env:SAGE_HASHCAT_PATH = $HashcatPath
$env:SAGE_ZIP2JOHN_PATH = Join-Path $JohnRun "zip2john.exe"
$env:SAGE_PLANNER_TYPE = "rule"
$env:SAGE_SCHEDULER_TYPE = $Scheduler
$env:SAGE_WORDLIST_PATH = $resolvedWordlist
$env:SAGE_RULES_PATH = Join-Path $rulesDir "best66.rule"
$env:SAGE_WORDLIST_RULES = $resolvedRules
$env:SAGE_SEED_WORDLISTS = $resolvedSeeds
$env:SAGE_MASK_LADDER = "?d?d?d?d,?l?l?l?l,?l?l?l?l?d?d"
$env:SAGE_HYBRID_MASKS = "?d?d?d?d,?d?d,!"
$env:SAGE_ADAPTIVE_SLICING = if ($NoSlicing) { "false" } else { "true" }
$env:SAGE_DECISION_BATCH_SIZE = "100000"
$env:SAGE_HASHCAT_STREAM_BATCH_SIZE = "100000"
$env:SAGE_STOP_ON_HIT = "false"

Write-Host "== 配置 =="
Write-Host "  hashcat     : $env:SAGE_HASHCAT_PATH"
Write-Host "  词表        : $env:SAGE_WORDLIST_PATH"
Write-Host "  规则链      : $env:SAGE_WORDLIST_RULES"
Write-Host "  种子词表    : $env:SAGE_SEED_WORDLISTS"
Write-Host "  调度器      : $env:SAGE_SCHEDULER_TYPE"
Write-Host "  自适应切片  : $env:SAGE_ADAPTIVE_SLICING"
Write-Host ""

Stop-Listening $Port
Push-Location $repo
try {
    Start-Process -FilePath $python `
        -ArgumentList "-m", "uvicorn", "sage_pass.main:app", "--app-dir", "src", "--port", "$Port" `
        -WorkingDirectory $repo -WindowStyle Hidden
    Start-Sleep -Seconds 3
    $health = try { (Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/health" -TimeoutSec 10).StatusCode } catch { $null }
    if ($health -ne 200) { throw "后端未就绪：/health 返回 $health" }
    Write-Host "后端已启动：http://127.0.0.1:$Port  （Swagger: http://127.0.0.1:$Port/docs）"

    if ($WithFrontend) {
        Stop-Listening 5173
        Push-Location (Join-Path $repo "frontend")
        try {
            Start-Process -FilePath "npm" -ArgumentList "run", "dev" -WorkingDirectory (Join-Path $repo "frontend") -WindowStyle Hidden
            Start-Sleep -Seconds 6
            Write-Host "前端已启动：http://127.0.0.1:5173"
        } finally { Pop-Location }
    }
} finally { Pop-Location }
