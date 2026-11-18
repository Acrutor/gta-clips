<#
  Crea (o actualiza) la tarea programada que cada 10 minutos mantiene grabando a los streamers de GTA más vistos
  hasta llegar al objetivo diario de clips (directos\config.json). Usuario actual, sin admin, sin ventanas.
  Registro: E:\YoutubeClips\datos\orquestador.log

  Uso:          E:\YoutubeClips\directos\crear_tarea_orquestador.ps1
  Para quitarla: schtasks /Delete /TN "YoutubeClips\Orquestador de directos" /F
#>
$py = "C:\Users\Adrian\AppData\Local\Programs\Python\Python314\pythonw.exe"
if (-not (Test-Path $py)) { Write-Host "No encuentro pythonw.exe en $py"; exit 1 }
$accion = "`"$py`" -I -X utf8 `"E:\YoutubeClips\directos\orquestador.py`""
schtasks /Create /F /SC MINUTE /MO 10 /TN "YoutubeClips\Orquestador de directos" /TR $accion
if ($LASTEXITCODE -eq 0) {
    schtasks /Run /TN "YoutubeClips\Orquestador de directos" | Out-Null
    Write-Host "Tarea creada y lanzada. Actividad en E:\YoutubeClips\datos\orquestador.log"
}
