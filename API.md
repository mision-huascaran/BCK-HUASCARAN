# SICEDU API — Referencia para el frontend y las pruebas

**Rama:** `development` · **Base URL (desarrollo):** `http://127.0.0.1:8000` · **Swagger:** `/docs`
**Formato:** JSON · **Autenticación:** JWT Bearer con sesión en la BD

> **Verificado el 2026-10-29** contra el OpenAPI generado por la app (`app.openapi()`) y el código de los
> servicios, al cierre de la **Tanda M** (ronda 6 de QA, segunda parte, sobre el Dashboard D7 `f098640`). Cubre
> los 101 endpoints que existen hoy. La Tanda M no cambia la forma de ninguna respuesta: agrega `422 sin_alumnos`
> a `GET /lectura/tablas` (D101) y dos decisiones provisionales, DP-1 (escritura de la rúbrica en un mes partido
> entre dos PA) y DP-2 (vencidos del Inicio del Supervisor durante un receso), descritas en
> `docs/decisiones-provisionales.md`.
>
> **Errores:** todo error de negocio responde `{"detail": "...", "motivo": "codigo", ...extra}`. Los `422`
> de validación responden `{"detail": "Los datos enviados no son válidos.", "motivo": "validacion",
> "errores": [{"campo": "...", "mensaje": "..."}]}`; un `422` de una regla sobre un campo usa el mismo formato
> con el mensaje de la regla en `detail`. **Los mensajes de los casos de uso llegan en `detail` tal cual, sin
> prefijos:** mostrarlos sin agregarles el nombre del campo.

---

## Índice

