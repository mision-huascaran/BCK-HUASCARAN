# Exporta las credenciales de las cuentas E2E para Bruno. Se usa con `.` (source), con
# la ruta del .env en ENV_FILE (`.` no recibe argumentos en sh):
#
#   ENV_FILE=ruta/al/.env . tests/bruno/credenciales-e2e.sh
#
# Del .env (el Secret file HUASCARAN_BACKEND_QA en Jenkins, el mismo que lee el seed
# `sembrar-e2e`) toma SOLO las líneas E2E_*_PASS: nada de cargar el .env entero y ningún
# valor en el log. Los correos son los fijos del seed (app/cli/seed_e2e.py).

if [ ! -f "${ENV_FILE:-}" ]; then
    echo "ERROR: ENV_FILE debe apuntar al .env con las contraseñas E2E_*_PASS" >&2
    return 1 2>/dev/null || exit 1
fi

while IFS='=' read -r clave valor || [ -n "$clave" ]; do
    case "$clave" in
        E2E_DOCENTE_PASS|E2E_SUPERVISOR_PASS|E2E_DIRECTIVO_PASS|E2E_INACTIVO_PASS) ;;
        *) continue ;;
    esac
    # Fin de línea de Windows y comillas alrededor, como las acepta docker compose.
    case "$valor" in *[[:cntrl:]]) valor=${valor%?} ;; esac
    case "$valor" in
        "'"*"'") valor=${valor#"'"}; valor=${valor%"'"} ;;
        '"'*'"') valor=${valor#'"'}; valor=${valor%'"'} ;;
    esac
    export "$clave=$valor"
done < "$ENV_FILE"

faltan=''
for variable in E2E_DOCENTE_PASS E2E_SUPERVISOR_PASS E2E_DIRECTIVO_PASS E2E_INACTIVO_PASS; do
    [ -n "$(printenv "$variable")" ] || faltan="$faltan $variable"
done
if [ -n "$faltan" ]; then
    echo "ERROR: faltan en el .env:$faltan" >&2
    return 1 2>/dev/null || exit 1
fi

export E2E_DOCENTE_USER='soloparaunuso123+e2e.docente@gmail.com'
export E2E_SUPERVISOR_USER='soloparaunuso123+e2e.supervisor@gmail.com'
export E2E_DIRECTIVO_USER='soloparaunuso123+e2e.directivo@gmail.com'
export E2E_INACTIVO_USER='soloparaunuso123+e2e.inactivo@gmail.com'
