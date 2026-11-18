<#
  Crea (o actualiza) la tarea programada que copia a Google Drive cada 5 minutos lo ya terminado:
  G:\Mi unidad\Clips YOutube GTA\<día>\<streamer>\{fragmentos, clips, aprobados}
  Se ejecuta con tu usuario, sin permisos de administrador y sin abrir ventanas (pythonw).
  Registro de actividad: E:\YoutubeClips\datos\archivar.log

  Uso (en PowerShell):  E:\YoutubeClips\archivo\crear_tarea_archivar.ps1
  Para quitarla:        schtasks /Delete /TN "YoutubeClips\Archivar en Drive" /F
  Sin fragmentos de 20 min (ahorra ~3,5 GB/hora de directo en Drive): añade -SinFragmentos
#>
param([switch]$SinFragmentos)

$py = "C:\Users\Adrian\AppData\Local\Programs\Python\Python314\pythonw.exe"
if (-not (Test-Path $py)) { Write-Host "No encuentro pythonw.exe en $py"; exit 1 }
$extra = if ($SinFragmentos) { ' --sin-fragmentos' } else { '' }
$accion = "`"$py`" -I -X utf8 `"E:\YoutubeClips\archivo\archivar.py`"$extra"

schtasks /Create /F /SC MINUTE /MO 5 /TN "YoutubeClips\Archivar en Drive" /TR $accion
if ($LASTEXITCODE -eq 0) {
    schtasks /Run /TN "YoutubeClips\Archivar en Drive" | Out-Null
    Write-Host "Tarea creada y lanzada. Actividad en E:\YoutubeClips\datos\archivar.log"
}
