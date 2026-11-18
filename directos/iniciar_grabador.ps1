<#
  Lanza el grabador de directos por tramos en un contenedor (imagen de OpenShorts).

  Ejemplos:
    .\iniciar_grabador.ps1 djmariio                       # tramos de 20 min, solape 2, envía a OpenShorts
    .\iniciar_grabador.ps1 djmariio -Extra '--tramo-min','10'
    .\iniciar_grabador.ps1 vicens -Extra '--no-procesar','--max-tramos','1','--tramo-min','4'   # prueba

  Ver progreso:  docker logs -f grabador-<canal>
  Parar limpio:  .\parar_grabador.ps1 <canal>   (envía el último tramo y termina)
#>
param(
    [Parameter(Mandatory = $true)][string]$Canal,
    [ValidateSet('es', 'en')][string]$Idioma = 'es',
    [string[]]$Extra = @()
)

$nombre = 'grabador-' + (($Canal -replace '^https?://[^/]+/', '') -replace '[^A-Za-z0-9_-]', '_').ToLower()
$existe = docker ps -a --filter "name=^$nombre$" --format '{{.Names}}'
if ($existe) {
    Write-Host "Ya existe el contenedor $nombre. Míralo con: docker logs -f $nombre  (o bórralo con: docker rm -f $nombre)"
    exit 1
}

$args_ = @(
    'run', '-d', '--name', $nombre,
    '--network', 'openshorts_default',
    '--restart', 'on-failure:3',
    '-v', 'E:/YoutubeClips:/yc',
    '--entrypoint', 'python',
    'openshorts-backend',
    '-u', '/yc/directos/grabar_directo.py', $Canal, '--idioma', $Idioma
) + $Extra

$id = docker @args_
if ($LASTEXITCODE -eq 0) {
    Write-Host "Grabador iniciado: $nombre ($($id.Substring(0,12)))"
    Write-Host "Progreso: docker logs -f $nombre"
} else {
    Write-Host "No se pudo iniciar el grabador."
}
