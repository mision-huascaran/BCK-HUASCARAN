# Decisiones provisionales para validar con el analista

Tanda M (ronda 6 de QA, segunda parte, 29/10/2026). Son temas que esperaban al analista
(Carlos) y que no podían esperar a la exposición. Se implementó la opción más conservadora,
aislada en una sola función para cambiarla o revertirla en un solo lugar. En el código llevan
la marca `# REGLA PROVISIONAL` en `app/services/reglas_provisionales.py`.

**Estado de todas: pendiente de validar con el analista después de la exposición del 29/10.**

DP-1 y DP-2 no cambian la forma de una respuesta de la API: solo cambian valores
(`escritura.permitida` de la rúbrica y los conteos del Inicio del Supervisor). DP-3 a DP-5 son
de la Tanda Q1 y DP-6 a DP-11, de la Tanda Q2 (CU014, semana 14); no están en
`reglas_provisionales.py`.

## DP-1 · El mes partido entre dos PA en la Rúbrica (D88)

| | |
|---|---|
| **CU** | CU029 (Rúbrica mensual), CU011 (vencidos de rúbrica). |
| **Problema** | Por R1 ("cada evaluación mensual se asocia al PA que tenga la mayor cantidad de días lectivos dentro del mes"), octubre de 2026 pertenece al PA3, que termina el 16/10, aunque el PA4 empieza el 19/10. Por eso, un Docente asignado solo al PA4 no podía evaluar la rúbrica de octubre sin un permiso. Además, como los vencidos de rúbrica solo contaban los meses cuyo PA de R1 era el PA en curso, la rúbrica de octubre nunca figuraba como vencida. |
| **Decisión** | R1 no cambia: la evaluación sigue asociada al PA de R1 (`rubrica_mensual.id_periodo_academico`), que usan el Registro de Vuelo y el Dashboard. Cambian dos cosas. **Quién puede evaluar el mes:** cualquier Docente con habilitación de escritura para el grupo en alguno de los PA con días lectivos en ese mes, sea por asignación del PA, por retroactiva durante su PR o por permiso de ese PA. Antes, solo el PA de R1. **Vencimiento:** la obligación del mes vence el día 1 del mes siguiente, como antes, y se exige en todo PA con días lectivos en el mes. Así, octubre vence el 01/11 y se ve en el Inicio mientras el PA4 está en curso. Un mes que cae entero en el PA anterior no se exige durante el PA siguiente. |
| **Qué cambia** | `GET /rubricas`: `escritura.permitida` (y su `id_permiso` o `aviso_permiso`, si escribe con un permiso). `PUT /rubricas`: acepta con esa misma regla. `GET /inicio/supervisor`: los vencidos e incompletos de rúbrica. |
| **Cómo revertirla** | En `app/services/reglas_provisionales.py`: que `regla_pas_que_evaluan_el_mes` devuelva solo `[PA de R1]` y que `regla_mes_exigible_en_pa` compare con el PA de R1. Las usan `rubrica_service.mes_evaluado` y `habilitacion_del_mes`, y `registros_vencidos._mes_exigible`. |
| **Tests** | `tests/test_rubricas.py`, `test_dp1_*`. |

## DP-2 · Los vencidos durante el receso (S21)

| | |
|---|---|
| **CU** | CU011 (Inicio del Supervisor: registros vencidos e incompletos, alertas operativas). |
| **Problema** | En CP-INI-29, al pasar del 18/12 (fin del PA4) al 19/12 (inicio del PR4), los vencidos del Inicio del Supervisor bajaban de 60, 70 y 68 a 0. Durante un receso no había "PA actual", así que no se evaluaba nada. Pero el receso es justamente cuando se regulariza el PA que terminó, con asignaciones retroactivas. Pasaba lo mismo con la rúbrica de septiembre de "QA Sur", que no figuró vencida durante su PR2. |
| **Decisión** | Durante el PR n, los vencidos e incompletos de CU011 se calculan sobre el PA n, el que se está regularizando, con las mismas reglas que tenían cuando era el PA en curso (incluida DP-1 para la rúbrica). Se exigen a los Docentes asignados a ese PA. Al terminar el PR se pasa al PA siguiente, como antes. |
| **Qué cambia** | `GET /inicio/supervisor`: `registros_vencidos`, `registros_incompletos`, sus desgloses por módulo y la alerta "Sincronización Olvidada", que también aparece durante el PR. **No cambia:** "Incumplimiento de Sede", que sigue mirando el PA en curso (un colegio en un PR no la genera); "Inactividad Operativa"; "Colegios operando"; "Docentes activos"; ni la Salud del Sistema. |
| **Cómo revertirla** | En `app/services/reglas_provisionales.py`: que `regla_pa_de_referencia` devuelva el periodo solo si es un PA. La usa `calendario.pa_de_referencia_para_vencidos`, que solo llama `registros_vencidos.estado_de_registros`. `pa_actual_de_colegios` no se tocó. |
| **Tests** | `tests/test_alertas_operativas.py`, `test_dp2_*`, y `tests/test_rubricas.py`, `test_dp2_*`. Dos tests anteriores cambiaron por esta regla (ver el reporte de la Tanda M). |

