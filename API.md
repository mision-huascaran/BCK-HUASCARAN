# SICEDU API — Referencia para el frontend

**Rama:** `development` · **Base URL (desarrollo):** `http://127.0.0.1:8000`
**Formato:** JSON · **Autenticación:** JWT Bearer

Esta referencia refleja el estado real del backend actual: autenticación, autorización por rol y endpoints de creación/consulta de colegios, alumnos, profesores y catálogos.

---

## Índice

- [Flujo de autenticación](#flujo-de-autenticación)
- [Roles](#roles)
- [Endpoints principales](#endpoints-principales)
- [Errores](#errores)
- [CORS](#cors)
- [Usuarios de prueba](#usuarios-de-prueba)
- [Levantar el backend](#levantar-el-backend)

---

## Flujo de autenticación

1. El usuario envía correo y contraseña a `POST /login`.
2. La API devuelve un `access_token` JWT con vigencia de `480` minutos (8 horas).
3. El frontend guarda el token y lo manda en cada petición protegida como:
   `Authorization: Bearer <access_token>`.
4. `GET /me` devuelve la información del usuario autenticado.
5. `POST /logout` es de cierre local: el backend no invalida el JWT; el frontend debe borrar el token del almacenamiento.

---

## Roles

| `id_rol` | Nombre       |
| -------- | ------------ |
| `1`      | `Docente`    |
| `2`      | `Supervisor`  |
| `3`      | `Directivo`  |

El backend ya implementa validación por rol con `require_role(...)` en `app/dependencies.py`.

- `POST /colegios` → requiere `Supervisor`
- `POST /alumnos` → requiere `Supervisor`
- `POST /profesores` → requiere `Supervisor`
- `GET /colegios`, `GET /grados`, `GET /programas` → cualquier usuario autenticado
- `GET /me` → cualquier usuario autenticado

---

## Endpoints principales

### `GET /`

Comprobación de salud del backend.

**Respuesta `200`:**

```json
{ "status": "ok" }
```

---

### `POST /login`

Autentica al usuario y devuelve un token.

**Body (`application/json`):**

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

**Errores:**

- `401` si el correo no existe o la contraseña es incorrecta.
- `403` si la contraseña es correcta pero la cuenta está desactivada.
- `422` si el body no es válido.

---

### `GET /me`

Devuelve el usuario autenticado.

**Headers:**

```http
Authorization: Bearer <access_token>
```

**Respuesta `200`:**

```json
{
  "id_usuario": 1,
  "id_rol": 2,
  "correo": "jefa.prueba@sicedu.test",
  "id_docente": null,
  "nombres": "Jefa",
  "apellidos": "de Prueba",
  "activo": true
}
```

---

### `POST /logout`

Cierre de sesión local. El backend responde con éxito pero no revoca el token real.

**Respuesta `200`:**

```json
{ "detail": "Sesión cerrada correctamente" }
```

---

### `POST /colegios` — requiere `Supervisor`

**Body:**

```json
{
  "nombre": "Colegio Yungay",
  "zona": "Yungay"
}
```

**Respuesta `200`:**

```json
{
  "id_colegio": 3,
  "nombre": "Colegio Yungay",
  "zona": "Yungay",
  "creado_por": 1,
  "creado_en": "2026-09-22",
  "modificado_por": null,
  "modificado_en": null
}
```

**Errores:**

- `403` si el usuario autenticado no tiene rol `Supervisor`

---

### `GET /colegios` — cualquier usuario autenticado

Devuelve un array con todos los colegios.

**Respuesta `200`:**

```json
[
  {
    "id_colegio": 1,
    "nombre": "Colegio de Prueba",
    "zona": "Zona de Prueba",
    "creado_por": 1,
    "creado_en": "2026-09-22",
    "modificado_por": null,
    "modificado_en": null
  }
]
```

---

### `POST /alumnos` — requiere `Supervisor`

**Body:**

```json
{
  "nombres": "Ana",
  "apellidos": "Pérez",
  "id_colegio": 1,
  "id_grado": 1,
  "id_programa_actual": 1,
  "activo": true
}
```

**Respuesta `200`:**

```json
{
  "id_alumno": 1,
  "nombres": "Ana",
  "apellidos": "Pérez",
  "id_colegio": 1,
  "id_grado": 1,
  "id_programa_actual": 1,
  "fecha_registro": "2026-09-22",
  "activo": true,
  "creado_por": 1,
  "creado_en": "2026-09-22",
  "modificado_por": null,
  "modificado_en": null
}
```

**Errores:**

- `404` si `id_colegio`, `id_grado` o `id_programa_actual` no existe.
- Ejemplo: `{"detail": "No existe un grado con id_grado=999"}`

---

### `GET /grados` — cualquier usuario autenticado

Devuelve el catálogo de grados.

**Respuesta `200`:**

```json
[
  { "id_grado": 1, "nombre": "1.º", "id_ciclo": 1 },
  { "id_grado": 2, "nombre": "2.º", "id_ciclo": 1 }
]
```

---

### `GET /programas` — cualquier usuario autenticado

Devuelve el catálogo de programas.

**Respuesta `200`:**

```json
[
  { "id_programa": 1, "nombre": "Alfabetización" },
  { "id_programa": 2, "nombre": "Comprensión Lectora" }
]
```

---

### `POST /profesores` — requiere `Supervisor`

**Body:**

```json
{
  "nombres": "Luis",
  "apellidos": "Ramos",
  "correo": "luis.ramos@sicedu.test",
  "activo": true
}
```

**Respuesta `200`:**

```json
{
  "id_usuario": 4,
  "id_rol": 1,
  "correo": "luis.ramos@sicedu.test",
  "id_docente": 2,
  "nombres": "Luis",
  "apellidos": "Ramos",
  "activo": true,
  "contraseña_temporal": "A1b2C3d4E5"
}
```

**Comportamiento especial de `contraseña_temporal`:**

- Si el correo de bienvenida se envía bien, la respuesta devuelve `null`.
- Si el envío falla, el valor real vuelve en la respuesta para que el Supervisor lo entregue manualmente.
- Actualmente, con las credenciales de Gmail de juguete, el envío falla con frecuencia; por eso es normal ver la contraseña temporal en la respuesta.

**Errores:**

- `409` si el correo ya existe.
- `500` si no existe el rol `Docente` en el catálogo.

---

## Errores

### `401 Unauthorized`

El backend devuelve un `detail` tipo string.

```json
{ "detail": "Credenciales inválidas o sesión expirada" }
```

Situaciones:

- token faltante
- token inválido
- token expirado
- usuario deshabilitado

### `403 Forbidden`

Se usa para autorización por rol.

```json
{ "detail": "No tienes permisos para realizar esta acción" }
```

### `404 Not Found`

Usado cuando un FK no existe.

```json
{ "detail": "No existe un grado con id_grado=999" }
```

### `422 Unprocessable Entity`

Se usa cuando falta un campo o el payload no es válido.

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

---

## CORS

La API acepta peticiones desde navegadores en varios orígenes locales por defecto.

**Configuración por defecto:**

```env
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000
```

Esto permite que un frontend en Vite o React pueda consumir la API sin proxy.

---

## Usuarios de prueba

Los usuarios se crean con `python -m app.seed_data`.

| Rol        | Correo                         | Contraseña        |
| ---------- | ------------------------------ | ----------------- |
| `Docente`  | `profesor.prueba@sicedu.test`  | `ProfesorTest123` |
| `Supervisor` | `jefa.prueba@sicedu.test`     | `JefaTest123`     |
| `Directivo` | `directivo.prueba@sicedu.test` | `DirectivoTest123` |

El usuario de rol `Docente` tiene `id_docente` asociado. Los de `Supervisor` y `Directivo` no lo tienen.

---

## Pendiente

Lo que aún no está implementado en el backend:

- Refresh token / renovación de sesión
- CRUD completo de alumnos, colegios y profesores
- Edición y eliminación de registros
- Endpoints de evaluaciones y periodos
- Control de permisos más granular por módulo

---

## Levantar el backend

Desde la raíz del proyecto, usa este comando:

```bash
python run.py
```

El script hace lo siguiente:

1. Verifica que Docker Desktop esté corriendo.
2. Crea o levanta el contenedor PostgreSQL `sicedu-db` en el puerto `5433`.
3. Crea el entorno virtual `venv` e instala dependencias si hace falta.
4. Genera `.env` si no existe.
5. Ejecuta `alembic upgrade head`.
6. Carga el seed con `python -m app.seed_data`.
7. Arranca `uvicorn` con recarga automática.

Opciones:

```bash
python run.py --puerto 8001
python run.py --sin-seed
```

El API queda en `http://127.0.0.1:8000` y el Swagger en `http://127.0.0.1:8000/docs`.

La configuración mínima del `.env` debe incluir:

```env
DATABASE_URL=postgresql+psycopg2://admin:postgres123@localhost:5433/sicedu
JWT_SECRET_KEY=dev-secret-key-cambiar-en-produccion
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=480
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000
```