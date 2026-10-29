# Decisiones del Dashboard (CU033, CU034 y CU035) para validar

Serie D1–D7 (octubre de 2026). Cada decisión indica el caso de uso, lo que se implementó y por
qué. Las marcadas **A1–A11** venían en el plan; las demás se tomaron durante la
implementación, donde el CU no lo definía, eligiendo lo más conservador.

Convenciones que se repiten en todo el Dashboard:

- `null` / `sin_informacion` significa "no hay datos"; nunca se convierte en 0. Un 0 solo
  aparece cuando está confirmado.
- Los porcentajes se calculan sobre la suma del conjunto, nunca promediando porcentajes.
- Para ordenar se usa el valor exacto; para mostrar, el redondeado.

## Decisiones del plan

| Id | CU | Decisión | Por qué |
|---|---|---|---|
| A1 | CU033, CU035 | El alcance de Supervisor y Directivo son **todos los colegios activos**. Está aislado en `colegios_del_alcance` (`app/services/dashboard/filtros.py`). | Ningún CU define cómo se asigna un alcance a esos roles. Si cambia, se cambia en un solo lugar. |
| A2 | CU033, CU035 | Los PA se muestran como "Primer periodo" … "Cuarto periodo" (`nombre_visible_periodo`); el resto del sistema sigue con "PA1"… | Es el texto que usa el Dashboard; no se toca `calendario.nombre_periodo`. |
| A3 | CU033, CU035 | Alumno atendido en un año = su pertenencia histórica cruza el año escolar de su colegio (inicio del PA1 al fin del PR4 de su calendario, hasta hoy si está en curso), sin mirar su estado actual. Colegio atendido = con al menos un alumno atendido. | Es la "pertenencia histórica" del CU, sin depender de cambios posteriores. |
| A4 | CU034 | Alumnos incluidos en el promedio de libros = las filas de las tablas consideradas (la lista de la clase de referencia, también "No aplica" y ausentes) y, por cada tabla esperada que falta, los alumnos del grupo y subprograma en la fecha de su clase. Cada alumno una vez por colegio; en el detalle, una vez por colegio y grado. | El CU pide no excluir del divisor a quien no tiene lecturas registradas. |
| A5 | CU034 | Registros esperados de Lectura = la misma regla de CU011 (`registros_vencidos.obligaciones_lectura`): semanas con clase habitual o reprogramada cuyo sábado ya llegó, por subprograma con alumnos. Incompleto = tabla esperada inexistente o con filas "Pendiente". | Así el Dashboard y los vencidos de CU011 no pueden diferir. |
| A6 | CU033 | Corte por colegio: `min(fin del PA en su calendario, hoy)`. Un colegio con el PA sin iniciar no se suma y se informa en `colegios_sin_informacion` (`periodo_no_iniciado`). | Cada colegio tiene su calendario; no se usa el de otro ni se excluye en silencio. |
| A7 | CU029, CU033 | La rúbrica pertenece al PA guardado en `rubrica_mensual.id_periodo_academico` (R1 al guardar); no se recalcula. | Una sola fuente del PA de cada evaluación. |
| A8 | CU033 | Nivel alfabético = la letra Raz-Kids colocada en la decisión vigente del Registro de Vuelo, con la escala completa `nivel_razkids` (aa a Z). | Es el dato confirmado; la escala completa da el mismo eje en todos los grupos. |
| A9 | CU029, CU033 | Fluidez y Comprensión salen de la misma rúbrica: la última **completa** del PA. Los pendientes se devuelven por dimensión aunque hoy coincidan. | CU029 manda usar la última evaluación completa y CU033 prohíbe tomar un nivel de una incompleta. |
| A10 | CU033, CU034, CU035 | Empates exactos: comparten posición y la siguiente salta ("1, 1, 3"). Colegios empatados en orden alfabético; grados, en orden escolar. | Ranking de competición, el más común. |
| A11 | CU033 | Los grupos con "Evaluación incompleta" entran al ranking con su índice y posición provisional; los que no tienen ninguna evaluación completa van aparte. | El CU distingue los dos casos. |

