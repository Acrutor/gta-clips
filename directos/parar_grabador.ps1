<#
  Para un grabador de forma limpia: deja de grabar, envía el último tramo y termina.
  Uso: .\parar_grabador.ps1 djmariio
#>
param([Parameter(Mandatory = $true)][string]$Canal)

$base = (($Canal -replace '^https?://[^/]+/', '') -replace '[^A-Za-z0-9_-]+', '_')
$nombre = 'grabador-' + $base.ToLower()
# Carpeta antigua (sin fecha) y las nuevas por día (<canal>_AAAAMMDD)
Get-ChildItem 'E:\YoutubeClips\datos\directos' -Directory | Where-Object { $_.Name -eq $base -or $_.Name -like "$($base)_*" } |
    ForEach-Object { New-Item -ItemType File -Path (Join-Path $_.FullName 'PARAR') -Force | Out-Null }
Write-Host "Señal de parada enviada a $nombre; esperando a que cierre (puede tardar 1-2 min)..."
docker wait $nombre | Out-Null
docker logs --tail 5 $nombre
docker rm $nombre | Out-Null
Write-Host "Grabador $nombre terminado y eliminado."
