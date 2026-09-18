<#
  Inicia el control remoto del TV.
  Uso:  .\start-tv-remote.ps1 [-Tv 192.168.20.110:5555] [-Lan] [-NoBrowser]
#>
param(
    [string]$Tv = "192.168.20.110:5555",
    [int]$Port = 8080,
    [switch]$Lan,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot

if (-not (Get-Command adb -ErrorAction SilentlyContinue)) { throw "adb no está en el PATH." }
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "python no está en el PATH." }

# Conectar al TV (si ya estaba conectado, adb lo indica y sigue)
Write-Host "Conectando a $Tv ..."
adb connect $Tv | Out-Host
$state = (adb -s $Tv get-state 2>&1) -join ""
if ($state -notmatch "device") { throw "No se pudo conectar al TV ($state). Revisa la IP y la depuración ADB." }

# Liberar el puerto si quedó una instancia anterior
Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }

# Apache (XAMPP) sirve la interfaz; se avisa si no está corriendo
if (-not (Get-Process httpd -ErrorAction SilentlyContinue)) {
    Write-Warning "Apache no está corriendo. Inicia Apache desde el panel de XAMPP para usar http://localhost/tv-remote/"
}

$srvArgs = @("$here\server.py", "--tv", $Tv, "--port", $Port)
if ($Lan) { $srvArgs += "--lan" }

Write-Host "Iniciando servidor (Ctrl+C para detenerlo) ..."
if (-not $NoBrowser -and -not $Lan) {
    Start-Job { Start-Sleep 2; Start-Process "http://localhost/tv-remote/" } | Out-Null
}
& python @srvArgs