## DP-3 · El alumno conserva "Nombres" y "Apellidos" por separado

| | |
|---|---|
| **CU** | CU014 (Alumnos, versión de la semana 14), CU016 (Usuarios). |
| **Problema** | CU014 pide un solo campo "Nombres y apellidos" en "Agregar", "Editar" y la importación de la nómina. |
| **Decisión** | Se mantienen los dos campos separados, como hoy: permiten ordenar por apellido y son coherentes con Usuarios (CU016). Además, la nómina real los separa en el 92 % de las filas ("APELLIDOS, Nombres") y en casi todo el resto (apellidos en MAYÚSCULAS). La importación (Tanda Q2) los separa con una regla fija. |
| **Qué cambia** | Nada en el nombre del alumno: no se aplica esa parte del CU. |
| **Pendiente** | Que el analista lo ratifique y actualice el CU. |

## DP-4 · Alcance del Docente sobre los alumnos con pertenencia pendiente

| | |
|---|---|
| **CU** | CU014: "El Docente ve a estos alumnos cuando su colegio forma parte de sus asignaciones vigentes." |
| **Problema** | El alcance del Docente es por par (colegio, grado), pero un alumno con pertenencia pendiente puede no tener grado o tener uno ajeno. |
| **Decisión** | **Lectura:** ve al alumno pendiente si su colegio está entre los colegios de su alcance de lectura, sin importar su grado. **Escritura:** puede escribir sobre él si el colegio está entre los de su alcance de escritura. Si pone o cambia el grado, o completa el grupo, el par (colegio, grado) final tiene que estar en su alcance (`403 alumno_fuera_de_alcance`). Puede corregir nombres, apellidos o DNI aunque el grado sea ajeno. Los alumnos con pertenencia siguen rigiéndose por los pares. El Supervisor no cambia. |
| **Qué cambia** | `GET /alumnos`, `GET /alumnos/{id}` y sus pestañas; `PATCH /alumnos/{id}`, `/activar` y `/desactivar`. |
| **Cómo revertirla** | En `app/services/alumno_service.py`: `_exigir_alcance_sobre` y la condición de alcance de `listar_alumnos`. |
| **Tests** | `tests/test_alumnos_pendientes.py`, `test_dp4_*`. |

## DP-5 · Alumno inactivo con pertenencia pendiente

| | |
|---|---|
| **CU** | CU014 (Editar; Activar o inactivar). |
| **Problema** | Un alumno sin intervalos puede estar inactivo, y el CU no dice cuándo confirma su pertenencia en ese caso. |
| **Decisión** | Puede editarse (incluso completar el grupo de su ficha), pero no confirma su pertenencia hasta que lo activen: `pertenece_desde` se rechaza con "Activa al alumno antes de confirmar desde qué fecha forma parte de su grupo." Activar o inactivar a un alumno pendiente no abre ni cierra intervalos, aunque la ficha tenga el grupo completo. |
| **Qué cambia** | `PATCH /alumnos/{id}`, `/activar` y `/desactivar`. |
| **Cómo revertirla** | En `app/services/alumno_service.py`: `_validar_fechas_de_pendiente` y `cambiar_estado`. |
| **Tests** | `tests/test_alumnos_pendientes.py`, `test_dp5_*`. |

## DP-6 · Separación del nombre en la importación

| | |
|---|---|
| **CU** | CU014 (importar nómina), con DP-3. |
| **Problema** | La nómina trae "NOMBRES Y APELLIDOS" en una sola columna y el alumno los guarda por separado. |
| **Decisión** | Sobre el texto recortado y sin espacios repetidos. Con coma: `apellidos` es lo anterior a la primera coma y `nombres`, lo posterior. Sin coma: las palabras iniciales en mayúsculas (con al menos una letra) son los apellidos, si queda alguna palabra para los nombres. Si nada funciona (todo en mayúsculas, todo en minúsculas o una coma con un lado vacío), `apellidos` es el texto completo, `nombres` queda vacío y la fila lleva la advertencia nueva `nombre_sin_separar`. Las mayúsculas y minúsculas se conservan. |
| **Qué cambia** | Las vistas previas pueden traer `nombre_sin_separar`: "Nombre sin separar. Revisa los nombres y apellidos después de importar." |
| **Cómo revertirla** | En `app/services/importacion_nomina.py`: `separar_nombre`. |
| **Tests** | `tests/test_importacion_nomina.py`, `test_separar_nombre*`. |

## DP-7 · DNI numérico de 7 dígitos

