# SICEDU API — Referencia para el frontend

**Rama:** `development` · **Base URL (desarrollo):** `http://127.0.0.1:8000`
**Formato:** JSON · **Autenticación:** JWT Bearer

Esta referencia refleja el estado real del backend actual: autenticación, recuperación
de contraseña, autorización por rol, gestión de cuentas y endpoints de creación,
consulta y edición de colegios, alumnos, profesores y catálogos.

Todos los endpoints documentados están verificados contra el servidor en ejecución.

---

## Índice

- [Flujo de autenticación](#flujo-de-autenticación)
- [Roles](#roles)
- [Endpoints principales](#endpoints-principales)
- [Cambio de contraseña](#cambio-de-contraseña)
- [Recuperar contraseña sin sesión](#recuperar-contraseña-sin-sesión)
- [Cuentas de usuario](#cuentas-de-usuario)
- [Asignaciones del docente](#asignaciones-del-docente)
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
| `2`      | `Supervisor` |
| `3`      | `Directivo`  |

### El principio que ordena los permisos

> **Colegios, profesores y cuentas los crea el Supervisor. El Docente solo gestiona alumnos.**

Todo lo de abajo se deduce de esas dos frases.

- El **Docente** da de alta y edita alumnos, pero **solo los de los colegios y grados
  que tiene asignados** (ver [Asignaciones del docente](#asignaciones-del-docente)).
- El **Supervisor** queda en **solo lectura sobre alumnos**: los consulta, no los crea
  ni los edita. Gestiona colegios, profesores y cuentas de Docente y Supervisor.
- El **Directivo** tiene una **vista ejecutiva de solo lectura y sin datos
  identificables**: ve los alumnos sin nombres ni apellidos. Gestiona únicamente
  cuentas de otros Directivos.

### Matriz de permisos

| Endpoint                                    | Docente | Supervisor | Directivo |
| ------------------------------------------- | :-----: | :--------: | :-------: |
| `GET /alumnos`                              | ✅ *    | ✅         | ✅ **    |
| `POST /alumnos`                             | ✅ *    | ❌ 403     | ❌ 403    |
| `PATCH /alumnos/{id}`                       | ✅ *    | ❌ 403     | ❌ 403    |
| `GET /colegios`                             | ✅ *    | ✅         | ✅        |
| `POST /colegios` · `PATCH /colegios/{id}`   | ❌ 403  | ✅         | ❌ 403    |
| `GET /profesores`                           | ❌ 403  | ✅         | ❌ 403    |
| `POST /profesores` · `PATCH /profesores/…`  | ❌ 403  | ✅         | ❌ 403    |
| `GET /usuarios`                             | ❌ 403  | ✅ ***     | ✅ ***    |
| `POST /usuarios` · `PATCH /usuarios/…`      | ❌ 403  | ✅ ***     | ✅ ***    |
| `GET /asignaciones`                         | ✅ *    | ✅         | ❌ 403    |
| `POST` · `DELETE /asignaciones`             | ❌ 403  | ✅         | ❌ 403    |
| `GET /grados` · `GET /programas` · `GET /me`| ✅      | ✅         | ✅        |

\* Recortado a sus asignaciones vigentes.
\*\* Sin nombres ni apellidos.
\*\*\* Solo sobre las cuentas de su parcela — ver [Cuentas de usuario](#cuentas-de-usuario).

**Sin autenticación:** `POST /login`, `POST /logout`, `GET /`,
`POST /password/recuperar`, `POST /password/restablecer`.

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

Devuelve un array con los colegios. Al **Docente** solo le llegan aquellos donde tiene
una asignación vigente; al `Supervisor` y al `Directivo`, todos.

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

### `POST /alumnos` — requiere `Docente`

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

El alumno debe caer en un **colegio y grado que el docente tenga asignado**; si no,
la respuesta es `403`.

**Respuesta `201`:**

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

- `403` si quien llama no es `Docente`, o si el alumno queda fuera de sus asignaciones:
  `{"detail": "Ese alumno no pertenece a un colegio y grado que tengas asignado"}`
- `404` si `id_colegio`, `id_grado` o `id_programa_actual` no existe.
- Ejemplo: `{"detail": "No existe un grado con id_grado=999"}`

---

### `GET /alumnos` — cualquier usuario autenticado

Lista alumnos con filtros y paginación. Son cientos de registros, así que la
respuesta **siempre viene paginada**: por defecto 50 por página.

**Parámetros (todos opcionales, combinables entre sí):**

| Parámetro  | Tipo      | Descripción                                        |
| ---------- | --------- | -------------------------------------------------- |
| `colegio`  | `number`  | Filtra por `id_colegio`.                           |
| `grado`    | `number`  | Filtra por `id_grado`.                             |
| `programa` | `number`  | Filtra por `id_programa_actual`.                   |
| `q`        | `string`  | Busca en nombres y apellidos.                      |
| `activo`   | `boolean` | Filtra por estado.                                 |
| `limit`    | `number`  | Por página. Por defecto `50`, máximo `500`.        |
| `offset`   | `number`  | Registros a saltar. Por defecto `0`.               |

Ejemplo: `GET /alumnos?colegio=2&grado=1&q=perez&limit=20&offset=0`

**Respuesta `200`:**

```json
{
  "total": 413,
  "limit": 50,
  "offset": 0,
  "items": [
    {
      "id_alumno": 1,
      "nombres": "Juan Carlos",
      "apellidos": "Pérez Quispe",
      "id_colegio": 2,
      "id_grado": 1,
      "id_programa_actual": 1,
      "fecha_registro": "2026-09-24",
      "activo": true,
      "creado_por": 1,
      "creado_en": "2026-09-24",
      "modificado_por": null,
      "modificado_en": null
    }
  ]
}
```

`total` es el total que cumple el filtro, no el de la página: sirve para calcular
cuántas páginas hay (`Math.ceil(total / limit)`). Los resultados vienen ordenados
por apellidos y luego nombres.

> **Sobre `q`:** ignora mayúsculas y tildes, y busca cada palabra por separado en
> cualquier orden. `perez` encuentra a "Pérez", y `juan perez` encuentra a
> "Juan Carlos Pérez Quispe" aunque el "Carlos" quede en medio. Puedes enviar
> directamente lo que el usuario escriba en el buscador, sin normalizarlo.

#### Lo que ve cada rol

La respuesta **no es la misma** para los tres roles. El recorte lo hace el servidor.

| Rol          | Qué recibe                                                              |
| ------------ | ------------------------------------------------------------------------ |
| `Docente`    | Solo los alumnos de sus colegios y grados asignados vigentes, completos. |
| `Supervisor` | Todos los alumnos, completos.                                            |
| `Directivo`  | Todos los alumnos, pero con `nombres` y `apellidos` en **`null`**.       |

> **Para el frontend:** `nombres` y `apellidos` son **opcionales** en la respuesta. En
> la vista de Directivo llegan en `null` y hay que identificar la fila por `id_alumno`.
> Si tu tipado los declara como `string` obligatorio, esa vista romperá.
>
> Un `Docente` sin asignaciones vigentes recibe `total: 0`. No es un error: es que no
> tiene ningún grado a cargo todavía.
>
> El parámetro `q` está **prohibido para el Directivo** y responde `403`. Buscar por
> nombre sobre datos anonimizados permitiría deducir quién es cada `id_alumno`
> probando nombres y mirando qué filas vuelven.

---

### `PATCH /alumnos/{id_alumno}` — requiere `Docente`

Corrige los datos de un alumno. **Actualización parcial:** solo los campos que envíes
se modifican; los que omitas quedan intactos.

Es también **la baja de un alumno**: se manda `{"activo": false}`. No existe
`DELETE /alumnos` a propósito — el borrado definitivo de alumnos está descartado.

**Body** (todos los campos son opcionales):

```json
{
  "nombres": "Juan Carlos",
  "apellidos": "Pérez Quispe",
  "id_colegio": 2,
  "id_grado": 3,
  "id_programa_actual": 1,
  "activo": true
}
```

**Respuesta `200`:** el alumno completo, ya actualizado, con `modificado_por` y
`modificado_en` puestos al usuario y fecha de la edición.

**Errores:**

- `403` si quien llama no es `Docente`, o si el alumno no está en sus asignaciones.
  También si intenta **mover** al alumno a un colegio o grado que no tiene asignado.
- `404` si el alumno no existe: `{"detail": "No existe un alumno con id_alumno=99"}`
- `404` si algún `id_colegio`, `id_grado` o `id_programa_actual` nuevo no existe.

---

### `PATCH /colegios/{id_colegio}` — requiere `Supervisor`

Corrige un colegio. Parcial, igual que el de alumnos.

**Body** (campos opcionales): `{ "nombre": "Colegio Carhuaz", "zona": "Carhuaz" }`

**Respuesta `200`:** el colegio completo actualizado.
**Error `404`:** `{"detail": "No existe un colegio con id_colegio=999"}`

---

### `PATCH /profesores/{id_usuario}` — requiere `Supervisor`

Corrige los datos de un profesor. Parcial.

**Body** (campos opcionales): `{ "nombres": "Ana", "apellidos": "Torres", "correo": "ana@sicedu.test" }`

**Respuesta `200`:** el profesor actualizado, con el mismo formato que `POST /profesores`
(`contraseña_temporal` llega en `null`, porque aquí no se genera ninguna).

**Errores:**

- `404` si no existe, o si ese `id_usuario` no es un profesor.
- `409` si el correo nuevo ya lo usa otra cuenta.

> Los nombres del profesor viven en dos tablas (`usuario` y `docente`). Este endpoint
> escribe en ambas, así que no hace falta que el frontend haga nada especial para
> mantenerlas sincronizadas.

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

### `GET /profesores` — requiere `Supervisor`

Devuelve un array con todos los profesores, activos e inactivos por igual.

**Respuesta `200`:**

```json
[
  {
    "id_usuario": 4,
    "id_docente": 2,
    "correo": "luis.ramos@sicedu.test",
    "nombres": "Luis",
    "apellidos": "Ramos",
    "activo": true
  }
]
```

Este schema (`ProfesorListItem`) es más chico que la respuesta de `POST /profesores` — no trae `id_rol` ni `contraseña_temporal`, solo lo necesario para armar una tabla/listado.

---

### `PATCH /profesores/{id_usuario}/desactivar` — requiere `Supervisor`

Sin body — el `id_usuario` va en la URL. Es borrado lógico: desactiva tanto la fila de `usuario` como la de `docente` juntas, no elimina ningún registro.

**Respuesta `200`:**

```json
{
  "id_usuario": 4,
  "id_rol": 1,
  "correo": "luis.ramos@sicedu.test",
  "id_docente": 2,
  "nombres": "Luis",
  "apellidos": "Ramos",
  "activo": false,
  "contraseña_temporal": null
}
```

`contraseña_temporal` siempre viene `null` en este endpoint — no aplica a esta operación.

**Errores:**

- `404` si el `id_usuario` no existe o no corresponde a un profesor (por ejemplo, si es el id de un Supervisor/Directivo): `{"detail": "No existe un profesor con id_usuario={id}"}`

---

### `PATCH /profesores/{id_usuario}/activar` — requiere `Supervisor`

Sin body — el `id_usuario` va en la URL. Operación inversa a `desactivar`: reactiva tanto `usuario` como `docente`, y el login vuelve a funcionar normalmente.

**Respuesta `200`:** misma forma que `desactivar`, con `"activo": true`.

**Errores:**

- `404` — mismo caso que `desactivar`.

---

## Cambio de contraseña

El cambio de contraseña requiere verificar un código de 6 caracteres enviado al correo del usuario — reemplaza el chequeo de "contraseña actual". El flujo son 3 llamadas:

1. `POST /me/password/codigo` — genera el código y lo envía por correo.
2. `POST /me/password/verificar-codigo` — opcional, solo para UX. Permite mostrarle al usuario feedback inmediato ("código correcto"/"código incorrecto") antes de avanzar a la pantalla de nueva contraseña. No consume el código ni tiene ningún efecto en el backend.
3. `POST /me/password` — el que realmente cambia la contraseña. Debe recibir el código de nuevo, el mismo que el usuario ingresó en el paso 2 — el backend nunca asume que el paso 2 se llamó antes, vuelve a validar el código de forma independiente. El frontend tiene que guardar el código que el usuario tipeó (en el estado de la pantalla 1) y reenviarlo en el request final del paso 3, no solo en el paso 2.

### `POST /me/password/codigo` — cualquier usuario autenticado

Sin body.

**Respuesta `200`:**

```json
{ "detail": "Código enviado a tu correo" }
```

**Errores:**

- `503` si el envío de correo falla: `{"detail": "No pudimos enviar el código, intenta de nuevo"}`

---

### `POST /me/password/verificar-codigo` — cualquier usuario autenticado

**Body:**

```json
{ "codigo": "AB12CD" }
```

**Respuesta `200`:**

```json
{ "detail": "Código correcto" }
```

**Errores:**

- `400`: `{"detail": "Código incorrecto o expirado"}`

---

### `POST /me/password` — cualquier usuario autenticado

**Body:**

```json
{
  "codigo": "AB12CD",
  "contraseña_nueva": "nuevacontrasena123",
  "confirmar_contraseña_nueva": "nuevacontrasena123"
}
```

**Respuesta `200`:**

```json
{ "detail": "Contraseña actualizada correctamente" }
```

**Errores:**

- `400` — código incorrecto o expirado, mismo mensaje que `POST /me/password/verificar-codigo`.
- `400` — `{"detail": "La contraseña nueva no puede ser igual a la actual"}`
- `422` — si `contraseña_nueva` y `confirmar_contraseña_nueva` no coinciden, o si la contraseña no cumple el formato (mínimo 8 caracteres, solo letras y números — sin símbolos, sin espacios).

El código expira 10 minutos después de generado, y se consume (no reutilizable) apenas se usa con éxito para cambiar la contraseña.

---

## Recuperar contraseña sin sesión

El flujo de "olvidé mi contraseña", desde la pantalla de login. **Ninguno de los dos
endpoints requiere token** — es la diferencia con los de [Cambio de contraseña](#cambio-de-contraseña),
que son para un usuario que ya entró y exigen `Authorization`.

### `POST /password/recuperar` — público

Envía un código de 6 caracteres al correo indicado. El código vale **10 minutos**.

**Body:**

```json
{ "correo": "profesor.prueba@sicedu.test" }
```

**Respuesta `200` — siempre, exista el correo o no:**

```json
{ "detail": "Si el correo está registrado, enviamos un código de verificación" }
```

> Responde `200` incluso con correos inexistentes o cuentas desactivadas, a propósito.
> Si devolviera `404` para los desconocidos, cualquiera podría ir probando direcciones
> para averiguar quién tiene cuenta en el sistema. Para el frontend esto significa que
> **no puedes saber si el correo existe**: muestra siempre la pantalla de "revisa tu
> correo" y deja que el usuario vuelva si no le llega nada.

### `POST /password/restablecer` — público

Cambia la contraseña usando el código recibido.

**Body:**

```json
{
  "correo": "profesor.prueba@sicedu.test",
  "codigo": "D6S3QC",
  "contraseña_nueva": "NuevaClave123",
  "confirmar_contraseña_nueva": "NuevaClave123"
}
```

**Reglas de la contraseña** (las valida el backend, conviene repetirlas en el formulario):

- Mínimo 8 caracteres.
- Solo letras y números, sin espacios ni símbolos.
- No puede ser igual a la actual.
- `contraseña_nueva` y `confirmar_contraseña_nueva` deben coincidir.

**Respuesta `200`:**

```json
{ "detail": "Contraseña actualizada correctamente" }
```

**Errores:**

| Código | Cuándo                                                        | `detail`                                          |
| ------ | ------------------------------------------------------------- | -------------------------------------------------- |
| `400`  | Código incorrecto, expirado, ya usado, o correo inexistente   | `"Código incorrecto o expirado"`                  |
| `400`  | La contraseña nueva es igual a la actual                       | `"La contraseña nueva no puede ser igual a la actual"` |
| `422`  | No coinciden, muy corta, o con símbolos                        | array de validación                               |

> El código es de **un solo uso**: al restablecer se borra, y reutilizarlo devuelve
> `400`. Si el usuario se equivoca al escribir la contraseña nueva y el formulario la
> rechaza, el código sigue vivo (no se consumió); pero si la petición llegó a
> completarse, hay que pedir uno nuevo con `/password/recuperar`.

---

## Cuentas de usuario

Alta, baja, edición y listado de cuentas. Abierto a **`Supervisor` y `Directivo`**,
pero cada uno solo alcanza su parcela.

### Parcelas: quién gestiona a quién

| Rol que llama | Puede gestionar cuentas de |
| ------------- | -------------------------- |
| `Supervisor`  | `Docente` y `Supervisor`   |
| `Directivo`   | `Directivo`                |

Salirse de la parcela devuelve **`403`** con `{"detail": "Un Supervisor no gestiona
cuentas de Directivo"}`. Afecta a los cuatro endpoints: listar, crear, editar y
activar/desactivar.

> Esto **no es solo un filtro de pantalla**. `GET /usuarios` devuelve únicamente las
> cuentas de la parcela, así que un Directivo no recibe Supervisores ni aunque pida
> `?rol=Supervisor`. Y un Supervisor que intente crear un Directivo con una petición
> directa recibe `403`, aunque la interfaz no le ofrezca el botón.

### `POST /usuarios`

Crea una cuenta. Para crear **Docentes** se sigue usando
[`POST /profesores`](#post-profesores--requiere-supervisor), que además crea la ficha
de docente.

**Body:**

```json
{
  "nombres": "Ana",
  "apellidos": "Torres",
  "correo": "ana.torres@sicedu.test",
  "id_rol": 2,
  "activo": true
}
```

**Respuesta `201`:**

```json
{
  "id_usuario": 4,
  "id_rol": 2,
  "correo": "ana.torres@sicedu.test",
  "id_docente": null,
  "nombres": "Ana",
  "apellidos": "Torres",
  "activo": true,
  "contraseña_temporal": null,
  "correo_enviado": true
}
```

| Campo                 | Tipo              | Descripción                                                                      |
| --------------------- | ----------------- | -------------------------------------------------------------------------------- |
| `correo_enviado`      | `boolean`         | Si el correo con la credencial salió bien.                                       |
| `contraseña_temporal` | `string \| null` | **Solo llega si `correo_enviado` es `false`.** Si el correo salió, viene `null`. |

> Mira `correo_enviado` antes de decidir qué mostrar: si es `true`, di solo "le
> enviamos sus credenciales por correo". Si es `false`, muestra la contraseña temporal
> en pantalla — es la única copia que existe — y advierte de que se anote.

**Errores:**

| Código | Cuándo                                                |
| ------ | ------------------------------------------------------ |
| `400`  | `id_rol` es el de `Docente` (usa `POST /profesores`)   |
| `403`  | El `id_rol` está fuera de tu parcela                   |
| `404`  | El `id_rol` no existe                                  |
| `409`  | El correo ya está registrado                           |

### `GET /usuarios`

Lista las cuentas **de tu parcela**, con el nombre del rol ya resuelto.

**Parámetro opcional:** `?rol=Supervisor` acota aún más, dentro de la parcela.

```json
[
  {
    "id_usuario": 1,
    "id_rol": 2,
    "rol": "Supervisor",
    "correo": "jefa.prueba@sicedu.test",
    "id_docente": null,
    "nombres": "Jefa",
    "apellidos": "de Prueba",
    "activo": true
  }
]
```

### `PATCH /usuarios/{id_usuario}`

Corrige los datos de una cuenta. Parcial: solo los campos enviados se modifican.

**Body** (campos opcionales): `{ "nombres": "Ana", "apellidos": "Torres", "correo": "ana@sicedu.test" }`

**Respuesta `200`:** la cuenta actualizada.

No permite cambiar `id_rol`: mover una cuenta de rol la cambia de parcela y afecta a
las reglas de última cuenta activa, así que queda fuera de este endpoint.

**Errores:** `403` fuera de parcela · `404` si no existe · `409` si el correo ya está en uso.

> Si la cuenta es de un `Docente`, los nombres se escriben también en su ficha
> `docente`, igual que en `PATCH /profesores`.

### `PATCH /usuarios/{id_usuario}/activar` · `/desactivar`

Cambian el estado de una cuenta de tu parcela. Si es la de un profesor, su ficha de
docente queda con el mismo estado.

**Errores:**

| Código | Cuándo                                                   | `detail`                                                              |
| ------ | --------------------------------------------------------- | ---------------------------------------------------------------------- |
| `403`  | La cuenta está fuera de tu parcela                        | `"Un Supervisor no gestiona cuentas de Directivo"`                    |
| `404`  | No existe ese usuario                                     | `"No existe un usuario con id_usuario=99"`                            |
| `409`  | Es tu propia cuenta                                       | `"No puedes desactivar tu propia cuenta. Pídeselo a otro Supervisor."` |
| `409`  | Es la última cuenta activa de `Supervisor` o `Directivo`  | `"No puedes desactivar la última cuenta activa de Supervisor..."`      |

> Las reglas las aplica el servidor, no el navegador: aunque el frontend no oculte el
> botón, la petición se rechaza. Muestra el `detail` tal cual, que ya viene redactado
> para el usuario final.

---

## Asignaciones del docente

Qué colegios y grados tiene a cargo cada docente, en qué periodo académico. **Es lo
que determina qué alumnos ve y puede tocar** un Docente: sin asignaciones vigentes,
`GET /alumnos` le devuelve `total: 0` y no puede crear ninguno.

Una asignación es **vigente** si la fecha de hoy cae dentro de su periodo académico.

### `GET /asignaciones`

- **`Supervisor`:** todas las asignaciones.
- **`Docente`:** solo las suyas — el recorte sale del token, no de un parámetro, así
  que no puede pedir las de otro cambiando la URL.
- **`Directivo`:** `403`.

**Respuesta `200`:**

```json
[
  {
    "id": 1,
    "id_docente": 1,
    "docente": "Docente de Prueba",
    "id_colegio": 1,
    "colegio": "Colegio de Prueba",
    "id_grado": 1,
    "grado": "1.º",
    "id_periodo_academico": 1,
    "periodo": "Periodo 1",
    "vigente": true
  }
]
```

Trae los nombres resueltos y el booleano `vigente`, para pintar la tabla sin cruzar
catálogos ni comparar fechas en el cliente.

### `POST /asignaciones` — requiere `Supervisor`

```json
{ "id_docente": 1, "id_colegio": 1, "id_grado": 1, "id_periodo_academico": 1 }
```

**Respuesta `201`:** la asignación creada.

**Errores:** `404` si el docente, colegio, grado o periodo no existe · `409` si esa
misma combinación ya está asignada.

### `DELETE /asignaciones/{id_asignacion}` — requiere `Supervisor`

**Respuesta `204`**, sin cuerpo. `404` si no existe.

> Aquí sí se borra de verdad, a diferencia de alumnos y cuentas, que solo se
> desactivan: una asignación es una relación de trabajo del periodo, no un registro
> histórico del que dependan datos académicos.

> **Importante:** las asignaciones necesitan un `periodo_academico` cargado. El seed
> crea un año escolar y un periodo que cubren el año en curso, más una asignación de
> ejemplo para el docente de prueba. En un entorno real, sin periodos no se puede
> asignar nada y **todos los docentes se quedan sin alumnos a la vista**.

---

## Errores## Errores

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

Ahora aparece en más sitios que antes, porque los permisos se afinaron por rol:
rol equivocado para el endpoint, cuenta fuera de tu parcela, alumno fuera de tus
asignaciones, o búsqueda por nombre desde la vista de Directivo. El `detail` dice cuál
de los cuatro es.

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

- **El núcleo académico:** reporte semanal, rúbrica semanal, evaluaciones diagnósticas
  y nivel final mensual. Son los Grupos 2 a 5 del modelo de datos y todavía no existen
  ni las tablas. Es lo que hoy resuelve el simulador del frontend.
- Refresh token / renovación de sesión
- Borrado de registros (hoy alumnos y profesores se desactivan, no se eliminan; es
  intencional, el borrado definitivo está descartado)
- Endpoints de `año_escolar` y `periodo_academico`: hoy solo los crea el seed, así que
  para abrir un periodo nuevo hay que tocar la base de datos a mano
- Cambiar el rol de una cuenta ya creada
- Paginación en `GET /colegios` y `GET /profesores` (hoy devuelven la lista completa;
  con los volúmenes actuales no es problema, pero `GET /alumnos` ya la necesita)

### Un aviso sobre el envío de correos

`POST /profesores` y `POST /usuarios` devuelven la contraseña temporal en la respuesta
**cuando el correo no se pudo enviar** (`correo_enviado: false`). Está pensado como
salida de emergencia, pero hoy el envío falla casi siempre porque `GMAIL_SMTP_USER` y
`GMAIL_SMTP_APP_PASSWORD` no están configurados, así que en la práctica la credencial
acaba pasando por pantalla en casi todas las altas.

Conviene configurar esas dos variables en el `.env` del servidor. Mientras tanto, el
frontend debe seguir manejando el caso `correo_enviado: false`, porque será el habitual.

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