## D1 · Filtros comunes y selección inicial

| CU | Decisión | Por qué |
|---|---|---|
| CU033, CU035 | Un colegio inactivo pedido por id da 403 `colegio_fuera_de_alcance`; un id que no existe da 422 en `id_colegios`. | Existe, pero no está en el alcance (A1). |
| CU033 | Un calendario que no tiene el PA pedido cuenta igual que la falta de calendario: va en `colegios_sin_calendario` (422 `calendario_no_configurado`). | El colegio no tiene un calendario aplicable para ese periodo. |
| CU034 | "Todos los colegios" sin ningún colegio activo da 422 con el texto de CU034. | No hay nada que consultar. |
| CU033 | `contexto.intervalo`: en un bloque del periodo, del primer inicio al último fin de ese PA entre los colegios; en uno anual, las fechas del año escolar. `parcial` = hoy está dentro del intervalo (un periodo futuro no es parcial). | Sirve para el título del bloque. |
| CU033 | Selección inicial: el PA en que están hoy todos los colegios del alcance; en un receso, el PA anterior y su año. Si no coinciden o hoy no cae en ningún PA ni PR, `null`. | El CU prohíbe elegir un calendario por aproximación. |
| — | Mensajes sin texto en el CU, con tuteo: "Este indicador es anual: no elijas un periodo académico.", "Elige todos los colegios o una lista de colegios, no ambas cosas.", "No repitas colegios en la selección." | Uniformidad con el resto del backend. |

## D2 · Distribución por subprograma y resultados de aprendizaje

| CU | Decisión | Por qué |
|---|---|---|
| CU033 | Si un alumno aparece en dos colegios por tener fechas de corte distintas, cuenta una vez, en el de corte más reciente. | Cada alumno, una vez por distribución. |
| CU033 | Una rúbrica completa de otro subprograma que el del alumno al corte lo deja pendiente. | Su escala es otra. |
| CU033 | `ColegioPrograma` es por colegio (no hay configuración por grado): el filtro de "grupos con el subprograma" se aplica por colegio y subprograma. | Es lo que tiene el modelo. |
| CU033 | En la distribución aparecen siempre los dos subprogramas del catálogo (si el total es > 0). En los resultados, los configurados en algún colegio con fecha de corte; uno sin alumnos aparece con `total_a_evaluar: 0`. | Lo pidió el plan; el 0 está confirmado. |
| CU033 | La escala de la rúbrica incluye "Pre-inicio" donde existe (Fluidez de Alfabetización). | Todos los niveles de `NivelRubrica`, ordenados por valor. |
| CU029 | La regla "última evaluación completa del PA" quedó en un solo lugar (`rubrica_service._ultimas_completas`); el Registro de Vuelo y el Dashboard la usan. | Antes había dos copias. |
| CU031 | El nivel global usa el grado del registro del Registro de Vuelo, no el de la pertenencia al corte. | Es el nivel confirmado tal como se decidió. |

## D3 · Asistencia

| CU | Decisión | Por qué |
|---|---|---|
| CU033, CU022 | Clase dictada = tiene al menos una fila de asistencia, como en CU011. Una fila sin estado cuenta como "sin marcar". | La misma noción que los vencidos. |
| CU033 | La clase de hoy sin asistencias no es "Sin registro" (fecha transcurrida = `fecha < hoy`, como CU011); con asistencias, sí es dictada. | Coherencia con CU011. |
| CU033 | El mes en curso sin estados sale `en_curso` con `porcentaje: null` y los conteos en 0, no `sin_informacion`. | Todavía no terminó. |
| CU033 | La serie mensual cubre la unión de los PA de los colegios. | Un solo eje para el gráfico. |
| CU033 | En el detalle por clase, un colegio que no existe o no está en la selección da 422 en `id_colegio`; uno fuera del alcance, 403. | Igual que en D1. |
| CU033 | "Sin registro" no vuelve provisional el porcentaje; se informa aparte. Solo las clases con alumnos sin marcar lo vuelven provisional. | El CU separa los dos casos. |

