#!/bin/sh
# Ajusta los permisos del volumen de datos (Easypanel y otros paneles lo montan como carpeta de root) y
# ejecuta el Agente Central con el usuario sin privilegios "agente".
set -e
if [ "$(id -u)" = "0" ]; then
    mkdir -p /app/datos
    chown -R agente:agente /app/datos
    exec setpriv --reuid=agente --regid=agente --init-groups "$@"
fi
exec "$@"
