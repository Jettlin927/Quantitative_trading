[CmdletBinding()]
param(
  [Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)]
  [string[]]$Files
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$RepoRootText = $RepoRoot.Path.TrimEnd("\", "/")
$FrontendFiles = @()
$FrontendTouched = $false

function Invoke-Checked {
  param(
    [string]$Command,
    [string[]]$Arguments
  )
  & $Command @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed: $Command $($Arguments -join ' ')"
  }
}

function Invoke-FrontendNpm {
  param([string[]]$Arguments)

  Push-Location $RepoRoot
  try {
    & docker compose exec -T frontend npm @Arguments
    if ($LASTEXITCODE -eq 0) {
      return
    }
    Write-Host "Docker frontend npm unavailable, falling back to local npm..." -ForegroundColor Yellow
  } finally {
    Pop-Location
  }

  $NpmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
  if (-not $NpmCommand) {
    $NpmCommand = Get-Command npm -ErrorAction Stop
  }

  Push-Location (Join-Path $RepoRoot "frontend")
  try {
    Invoke-Checked $NpmCommand.Source $Arguments
  } finally {
    Pop-Location
  }
}

function Invoke-PythonCompile {
  param([string]$Path)

  $CacheFile = Join-Path $RepoRoot ".py_compile_check_$PID.pyc"
  try {
    Invoke-Checked "python" @(
      "-c",
      "import py_compile, sys; py_compile.compile(sys.argv[1], cfile=sys.argv[2], doraise=True)",
      $Path,
      $CacheFile
    )
  } finally {
    Remove-Item -LiteralPath $CacheFile -Force -ErrorAction SilentlyContinue
  }
}

function Test-PowerShellSyntax {
  param([string]$Path)

  $ParseErrors = $null
  $Source = Get-Content -Raw -Encoding UTF8 -LiteralPath $Path
  [void][System.Management.Automation.PSParser]::Tokenize($Source, [ref]$ParseErrors)

  if ($ParseErrors -and $ParseErrors.Count -gt 0) {
    $Message = ($ParseErrors | ForEach-Object { "$($_.Message) at line $($_.Token.StartLine)" }) -join "; "
    throw "PowerShell syntax failed for ${Path}: $Message"
  }
}

function Resolve-RepoPath {
  param([string]$Path)
  if ([System.IO.Path]::IsPathRooted($Path)) {
    return (Resolve-Path -LiteralPath $Path).Path
  }
  return (Resolve-Path -LiteralPath (Join-Path $RepoRoot $Path)).Path
}

function Get-RepoRelativePath {
  param([string]$FullPath)
  if ($FullPath.StartsWith($RepoRootText, [System.StringComparison]::OrdinalIgnoreCase)) {
    return $FullPath.Substring($RepoRootText.Length).TrimStart("\", "/")
  }
  return $FullPath
}

foreach ($File in $Files) {
  $FullPath = Resolve-RepoPath $File
  $RelativePath = Get-RepoRelativePath $FullPath
  $Normalized = $RelativePath -replace "/", "\"
  $Extension = [System.IO.Path]::GetExtension($FullPath).ToLowerInvariant()

  if ($Extension -eq ".py") {
    Write-Host "py_compile $RelativePath"
    Invoke-PythonCompile $FullPath
    continue
  }

  if ($Extension -eq ".ps1") {
    Write-Host "powershell syntax $RelativePath"
    Test-PowerShellSyntax $FullPath
    continue
  }

  if ($Extension -eq ".json") {
    Write-Host "json parse $RelativePath"
    $null = Get-Content -Raw -Encoding UTF8 -LiteralPath $FullPath | ConvertFrom-Json
    continue
  }

  if ($Normalized -eq "docker-compose.yml" -or $Normalized -eq "docker-compose.yaml") {
    Write-Host "docker compose config --quiet"
    Push-Location $RepoRoot
    try {
      Invoke-Checked "docker" @("compose", "config", "--quiet")
    } finally {
      Pop-Location
    }
    continue
  }

  if ($Normalized -like "frontend\*") {
    $FrontendTouched = $true
    if ($Extension -in @(".js", ".jsx")) {
      $FrontendRelative = $FullPath.Substring((Join-Path $RepoRootText "frontend").Length).TrimStart("\", "/")
      $FrontendFiles += ($FrontendRelative -replace "\\", "/")
    }
  }
}

if ($FrontendFiles.Count -gt 0) {
  Write-Host "eslint $($FrontendFiles -join ' ')"
  $LintArgs = @("run", "lint:file", "--") + $FrontendFiles
  Invoke-FrontendNpm $LintArgs
}

if ($FrontendTouched) {
  Write-Host "tsc --noEmit"
  Invoke-FrontendNpm @("run", "typecheck")
  Write-Host "vite build"
  Invoke-FrontendNpm @("run", "build")
}