| | |
|---|---|
| **CU** | CU014 (importar nómina). |
| **Problema** | Excel pierde el cero inicial de un DNI guardado como número. |
| **Decisión** | Una celda numérica de 7 dígitos se completa con un cero a la izquierda. Un texto de 7 dígitos no: se trata como DNI no válido. |
| **Cómo revertirla** | En `app/services/importacion_nomina.py`: `leer_dni`. |
| **Tests** | `tests/test_importacion_nomina.py`, `test_leer_dni_*`. |

## DP-8 · Celdas vacías y grados no ofrecidos

| | |
|---|---|
| **CU** | CU014 (importar nómina). |
| **Decisión** | Un DNI o un grado vacíos (o sin su columna) se importan sin ese dato y sin advertencia, porque no hay nada que corregir. Un grado válido que el colegio no ofrece cuenta como "no reconocido": sin grado y con la advertencia `grado_no_reconocido`. |
| **Cómo revertirla** | En `app/services/importacion_nomina.py`: `leer_grado`, `leer_dni` e `_id_grado`. |
| **Tests** | `tests/test_importacion_nomina.py`, `test_grado_*`. |

## DP-9 · Encabezados y DNI repetido dentro del archivo

| | |
|---|---|
| **CU** | CU014 (importar nómina). |
| **Decisión** | La fila de encabezados es la primera, dentro de las primeras 20 de la hoja, que tenga II.EE y NOMBRES Y APELLIDOS. Si un encabezado se repite, vale la primera columna. Los nombres aceptados están en una constante. Un DNI válido que aparece en más de una fila con nombre (en cualquier hoja y aunque su colegio no sea válido) rechaza todas esas filas con `dni_repetido`. |
| **Cómo revertirla** | En `app/services/importacion_nomina.py`: `ENCABEZADOS`, `FILAS_DE_ENCABEZADO` y `clasificar`. |
| **Tests** | `tests/test_importacion_nomina.py`, `test_encabezado*` y `test_dni_repetido_*`. |

## DP-10 · Hoja omitida y conteo del límite

| | |
|---|---|
| **CU** | CU014 (importar nómina). |
| **Decisión** | Una hoja omitida es una observación con `fila: null` que no suma en K ("filas que no se importarán"). El límite de 3000 cuenta las filas con nombre de las hojas no omitidas, y se responde antes de clasificar. |
| **Cómo revertirla** | En `app/services/importacion_nomina.py`: `_leer_hoja`. |
| **Tests** | `tests/test_importacion_nomina.py`, `test_mas_de_3000_*`; `tests/test_importacion_api.py`, `test_vista_previa_con_todos_los_motivos`. |

## DP-11 · Transporte del archivo, límites y protocolo de confirmación

| | |
|---|---|
| **CU** | CU014 (importar nómina). |
| **Problema** | `python-multipart` tuvo varios CVE MEDIUM y HIGH en 2026, y Trivy en Jenkins solo deja pasar LOW. La vista previa no debe guardarse en el servidor y un reintento no debe importar dos veces. |
| **Decisión** | El `.xlsx` viaja como cuerpo crudo y el nombre, en el query `nombre_archivo`. El cuerpo se lee por partes con un tope de 5 MB, y `ProteccionHttp` no lo lee entero en estas rutas. Antes de abrirlo: zip válido, como máximo 50 MB descomprimidos y 2000 entradas; se abre con `defusedxml` disponible y sin vínculos externos. Cualquier fallo de lectura es `422 archivo_invalido`. La huella es el SHA-256 del análisis completo y la confirmación la recalcula y exige que coincida. `id_importacion` lo genera el front al elegir el archivo; reenviarlo nunca importa dos veces. |
| **Qué cambia** | El front manda el archivo con `fetch(url, {method: 'POST', body: archivo})`, sin `FormData`. |
| **Cómo revertirla** | `_leer_cuerpo` en `app/routers/alumnos.py`, `RUTA_DE_ARCHIVOS` en `app/core/proteccion_http.py`, `_validar_zip` en `app/services/importacion_nomina.py` y `confirmar` en `app/services/importacion_service.py`. |
| **Tests** | `tests/test_importacion_api.py` y `tests/test_importacion_nomina.py`, `test_archivo_ilegible`. |

## S22 · Un alumno que cambia de grupo a mitad de mes no aparece en el grupo nuevo (pendiente, sin cambio)

| | |
|---|---|
| **CU** | CU029 y CU030 (Rúbrica mensual, regla R2). |
| **Estado** | **Sin cambio en el código:** se mantiene el comportamiento actual (regla R2, `regla_grupo_del_mes` y `rubrica_service.alumnos_del_mes`) hasta que Carlos decida. No se investigó en esta tanda. |
| **Pendiente** | Validar con el analista después de la exposición del 29/10 en qué grupo (o en cuáles) debe aparecer en el mes del cambio un alumno que cambia de grupo a mitad de mes. |
