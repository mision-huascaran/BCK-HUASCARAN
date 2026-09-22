# SICEDU API — Referencia para el frontend

**Rama:** `development` · **Base URL (desarrollo):** `http://127.0.0.1:8000`
**Formato:** JSON · **Autenticación:** JWT Bearer

Todos los endpoints de esta referencia están verificados contra el servidor en
ejecución. Lo que aparece aquí responde tal cual está escrito.

---

## Índice

- [Flujo de autenticación](#flujo-de-autenticación)
- [Endpoints](#endpoints)
- [El token JWT](#el-token-jwt)
- [Roles](#roles)
- [Usuarios de prueba](#usuarios-de-prueba)
- [Errores](#errores)
- [CORS](#cors)
- [Levantar el backend](#levantar-el-backend)

---

## Flujo de autenticación

1. El usuario envía correo y contraseña a `POST /login`.
2. La API devuelve un `access_token` (JWT, válido **480 minutos = 8 horas**).
3. El frontend guarda el token y lo manda en cada petición protegida:
   `Authorization: Bearer <access_token>`.
4. `GET /me` devuelve los datos del usuario autenticado.
5. `POST /logout` es simbólico: el token se invalida **borrándolo en el cliente**.

---

## Endpoints

### `GET /`

Comprobación de que el servicio responde. No requiere token.

**Respuesta `200`:**

```json
{ "status": "ok" }
```

---

### `POST /login`

Autentica al usuario y devuelve el token. No requiere token.

**Body (`application/json`):**

| Campo      | Tipo     | Requerido | Descripción                |
| ---------- | -------- | --------- | -------------------------- |
| `correo`   | `string` | Sí        | Correo del usuario.        |
| `password` | `string` | Sí        | Contraseña en texto plano. |

```json
{
  "correo": "profesor.prueba@sicedu.test",
  "password": "ProfesorTest123"
}
```

**Respuesta `200`:**

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

| Campo          | Tipo     | Descripción                        |
| -------------- | -------- | ---------------------------------- |
| `access_token` | `string` | JWT firmado. Expira a las 8 horas. |
| `token_type`   | `string` | Siempre `"bearer"`.                |

**Respuesta `401`** — credenciales incorrectas, o usuario con `activo = false`:

```json
{ "detail": "Correo o contraseña incorrectos" }
```

> El mismo mensaje cubre los tres casos (correo inexistente, contraseña errónea,
> usuario desactivado). Es intencional: no revela si un correo está registrado.
> En la interfaz, muéstralo tal cual y no intentes distinguir la causa.

**Respuesta `422`** — falta un campo o el body no es válido. Ver [Errores](#errores).

---

### `GET /me`

Datos del usuario autenticado. **Requiere token.**

**Headers:**

```
Authorization: Bearer <access_token>
```

**Recibe:** nada más (sin body ni parámetros).

**Respuesta `200`:**

```json
{
  "id_usuario": 1,
  "id_rol": 1,
  "correo": "profesor.prueba@sicedu.test",
  "id_docente": 1,
  "nombres": "Docente",
  "apellidos": "de Prueba",
  "activo": true
}
```

| Campo        | Tipo             | Descripción                                                         |
| ------------ | ---------------- | ------------------------------------------------------------------- |
| `id_usuario` | `number`         | Identificador del usuario.                                          |
| `id_rol`     | `number`         | Rol asignado. Ver [Roles](#roles).                                  |
| `correo`     | `string`         | Correo del usuario.                                                 |
| `id_docente` | `number \| null` | Ficha de docente vinculada. **`null` si el usuario no es docente.** |
| `nombres`    | `string`         | Nombres.                                                            |
| `apellidos`  | `string`         | Apellidos.                                                          |
| `activo`     | `boolean`        | Si la cuenta está habilitada.                                       |

> `id_docente` es nulo para roles no docentes (por ejemplo `Jefa_Profesores`).
> Tipa ese campo como opcional y compruébalo antes de usarlo, o romperás la vista
> al entrar con una cuenta administrativa.

**Respuesta `401`** — sin token, token mal formado, expirado, o usuario desactivado.
Ver [Errores](#errores).

---

### `POST /logout`

Cierra la sesión. No requiere token y **no invalida el JWT en el servidor**.

**Recibe:** nada.

**Respuesta `200`:**

```json
{ "detail": "Sesión cerrada correctamente" }
```

> Importante: no hay lista de tokens revocados. Un token robado sigue siendo
> válido hasta que expire, aunque se haya llamado a `/logout`. El cierre de sesión
> real lo hace el frontend al borrar el token de su almacenamiento; llamar a este
> endpoint es opcional.

---

## El token JWT

El `access_token` es un JWT estándar. Su payload, una vez decodificado:

```json
{
  "id_usuario": 1,
  "id_rol": 1,
  "correo": "profesor.prueba@sicedu.test",
  "id_docente": 1,
  "exp": 1789716522
}
```

Puedes leer `id_rol` desde el token (con `jwt-decode`, por ejemplo) para pintar el
menú según el rol sin esperar a `GET /me`, y aprovechar `exp` (segundos Unix) para
detectar la expiración antes de lanzar la petición.

Eso sirve para la interfaz, no para la seguridad: el payload va en base64, es
legible y editable por cualquiera. Quien decide de verdad es el backend en cada
petición, así que no bases permisos reales en lo que dice el token en el cliente.

---

## Roles

| `id_rol` | Nombre            |
| -------- | ----------------- |
| `1`      | `Profesor`        |
| `2`      | `Jefa_Profesores` |
| `3`      | `Directivos`      |

La API todavía **no restringe ningún endpoint por rol**: cualquier usuario
autenticado puede llamar a `/me`. El control por rol está pendiente en el backend.

---

## Usuarios de prueba

Creados por `python -m app.seed_data`. Son ficticios, no son datos reales.

| Rol               | Correo                        | Contraseña        |
| ----------------- | ----------------------------- | ----------------- |
| `Profesor`        | `profesor.prueba@sicedu.test` | `ProfesorTest123` |
| `Jefa_Profesores` | `jefa.prueba@sicedu.test`     | `JefaTest123`     |
| `Directivos`      | `directivo.prueba@sicedu.test` | `DirectivoTest123` |

El usuario `Profesor` trae `id_docente: 1`; los de `Jefa_Profesores` y `Directivos`
traen `id_docente: null` — útil para probar los dos caminos de la interfaz.

---

## Errores

**`401 Unauthorized`** — el `detail` es un **string**:

| Situación                                   | `detail`                                     |
| ------------------------------------------- | -------------------------------------------- |
| Credenciales incorrectas en login           | `"Correo o contraseña incorrectos"`          |
| Petición sin cabecera `Authorization`       | `"Not authenticated"`                        |
| Token inválido, expirado o usuario inactivo | `"Credenciales inválidas o sesión expirada"` |

```json
{ "detail": "Credenciales inválidas o sesión expirada" }
```

**`422 Unprocessable Entity`** — el `detail` es un **array**:

```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "password"],
      "msg": "Field required",
      "input": { "correo": "x@y.z" }
    }
  ]
}
```

> `detail` cambia de tipo según el error: string en los `401`, array en los `422`.
> Si tu manejador de errores asume siempre string, un `422` mostrará `[object Object]`.
> Conviene comprobar `Array.isArray(detail)` en un único punto:

```js
function mensajeDeError(detail) {
  if (Array.isArray(detail)) return detail.map((e) => e.msg).join(", ");
  return detail ?? "Error inesperado";
}
```

---

## CORS

**Ya está configurado.** Puedes llamar a la API directamente desde el navegador,
sin proxy, y enviar tanto `Content-Type` como `Authorization`.

Orígenes permitidos por defecto:

| Origen                  | Uso habitual              |
| ----------------------- | ------------------------- |
| `http://127.0.0.1:5173` | Vite (React, Vue, Svelte) |
| `http://localhost:5173` | Vite, por nombre de host  |
| `http://127.0.0.1:3000` | Next.js, Create React App |
| `http://localhost:3000` | Next.js / CRA, por nombre |

Cada puerto está listado con las dos formas, `127.0.0.1` y `localhost`, así que
funciona entres como entres a tu aplicación.

Verificado: el preflight `OPTIONS /login` desde los cuatro orígenes devuelve `200`
con su `access-control-allow-origin` correspondiente, y `POST /login` y `GET /me`
responden con las cabeceras puestas. Un origen no listado recibe `400 Bad Request`
en el preflight y ninguna cabecera `Access-Control-*`, así que el navegador lo
bloquea.

### Si tu frontend corre en otro puerto

No hace falta tocar el código. La lista sale de la variable `CORS_ORIGINS` del
`.env`, separada por comas:

```
CORS_ORIGINS=http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:4200
```

Reinicia el servidor después de cambiarla.

> Los orígenes deben coincidir **exactamente**: el esquema, el host y el puerto.
> `http://localhost:5173` y `http://127.0.0.1:5173` son orígenes distintos para el
> navegador, igual que `http://` y `https://`. Por eso ambas formas están en la
> lista; si añades un puerto nuevo, añádelo también por duplicado.

En producción, añade ahí el dominio real del frontend. Conviene no usar `["*"]`:
con `allow_credentials=True` el comodín no es válido según la especificación y los
navegadores rechazan la respuesta.

---

## Ejemplo de consumo

```js
const API = "http://127.0.0.1:8000";

export async function login(correo, password) {
  const res = await fetch(`${API}/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ correo, password }),
  });

  const data = await res.json();
  if (!res.ok) throw new Error(mensajeDeError(data.detail));

  localStorage.setItem("token", data.access_token);
  return data;
}

export async function getMe() {
  const res = await fetch(`${API}/me`, {
    headers: { Authorization: `Bearer ${localStorage.getItem("token")}` },
  });

  if (res.status === 401) {
    localStorage.removeItem("token"); // token expirado: forzar nuevo login
    throw new Error("Sesión expirada");
  }
  return res.json();
}
```

Centraliza el manejo del `401` en un interceptor (o en un wrapper como el de
arriba): como el token dura 8 horas, la sesión caducará mientras el usuario navega,
y conviene que eso lo redirija al login desde un solo sitio.

---

## Documentación interactiva

| Ruta            | Contenido                                  |
| --------------- | ------------------------------------------ |
| `/docs`         | Swagger UI — permite probar los endpoints. |
| `/redoc`        | ReDoc — referencia de lectura.             |
| `/openapi.json` | Esquema OpenAPI 3.1.                       |

Para TypeScript, los tipos se pueden generar desde el esquema en lugar de
escribirlos a mano, y así no se desincronizan del backend:

```bash
npx openapi-typescript http://127.0.0.1:8000/openapi.json -o src/types/api.ts
```

---

## Pendiente

No existe todavía; se documentará aquí conforme se implemente:

- [ ] Restricción de endpoints por rol
- [ ] Refresh token / renovación de sesión
- [ ] Registro y gestión de usuarios
- [ ] CRUD de alumnos, colegios, grados y programas
- [ ] Endpoints de evaluaciones por periodo

Las tablas ya existen en la base de datos (`alumno`, `colegio`, `grado`, `programa`,
`periodo_academico`, `periodo_evaluacion`, `docente`, entre otras), pero ningún
endpoint las expone aún.

---

## Levantar el backend

Desde la raíz del proyecto, un solo comando:

```bash
python run.py
```

Se encarga de todo, y cada paso se salta si ya está hecho:

1. Enciende Docker Desktop si está apagado.
2. Arranca el contenedor `sicedu-db` (PostgreSQL 16, puerto 5433), o lo crea si no existe.
3. Crea el `venv` e instala dependencias (solo si cambió `requirements.txt`).
4. Crea el `.env` con valores de desarrollo si no existe.
5. Aplica las migraciones de Alembic.
6. Carga los usuarios de prueba.
7. Inicia la API con recarga automática.

Opciones: `--puerto 8001` para usar otro puerto, `--sin-seed` para no cargar datos
de prueba. `Ctrl+C` detiene la API; la base de datos sigue corriendo en Docker.

La primera vez tarda unos minutos (descarga la imagen de Postgres e instala
dependencias); las siguientes arranca en segundos.

<details>
<summary>Pasos manuales equivalentes</summary>

```bash
docker start sicedu-db
venv\Scriptsctivate
pip install -r requirements.txt
alembic upgrade head
python -m app.seed_data
uvicorn app.main:app --reload
```

</details>

Queda sirviendo en `http://127.0.0.1:8000`. Comprueba que responde:

```bash
curl http://127.0.0.1:8000/
```

El `.env` debe definir estas variables (ojo: `JWT_SECRET_KEY`, no `SECRET_KEY`;
la aplicación no arranca si falta alguna de las dos primeras):

```
DATABASE_URL=postgresql+psycopg2://admin:postgres123@localhost:5433/sicedu
JWT_SECRET_KEY=dev-secret-key-cambiar-en-produccion
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=480
CORS_ORIGINS=http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:3000,http://localhost:3000
```