## D4 · Comparación de resultados

| CU | Decisión | Por qué |
|---|---|---|
| CU033 | El índice se calcula como fracción exacta; se muestra con 2 decimales redondeando la mitad hacia arriba (1,625 → 1,63). | `round()` de Python redondea la mitad al par. |
| CU033 | Un grupo con el subprograma configurado y sin alumnos a evaluar no aparece en ninguna lista. | No hay nada que comparar ni es "No aplica". |
| CU033 | En "Detalle por grado", `no_aplica` lista los grados elegidos que el colegio tiene (`ColegioGrado`). Un colegio con el PA sin iniciar va solo en `colegios_sin_informacion`. | Un solo aviso por colegio. |
| CU033 | "Todos los grados" = todos los del catálogo. | Selección inicial del CU. |
| CU033 | La respuesta incluye además `fechas_corte`, como en D2. | El CU pide mostrar la fecha usada. |

## D5 · Libros leídos

| CU | Decisión | Por qué |
|---|---|---|
| CU034 | Libros de una fila = 1 si tiene LSN + su LSL. LSL `null` no suma ni es 0; "No realizada" aporta 0 confirmado. | Lo pidió el plan. |
| CU034 | Una fila tiene dato si tiene alguna cantidad o está confirmada (completada, "No aplica" o "No realizada"). Un mes con solo ausentes da 0 completo. | Es una declaración confirmada. |
| CU034 | Solo faltan las tablas esperadas ya vencidas. Las tablas que no son obligación (la semana en curso, semanas con solo clase adicional) aportan sus cantidades, pero sus pendientes no cuentan como faltantes. | "Los meses futuros no se consideran información faltante antes de que transcurran". |
| CU034 | Una semana esperada sin tabla se atribuye al mes de su clase habitual. | Es la clase que la genera. |
| CU034, CU035 | En el resumen general, cada alumno cuenta una vez por colegio y se suman los colegios. | "Una sola vez por colegio". |
| CU034 | Un colegio sin calendario va aparte con el motivo `sin_calendario` (además de `sin_alumnos` y `sin_informacion`). | No tiene año escolar donde medir. |
| CU034 | En `/libros`, todos los colegios usan los meses de la unión de sus calendarios; en el detalle, los del colegio. | Columnas comunes en la tabla. |
| CU034 | Desglose: un alumno que no perteneció a los colegios seleccionados en el año da 422; si solo perteneció a colegios fuera del alcance, 403. | Igual que el detalle de D3. |

## D6 · Evolución anual y comparación entre colegios

| CU | Decisión | Por qué |
|---|---|---|
| CU033 | Cada año de la evolución incluye además `colegios: [{colegio, alumnos_atendidos}]`. | Muestra que un alumno que cambió de colegio está en los dos. |
| CU033, CU035 | `parcial` = hoy está entre el primer inicio y el último fin de los calendarios del año en los colegios seleccionados. | El año escolar es por colegio. |
| CU035 | Con varios colegios y ninguno con información: `ranking` vacío, todos en `sin_informacion` y `mensaje: null`. | El CU no da un texto para este caso. |
| CU035 | La comparación de asistencia ordena por el porcentaje exacto con empates "1, 1, 3"; solo "Registro incompleto" vuelve provisional la posición ("Sin registro" es una advertencia). | Mismo criterio que el bloque de asistencia. |
| CU035 | El resumen anual (paso 5) = el año seleccionado de `/dashboard/evolucion-anual` + el `resumen` de `/dashboard/libros`. | No hace falta otro endpoint. |
