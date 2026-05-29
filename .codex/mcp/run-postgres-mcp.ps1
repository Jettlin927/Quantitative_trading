$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Resolve-Path -LiteralPath (Join-Path $scriptDir '..\..')
$envPath = Join-Path $projectRoot '.env'

function Read-DotEnv {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path
    )

    $values = @{}
    if (-not (Test-Path -LiteralPath $Path)) {
        return $values
    }

    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $trimmed = $line.Trim()
        if ($trimmed.Length -eq 0 -or $trimmed.StartsWith('#')) {
            continue
        }

        $separatorIndex = $trimmed.IndexOf('=')
        if ($separatorIndex -lt 1) {
            continue
        }

        $key = $trimmed.Substring(0, $separatorIndex).Trim()
        $value = $trimmed.Substring($separatorIndex + 1).Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }

        $values[$key] = $value
    }

    return $values
}

function Get-EnvValue {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable] $DotEnv,

        [Parameter(Mandatory = $true)]
        [string] $Name,

        [Parameter(Mandatory = $true)]
        [string] $DefaultValue
    )

    if ($DotEnv.ContainsKey($Name) -and -not [string]::IsNullOrWhiteSpace($DotEnv[$Name])) {
        return $DotEnv[$Name]
    }

    $processValue = [Environment]::GetEnvironmentVariable($Name)
    if (-not [string]::IsNullOrWhiteSpace($processValue)) {
        return $processValue
    }

    return $DefaultValue
}

$dotEnv = Read-DotEnv -Path $envPath

$pgHostName = Get-EnvValue -DotEnv $dotEnv -Name 'POSTGRES_HOST' -DefaultValue '127.0.0.1'
$pgPort = Get-EnvValue -DotEnv $dotEnv -Name 'POSTGRES_PORT' -DefaultValue '5432'
$pgDb = Get-EnvValue -DotEnv $dotEnv -Name 'POSTGRES_DB' -DefaultValue 'quant_trading'
$pgUser = Get-EnvValue -DotEnv $dotEnv -Name 'POSTGRES_USER' -DefaultValue 'quant'
$pgPassword = Get-EnvValue -DotEnv $dotEnv -Name 'POSTGRES_PASSWORD' -DefaultValue 'quant_password'

$encodedUser = [System.Uri]::EscapeDataString($pgUser)
$encodedPassword = [System.Uri]::EscapeDataString($pgPassword)
$encodedDb = [System.Uri]::EscapeDataString($pgDb)

$connectionString = 'postgresql://{0}:{1}@{2}:{3}/{4}?mode=readonly' -f $encodedUser, $encodedPassword, $pgHostName, $pgPort, $encodedDb

& npx -y '@sarmadparvez/postgresql-mcp' $connectionString
exit $LASTEXITCODE

