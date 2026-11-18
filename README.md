# YoutubeClips — clips de GTA para Shorts y TikTok

Herramienta personal y autoalojada que graba directos y VODs de streamers de GTA, elige los mejores momentos con IA,
los compone en vertical (cámara arriba, juego abajo, subtítulos), genera títulos y miniaturas, y los deja listos en
Google Drive para subirlos; además sube hasta 6 al día a YouTube por la API oficial.

| Carpeta | Qué hace |
|---|---|
| `descubrimiento/` | Busca en Twitch y YouTube los directos y vídeos largos de GTA con más audiencia |
| `directos/` | Orquestador (tarea cada 10 min), grabador de directos por tramos y respaldo con VODs |
| `filtro/` | Filtro de calidad automático (voz, idioma, duplicados, revisión visual con Gemini) |
| `subida/` | Plantilla de streamer, títulos/miniaturas, OAuth y subida a YouTube |
| `archivo/` | Copia los clips finales a Drive por día/streamer y avisa al móvil (ntfy) |
| `.claude/skills/` | Skills `yt-*` (MIT, Jakeschincariol/youtube-agent-skill) |

El procesado de vídeo lo hace [OpenShorts](https://github.com/mutonby/openshorts) (MIT) en Docker, que no se incluye aquí.
Las claves van en un `.env` local y los tokens OAuth en `secretos/`: ninguno de los dos se sube al repositorio.
Más contexto en `CLAUDE.md` y el historial en `Bitacora.md`.
