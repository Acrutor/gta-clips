<#
  Crea (o actualiza) la tarea que sube SOLA a YouTube los clips aprobados (canal ES), por la API oficial:
  - cada 30 min mira si toca subir; como mucho 1 por pasada y 1 cada 1,5-2,5 h (horas distintas cada día)
  - tope de 6 al día (cuota de la API); los de más prioridad (🔥 picante) primero
  - se suben en PÚBLICO (18-nov-2026, decisión del usuario) con título, descripción, etiquetas, crédito y miniatura.
    Si YouTube los fuerza a privado (app sin auditar), subir.log lo dice en "privacidad real".
  Registro: E:\YoutubeClips\datos\subir.log

  Uso:           E:\YoutubeClips\subida\crear_tarea_subida.ps1
  Para quitarla: schtasks /Delete /TN "YoutubeClips\Subir a YouTube" /F
#>
$py = "C:\Users\Adrian\AppData\Local\Programs\Python\Python314\pythonw.exe"
if (-not (Test-Path $py)) { Write-Host "No encuentro pythonw.exe en $py"; exit 1 }
$accion = "`"$py`" -I -X utf8 `"E:\YoutubeClips\subida\subir.py`" --subir --max-por-pasada 1 --min-intervalo 90 --max-dia 6 --privacidad public"
schtasks /Create /F /SC MINUTE /MO 30 /TN "YoutubeClips\Subir a YouTube" /TR $accion
if ($LASTEXITCODE -eq 0) {
    schtasks /Run /TN "YoutubeClips\Subir a YouTube" | Out-Null
    Write-Host "Tarea creada y lanzada. Actividad en E:\YoutubeClips\datos\subir.log"
}
