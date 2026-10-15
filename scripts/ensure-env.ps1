param([switch]$SkipNode)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$RequirementsFile = Join-Path $ProjectRoot 'backend\requirements.txt'
$FrontendDir = Join-Path $ProjectRoot 'frontend'
$VitePackage = Join-Path $FrontendDir 'node_modules\vite\package.json'

function Test-Python310OrNewer {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $false }
    try {
        $version = & $Path --version 2>$null
        if ($LASTEXITCODE -ne 0) { return $false }
        if ($version -match 'Python\s+(\d+)\.(\d+)') {
            return [int]$Matches[1] -gt 3 -or ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -ge 10)
        }
    } catch {}
    return $false
}

function Find-SystemPython {
    $candidates = @()
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        try {
            $candidate = & $py.Source -3 -c 'import sys; print(sys.executable)' 2>$null | Select-Object -First 1
            if ($LASTEXITCODE -eq 0) { $candidates += [string]$candidate }
        } catch {}
    }
    foreach ($name in @('python.exe', 'python3.exe')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) { $candidates += $command.Source }
    }
    foreach ($candidate in $candidates | Select-Object -Unique) {
        if ($candidate -match '\\WindowsApps\\') { continue }
        if (Test-Python310OrNewer -Path $candidate) { return $candidate }
    }
    return $null
}

function Test-BackendDependencies {
    param([string]$Python)
    try {
        & $Python -c 'import fastapi, uvicorn, sqlalchemy, multipart, jose, passlib, pandas, openpyxl, httpx, jinja2, aiofiles, dotenv, xlsxwriter' 2>$null
        return $LASTEXITCODE -eq 0
    } catch {}
    return $false
}

function Ensure-PythonEnvironment {
    if (-not (Test-Path -LiteralPath $RequirementsFile)) {
        Write-Host "ERROR: Backend requirements file not found: $RequirementsFile" -ForegroundColor Red
        return $false
    }
    if (-not (Test-Python310OrNewer -Path $VenvPython)) {
        $systemPython = Find-SystemPython
        if (-not $systemPython) {
            Write-Host 'ERROR: Python 3.10+ was not found. Install Python and add it to PATH.' -ForegroundColor Red
            return $false
        }
        $venvDir = Join-Path $ProjectRoot '.venv'
        if (Test-Path -LiteralPath $venvDir) {
            Write-Host 'Existing .venv cannot run. Rebuilding...'
            Remove-Item -LiteralPath $venvDir -Recurse -Force
        } else {
            Write-Host 'Creating Python virtual environment...'
        }
        & $systemPython -m venv $venvDir
        if ($LASTEXITCODE -ne 0 -or -not (Test-Python310OrNewer -Path $VenvPython)) {
            Write-Host 'ERROR: Failed to create Python virtual environment.' -ForegroundColor Red
            return $false
        }
    }
    if (-not (Test-BackendDependencies -Python $VenvPython)) {
        Write-Host 'Installing backend Python dependencies...'
        & $VenvPython -m pip install -r $RequirementsFile | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Write-Host 'ERROR: pip install failed. Check network, Python, and requirements.txt.' -ForegroundColor Red
            return $false
        }
        if (-not (Test-BackendDependencies -Python $VenvPython)) {
            Write-Host 'ERROR: Backend imports still fail after dependency installation.' -ForegroundColor Red
            return $false
        }
    }
    return $true
}

function Ensure-FrontendEnvironment {
    if ($SkipNode) { return $true }
    $node = Get-Command node.exe -ErrorAction SilentlyContinue
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $node -or -not $npm) {
        Write-Host 'ERROR: Node.js/npm was not found. Install Node.js and add it to PATH.' -ForegroundColor Red
        return $false
    }
    if (-not (Test-Path -LiteralPath $VitePackage)) {
        Write-Host 'Installing frontend npm dependencies...'
        Push-Location $FrontendDir
        try {
            & $npm.Source install | Out-Host
            $npmSucceeded = $LASTEXITCODE -eq 0
        } finally { Pop-Location }
        if (-not $npmSucceeded -or -not (Test-Path -LiteralPath $VitePackage)) {
            Write-Host 'ERROR: npm install failed or Vite is still missing.' -ForegroundColor Red
            return $false
        }
    }
    return $true
}

if (-not (Ensure-PythonEnvironment)) { exit 1 }
if (-not (Ensure-FrontendEnvironment)) { exit 1 }
Write-Host 'Environment check passed.'
exit 0
