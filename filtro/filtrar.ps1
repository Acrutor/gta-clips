<#
  Ejecuta el filtro de calidad dentro de un contenedor efímero (trae python, ffmpeg y requests).
  Ejemplos:
    .\filtrar.ps1 --todos                          # evalúa los clips de todos los directos grabados
    .\filtrar.ps1 29330ce7-6d80-4854-8013-c0a51f6d77a5 --reevaluar
    .\filtrar.ps1 --todos --sin-gemini             # sin llamar a Gemini (no gasta cuota)
#>
docker run --rm --network openshorts_default -v 'E:/YoutubeClips:/yc' --entrypoint python `
    openshorts-backend -u /yc/filtro/filtrar.py @args
