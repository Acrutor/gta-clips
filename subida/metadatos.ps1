# Genera título con gancho + miniatura para los clips aprobados que aún no los tienen (corre en un contenedor).
docker run --rm -v 'E:/YoutubeClips:/yc' --entrypoint python openshorts-backend -u /yc/subida/metadatos.py @args
