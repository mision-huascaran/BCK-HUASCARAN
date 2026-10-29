# Decisiones provisionales para validar con el analista

Tanda M (ronda 6 de QA, segunda parte, 29/10/2026). Son temas que esperaban al analista
(Carlos) y que no podían esperar a la exposición. Se implementó la opción más conservadora,
aislada en una sola función para cambiarla o revertirla en un solo lugar. En el código llevan
la marca `# REGLA PROVISIONAL` en `app/services/reglas_provisionales.py`.

**Estado de todas: pendiente de validar con el analista después de la exposición del 29/10.**

Ninguna cambia la forma de una respuesta de la API: solo cambian valores (`escritura.permitida`
de la rúbrica y los conteos del Inicio del Supervisor).

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

## S22 · Un alumno que cambia de grupo a mitad de mes no aparece en el grupo nuevo (pendiente, sin cambio)

| | |
|---|---|
| **CU** | CU029 y CU030 (Rúbrica mensual, regla R2). |
| **Estado** | **Sin cambio en el código:** se mantiene el comportamiento actual (regla R2, `regla_grupo_del_mes` y `rubrica_service.alumnos_del_mes`) hasta que Carlos decida. No se investigó en esta tanda. |
| **Pendiente** | Validar con el analista después de la exposición del 29/10 en qué grupo (o en cuáles) debe aparecer en el mes del cambio un alumno que cambia de grupo a mitad de mes. |
