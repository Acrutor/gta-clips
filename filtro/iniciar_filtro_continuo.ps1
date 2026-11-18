<#
  Deja el filtro de calidad corriendo en segundo plano: cada 2 minutos evalúa los clips nuevos
  de todos los directos grabados (los ya decididos se saltan; los «pendientes» por fallo de Gemini se reintentan)
  arregla la cámara del streamer si quedó cortada (subida\camara.py) y genera título con gancho + miniatura
  para los aprobados (subida\metadatos.py).
  Ver actividad:  docker logs -f filtro-continuo
  Parar:          docker rm -f filtro-continuo
#>
$existe = docker ps -a --filter "name=^filtro-continuo$" --format '{{.Names}}'
if ($existe) { Write-Host "Ya existe filtro-continuo (docker rm -f filtro-continuo para recrearlo)."; exit 1 }
docker run -d --name filtro-continuo --restart unless-stopped --network openshorts_default `
    -v 'E:/YoutubeClips:/yc' --entrypoint sh openshorts-backend `
    -c 'while true; do python -u /yc/filtro/filtrar.py --todos; python -u /yc/subida/camara.py; python -u /yc/subida/metadatos.py; sleep 120; done' | Out-Null
Write-Host "Filtro continuo iniciado. Ver: docker logs -f filtro-continuo"