- [Convenciones](#convenciones)
- [Roles y matriz de permisos](#roles-y-matriz-de-permisos)
- [Autenticación y sesiones](#autenticación-y-sesiones)
- [Contraseñas y PIN](#contraseñas-y-pin)
- [Cuentas de usuario](#cuentas-de-usuario)
- [Asignaciones](#asignaciones)
- [Permisos de corrección](#permisos-de-corrección)
- [Colegios](#colegios)
- [Calendarios](#calendarios)
- [Catálogos](#catálogos)
- [Alumnos](#alumnos)
- [Clases y reprogramaciones](#clases-y-reprogramaciones)
- [Asistencias](#asistencias)
- [Seguimiento de lectura](#seguimiento-de-lectura)
- [Rúbrica mensual](#rúbrica-mensual)
- [Registro de vuelo](#registro-de-vuelo)
- [Actividades de trabajo](#actividades-de-trabajo)
- [Sincronización](#sincronización)
- [Inicio por rol](#inicio-por-rol)
- [Seguimiento de docentes](#seguimiento-de-docentes)
- [Dashboard (CU033, CU034 y CU035)](#dashboard-cu033-cu034-y-cu035)
- [Errores comunes](#errores-comunes)
- [CORS](#cors)
- [Usuarios de prueba](#usuarios-de-prueba)
- [Levantar el backend](#levantar-el-backend)

---

## Convenciones

- **Fechas:** `fecha` y los días son `AAAA-MM-DD` en hora de Lima. Los instantes (`inicio`, `fin`,
  `realizado_en`...) son ISO 8601 **con zona** (`2026-10-26T10:00:00-05:00` o con `Z`); las respuestas
  salen en UTC.
- **Paginación:** los listados paginados reciben `?page=` (desde 1; `0` es `422`) y responden siempre en
  páginas de **40**:

  ```json
  { "items": [], "total": 0, "page": 1, "page_size": 40, "total_pages": 0 }
  ```

  No existen `limit` ni `offset`.
- **Orden de los listados:** los nombres de personas, colegios y secciones se ordenan **sin distinguir
  mayúsculas ni tildes** ("ALVA", "álvarez", "Ávila", "Ñahui"), con un desempate estable por id. El front no
  debe reordenar en el cliente.
- **`PATCH`:** omitir un campo es "no cambiarlo"; enviarlo en `null` es `422` (salvo los campos que se pueden
  vaciar, como `distrito`).
- **Escrituras de los módulos de registro** (asistencias, lectura, rúbrica, registro de vuelo): cada ítem es
  una **operación idempotente** con `id_operacion` (UUID del cliente), `version` (la leída; `null` si es
  nuevo), `realizado_en` (hora real, para las hechas sin conexión) e `id_actividad`. El lote responde `200`
  con un resultado por operación:

  ```json
  { "id_operacion": "…", "estado": "aceptada", "motivo": null, "detail": null, "registro": { } }
  ```

  `estado: "rechazada"` trae `motivo` y `detail` (el mensaje para mostrar). Reenviar el mismo
  `id_operacion` devuelve el mismo resultado sin volver a escribir.
- **Actividad de trabajo:** el Docente solo escribe con una actividad en curso. Sin ella, el mensaje es
  "Debes iniciar una actividad de trabajo para registrar o modificar información." con `motivo:
  actividad_requerida`: como `409` en Alumnos y como rechazo de la operación (o `422`, en las que no van en
  lote) en los módulos de registro. El Supervisor no la necesita.

---

## Roles y matriz de permisos

| `id_rol` | Nombre       |
| -------- | ------------ |
| `1`      | `Docente`    |
| `2`      | `Supervisor` |
| `3`      | `Directivo`  |

- El **Supervisor** gestiona cuentas, colegios, calendarios, asignaciones y permisos, y consulta todo.
- El **Docente** registra en los módulos académicos, solo en lo que tiene habilitado (sus asignaciones o un
  permiso de corrección) y con una actividad en curso. Consulta lo de su alcance.
- El **Directivo** ve su Inicio y los bloques agregados del Dashboard (indicadores institucionales, sin datos
  personales de alumnos).

Un rol fuera de la lista responde `403 sin_permiso` ("No tienes permisos para realizar esta acción").

| Endpoints | Docente | Supervisor | Directivo |
| --- | :-: | :-: | :-: |
| `POST /login`, `/password/*`, `POST /logout`, `GET /` | público | público | público |
| `GET /me`, `POST /me/password*`, `POST /me/sesiones/{id}/cerrar` | ✅ | ✅ | ✅ |
| Catálogos: `GET /anios-escolares`, `/grados`, `/programas`, `/periodos-academicos`, `/niveles-razkids`, `/niveles-rubrica` | ✅ | ✅ | ✅ |
| `GET /semanas` | ✅ * | ✅ | ❌ |
| `/usuarios` (todo), `GET /asignaciones`, `GET /permisos/opciones`, `/usuarios/{id}/permisos` | ❌ | ✅ | ❌ |
| `GET /colegios`, `/colegios/ubicaciones`, `/colegios/{id}`, `/colegios/{id}/clases` | ✅ * | ✅ | ❌ |
| `POST`/`PATCH /colegios…`, `GET /colegios/estado-calendario`, reprogramar clases | ❌ | ✅ | ❌ |
| `GET /calendarios`, `GET /calendarios/periodo`, `GET /clases/{id}/reprogramaciones` | ✅ * | ✅ | ❌ |
| `POST`/`PUT /calendarios…` | ❌ | ✅ | ❌ |
| `/alumnos` (lectura y escritura) | ✅ * | ✅ | ❌ |
| `GET /asistencias`, `/lectura/tablas`, `/rubricas`, `/rubricas/criterios`, `/registro-vuelo` | ✅ * | ✅ (solo lectura) | ❌ |
| `PUT /asistencias`, escrituras de lectura, `PUT /rubricas`, `PUT /registro-vuelo` | ✅ * | ❌ | ❌ |
| `/actividades…`, `/sincronizacion…`, `GET /me/asignaciones`, `/me/habilitaciones`, `/me/permisos` | ✅ | ❌ | ❌ |
| `GET /inicio/docente` · `/inicio/supervisor` · `/inicio/directivo` | solo el suyo | solo el suyo | solo el suyo |
| `/seguimiento/docentes…` | ❌ | ✅ | ❌ |
| `GET /dashboard/filtros`, `/resultados-aprendizaje`, `/asistencia`, `/libros`, `/evolucion-anual`, `/comparacion-colegios` | ❌ | ✅ | ✅ |
| `GET /dashboard/distribucion-subprograma`, `/comparacion-resultados`, `/asistencia/clases`, `/libros/colegios/{id}`, `/libros/desglose` | ❌ | ✅ | ❌ |

\* Solo su alcance: los colegios y grados de sus asignaciones (y de sus permisos vigentes, para consultar).
Fuera de él: `403 aula_fuera_de_alcance`, `alumno_fuera_de_alcance` o `colegio_fuera_de_alcance`.

---

## Autenticación y sesiones

1. `POST /login` abre una **sesión de 8 horas** y devuelve el `access_token`.
2. Cada petición protegida lleva `Authorization: Bearer <access_token>`. El token vale solo mientras su
   sesión siga abierta y vigente en la BD.
3. `POST /logout` cierra esa sesión en el servidor.

### `GET /` — público

Salud del backend: `{ "status": "ok" }`.

### `POST /login` — público

```json
{ "correo": "profesor.prueba@sicedu.test", "password": "ProfesorTest123" }
```

**Respuesta `200`:**

```json
{
  "access_token": "eyJhbGciOi…",
  "token_type": "bearer",
  "sesion": { "id": "…uuid…", "inicio": "2026-10-26T15:00:00Z", "expira": "2026-10-26T23:00:00Z" },
  "usuario": { "id": 4, "nombres": "Rosa", "apellidos": "Quispe", "rol": "Docente", "es_supervisor_original": false }
}
```

**Errores:**

| Código | `motivo` | `detail` |
| --- | --- | --- |
| `401` | `credenciales_invalidas` | "Correo o contraseña incorrectos." |
| `403` | `cuenta_desactivada` | "Tu cuenta se encuentra desactivada. Comunícate con el Supervisor para solicitar su habilitación." |
| `403` | `cambio_contrasena_requerido` | "Debes establecer una nueva contraseña para continuar." (usar `POST /password/primer-ingreso`) |
| `403` | `contrasena_temporal_expirada` | "Tu contraseña temporal ha expirado. Usa '¿Olvidaste tu contraseña?' para generar una nueva." |
| `429` | `bloqueo_temporal` | "Demasiados intentos fallidos. Por seguridad, inténtalo nuevamente en 15 minutos." |

El aviso de cuenta desactivada solo se muestra mientras no se alcanzó el bloqueo; los intentos con la cuenta
desactivada también cuentan como fallos.

### `GET /me` — autenticado

```json
{
  "id_usuario": 4, "id_rol": 1, "correo": "profesor.prueba@sicedu.test", "id_docente": 1, "dni": "00000002",
  "nombres": "Rosa", "apellidos": "Quispe", "activo": true, "es_supervisor_original": false,
  "sesion": { "inicio": "2026-10-26T15:00:00Z", "expira": "2026-10-26T23:00:00Z" }
}
```

### `POST /logout` — público (con token)

Cierra la sesión del token. Responde **`204`** sin cuerpo, también si la sesión ya estaba cerrada, venció o no
existe. Solo exige un token con firma válida (si no, `401`).

### `POST /me/sesiones/{id_sesion}/cerrar` — autenticado

Registra un "Cerrar sesión" hecho **sin conexión** en una sesión anterior del usuario (CU008). Se llama con el
token de la sesión nueva, después del nuevo login.

**Body:** `{ "fin": "2026-10-26T18:20:00-05:00" }` (hora real del cierre) **o** `{ "fin_minimo": "…" }` (si
no hay continuidad del reloj), nunca los dos.

**Respuesta `200`:**

```json
{
  "sesion": { "id": "…", "inicio": "…", "expira": "…", "fin": "…", "tipo_cierre": "Manual", "fin_verificado": true },
  "actividad": { "id": "…", "id_sesion": "…", "inicio": "…", "fin": "…",
                 "tipo_cierre": "Forzado por cierre de sesión", "estado": "finalizada" }
}
```

La actividad activa (o cerrada por expiración) queda "Forzado por cierre de sesión"; una que el docente ya
finalizó no se toca. Idempotente. Con `fin_minimo`, el cierre queda en el mayor entre `fin_minimo` y la última
petición recibida de esa sesión, con `fin_verificado: false`.

**Errores `422`:** `id_sesion_invalido` (inexistente o de otro usuario), `sesion_actual` (es la del token: usar
`POST /logout`), `fin_invalido` (fuera de la sesión o en el futuro), `validacion`.

---

## Contraseñas y PIN

El PIN es de 6 dígitos, vence a los **15 minutos** y admite **5 intentos**. Errores del PIN (`400`):

| `motivo` | `detail` |
| --- | --- |
| `incorrecto` | "El código ingresado es incorrecto. Inténtalo nuevamente." |
| `expirado` | "El código ha expirado. Solicita un nuevo PIN." |
| `intentos_agotados` | "Has agotado los intentos permitidos. Solicita un nuevo PIN para continuar." |

La contraseña nueva sigue la política de CU006 (`422 validacion` con el detalle en `errores`) y debe ser
distinta de la anterior (`400 password_igual`, "La nueva contraseña debe ser diferente de la contraseña
anterior."). Un fallo al guardarla es `500 error_actualizacion` ("Error al actualizar la contraseña. Inténtalo
nuevamente.").

### `POST /password/primer-ingreso` — público

Primer ingreso de una cuenta con contraseña temporal (CU001, CU016): fija la definitiva y abre la sesión
(responde igual que `POST /login`).

```json
{ "correo": "…", "contraseña_temporal": "…", "contraseña_nueva": "…", "confirmar_contraseña_nueva": "…" }
```

**Errores:** `401 credenciales_invalidas` (cuenta inexistente, inactiva, sin cambio pendiente o temporal
incorrecta), `403 contrasena_temporal_expirada`, `422 validacion`, `400 password_igual`, `429 bloqueo_temporal`.

### `POST /password/recuperar` — público

`{ "correo": "…" }`. Responde **siempre** `200` con `{ "mensaje": "Si el correo está registrado y activo,
recibirás un PIN." }`, exista o no la cuenta, y el correo sale en segundo plano.

### `POST /password/verificar-codigo` — público

`{ "correo": "…", "codigo": "123456" }` → `{ "valido": true }`. Valida el PIN **sin consumirlo**. Errores: los
del PIN.

### `POST /password/restablecer` — público

`{ "correo", "codigo", "contraseña_nueva", "confirmar_contraseña_nueva" }` → `{ "mensaje": "…" }`. Cambia la
contraseña y **cierra todas las sesiones** de la cuenta. Errores: los del PIN, `422 validacion`,
`400 password_igual`.

### `POST /me/password/codigo` — autenticado

Sin body. Envía un PIN al correo de la cuenta: `{ "mensaje": "Se envió un PIN a tu correo." }`.

**Errores:** `429 limite_solicitudes` ("Se alcanzó el límite de solicitudes de PIN. Inténtalo nuevamente más
tarde."), `503 envio_fallido` (no salió el correo).

### `POST /me/password/verificar-codigo` — autenticado

`{ "codigo": "123456" }` → `{ "valido": true }`.

### `POST /me/password` — autenticado

`{ "codigo", "contraseña_nueva", "confirmar_contraseña_nueva" }` → `{ "mensaje": "…" }`. Cierra las demás
sesiones del usuario.

---

## Cuentas de usuario

Solo el **Supervisor**, para los tres roles (CU016).

### `GET /usuarios`

**Query:** `rol` (`Docente` | `Supervisor` | `Directivo`), `activo` (por defecto `true`), `q`, `page`.

- **`q`** busca por nombre **o** correo: cada palabra en "nombres apellidos" (sin mayúsculas ni tildes, en
  cualquier orden: `perez` encuentra "Pérez") o el texto en el correo. `%` y `_` se buscan literalmente.
- **Orden:** Docentes, Supervisores y Directivos; dentro de cada grupo, por apellidos y nombres sin distinguir
  mayúsculas ni tildes, y por id si empatan.

**Respuesta `200`** (paginada):

```json
{
  "items": [{
    "id": 4, "nombres": "Rosa", "apellidos": "Quispe", "dni": "40000003", "correo": "rosa@sicedu.test",
    "rol": "Docente", "activo": true, "es_supervisor_original": false, "debe_cambiar_contrasena": false,
    "colegios_asignados": ["AMASHCA", "HUARAZ"]
  }],
  "total": 1, "page": 1, "page_size": 40, "total_pages": 1
}
```

`colegios_asignados`: para un Docente, los colegios de sus asignaciones del PA en que hoy está cada colegio;
para Supervisor y Directivo, `["Global"]`. `debe_cambiar_contrasena`: todavía no hizo su primer ingreso.

### `POST /usuarios`

```json
{
  "nombres": "Ana", "apellidos": "Torres", "dni": "45678912", "correo": "ana.torres@sicedu.test", "id_rol": 1,
  "asignacion": { "id_anio_escolar": 2, "colegios": [{ "id_colegio": 1, "grados": [1, 2] }] }
}
```

`asignacion` es obligatoria para un Docente y prohibida para los demás roles (ver
[Asignación de un Docente](#asignación-de-un-docente-cu016-d67)). La cuenta nace con una contraseña temporal
de **48 horas** que se envía por correo.

**Respuesta `201`:**

```json
{ "usuario": { "id": 9, "…": "como en GET /usuarios" }, "correo_enviado": true, "contraseña_temporal": null }
```

Si el correo no sale, `correo_enviado` es `false` y la contraseña viene en `contraseña_temporal` (es la única
copia: mostrarla y pedir que se anote).

**Errores:** `409 correo_duplicado`, `409 dni_duplicado`, `422 rol_inexistente`, `422 asignacion_requerida`
("La asignación es obligatoria para un Docente."), `422 asignacion_no_permitida`, `422 validacion` (DNI de 8
dígitos, correo, nombres vacíos), y los de la asignación.

#### Asignación de un Docente (CU016, D67)

Un Docente puede atender **varios colegios** en un año escolar. El mismo objeto se usa en `POST /usuarios` y
en `PATCH /usuarios/{id_usuario}`:

```json
"asignacion": {
  "id_anio_escolar": 2,
  "colegios": [
    {"id_colegio": 1, "grados": [1, 2, 3], "inicio_asignacion": null},
    {"id_colegio": 4, "grados": [5], "inicio_asignacion": "retroactivo"}
  ]
}
```

| Campo | Regla |
| --- | --- |
| `id_anio_escolar` | Obligatorio. |
| `colegios` | Obligatorio, al menos 1. Un `id_colegio` repetido es `422` en `asignacion.colegios.{i}.id_colegio`. |
| `colegios[].grados` | Opcional; si viene, no vacío. Sin él: en un alta, todos los grados del colegio; en una edición, los que el Docente ya tenía en ese colegio en el periodo de inicio (o todos, si no lo tenía). |
| `colegios[].inicio_asignacion` | `"retroactivo"` o `"proximo"`. Obligatorio solo si **ese** colegio está hoy en un PR; si falta, `422 inicio_asignacion_requerido` con `id_colegio`, `colegio`, `periodo` y `errores[].campo` = `asignacion.colegios.{i}.inicio_asignacion`. En un PA o en un año que no empezó se ignora. `"proximo"` en el PR4 es `422` ("Este año escolar ya no tiene otro periodo académico. Para asignar al Docente al siguiente año, selecciona ese año escolar."). |

- El **inicio** de cada colegio sale de su calendario (CU027): año sin empezar, desde el PA1; en un PA, desde
  ese PA; en un PR, lo que diga su `inicio_asignacion`. "Retroactivo" agrega el PA recién culminado
  (habilita regularizarlo solo durante ese PR) y no sustituye a su titular.
- Se valida **todo** (colegios activos, grados, año, calendario, inicio y grados libres) antes de escribir. Si
  un colegio-grado ya tiene otro Docente en alguno de los PA afectados: `409 grado_asignado` con "No se puede
  guardar la asignación. El colegio [colegio], grado [grado], ya tiene un Docente asignado en PA[n] del año
  escolar [año]." y no se guarda nada (en un alta, tampoco la cuenta).
- Otros `422`: `colegio_inexistente`, `colegio_inactivo`, `colegio_sin_grados`, `grado_no_ofrecido`,
  `anio_inexistente`, `anio_sin_periodos`, `calendario_no_configurado`, `sin_periodos_asignables`.
- **En `PATCH`, `colegios` es el conjunto completo del año:** cada colegio queda con esos grados en sus PA no
  terminados (más el retroactivo); los colegios que el Docente tenía en ese año y no vienen se liberan en sus
  PA no terminados según su calendario (los culminados quedan como historial). Otros años no se tocan. Para
  quitarle todas las asignaciones se inactiva la cuenta.
- La auditoría guarda el antes y el después como texto, un bloque por colegio unido por `; `:
  `AMASHCA [1.º, 2.º] desde P3 2026; HUARAZ [5.º] desde P3 2026 (P2 retroactivo)`.

### `GET /usuarios/{id_usuario}`

La cuenta (como en el listado) con `asignacion` (solo Docentes con asignaciones; `null` en los demás casos),
agrupada por colegio para prellenar el formulario. Es el año de referencia si el Docente tiene asignaciones en
él; si no, el más reciente en que tenga. Colegios por nombre y periodos por número.

```json
"asignacion": {
  "id_anio_escolar": 2,
  "colegios": [
    {"id_colegio": 1, "colegio": "AMASHCA", "solo_historial": false, "periodos": [
      {"id_periodo": 10, "numero": 3, "vigente": true, "retroactiva": false, "estado": "vigente",
       "grados": [{"id_grado": 1, "nombre": "1.º"}]}
    ]}
  ]
}
```

`id_periodo` es el `id_periodo_academico` de ese PA. `vigente`: es el PA en que hoy está ese colegio.
`retroactiva`: ese PA está habilitado con "Retroactivo". `estado`, según el calendario de ese colegio:
`vigente` (el PA en que hoy está el colegio, o una retroactiva cuyo PR todavía no terminó), `proximo` (un PA
que todavía no empieza) o `culminado` (historial). `solo_historial`: todos los periodos de ese colegio están
`culminado`. **El front no debe reenviar en el `PATCH` los colegios con `solo_historial: true`:** la lista
del `PATCH` es el conjunto deseado, y un colegio reenviado se reasigna en el PA en curso. Se muestran como
historial. **Error:** `404 usuario_no_encontrado`.

### `PATCH /usuarios/{id_usuario}`

**Body** (todo opcional): `nombres`, `apellidos`, `dni`, `correo`, `id_rol`, `asignacion`. Responde la cuenta
(`UsuarioItem`). Los nombres se copian también a la ficha del Docente.

- **Cambiar de rol** (`id_rol`): de Docente a otro rol se liberan sus grados desde el PA vigente y su ficha
  queda inactiva; de otro rol a Docente, `asignacion` es obligatoria.
- **Errores:** `404 usuario_no_encontrado`, `409 correo_duplicado` / `dni_duplicado`, `422 rol_inexistente`,
  `409 auto_cambio_rol` ("No puedes cambiar el rol de tu propia cuenta."), `403 supervisor_original_protegido`
  ("No se puede cambiar el rol del Supervisor original."), `422 asignacion_no_permitida`, los de la
  asignación.

### `PATCH /usuarios/{id_usuario}/activar` · `/desactivar`

Sin body; responden la cuenta. Desactivar cierra todas sus sesiones (y su actividad activa) y, si es Docente,
libera sus grados desde el PA vigente. Reactivar no restaura las asignaciones.

**Errores:** `404 usuario_no_encontrado`, `409 auto_desactivacion` ("No puedes desactivar tu propia cuenta."),
`409 supervisor_original_protegido`.

---

## Asignaciones

Se crean y cambian con `POST`/`PATCH /usuarios` (ya no existen `POST` ni `DELETE /asignaciones`).

### `GET /asignaciones` — Supervisor

**Query:** `id_docente`, `id_periodo_academico`, `id_colegio`. Una fila por docente, colegio, grado y periodo,
sin paginar:

```json
[{
  "id": 31, "id_docente": 1, "docente": "Rosa Quispe", "id_colegio": 1, "colegio": "AMASHCA",
  "id_grado": 1, "grado": "1.º", "id_periodo_academico": 10, "periodo": 3,
  "id_anio_escolar": 2, "anio": "2026", "vigente": true
}]
```

### `GET /me/asignaciones` — Docente

Lo que tiene **a cargo hoy** (asignaciones del PA vigente de cada colegio y retroactivas durante su PR), por
colegio:

```json
[{
  "id_colegio": 1, "colegio": "AMASHCA", "ciclos": ["III"], "subprogramas": ["Alfabetización"],
  "cantidad_alumnos": 12,
  "grados": [{ "id_grado": 1, "nombre": "1.º", "ciclos": ["III"], "cantidad_alumnos": 12,
               "subprogramas": [{ "id": 1, "nombre": "Alfabetización" }],
               "secciones": [{ "id": 3, "nombre": "Única" }] }]
}]
```

### `GET /me/habilitaciones` — Docente

Lo que el front guarda para validar **sin conexión**:

```json
{
  "generado_en": "2026-10-26T15:00:00Z",
  "asignaciones": [{ "id_colegio": 1, "id_grado": 1,
                     "colegio": { "id": 1, "nombre": "AMASHCA" }, "grado": { "id": 1, "nombre": "1.º" },
                     "id_periodo_academico": 10, "retroactiva": false }],
  "permisos": [{ "id_permiso": 5, "id_colegio": 1, "id_grado": 2,
                 "colegio": { "id": 1, "nombre": "AMASHCA" }, "grado": { "id": 2, "nombre": "2.º" },
                 "id_periodo_academico": 9, "modulo": "lectura", "inicio": "…", "fin": "…" }],
  "calendarios": [{ "id_colegio": 1, "…": "como GET /calendarios" }]
}
```

Incluye asignaciones de PA en curso o futuros, retroactivas cuyo PR no terminó, permisos programados o
vigentes y el calendario de cada colegio y año que aparece en ellos. Solo de colegios activos.

`colegio` y `grado` (`{id, nombre}`) traen el nombre para armar el filtro de aulas sin conexión; `id_colegio` e
`id_grado` se mantienen con el mismo valor que sus `id`.

---

## Permisos de corrección

Permiso excepcional para que un Docente registre o corrija un PA cuyo plazo ordinario ya terminó (CU028).
Solo el Supervisor los gestiona. Módulos: `asistencias`, `lectura`, `rubrica`, `registro_vuelo`.

### `GET /permisos/opciones` — Supervisor

**Query obligatoria:** `id_colegio`, `id_anio_escolar`. Responde
`{ "periodos": [{ "id": 9, "nombre": "PA1" }], "grados": [{ "id": 1, "nombre": "1.º" }] }`: los PA del colegio
cuyo plazo ordinario ya terminó y los grados que ofrece.

### `POST /usuarios/{id_usuario}/permisos` — Supervisor

```json
{
  "id_anio_escolar": 2, "id_colegio": 1, "id_grado": 2, "id_periodo_academico": 9, "modulo": "lectura",
  "inicio": "2026-10-27T08:00:00-05:00", "desde_ahora": false, "fin": "2026-10-28T18:00:00-05:00",
  "motivo": "Regularización de la semana 14"
}
```

`inicio` y `fin` sin zona se toman como hora de Lima (conviene enviarlos con zona). Con `desde_ahora: true`, el
inicio es el momento de la petición.

**Respuesta `201`:** el permiso (`PermisoDetalle`): `id`, `anio_escolar`, `colegio`, `grado`, `periodo`
(`{id, nombre}`), `modulo`, `inicio`, `fin`, `estado` (`programado` | `vigente` | `vencido` | `revocado`),
`otorgado_en`, `motivo`, `otorgado_por`, `revocado_por`, `revocado_en`, `motivo_revocacion`.

**Errores:**

| Código | `motivo` | `detail` |
| --- | --- | --- |
| `404` | `docente_no_encontrado` | "No existe el Docente." |
| `409` | `docente_inactivo` | "La cuenta del Docente está inactiva. No se puede otorgar el permiso." |
| `409` | `permiso_superpuesto` | "Ya existe un permiso para este mismo alcance con una vigencia que coincide total o parcialmente con la propuesta." |
| `422` | `validacion` (campo `inicio`) | "Selecciona una fecha y hora futura o utiliza la opción ‘Desde ahora’." |
| `422` | `validacion` (campo `fin`) | "El fin de vigencia debe ser posterior al inicio." |
| `422` | `validacion` (campo `motivo`) | "Ingresa el motivo de la autorización." |
| `422` | `validacion` (campo `id_periodo_academico`) | "Este periodo todavía admite regularización ordinaria y no requiere un permiso excepcional." |

### `POST /usuarios/{id_usuario}/permisos/lote` — Supervisor

Varios permisos del mismo docente, año, colegio y PA, con la misma vigencia y el mismo motivo: uno por cada
combinación de grado y módulo. **Todo o nada**: si una combinación falla, no se guarda ninguna.

```json
{
  "id_anio_escolar": 2, "id_colegio": 4, "id_periodo_academico": 7,
  "inicio": null, "desde_ahora": true, "fin": "2026-11-05T23:59:00-05:00",
  "motivo": "Regularizar PA3 por reemplazo",
  "combinaciones": [
    { "id_grado": 3, "modulo": "asistencias" },
    { "id_grado": 3, "modulo": "lectura" },
    { "id_grado": 4, "modulo": "rubrica" }
  ]
}
```

Los campos comunes siguen las reglas del `POST` individual (`inicio` y `fin` sin zona son hora de Lima). Con
`desde_ahora: true`, todos los permisos comparten el mismo inicio. `combinaciones`: de 1 a 24, sin repetir
(grado, módulo).

**Respuesta `201`:** `PermisoDetalle[]`, en el mismo orden que `combinaciones`.

**Errores:** los del `POST` individual. Los de los campos comunes (`motivo`, `inicio`, `fin`,
`id_anio_escolar`, `id_colegio`, `id_periodo_academico`) son el `422` por campo de siempre. Además:

| Código | `motivo` | Campo / `detail` |
| --- | --- | --- |
| `422` | `validacion` | `combinaciones`: "Hay combinaciones de grado y módulo repetidas." |
| `422` | `validacion` | `combinaciones`: "Debe tener al menos un elemento." / "Debe tener como máximo 24 elementos." |

Si fallan una o más combinaciones (grado que el colegio no ofrece: `422` en `combinaciones.{i}.id_grado`;
superposición: `409 permiso_superpuesto`), se responde el status, `motivo` y `detail` de la **primera** que
falló (con sus campos propios: `errores` o `permiso_existente`), más todas las rechazadas en orden de índice
(base 0):

```json
{
  "detail": "Ya existe un permiso para este mismo alcance con una vigencia que coincide total o parcialmente con la propuesta.",
  "motivo": "permiso_superpuesto",
  "permiso_existente": { "id": 12, "inicio": "…", "fin": "…" },
  "combinaciones_rechazadas": [
    { "indice": 1, "id_grado": 3, "modulo": "lectura", "motivo": "permiso_superpuesto", "detail": "…",
      "permiso_existente": { "id": 12, "inicio": "…", "fin": "…" } }
  ]
}
```

`permiso_existente` aparece dentro del ítem solo en las superposiciones. El `POST` individual no cambia.

### `GET /usuarios/{id_usuario}/permisos` — Supervisor

**Query:** `id_anio_escolar`, `id_colegio`, `id_periodo_academico`, `modulo`, `estado`, `page`. Paginado, del
más reciente otorgado al más antiguo, con el `estado` calculado. `404` si el usuario no es un Docente.

### `GET /usuarios/{id_usuario}/permisos/{id_permiso}` — Supervisor

El `PermisoDetalle`. **Error:** `404 permiso_no_encontrado` ("No existe el permiso.").

### `POST /usuarios/{id_usuario}/permisos/{id_permiso}/revocar` — Supervisor

`{ "motivo": "…" }` → el `PermisoDetalle` revocado. **Errores:** `404 permiso_no_encontrado`,
`409 permiso_revocado` ("El permiso ya fue revocado. Se actualizó su información."), `409 permiso_vencido` ("El
permiso ya venció y no requiere revocación."), `422 validacion` ("Ingresa el motivo de la revocación.").

Una operación hecha sin conexión **antes** de la revocación se acepta al sincronizar: el permiso se valida a la
hora real de la operación (`realizado_en`).

### `GET /me/permisos` — Docente

Sus permisos vigentes, con la forma de los ítems de `GET /usuarios/{id}/permisos`, por fin de vigencia.

---

## Colegios

Lectura: Supervisor (todos) y Docente (los de su alcance). Escritura: solo Supervisor (CU013).

### `GET /colegios`

**Query:** `departamento`, `distrito` (sin distinguir mayúsculas, tildes ni espacios extremos), `activo` (por
defecto `true`), `page`. Paginado, por nombre sin distinguir mayúsculas ni tildes.

```json
{
  "items": [{ "id": 1, "nombre": "AMASHCA", "nivel_educativo": "Primaria", "departamento": "Áncash",
              "provincia": "Carhuaz", "distrito": "Marcará", "activo": true }],
  "total": 1, "page": 1, "page_size": 40, "total_pages": 1
}
```

### `GET /colegios/ubicaciones`

**Query:** `departamento`. `{ "departamentos": ["Áncash", "Lima"], "distritos": ["Marcará", "Miraflores"] }`:
los distintos de los colegios visibles, sin distinguir mayúsculas ni tildes. Con `departamento`, los distritos
se limitan a ese departamento.

### `GET /colegios/estado-calendario` — Supervisor

**Query:** `id_anio_escolar` (por defecto, el del calendario que contiene hoy). Colegios activos con el periodo
en que hoy está cada uno, para el modal de asignación:

```json
[{ "id": 1, "nombre": "AMASHCA", "estado_calendario": { "tipo": "PR", "numero": 2, "es_pr4": false, "fecha_fin": "2026-08-02" } }]
```

`estado_calendario` es `null` si el colegio no tiene calendario. Si es un PR, la asignación de ese colegio
pide `inicio_asignacion` ("Próximo" no existe con `es_pr4: true`).

### `GET /colegios/{id_colegio}`

```json
{
  "id": 1, "nombre": "AMASHCA", "nivel_educativo": "Primaria", "departamento": "Áncash", "provincia": "Carhuaz",
  "distrito": "Marcará", "activo": true,
  "grados": [{ "id": 1, "nombre": "1.º", "dia_clase": 1, "secciones": [{ "id": 3, "nombre": "Única" }] }],
  "programas": [{ "id": 1, "nombre": "Alfabetización" }]
}
```

**Errores:** `404 colegio_no_encontrado`, `403 colegio_fuera_de_alcance` (Docente).

### `POST /colegios` — Supervisor

```json
{
  "nombre": "Colegio Yungay", "departamento": "Áncash", "provincia": "Yungay", "distrito": null,
  "nivel_educativo": "Primaria",
  "grados": [{ "id_grado": 1, "dia_clase": 1, "secciones": ["A", "B"] }],
  "programas": [1, 2]
}
```

`dia_clase`: 1 = lunes … 5 = viernes. Sin `secciones`, un grado nuevo nace con "Única". Responde `201` con el
`ColegioDetalle`.

**Errores:** `409 colegio_duplicado` ("Ya existe un colegio registrado con ese nombre.", sin distinguir
mayúsculas, tildes ni espacios, activo o no), `422 grado_inexistente`, `422 programa_inexistente`,
`422 validacion` (grado repetido: "No se puede repetir un grado.").

### `PATCH /colegios/{id_colegio}` — Supervisor

Todo opcional. `grados` y `programas`, si se envían, son la **lista completa** (reemplazan a la actual). Las
secciones nuevas se agregan; las existentes se conservan aunque no vengan.

**Errores:** los de `POST`, `404 colegio_no_encontrado`, `409 grado_en_uso` ("No se puede retirar 2.º: tiene
…"), `409 programa_en_uso` ("No se puede retirar Alfabetización: tiene 3 alumnos activos.").

### `PATCH /colegios/{id_colegio}/activar` · `/desactivar` — Supervisor

Sin body; responden el `ColegioItem`. Desactivar es un borrado lógico: no toca alumnos, registros ni
asignaciones, pero el colegio sale del alcance de los Docentes y sus alumnos dejan de listarse en
`GET /alumnos`.

---

## Calendarios

Un calendario **general** por año y, opcionalmente, uno **particular** por colegio (CU027). Cada uno tiene 8
periodos (`PA1`, `PR1`, …, `PA4`, `PR4`) y días no lectivos.

### `GET /calendarios` — Supervisor y Docente

**Query:** `id_anio_escolar` **o** `anio` (año calendario, p. ej. `2027`), no ambos; por defecto, el año de
referencia. `id_colegio`: el particular activo del colegio o, si no tiene, el general (`origen`).

```json
{
  "id_calendario": 3, "id_anio_escolar": 2, "anio": "2026", "colegio": { "id": 1, "nombre": "AMASHCA" },
  "origen": "particular", "version": 4,
  "periodos": [{ "tipo": "PA", "numero": 1, "id_periodo_academico": 9, "fecha_inicio": "2026-03-16", "fecha_fin": "2026-05-15" }],
  "dias_no_lectivos": [{ "motivo": "Feriado", "fecha_inicio": "2026-06-29", "fecha_fin": "2026-06-29", "origen": "automatico" }],
  "periodo_actual": { "tipo": "PA", "numero": 3, "id_periodo_academico": 11, "fecha_inicio": "…", "fecha_fin": "…" },
  "fecha_referencia": "2026-10-26"
}
```

`periodo_actual: null` = "Sin periodo aplicable a la fecha actual".

**Errores:** `404 calendario_no_configurado`, `422 anio_inexistente`, `422 colegio_inexistente`,
`422 validacion` (año por id y por valor a la vez), `403 colegio_fuera_de_alcance`.

### `GET /calendarios/periodo` — Supervisor y Docente

**Query obligatoria:** `id_colegio`, `fecha`. El periodo que contiene la fecha en el calendario del colegio:

```json
{ "fecha": "2026-10-26", "tipo": "PA", "numero": 3, "id_periodo_academico": 11, "fecha_inicio": "…",
  "fecha_fin": "…", "es_lectivo": true, "motivo_no_lectivo": null }
```

`motivo_no_lectivo`: `fuera_de_periodo`, `receso`, `fin_de_semana` o el motivo del día no lectivo.
**Errores:** `422 colegio_inexistente`, `403 colegio_fuera_de_alcance`.

### `POST /calendarios` — Supervisor

Crea el calendario **general** de un año. Con `anio`, si el año escolar no existe se crea en la misma
transacción; si no, se indica `id_anio_escolar` (uno de los dos).

```json
{
  "anio": 2027,
  "periodos": [{ "tipo": "PA", "numero": 1, "fecha_inicio": "2027-03-15", "fecha_fin": "2027-05-14" }],
  "dias_no_lectivos": [{ "motivo": "Feriado", "fecha_inicio": "2027-06-29", "fecha_fin": "2027-06-29" }]
}
```

Las fechas del año pasan a ser las del calendario (inicio del PA1 a fin del PR4) y se generan sus semanas.
Crear el año no lo declara vigente. Responde `201` con el calendario.

**Errores:** `409 calendario_existente`, `422 anio_inexistente`, `422 validacion` con el campo afectado
(periodos faltantes, repetidos, fuera de orden, superpuestos o con inicio > fin; más de 12 meses; choque con el
año vecino; días no lectivos fuera de los periodos o superpuestos; motivo vacío).

### `POST /calendarios/particulares` — Supervisor

`{ "id_colegio", "id_anio_escolar", "periodos": [...], "dias_no_lectivos": [...] }`. Sin `dias_no_lectivos` se
copian los del general. Responde `201`.

**Errores:** `409 calendario_general_requerido`, `409 calendario_particular_existente`,
`409 calendario_incompatible` (con la lista `incompatibilidades`: registros que quedarían fuera),
`422 anio_inexistente`, `422 colegio_inexistente`, `422 validacion`.

### `PUT /calendarios/{id_calendario}` — Supervisor

`{ "version": 4, "periodos": [...], "dias_no_lectivos": [...] }`. Reemplaza periodos y días no lectivos del
general o de un particular, con la `version` leída. Sin cambios reales no sube la versión. Si es el general, el
año pasa a las fechas del nuevo PA1 y PR4 y se ajustan sus semanas.

**Errores:** `404 calendario_no_encontrado`, `409 modificacion_concurrente`, `409 calendario_inactivo`,
`409 calendario_incompatible` ("No se puede guardar el calendario: el cambio afecta registros existentes.", con
`incompatibilidades`), `422 validacion`.

### `POST /calendarios/{id_calendario}/usar-general` — Supervisor

Body opcional `{ "version": 4 }`. El colegio deja sus fechas particulares (se conservan como historial) y
vuelve al general; responde el calendario que le aplica desde ahora.

**Errores:** `404 calendario_no_encontrado`, `409 calendario_es_general` ("El calendario general no se puede
desactivar."), `409 calendario_inactivo`, `409 modificacion_concurrente`, `409 calendario_incompatible`.

---

## Catálogos

Cualquier usuario autenticado, salvo `GET /semanas`.

| Endpoint | Respuesta |
| --- | --- |
| `GET /anios-escolares` | `[{ "id", "nombre", "fecha_inicio", "fecha_fin", "vigente" }]` |
| `GET /grados` | `[{ "id", "id_grado", "nombre", "id_ciclo" }]` |
| `GET /programas` | `[{ "id", "id_programa", "nombre" }]` (los subprogramas: Alfabetización y Comprensión Lectora) |
| `GET /periodos-academicos?id_anio_escolar=&id_colegio=` | `[{ "id", "id_anio_escolar", "numero", "tipo": "PA", "fecha_inicio", "fecha_fin", "vigente" }]`: PA1 a PA4 con las fechas del general o, con `id_colegio`, del calendario que aplica a ese colegio (fechas `null` si el año no tiene calendario) |
| `GET /niveles-razkids` | `[{ "id", "letra" }]`: el `id` es el orden de los niveles (A, B, C…) |
| `GET /niveles-rubrica?id_programa=` | `[{ "id", "id_programa", "dimension", "orden", "valor", "nombre_nivel" }]` |

### `GET /semanas` — Supervisor y Docente

**Query obligatoria:** `id_anio_escolar`, `mes`, `id_colegio`, `id_grado`, `id_seccion`; opcional
`id_programa`. Las semanas (lunes a viernes) del año ubicadas en ese mes, para elegir la tabla de lectura
(CU023):

```json
[{ "id": 40, "id_semana": 40, "numero_semana": 14, "fecha_inicio": "2026-06-15", "fecha_fin": "2026-06-19", "cerrada": false }]
```

Una semana que cae en dos meses aparece en uno solo: el de su tabla del grupo o, sin tabla, el de su clase
habitual o reprogramada. **Errores:** `403 sin_permiso` (Directivo), `403 aula_fuera_de_alcance`,
`404 colegio_no_encontrado`, `404 anio_no_encontrado`, `422 seccion_inexistente`.

---

## Alumnos

Supervisor (todos, sin actividad) y Docente (su alcance, con actividad para escribir) (CU014, CU015).

### `GET /alumnos`

**Query:** `id_colegio`, `id_grado`, `id_seccion`, `id_programa`, `ciclo` (`III` | `IV` | `V`), `activo` (por
defecto `true`), `q`, `page`.

- `q`: cada palabra en "nombres apellidos", sin distinguir mayúsculas ni tildes y en cualquier orden.
- Orden: apellidos y nombres sin distinguir mayúsculas ni tildes, y por id si empatan.
- El Supervisor no ve los alumnos de colegios inactivos.

```json
{
  "items": [{
    "id": 12, "nombres": "Luis", "apellidos": "Pérez Quispe", "colegio": { "id": 1, "nombre": "AMASHCA" },
    "grado": { "id": 1, "nombre": "1.º" }, "programa": { "id": 1, "nombre": "Alfabetización" }, "ciclo": "III",
    "seccion": { "id": 3, "nombre": "Única" }, "activo": true
  }],
  "total": 1, "page": 1, "page_size": 40, "total_pages": 1
}
```

### `POST /alumnos`

```json
{ "nombres": "Luis", "apellidos": "Pérez", "id_colegio": 1, "id_grado": 1, "id_seccion": 3,
  "id_programa": 1, "pertenece_desde": "2026-03-16" }
```

Sin `id_seccion`, la única sección activa del grado (`422` en `id_seccion` si el grado tiene varias, o si la
enviada no es una sección activa de ese colegio y grado). `pertenece_desde` no puede ser futura. Responde `201`
con el `AlumnoItem`.

**Errores:** `403 alumno_fuera_de_alcance` (Docente fuera de su alcance), `409 actividad_requerida`,
`422 colegio_inexistente`, `422 colegio_inactivo`, `422 grado_no_ofrecido`, `422 programa_no_ofrecido`,
`422 validacion` ("En 1.º grado todos los alumnos pertenecen a Alfabetización.", fechas futuras, nombres
vacíos).

### `GET /alumnos/{id_alumno}`

Cabecera de la vista de detalle: los campos del `AlumnoItem` más `fecha_registro` y `pertenece_desde`.
`fecha_registro` es la fecha de creación de la ficha (`YYYY-MM-DD`, hora de Lima): la que CU014 propone para
"Pertenece al grupo desde" cuando falta. **Errores:** `404 alumno_no_encontrado`, `403 alumno_fuera_de_alcance`.

### `PATCH /alumnos/{id_alumno}`

Todo opcional: `nombres`, `apellidos`, `id_colegio`, `id_grado`, `id_seccion`, `id_programa` y
`cambio_grupo_desde` (obligatorio si cambia el grupo o el subprograma: "Indica desde qué fecha el alumno
pertenece a su nuevo grupo.") y `pertenece_desde`. Responde el `AlumnoItem`.

`pertenece_desde` ("Pertenece al grupo desde", CU014) es solo para un **alumno activo sin pertenencia**
(`GET /alumnos/{id}` con `pertenece_desde: null`):

- Para él es **obligatorio** en cualquier `PATCH`: sin él, `422` en `pertenece_desde` ("Confirma desde qué
  fecha el alumno forma parte de este grupo."). El front propone `fecha_registro`.
- Hoy o antes (hora de Lima), con el mismo mensaje que al crear. Se aplican los demás cambios y se abre el
  primer intervalo con el grupo final del alumno, todo en la misma transacción, validado contra las listas
  de clases ya guardadas como al crear (`409 conflicto_listas_historicas`). Queda en el historial como fecha
  efectiva.
- No lleva `cambio_grupo_desde` aunque cambie el grupo: si llega, `422` en `cambio_grupo_desde` ("Este alumno
  todavía no tiene un inicio de pertenencia: usa 'Pertenece al grupo desde'.").
- A un alumno que ya tiene pertenencia, o a uno inactivo (vuelve con "Activar" y su `aplicar_desde`), se le
  rechaza: `422` en `pertenece_desde` ("El alumno ya tiene un inicio de pertenencia registrado.").
- El Docente necesita, como en toda edición, el alumno en su alcance y una actividad activa.

**Errores:** los de `POST`, `404 alumno_no_encontrado`, `403 cambio_colegio_no_permitido` ("Solo el Supervisor
puede cambiar el colegio de un alumno."), `409 conflicto_listas_historicas` (el cambio contradice listas de
clases ya guardadas; trae las `clases` afectadas). En un colegio inactivo solo se pueden corregir los nombres.

### `PATCH /alumnos/{id_alumno}/activar` · `/desactivar`

`{ "aplicar_desde": "2026-10-20" }` (hoy o antes). Desactivar: deja de pertenecer a su grupo desde esa fecha.
Activar: vuelve a su grupo (exige que el colegio esté activo). Responden el `AlumnoItem`.

### Vista de detalle (CU015)

| Endpoint | Respuesta |
| --- | --- |
| `GET /alumnos/{id}/resumen` | `{ "nivel_colocado": { "letra", "id_nivel_rk", "numero_periodo", "nivel_descriptivo" }, "libros_leidos_anio", "asistencia": { "porcentaje", "asistidas", "contabilizadas" } }` |
| `GET /alumnos/{id}/lectura` | Las 5 semanas más recientes: `[{ "semana", "fecha_inicio", "fecha_fin", "mes", "id_programa", "estado_tabla", "motivo_no_realizada", "asistencia", "lsn_titulo", "lsn_nota", "lsn_maximo", "cantidad_lsl", "total_libros", "observacion", "estado_fila" }]` |
| `GET /alumnos/{id}/rubrica` | `{ "evaluaciones": [{ "mes", "fluidez", "comprension", "estado", "resultado_decimas" }] }` (las 5 más recientes) |
| `GET /alumnos/{id}/registro-vuelo` | Una fila por PA: `[{ "numero", "id_periodo_academico", "entrada", "origen_entrada", "nivel_colocado", "id_nivel_colocado", "proyeccion", "nivel_programa" }]` |
| `GET /alumnos/{id}/historial?page=` | Paginado: `[{ "fecha", "usuario", "rol", "accion", "cambios": [{ "campo", "anterior", "nuevo" }] }]` |

En el historial, `accion` es un código (`crear` | `editar` | `activar` | `inactivar`) que el front traduce al
verbo de CU014; `rol` es el nombre del rol del autor ("Docente", "Supervisor"); en `crear` el arreglo
`cambios` llega vacío.

---

## Clases y reprogramaciones

### `GET /colegios/{id_colegio}/clases` — Supervisor y Docente

**Query:** `id_grado`, `id_seccion`, `desde`, `hasta` (por defecto, el año del calendario del colegio que
contiene hoy), `id_programa`, `page`. Con `page`, paginado de 40; **sin `page`, el arreglo completo**.

```json
[{ "id_clase": 51, "id_grado": 2, "id_seccion": 4, "fecha_original": "2026-06-15", "fecha": "2026-06-16",
   "tipo": "habitual", "reprogramada": true, "tiene_asistencias": false, "id_periodo_academico": 10,
   "grado": { "id": 2, "nombre": "2.º" }, "seccion": { "id": 4, "nombre": "A" },
   "periodo": { "id": 10, "nombre": "PA2" }, "anio_escolar": { "id": 2, "nombre": "2026" },
   "version": 2, "subprogramas": [{ "id": 2, "nombre": "Comprensión Lectora" }] }]
```

`grado`, `seccion` y `periodo` son los mismos `id_grado`, `id_seccion` e `id_periodo_academico` con su nombre
(el PA como "PA1"…"PA4"), y `anio_escolar`, el año de ese PA: para mostrar sin consultar catálogos.

Habituales calculadas (`id_clase: null` si todavía no tienen fila), reprogramadas en su fecha nueva y
adicionales, por fecha, grado y sección. **Errores:** `403 aula_fuera_de_alcance`, `404 colegio_no_encontrado`,
`422 rango_fechas_invalido`.

### `POST /colegios/{id_colegio}/clases/reprogramaciones` — Supervisor

```json
{ "id_grado": 2, "id_seccion": 4, "fecha_original": "2026-06-15", "fecha_nueva": "2026-06-16",
  "motivo": "Actividad del colegio", "version": 2 }
```

Traslada una clase habitual a otra fecha lectiva del mismo PA. Se puede volver a trasladar. Responde `201`
con el `ClaseItem`.

**Errores:** `404 colegio_no_encontrado`, `404 clase_no_programada`; `409 clase_con_asistencias` ("Esta clase ya
tiene asistencias registradas y no puede reprogramarse."), `409 modificacion_concurrente`;
`422 seccion_inexistente`, `422 validacion` (motivo vacío), `422 fecha_sin_cambio`, `422 fecha_en_receso`,
`422 fecha_no_lectiva`, `422 fecha_de_otro_periodo` ("La nueva fecha debe pertenecer al mismo periodo académico
de la clase."), `422 fecha_ocupada`.

### `GET /clases/{id_clase}/reprogramaciones` — Supervisor y Docente

Traslados de la clase, del más antiguo al más reciente:
`[{ "id", "fecha_anterior", "fecha_nueva", "motivo", "usuario": { "id", "nombre" }, "fecha" }]`.
**Errores:** `403 aula_fuera_de_alcance`, `404 clase_no_encontrada`.

---

## Asistencias

### `GET /asistencias` — Supervisor y Docente

**Query obligatoria:** `id_colegio`, `id_grado`, `id_seccion`, `fecha`.

```json
{
  "clase": { "id_clase": 51, "tipo": "habitual", "etiqueta": "Clase programada", "fecha_original": "2026-06-15",
             "fecha": "2026-06-15", "aviso": null },
  "alumnos": [{ "id_alumno": 12, "nombre": "Ana Torres", "id_programa": 2, "inactivo": false,
                "estado": "presente", "observacion": null, "version": 1 }],
  "escritura": { "permitida": true, "motivo": null, "id_permiso": null, "aviso_permiso": null }
}
```

- La lista es la **congelada** si la clase ya tiene asistencias; si no, los alumnos del grupo en esa fecha. Por
  apellidos y nombres sin distinguir mayúsculas ni tildes.
- `etiqueta`: "Clase programada", "Clase reprogramada" o "Clase adicional"; `null` sin clase, con el `aviso`
  ("No hay una clase programada para esta fecha.", o el de la clase trasladada).
- `escritura`: si el usuario puede registrar, con el `motivo` si no; `id_permiso` y `aviso_permiso` cuando lo
  habilita un permiso.

**Errores:** `403 aula_fuera_de_alcance`, `404 colegio_no_encontrado`, `422 seccion_inexistente`,
`422 fecha_futura`, `422 fecha_en_receso`, `422 fecha_no_lectiva`, `422 fecha_fuera_de_periodo` (con el mensaje
en `detail`).

### `PUT /asistencias` — Docente

```json
{
  "id_colegio": 1, "id_grado": 2, "id_seccion": 4, "fecha": "2026-06-15", "adicional": false,
  "registros": [{ "id_operacion": "…uuid…", "version": null, "realizado_en": null, "id_actividad": "…uuid…",
                  "id_alumno": 12, "estado": "presente", "observacion": null }]
}
```

Se identifica la clase por `id_clase` o por grupo y `fecha`; `adicional: true` registra una clase adicional.
Cada registro debe traer `estado` u `observacion` (si no: `422 validacion`, "Debes indicar el estado o la
observación."). Responde `200`:

```json
{ "clase": { "…": "como en GET" }, "resultados": [{ "id_operacion": "…", "estado": "aceptada", "registro": { } }] }
```

**Rechazos por operación:** `fecha_futura`, `fecha_en_receso`, `fecha_no_lectiva`, `fecha_fuera_de_periodo`,
`sin_habilitacion`, `fuera_de_actividad`, `actividad_requerida`, `sin_clase_programada`,
`alumno_fuera_de_la_clase`, `realizado_en_futuro`. Una `aceptada` con `motivo: registro_mas_reciente` indica que
ganó una captura posterior y no se aplicó.

**Errores del lote:** `403 aula_fuera_de_alcance`, `404 clase_no_encontrada`, `422 seccion_inexistente`,
`422 validacion`.

---

## Seguimiento de lectura

Una tabla por grupo, subprograma y **semana** (el mes no la identifica), con una fila por alumno de la lista
histórica de su clase de referencia (CU023 a CU026).

### `GET /lectura/tablas` — Supervisor y Docente

**Query obligatoria:** `id_colegio`, `id_grado`, `id_seccion`, `id_programa`, `id_anio_escolar`, `mes`,
`id_semana`.

```json
{
  "id": 7, "version": 3, "estado": "normal", "motivo_no_realizada": null,
  "id_colegio": 1, "id_grado": 2, "id_seccion": 4, "id_programa": 2, "id_anio_escolar": 2, "mes": 6, "id_semana": 40,
  "clase_referencia": { "id_clase": 51, "fecha": "2026-06-15", "tipo": "habitual", "reprogramada": false },
  "filas": [{ "id_alumno": 12, "nombre": "Ana Torres", "inactivo": false, "asistencia": "presente",
              "nivel_academico": null, "lsn_titulo": null, "lsn_nota": null, "lsn_maximo": null,
              "cantidad_lsl": null, "total_libros": 0, "observacion": null, "estado_fila": "pendiente",
              "revisar_asistencia": false, "version": 1 }],
  "escritura": { "permitida": true, "motivo": null, "id_permiso": null, "aviso_permiso": null },
  "declaraciones": []
}
```

Filas por apellidos y nombres sin distinguir mayúsculas ni tildes. Se devuelve aunque el `mes` enviado no sea el
suyo (la respuesta trae su `mes`). `declaraciones`: el historial de "No realizada".

**Errores:** `404 tabla_no_existe` ("No existe una tabla de lectura semanal para los filtros seleccionados.",
con `clase_referencia_sugerida` y `escritura` para crearla), `422 semana_sin_clase` (la semana no tiene clase
del grupo), `422 sin_alumnos` ("No hay estudiantes que pertenecieran a este grupo en la fecha de la clase
seleccionada.": la semana sí tiene clase o la tendría por el día de clase del grado, pero el grupo no tenía
alumnos de ese subprograma en su fecha; D101), `403 aula_fuera_de_alcance`, `404 colegio_no_encontrado`, `404 semana_no_encontrada`,
`422 seccion_inexistente`, `422 programa_inexistente`.

### `POST /lectura/tablas` — Docente

Los filtros de la consulta más `id_operacion` (y `version`, `realizado_en`, `id_actividad`) y, opcional, la
clase de referencia (`id_clase_referencia` o `fecha_clase_referencia`; por defecto, la habitual o reprogramada
de la semana). Crea una fila vacía por alumno de esa clase con el subprograma. Responde `201` con la tabla.

**Errores:** `409 tabla_existente` (con su `id`); `422 semana_futura` ("No es posible adelantar registros.
Seleccione la semana actual o una anterior.", texto de CU023), `422 clase_no_realizada`,
`422 semana_sin_clase`, `422 clase_fuera_de_la_semana`, `422 clase_referencia_requerida`, `422 sin_alumnos`,
`422 sin_habilitacion`, `422 actividad_requerida`, `422 fuera_de_actividad`; además los de la consulta.
`sin_alumnos` y `semana_sin_clase` se distinguen como en la consulta (D101).

### `PUT /lectura/tablas/{id_tabla}/filas` — Docente

Arreglo de operaciones: `[{ "id_operacion", "version", "realizado_en", "id_actividad", "id_alumno",
"lsn_titulo", "lsn_nota", "lsn_maximo", "cantidad_lsl", "observacion" }]` → arreglo de resultados.

**Rechazos:** `sin_asistencia` ("El estudiante debe tener asistencia Presente o Tardanza en la clase de
referencia para registrar su lectura."), `lsn_incompleto` ("Para registrar un LSN, completa el título, la nota
obtenida y el puntaje máximo."), `lsn_invalido` ("Selecciona un máximo de 5 o 10 e ingresa una nota entre 0 y
ese máximo."), `alumno_fuera_de_la_tabla`, `lectura_no_realizada`, `modificacion_concurrente` (con la fila
vigente), `sin_habilitacion`, `actividad_requerida`, `fuera_de_actividad`.

**Errores del lote:** `404 tabla_no_encontrada`, `403 aula_fuera_de_alcance`, `422 validacion`.

### `PATCH /lectura/tablas/{id_tabla}/clase-referencia` — Docente

`{ "id_operacion", "version", "id_clase" | "fecha" }`: otra clase del grupo en la misma semana, solo sin datos
de lectura y si no está "No realizada". Si la clase es de otro mes, la tabla pasa a ese `mes`.

**Errores:** `404 tabla_no_encontrada`; `409 modificacion_concurrente`, `409 tabla_con_datos`,
`409 lectura_no_realizada`, `409 clase_ya_usada`; `422 clase_fuera_de_la_semana`, `422 clase_no_realizada`,
`422 sin_alumnos`, `422 sin_habilitacion`.

### `POST /lectura/tablas/{id_tabla}/no-realizada` — Docente

`{ "id_operacion", "version", "motivo" }`: marca la tabla "No realizada" y bloquea sus datos y observaciones.
**Errores:** `404 tabla_no_encontrada`; `422 validacion` (motivo vacío), `422 sin_habilitacion`,
`422 actividad_requerida`; `409 tabla_con_datos`, `409 modificacion_concurrente`.

### `POST /lectura/tablas/{id_tabla}/habilitar-registro` — Docente

"Habilitar registro de lectura" (CU026): `{ "id_operacion", "version", "motivo" }` (el "Motivo de la
corrección"). Revierte la declaración vigente; la tabla vuelve a "normal" y el historial queda en
`declaraciones`. No amplía el plazo.

**Errores:** `404 tabla_no_encontrada`; `422 validacion` (motivo vacío), `422 sin_habilitacion`,
`422 actividad_requerida`; `409 declaracion_no_vigente`, `409 modificacion_concurrente` (con `id` y `version`).

---

## Rúbrica mensual

### `GET /rubricas` — Supervisor y Docente

**Query obligatoria:** `id_colegio`, `id_grado`, `id_seccion`, `id_programa`, `id_anio_escolar`, `mes`; `page`.

```json
{
  "items": [{ "id_alumno": 12, "nombre": "Ana Torres", "inactivo": false, "id_rubrica": null,
              "fluidez": null, "comprension": null, "estado": "pendiente", "resultado_decimas": null, "version": null }],
  "total": 1, "page": 1, "page_size": 40, "total_pages": 1,
  "ciclo": "III", "id_ciclo": 1, "id_periodo_academico": 10,
  "escritura": { "permitida": true, "motivo": null, "id_permiso": null, "aviso_permiso": null },
  "detail": null
}
```

Los alumnos que corresponde evaluar en el grupo ese mes (R2), incluidos los hoy inactivos, por apellidos y
nombres sin distinguir mayúsculas ni tildes. `fluidez` y `comprension` en códigos 0-4. `id_periodo_academico`:
el PA del mes (R1), al que se asocia la evaluación. Sin alumnos: `items` vacío y el texto en `detail`.

`escritura.permitida` (**DP-1, provisional**): `true` si el Docente tiene habilitación de escritura para el
grupo en **cualquiera** de los PA con días lectivos en el mes (asignación del PA, retroactiva durante su PR o
permiso de ese PA), no solo en el de R1. Así, octubre de 2026 (R1: PA3) lo puede evaluar un Docente asignado
solo al PA4. Si escribe con un permiso, `id_permiso` y `aviso_permiso` son los de ese permiso.

**Errores:** `422 mes_futuro`, `422 mes_sin_actividad`, `422 seccion_inexistente`, `422 programa_inexistente`;
`403 aula_fuera_de_alcance`; `404 colegio_no_encontrado`, `404 anio_no_encontrado`.

### `PUT /rubricas` — Docente

Arreglo: `[{ "id_operacion", "version", "realizado_en", "id_actividad", "id_alumno", "id_colegio",
"id_grado", "id_seccion", "id_programa", "id_anio_escolar", "mes", "fluidez", "comprension" }]` → arreglo de
resultados. Se puede guardar incompleta; un ítem sin cambios no escribe.

**Rechazos:** `valor_no_permitido` ("Selecciona una valoración permitida para esta dimensión."),
`sin_habilitacion` (con la regla de DP-1 de `GET /rubricas`: basta la habilitación en cualquiera de los PA con
días lectivos en el mes; la evaluación se guarda igual con el PA de R1), `alumno_fuera_del_grupo` ("El estudiante no pertenecía a este grupo en el mes
seleccionado."), `evaluacion_en_otro_grupo` ("Este alumno ya tiene una evaluación registrada para este mes.
Para corregirla, contacta al Docente que la registró o a tu Supervisor."), `modificacion_concurrente` (con la
vigente), `mes_futuro`, `mes_sin_actividad`,
`aula_fuera_de_alcance`, `actividad_requerida`, `fuera_de_actividad`.

### `GET /rubricas/criterios` — Supervisor y Docente

**Query obligatoria:** `id_programa`, `id_ciclo`. Los niveles con su descriptor para el ciclo:
`[{ "dimension": "Fluidez", "valor": 3, "nombre": "…", "descriptor": "…" }]`.
**Errores:** `422 programa_inexistente`, `422 ciclo_inexistente`.

---

## Registro de vuelo

### `GET /registro-vuelo` — Supervisor y Docente

**Query obligatoria:** `id_colegio`, `id_grado`, `id_seccion`, `id_programa`, `id_anio_escolar`; `page`.
Una fila por alumno que perteneció al grupo en el año (incluidos los hoy inactivos), paginado de 40:

```json
{
  "items": [{
    "id_alumno": 12, "nombre": "Ana Torres", "id_programa": 2, "inactivo": false, "avance_anual": 2,
    "periodos": [{
      "numero": 2, "id_periodo_academico": 10, "id_registro": 33, "version": 1,
      "entrada": { "nivel": { "id": 5, "letra": "E" }, "origen": "periodo_anterior", "texto": "E", "de_otro_colegio": false },
      "sugerencia": { "accion": "subir", "nivel": { "id": 6, "letra": "F" }, "texto": "…", "resultado_decimas": 31,
                      "excepcion": null, "rubrica": { "id": 8, "mes": "Junio", "fluidez": "…", "comprension": "…", "resultado_decimas": 31 } },
      "nivel_colocado": { "id": 6, "letra": "F" }, "nivel_programa": "…", "proyeccion": null,
      "observacion": null, "aviso_entrada_cambio": false, "aviso": null, "incompleto": false
    }]
  }],
  "total": 1, "page": 1, "page_size": 40, "total_pages": 1,
  "periodos": [{ "numero": 2, "id_periodo_academico": 10, "nombre": "PA2", "fecha_inicio": "…", "fecha_fin": "…",
                 "estado": "en_curso", "escritura": { "permitida": true } }],
  "aviso": null
}
```

`entrada.de_otro_colegio`: la entrada viene de un PA anterior en otro grupo ("Registrado antes del
traslado."). Sin alumnos: `items` vacío y `aviso`.

**Errores:** `403 aula_fuera_de_alcance`; `404 colegio_no_encontrado`, `404 anio_no_encontrado`;
`422 seccion_inexistente`, `422 programa_inexistente`.

### `PUT /registro-vuelo` — Docente

Arreglo: `[{ "id_operacion", "version", "realizado_en", "id_actividad", "id_alumno", "id_periodo_academico",
"id_colegio", "id_grado", "id_seccion", "id_programa", "id_nivel_colocado", "id_nivel_proyeccion",
"observacion" }]` → arreglo de resultados. Enviar entrada, sugerencia o nivel del programa es `422 validacion`.

**Rechazos:** `modificacion_concurrente` (con el vigente), `nivel_invalido`, `nivel_colocado_obligatorio`,
`periodo_futuro`, `periodo_inexistente`, `alumno_fuera_del_grupo`, `sin_habilitacion`, `aula_fuera_de_alcance`,
`actividad_requerida`, `fuera_de_actividad`.

---

## Actividades de trabajo

Solo el Docente (CU009, CU017 a CU019).

### `POST /actividades`

`{ "id_actividad": "…uuid…", "inicio": null, "id_sesion": null }`. Inicia una actividad en la sesión actual o,
con `id_sesion`, registra una ocurrida en otra sesión del docente (`inicio` obligatorio). Responde `201`
(o `200` si ese `id_actividad` ya estaba registrado, sin cambios):

```json
{
  "actividad": { "id": "…", "id_sesion": "…", "inicio": "…", "fin": null, "tipo_cierre": null, "estado": "en_curso" },
  "sesion_expira": "2026-10-26T23:00:00Z", "actividad_sesion_expira": "2026-10-26T23:00:00Z"
}
```

**Errores:** `422 sin_asignaciones` ("No puedes iniciar una actividad porque no tienes asignaciones habilitadas
ni permisos …"), `409 actividad_activa_existente` (ya tiene una en curso), `409 actividad_id_en_uso`,
`409 actividad_superpuesta` (se cruza con otra de esa sesión), `422 inicio_invalido` (hora de inicio fuera de la
sesión o posterior a la actual).

### `POST /actividades/{id_actividad}/finalizar`

Body opcional `{ "fin": "…" }`. Finaliza la actividad (no cierra la sesión). Si ya estaba finalizada, responde
`200` con su estado, salvo que la hubiera cerrado el servidor y `fin` sea anterior: entonces se corrige a esa
hora como cierre manual. **Errores:** `404 actividad_no_encontrada`, `422 fin_invalido`.

### `GET /actividades`

**Query:** `desde`, `hasta` (días de Lima, inclusivos, sobre el inicio), `estado` (`en_curso` | `finalizada`),
`sincronizacion` (`sincronizada` | `pendiente` | `error`), `tipo_cierre`, `page`. Paginado, de la más reciente a
la más antigua:

```json
{ "items": [{ "id": "…", "id_docente": 1, "fecha": "2026-10-26", "inicio": "…", "fin": "…",
              "duracion_segundos": 3600, "estado": "finalizada", "tipo_cierre": "Manual por finalización de actividad",
              "sincronizacion": "sincronizada", "cambios": 12,
              "productividad": [{ "modulo": "asistencia", "cantidad": 12 }], "productividad_texto": "12 Asistencias" }],
  "total": 1, "page": 1, "page_size": 40, "total_pages": 1 }
```

### `GET /actividades/{id_actividad}`

Detalle (CU019): los campos del resumen más `sincronizado_en`, `registros_pendientes`, `cambios_por_modulo`
(`[{ "modulo", "total", "creados", "editados", "activados", "inactivados" }]`) y `asignaciones` involucradas
(`[{ "colegio", "grado", "ciclo", "seccion" }]`). Ajena o inexistente: `404`.

---

## Sincronización

Solo el Docente. Las escrituras hechas sin conexión se envían a los mismos endpoints de cada módulo con su
`id_operacion` y `realizado_en`; estos tres endpoints acompañan ese envío.

### `POST /sincronizacion/pendientes`

Aviso de las operaciones creadas sin conexión que todavía no se enviaron (se manda al reconectar, antes que
las operaciones):

```json
[{ "id_operacion": "…", "id_dispositivo": "tablet-3", "id_actividad": "…", "modulo": "asistencias",
   "realizado_en": "2026-10-26T10:15:00-05:00", "registro": { } }]
```

Responde `{ "recibidos": 1, "registrados": 1 }`. Idempotente. Cuentan como `pendiente` en la actividad y en el
docente hasta que la operación se procesa.

### `GET /sincronizacion/operaciones/{id_operacion}`

El resultado de una operación que se quedó sin respuesta:
`{ "estado": "aceptada" | "rechazada" | "descartada" | "desconocida", "motivo": "…", "respuesta": { } }`.

### `POST /sincronizacion/operaciones/{id_operacion}/descartar`

"Cargar versión actual" / "Descartar": una operación propia `rechazada` pasa a `descartada` y deja de contar
como error. **Errores:** `404 operacion_no_encontrada`, `409 operacion_no_rechazada`.

---

## Inicio por rol

### `GET /inicio/docente` — Docente (CU010)

```json
{
  "asignaciones": [{ "…": "como GET /me/asignaciones" }],
  "totales": { "ciclos": ["III"], "subprogramas": ["Alfabetización"], "cantidad_alumnos": 12 },
  "actividad_activa": { "id": "…", "inicio": "…" },
  "sesion_expira": "…", "puede_iniciar_actividad": false,
  "motivo_no_puede_iniciar": "actividad_activa", "motivo_no_puede": "…",
  "permisos_vigentes": [{ "id_permiso": 5, "colegio": { }, "grado": { }, "anio_escolar": { }, "periodo": "PA1",
                          "modulo": "lectura", "fin": "…" }],
  "registros_offline_pendientes": 0
}
```

`motivo_no_puede_iniciar`: `actividad_activa` o `sin_asignaciones`.

### `GET /inicio/supervisor` — Supervisor (CU011)

```json
{
  "colegios_registrados": 12, "docentes_activos": 30, "docentes_con_actividad_activa": 4,
  "registros_vencidos": 18, "registros_incompletos": 5,
  "vencidos_por_modulo": { "asistencias": 10, "lectura": 5, "rubrica": 3, "registro_vuelo": 0 },
  "incompletos_por_modulo": { "asistencias": 0, "lectura": 5, "rubrica": 0, "registro_vuelo": 0 },
  "alertas": [{ "tipo": "inactividad_operativa", "mensaje": "Inactividad Operativa - Rosa Quispe: …", "critica": false }]
}
```

- **Docentes activos** (regla confirmada, CU011 semana 12): durante un PA, los que tienen una asignación
  habilitada para ese PA; durante un PR, los que tienen una retroactiva habilitada del PA anterior, un permiso
  de corrección vigente o una asignación del PA inmediatamente siguiente. Cada Docente cuenta una vez.
- **Alertas:** `sincronizacion_olvidada`, `inactividad_operativa` e `incumplimiento_sede`, en ese orden; dentro
  de cada tipo, por nombre del docente o del colegio (sin distinguir mayúsculas ni tildes).
- **Vencidos durante un receso (DP-2, provisional):** durante el PR n, `registros_vencidos`,
  `registros_incompletos`, sus desgloses por módulo y la alerta `sincronizacion_olvidada` se calculan sobre el
  PA n, el que se regulariza, con las mismas reglas que cuando estaba en curso (y DP-1 para la rúbrica). Al
  empezar el PA siguiente se pasa a ese PA. `incumplimiento_sede` sigue mirando el PA en curso (un colegio en
  un PR no la genera), y `docentes_activos` y `colegios_registrados` no cambian. Misma forma de respuesta.
- **Rúbrica (DP-1):** la obligación de un mes se exige en todo PA con días lectivos en el mes y vence el día 1
  del mes siguiente: octubre figura vencido el 01/11 con el PA4 en curso. Un mes íntegro del PA anterior no se
  exige durante el PA siguiente.

### `GET /inicio/directivo` — Directivo (CU012)

`{ "beneficiarios_activos": 340, "colegios_operando": 11, "salud_sistema": 92 }`, sin datos personales.
`salud_sistema`: el % de registros sincronizados sobre los sincronizados más los pendientes (`null` si no hay
ninguno).

---

## Seguimiento de docentes

Solo el Supervisor (CU020, CU021).

### `GET /seguimiento/docentes`

**Query:** `id_colegio` (asignados al PA actual de ese colegio), `desde`, `hasta` (sobre la última conexión;
quien nunca entró queda fuera), `sincronizacion` (`error` | `pendiente` | `sin_informacion` | `al_dia`),
`activo` (por defecto `true`), `page`.

```json
{ "items": [{ "id_docente": 1, "nombres": "Rosa", "apellidos": "Quispe", "activo": true,
              "colegios": [{ "id": 1, "nombre": "AMASHCA" }], "ultima_conexion": "…",
              "sincronizacion": "pendiente", "registros_pendientes": 3 }],
  "total": 1, "page": 1, "page_size": 40, "total_pages": 1 }
```

Orden: `error`, `pendiente`, `sin_informacion`, `al_dia`; dentro de cada estado, por nombres y apellidos sin
distinguir mayúsculas ni tildes.

### `GET /seguimiento/docentes/{id_docente}`

La cabecera de CU021 (un `DocenteSeguimiento`). **Error:** `404 docente_no_encontrado`.

### `GET /seguimiento/docentes/{id_docente}/actividades`

El historial del docente, con los mismos filtros y la misma forma que `GET /actividades`.

### `GET /seguimiento/docentes/{id_docente}/actividades/{id_actividad}/registros`

**Query obligatoria:** `modulo` (`asistencias` | `lectura` | `rubrica` | `registro_vuelo` | `alumnos`); `page`.
Los registros de ese módulo tocados en la actividad, con el contexto para abrirlos en solo lectura:

```json
{ "items": [{ "modulo": "asistencias", "id": 51, "colegio": { "id": 1, "nombre": "AMASHCA" },
              "grado": { "id": 2, "nombre": "2.º" }, "seccion": { "id": 4, "nombre": "Única" },
              "fecha": "2026-06-15", "cambios": 12 }],
  "total": 1, "page": 1, "page_size": 40, "total_pages": 1 }
```

Según el módulo vienen además `programa`, `alumno`, `id_semana`, `semana_inicio`, `mes`, `id_anio_escolar` o
`id_periodo_academico`. **Errores:** `404 docente_no_encontrado`, `404 actividad_no_encontrada`,
`422 validacion`.

---

## Dashboard (CU033, CU034 y CU035)

Supervisor y Directivo (CU033 Dashboard del Supervisor, CU034 Consolidado de libros leídos, CU035 Dashboard
del Directivo). El Docente no tiene Dashboard (`403 sin_permiso`). El Directivo solo ve agregados: los
endpoints con nombres de alumnos, listados nominales o detalle de un colegio son **solo del Supervisor**.

| Endpoint | Rol | Bloque | Filtros |
| --- | --- | --- | --- |
| `GET /dashboard/filtros` | Supervisor, Directivo | Carga inicial (CU033 y CU035, paso 3) | — |
| `GET /dashboard/distribucion-subprograma` | Supervisor | Distribución por subprograma (CU033) | del periodo |
| `GET /dashboard/resultados-aprendizaje` | Supervisor, Directivo | Resultados de aprendizaje (CU033 paso 6, CU035 paso 6) | del periodo |
| `GET /dashboard/comparacion-resultados` | Supervisor | Comparación de resultados (CU033 paso 7) | del periodo |
| `GET /dashboard/asistencia` | Supervisor, Directivo | Asistencia de alumnos (CU033 paso 8, CU035 paso 7) | del periodo |
| `GET /dashboard/asistencia/clases` | Supervisor | Detalle por grado, sección y clase (CU033) | del periodo |
| `GET /dashboard/libros` | Supervisor, Directivo | Consolidado de libros (CU034; CU035 paso 5) | anuales |
| `GET /dashboard/libros/colegios/{id_colegio}` | Supervisor | Detalle de un colegio por grado y alumno (CU034 paso 6) | anuales |
| `GET /dashboard/libros/desglose` | Supervisor | Desglose de la cantidad de un alumno en un mes (CU034 paso 8) | anuales |
| `GET /dashboard/evolucion-anual` | Supervisor, Directivo | Cobertura y evolución anual (CU033 paso 9, CU035 paso 8) | anuales |
| `GET /dashboard/comparacion-colegios` | Supervisor, Directivo | Comparación entre colegios (CU035 paso 9) | según el indicador |

> **Resumen anual de CU035 (paso 5)** = `GET /dashboard/evolucion-anual` (el año seleccionado: alumnos y
> colegios atendidos) + `GET /dashboard/libros` → `resumen` (total y promedio de libros por alumno). No hay un
> endpoint aparte.

### Filtros comunes

Todos los bloques (salvo `/dashboard/filtros`) reciben en el query:

| Parámetro | Bloques del periodo | Bloques anuales | Regla |
| --- | --- | --- | --- |
| `id_anio_escolar` | obligatorio | obligatorio | 422 si no existe. |
| `id_periodo_academico` | **obligatorio** | **prohibido** | Debe ser un PA del año. Del periodo sin él: 422 "Selecciona un periodo académico."; anual con él: 422 "Este indicador es anual: no elijas un periodo académico.". |
| `todos_los_colegios` | `true` o … | `true` o … | Todo el alcance (los colegios activos). |
| `id_colegios` | … la lista (repetido: `?id_colegios=1&id_colegios=4`) | ídem | **Exactamente uno de los dos.** |

Errores de los filtros (`campo` en `errores`):

- `422 id_colegios`: ni `todos_los_colegios` ni lista (o lista vacía): "Selecciona un año escolar y al menos un
  colegio." · los dos a la vez: "Elige todos los colegios o una lista de colegios, no ambas cosas." · repetidos:
  "No repitas colegios en la selección." · uno que no existe: "Uno de los colegios seleccionados no existe.".
- `403 colegio_fuera_de_alcance`: un colegio que existe pero no está en el alcance (p. ej. inactivo).
- `422 calendario_no_configurado` (solo bloques del periodo): a algún colegio le falta el calendario aplicable con
  ese PA. `detail` = "No hay un calendario configurado para todos los colegios seleccionados. Revisa el año
  escolar o la selección de colegios." y `colegios_sin_calendario: [{id, nombre}]`. Ningún colegio se excluye en
  silencio.

### `contexto` (en todas las respuestas)

```json
{ "contexto": {
    "anio_escolar": { "id": 2, "nombre": "2026" },
    "periodo": { "id": 9, "numero": 2, "nombre": "Segundo periodo" },
    "colegios": [{ "id": 1, "nombre": "Amashca" }, { "id": 4, "nombre": "Huaripampa" }],
    "alcance": "varios",
    "fecha_consulta": "2026-10-29T15:00:00Z",
    "intervalo": { "desde": "2026-05-18", "hasta": "2026-07-17" },
    "parcial": true } }
```

`periodo` es `null` en los bloques anuales. `alcance`: `todos` (con `todos_los_colegios`), `uno` o `varios`.
`colegios` va ordenado por nombre sin mayúsculas ni tildes. `intervalo`: del primer inicio al último fin de ese PA
entre los colegios (en un bloque anual, las fechas del año; en la evolución anual, de todos los años incluidos).
`parcial`: hoy está dentro del intervalo (acumulado hasta la fecha de consulta).

### Estados que el front tiene que distinguir

| Valor | Significa | Cómo mostrarlo |
| --- | --- | --- |
| `null` / `sin_informacion` | No hay datos: ningún estado, cantidad ni evaluación. | "Sin información" o el `mensaje` del bloque. **Nunca 0.** |
| `0` (con estado `completo` / `con_datos`) | Un cero confirmado (p. ej. solo ausentes o "No realizada"). | Como 0. Puede tener posición. |
| `futuro` | El mes o el año todavía no empieza. | Sin punto ni valor; no es información faltante. |
| `en_curso` | El mes contiene hoy: acumulado hasta la fecha de consulta. | Rotular "En curso". |
| `incompleto` / `provisional` / `evaluacion_incompleta` / `registro_incompleto` | Hay datos, pero faltan registros esperados. | Mostrar el valor con el aviso; la posición es provisional. En libros, la `etiqueta` "Total registrado hasta el momento". |
| `no_aplica` | El subprograma no está configurado en ese colegio. | "No aplica": sin cero ni posición. |
| `periodo_no_iniciado` (en `colegios_sin_informacion`) | El PA de ese colegio todavía no empezó: no se suma. | Aviso junto al bloque. |
| `posicion: null` | Sin con quién compararlo (un solo colegio, o uno solo con datos) o en vistas sin índice. | Sin posición. |

Los porcentajes se calculan sobre la suma del conjunto (nunca promediando porcentajes) y llevan 1 decimal; los
índices y promedios, 2 decimales. **El orden usa el valor exacto.** Empates exactos: misma posición y la siguiente
salta (`1, 1, 3`), en orden alfabético (colegios) o escolar (grados).

### `GET /dashboard/filtros` — Supervisor y Directivo

Carga inicial. Sin parámetros.

```json
{ "colegios": [{ "id": 1, "nombre": "Amashca" }],
  "anios": [{ "id": 2, "nombre": "2026", "periodos": [
      { "id": 9, "numero": 1, "nombre": "Primer periodo" }, { "id": 10, "numero": 2, "nombre": "Segundo periodo" },
      { "id": 11, "numero": 3, "nombre": "Tercer periodo" }, { "id": 12, "numero": 4, "nombre": "Cuarto periodo" }] }],
  "seleccion_inicial": { "id_anio_escolar": 2, "id_periodo_academico": 10 } }
```

`seleccion_inicial`: el PA en que están hoy todos los colegios del alcance; en un receso, el PA anterior y su año
(aunque el receso esté en el año calendario siguiente). `null` si no coinciden o si hoy no cae en ningún PA ni PR
configurado: el front muestra "Selecciona un año escolar y un periodo académico para consultar los indicadores.".

### `GET /dashboard/distribucion-subprograma` — Supervisor

Filtros del periodo. Alumnos por subprograma según su pertenencia en la **fecha de corte** de su colegio
(`min(fin del PA, hoy)`), sin excluirlos por su estado actual.

```json
{ "contexto": { "…": "…" },
  "fechas_corte": [{ "colegio": { "id": 1, "nombre": "Amashca" }, "fecha": "2026-06-17" }],
  "colegios_sin_informacion": [{ "colegio": { "id": 4, "nombre": "Huaripampa" }, "motivo": "periodo_no_iniciado" }],
  "total": 120,
  "subprogramas": [
    { "subprograma": { "id": 1, "nombre": "Alfabetización" }, "alumnos": 70, "porcentaje": 58.3 },
    { "subprograma": { "id": 2, "nombre": "Comprensión Lectora" }, "alumnos": 50, "porcentaje": 41.7 }],
  "sin_datos": false, "mensaje": null }
```

Sin alumnos: `sin_datos: true`, `total: null`, `subprogramas: []` y `mensaje: "No hay información disponible para
esta selección."`. Un alumno cuenta una vez; solo en grupos con el subprograma configurado.

### `GET /dashboard/resultados-aprendizaje` — Supervisor y Directivo

Filtros del periodo. Por subprograma configurado: el resultado `global` (nivel del programa de la decisión vigente
del Registro de Vuelo, `fuente: "registro_vuelo"`) y las dimensiones `fluidez` y `comprension` (última rúbrica
**completa** del PA, `fuente: "rubrica"`).

```json
{ "contexto": { "…": "…" }, "fechas_corte": ["…"], "colegios_sin_informacion": [],
  "subprogramas": [{
    "subprograma": { "id": 1, "nombre": "Alfabetización" }, "total_a_evaluar": 70,
    "distribuciones": [
      { "resultado": "global", "fuente": "registro_vuelo",
        "niveles": [{ "nombre": "Inicio", "alumnos": 10, "porcentaje": 20.0 },
                    { "nombre": "En proceso", "alumnos": 20, "porcentaje": 40.0 },
                    { "nombre": "Logrado", "alumnos": 15, "porcentaje": 30.0 },
                    { "nombre": "Avanzado", "alumnos": 5, "porcentaje": 10.0 }],
        "evaluados": 50, "pendientes": 20, "base_porcentaje": "evaluados", "mensaje": null },
      { "resultado": "fluidez", "fuente": "rubrica", "niveles": ["… Pre-inicio, Inicio, En proceso, Logrado, Avanzado"],
        "evaluados": 0, "pendientes": 70, "base_porcentaje": "evaluados",
        "mensaje": "No hay evaluaciones completas para esta selección." },
      { "resultado": "comprension", "fuente": "rubrica", "…": "…" }] }],
  "sin_datos": false, "mensaje": null }
```

Todos los niveles de cada escala, también los de 0. Porcentajes sobre `evaluados`; con `evaluados == 0`, los
`porcentaje` van en `null` y aparece el `mensaje`. `pendientes = total_a_evaluar - evaluados`. Sin alumnos a
evaluar: `sin_datos: true` con "No hay información disponible para esta selección.".

### `GET /dashboard/comparacion-resultados` — Supervisor

Filtros del periodo, más:

| Parámetro | Valores |
| --- | --- |
| `id_programa` | obligatorio (422 si no existe) |
| `todos_los_grados` o `id_grados` (repetido) | exactamente uno (422 en `id_grados`: "Selecciona al menos un grado.", ambos, repetidos o inexistentes) |
| `vista` | `resumen_colegio` (por defecto) · `detalle_grado` |
| `resultado` | `global` (por defecto) · `fluidez` · `comprension` · `nivel_alfabetico` |

```json
{ "contexto": { "…": "…" }, "fechas_corte": ["…"], "colegios_sin_informacion": [],
  "subprograma": { "id": 1, "nombre": "Alfabetización" }, "grados": [{ "id": 1, "nombre": "1.º" }],
  "vista": "resumen_colegio", "resultado": "global",
  "escala": [{ "codigo": "Inicio", "nombre": "Inicio", "valor_indice": 1 }, "… En proceso 2, Logrado 3, Avanzado 4"],
  "ranking": [{ "posicion": 1, "provisional": false, "evaluacion_incompleta": false,
                "colegio": { "id": 1, "nombre": "Amashca" }, "grado": null, "indice": 3.25,
                "total_a_evaluar": 20, "evaluados": 20, "pendientes": 0,
                "distribucion": [{ "codigo": "Inicio", "alumnos": 1, "porcentaje": 5.0 }, "…"] }],
  "sin_evaluaciones": [{ "colegio": { "…": "…" }, "grado": null, "total_a_evaluar": 8, "evaluados": 0, "pendientes": 8 }],
  "no_aplica": [{ "colegio": { "…": "…" }, "grado": null }] }
```

Índice (solo `global`) = suma de 1 a 4 de los evaluados / evaluados, sobre el conjunto del colegio (no promedia
grados). Con pendientes: `evaluacion_incompleta` y `provisional`. En `fluidez`, `comprension` y
`nivel_alfabetico`, `indice`, `posicion` y `provisional` van en `null`, la lista va en orden alfabético y
`valor_indice` es `null`. Nivel alfabético: las letras de `nivel_razkids` de `aa` a `Z`. Un grupo configurado sin
alumnos no aparece en ninguna lista.

### `GET /dashboard/asistencia` — Supervisor y Directivo

Filtros del periodo. Clases de la programación (habituales, reprogramadas en su fecha vigente y adicionales con
asistencia) del PA de cada colegio hasta hoy.

```json
{ "contexto": { "…": "…" },
  "resumen": { "porcentaje": 92.3, "presentes": 400, "tardanzas": 20, "ausentes": 35, "sin_marcar": 12,
               "clases_dictadas": 30, "clases_incompletas": 2, "clases_sin_registro": 3, "provisional": true,
               "asistencias_esperadas": 467, "asistencias_completadas": 455, "mensaje": null },
  "mensual": [
    { "anio": 2026, "mes": 5, "estado": "con_datos", "porcentaje": 90.1, "presentes": 120, "tardanzas": 5,
      "ausentes": 14, "sin_marcar": 0, "provisional": false },
    { "anio": 2026, "mes": 6, "estado": "en_curso", "porcentaje": 93.0, "…": "…" },
    { "anio": 2026, "mes": 7, "estado": "futuro", "porcentaje": null, "presentes": null, "tardanzas": null,
      "ausentes": null, "sin_marcar": null, "provisional": null }],
  "por_colegio": [{ "colegio": { "id": 1, "nombre": "Amashca" }, "porcentaje": 91.0, "…": "mismos campos" }],
  "colegios_sin_informacion": [] }
```

Porcentaje = (P + T) / (P + T + A) × 100; sin marcar no es ausencia. Sin estados: `porcentaje: null` y
`mensaje: "Sin información"`. `provisional`: hay clases dictadas con alumnos sin marcar. `clases_sin_registro`:
programadas ya transcurridas sin ninguna asistencia (no son dictadas ni ausencias). `mensual.estado`:
`con_datos`, `sin_informacion` (mes pasado sin estados), `en_curso` o `futuro`.

### `GET /dashboard/asistencia/clases` — Supervisor

Filtros del periodo, más `id_colegio` (obligatorio, de la selección) e `id_grado` / `id_seccion` opcionales.

```json
{ "contexto": { "…": "…" },
  "grupos": [{
    "grado": { "id": 3, "nombre": "3.º" }, "seccion": { "id": 7, "nombre": "A" },
    "resumen": { "porcentaje": 89.5, "…": "mismos campos que por_colegio" },
    "clases": [{ "id_clase": 55, "fecha": "2026-06-08", "tipo": "habitual", "reprogramada": false,
                 "lista_historica": 20, "presentes": 15, "tardanzas": 2, "ausentes": 2, "sin_marcar": 1,
                 "porcentaje": 89.5, "estado": "registro_incompleto" }],
    "clases_sin_registro": [{ "id_clase": null, "fecha": "2026-06-15", "tipo": "habitual", "reprogramada": false,
                              "fecha_original": "2026-06-15" }] }] }
```

`estado`: `completa` o `registro_incompleto`. `id_clase` abre CU022 en consulta (`null` en una habitual que nunca
tuvo fila). **Errores:** `422 id_colegio` si no existe o no está en la selección; `403 colegio_fuera_de_alcance`;
`422 id_grado` / `id_seccion` si no son de ese colegio.

### `GET /dashboard/libros` — Supervisor y Directivo

Filtros anuales. Libros (LSN + LSL, con relecturas) atribuidos al mes de la clase de referencia, en el año de cada
colegio hasta hoy.

```json
{ "contexto": { "…": "…", "periodo": null },
  "resumen": { "total": 340, "estado": "incompleto", "provisional": true, "alumnos_incluidos": 120,
               "promedio": 2.83, "etiqueta": "Total registrado hasta el momento" },
  "meses": [{ "anio": 2026, "mes": 3, "estado": "completo", "total": 40, "en_curso": false },
            { "anio": 2026, "mes": 6, "estado": "incompleto", "total": 25, "en_curso": true },
            { "anio": 2026, "mes": 11, "estado": "futuro", "total": null, "en_curso": false }],
  "colegios": [{ "posicion": 1, "provisional": false, "colegio": { "id": 1, "nombre": "Amashca" },
                 "total": 120, "estado": "completo", "alumnos_incluidos": 40, "promedio": 3.0, "meses": ["…"] }],
  "colegios_sin_informacion": [{ "colegio": { "…": "…" }, "alumnos_incluidos": 0, "motivo": "sin_alumnos" }] }
```

`estado`: `completo`, `incompleto` (hay cantidades y faltan registros esperados de Seguimiento de Lectura),
`sin_informacion` (`total: null`) o `futuro`. Promedio = total / alumnos incluidos (las listas históricas; un
alumno una vez por colegio). Sin promedio (`motivo`: `sin_alumnos`, `sin_informacion` o `sin_calendario`): en
`colegios_sin_informacion`, sin posición.

### `GET /dashboard/libros/colegios/{id_colegio}` — Supervisor

Filtros anuales; el colegio debe estar en la selección.

```json
{ "contexto": { "…": "…" }, "colegio": { "id": 1, "nombre": "Amashca" },
  "resumen": { "…": "como en /libros, del colegio" }, "meses": ["…"],
  "grados": [{ "posicion": 1, "provisional": false, "grado": { "id": 3, "nombre": "3.º" }, "total": 50,
               "estado": "completo", "alumnos_incluidos": 15, "promedio": 3.33,
               "alumnos": [{ "alumno": { "id": 9, "nombre": "Ana Torres" }, "total": 4, "estado": "completo",
                             "meses": [{ "anio": 2026, "mes": 3, "estado": "completo", "total": 2, "en_curso": false }] }] }],
  "grados_sin_informacion": [] }
```

El grado de cada cantidad es el de la pertenencia en la fecha de la clase de referencia: un alumno que cambió de
grado aparece en los dos y cuenta una vez en el colegio. Alumnos por apellidos y nombres sin mayúsculas ni tildes.
**Errores:** como `/asistencia/clases` (`id_colegio`).

### `GET /dashboard/libros/desglose` — Supervisor

Filtros anuales, más `id_alumno`, `anio` y `mes` (1 a 12).

```json
{ "contexto": { "…": "…" }, "alumno": { "id": 9, "nombre": "Ana Torres" }, "anio": 2026, "mes": 3, "total": 2,
  "registros": [{ "id_lectura_semanal": 31, "clase_referencia": { "id_clase": 55, "fecha": "2026-03-09" },
                  "subprograma": { "id": 1, "nombre": "Alfabetización" }, "colegio": { "…": "…" },
                  "grado": { "…": "…" }, "seccion": { "…": "…" }, "estado_tabla": "normal",
                  "lsn": 1, "lsl": 1, "total": 2 }] }
```

`id_lectura_semanal` abre el Seguimiento de Lectura en consulta. `lsl` y `total` en `null` si la fila no tiene
cantidades. **Errores:** `422 id_alumno` si no existe o no perteneció en el año a ningún colegio seleccionado;
`403 colegio_fuera_de_alcance` si solo perteneció a colegios fuera del alcance.

### `GET /dashboard/evolucion-anual` — Supervisor y Directivo

Filtros anuales. Los años con información hasta el seleccionado (incluido), en orden.

```json
{ "contexto": { "…": "…", "intervalo": { "desde": "2025-03-03", "hasta": "2026-11-14" } },
  "anios": [
    { "anio_escolar": { "id": 1, "nombre": "2025" }, "parcial": false, "alumnos_atendidos": 300,
      "colegios_atendidos": 9, "libros": { "total": 2100, "estado": "completo" },
      "colegios": [{ "colegio": { "id": 1, "nombre": "Amashca" }, "alumnos_atendidos": 35 }] },
    { "anio_escolar": { "id": 2, "nombre": "2026" }, "parcial": true, "alumnos_atendidos": 340,
      "colegios_atendidos": 9, "libros": { "total": 1800, "estado": "incompleto" }, "colegios": ["…"] }],
  "mensaje": null }
```

Alumnos únicos por pertenencia histórica (un alumno que cambió de colegio cuenta una vez en el total y en cada
colegio donde estuvo); no se suman los PA. Un año sin alumnos atendidos no aparece. Un solo año: `mensaje: "No hay
información de otros años para mostrar la evolución."`. Sin variaciones interanuales. `libros.total` es `null` si
no hay datos.

### `GET /dashboard/comparacion-colegios` — Supervisor y Directivo

Filtros comunes, más `indicador`: `nivel_global_alfabetizacion`, `nivel_global_comprension`, `asistencia` (los tres
con `id_periodo_academico` **obligatorio**) o `promedio_libros` (**sin** periodo: acumulado del año).

```json
{ "contexto": { "…": "…" }, "indicador": "asistencia", "intervalo": "periodo",
  "ranking": [{ "posicion": 1, "provisional": false, "colegio": { "id": 1, "nombre": "Amashca" }, "valor": 93.5,
                "poblacion": { "descripcion": "asistencias registradas", "cantidad": 455 }, "advertencias": [] }],
  "sin_informacion": [{ "colegio": { "…": "…" }, "motivo": "sin_estados_registrados" }],
  "mensaje": null }
```

`valor` es el del bloque correspondiente con los mismos filtros: índice 1-4 de `comparacion-resultados`
(`resumen_colegio`, todos los grados; población: alumnos evaluados), porcentaje de `asistencia` (asistencias
registradas) o promedio de `libros` (alumnos incluidos). `advertencias`: `evaluacion_incompleta`,
`registro_incompleto`, `clases_sin_registro`, `informacion_incompleta`. Un solo colegio: sin posiciones y
`mensaje: "Selecciona al menos dos colegios para compararlos."`; varios con uno solo con datos: sin posiciones y
`mensaje: "No hay información suficiente de otros colegios para establecer una comparación."`. Motivos de
`sin_informacion`: `no_aplica`, `sin_evaluaciones`, `sin_estados_registrados`, `periodo_no_iniciado`,
`sin_alumnos`, `sin_informacion`, `sin_calendario`. No combina indicadores en una puntuación.

---

## Errores comunes

| Código | `motivo` | `detail` | Cuándo |
| --- | --- | --- | --- |
| `401` | `sesion_invalida` | "La sesión no es válida. Inicia sesión nuevamente." | Sin token, token malformado, sesión cerrada o inexistente |
| `401` | `sesion_expirada` | "La sesión ha expirado. Inicia sesión nuevamente." | Pasaron las 8 horas de la sesión |
| `403` | `sin_permiso` | "No tienes permisos para realizar esta acción" | El rol no puede usar el endpoint |
| `403` | `*_fuera_de_alcance` | (según el recurso) | El Docente pide algo fuera de su alcance |
| `404` | `*_no_encontrado` / `*_no_encontrada` | (según el recurso) | El recurso de la ruta no existe |
| `409` | `actividad_requerida` | "Debes iniciar una actividad de trabajo para registrar o modificar información." | El Docente escribe en Alumnos sin actividad en curso (en los módulos de registro llega como rechazo de la operación) |
| `409` | `modificacion_concurrente` | (según el recurso) | La `version` enviada ya no es la vigente |
| `422` | `validacion` | "Los datos enviados no son válidos." (o el mensaje de la regla) | Ver el formato al inicio |

Los `401` traen la cabecera `WWW-Authenticate: Bearer`. Las rutas inexistentes responden
`404 {"detail": "Recurso no encontrado."}` y los métodos no admitidos, `405 {"detail": "Método no permitido."}`.

---

## CORS

Orígenes permitidos por defecto (variable `CORS_ORIGINS`, separados por coma):

```env
CORS_ORIGINS=https://localhost:5173,https://127.0.0.1:5173,https://localhost:3000,https://127.0.0.1:3000
```

`localhost` y `127.0.0.1` son orígenes distintos para el navegador: van ambos.

---

## Usuarios de prueba

Los crea `python -m app.seed_data` (`run.py` lo ejecuta salvo con `--sin-seed`).

| Rol | Correo | Contraseña |
| --- | --- | --- |
| `Docente` | `profesor.prueba@sicedu.test` | `ProfesorTest123` |
| `Supervisor` | `jefa.prueba@sicedu.test` | `JefaTest123` |
| `Directivo` | `directivo.prueba@sicedu.test` | `DirectivoTest123` |

Las cuentas creadas con `POST /usuarios` reciben una contraseña temporal por correo; si `GMAIL_SMTP_USER` y
`GMAIL_SMTP_APP_PASSWORD` no están configurados, la respuesta trae `correo_enviado: false` y la contraseña en
`contraseña_temporal`.

---

## Levantar el backend

```bash
python run.py
```

1. Verifica que Docker Desktop esté corriendo.
2. Crea o levanta el contenedor PostgreSQL `sicedu-db` en el puerto `5433`.
3. Crea el entorno virtual `venv` e instala dependencias si hace falta.
4. Genera `.env` si no existe.
5. Ejecuta `alembic upgrade head`, carga los datos maestros y el seed.
6. Arranca `uvicorn` con recarga automática.

Opciones: `python run.py --puerto 8001` · `python run.py --sin-seed`.

La API queda en `http://127.0.0.1:8000` y el Swagger en `http://127.0.0.1:8000/docs`.
