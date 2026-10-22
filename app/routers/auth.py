"""Inicio de sesión, cierre de sesión y perfil (CU002, CU003, CU007, CU008)."""
import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Response, status
from sqlmodel import Session

from app.core.database import get_db
from app.dependencies import (
    SesionActual,
    get_sesion_actual,
    ids_del_token,
    oauth2_scheme,
    verificar_firma,
)
from app.schemas.auth import (
    CerrarSesionRequest,
    CierreSesionResponse,
    LoginRequest,
    SesionCerrada,
    SesionMe,
    TokenResponse,
    MeResponse,
)
from app.services.actividad import a_item
from app.services.auth_service import a_token_response, cerrar_sesion, cerrar_sesion_anterior, iniciar_sesion

router = APIRouter(tags=["auth"])

Db = Annotated[Session, Depends(get_db)]


@router.post("/login", response_model=TokenResponse)
def login(data: LoginRequest, db: Db):
    """Abre una sesión de 8 horas. Errores: 401 `credenciales_invalidas`,
    403 `cuenta_desactivada`, 403 `cambio_contrasena_requerido` (cuenta con contraseña
    temporal: usar POST /password/primer-ingreso), 403 `contrasena_temporal_expirada`
    (recuperar con PIN), 429 `bloqueo_temporal`."""
    return a_token_response(iniciar_sesion(db, data.correo, data.password))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def logout(db: Db, token: Annotated[Optional[str], Depends(oauth2_scheme)] = None):
    """Cierra la sesión del token. Idempotente: si la sesión ya estaba cerrada, venció o
    no existe, responde 204 igual. Solo exige un token con firma válida (si no, 401)."""
    ids = ids_del_token(verificar_firma(token))
    if ids is not None:
        cerrar_sesion(db, *ids)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=MeResponse)
def me(actual: Annotated[SesionActual, Depends(get_sesion_actual)]):
    usuario = actual.usuario
    return MeResponse.model_validate(
        {
            **usuario.model_dump(),
            "sesion": SesionMe(inicio=actual.sesion.inicio, expira=actual.sesion.expira),
        }
    )


@router.post(
    "/me/sesiones/{id_sesion}/cerrar",
    response_model=CierreSesionResponse,
    responses={401: {"description": "Token ausente, inválido o de una sesión cerrada o vencida"}},
)
def cerrar_otra_sesion(
    id_sesion: uuid.UUID,
    data: CerrarSesionRequest,
    db: Db,
    actual: Annotated[SesionActual, Depends(get_sesion_actual)],
):
    """Registra un "Cerrar sesión" hecho sin conexión en una sesión anterior del usuario,
    con la hora real `fin` (CU008). Se llama con el token de la sesión nueva, después
    del nuevo login. Cualquier rol.

    La sesión queda 'Manual' en `fin` (aunque el servidor ya la hubiera cerrado por
    expiración) y su actividad activa o cerrada por expiración, "Forzado por cierre de
    sesión" en `fin`. Idempotente: si ya estaba cerrada como 'Manual' o invalidada,
    responde 200 sin cambios. Una actividad finalizada por el docente no se toca.

    Errores 422: `id_sesion_invalido` (inexistente o de otro usuario), `sesion_actual`
    (es la del token: usar POST /logout), `fin_invalido` (fuera de la sesión o en el
    futuro), `validacion` (`fin` y `fin_minimo` ausentes o los dos a la vez, o sin zona
    horaria).

    Con `fin_minimo` (sin continuidad del reloj) el cierre queda en max(fin_minimo,
    última petición recibida de esa sesión), sin pasar de su vencimiento, con
    `fin_verificado: false`.
    """
    sesion, actividad = cerrar_sesion_anterior(
        db, actual.usuario.id_usuario, actual.sesion.id_sesion, id_sesion, data.fin, data.fin_minimo
    )
    return CierreSesionResponse(
        sesion=SesionCerrada(
            id=sesion.id_sesion,
            inicio=sesion.inicio,
            expira=sesion.expira,
            fin=sesion.fin,
            tipo_cierre=sesion.tipo_cierre,
            fin_verificado=sesion.fin_verificado,
        ),
        actividad=a_item(actividad) if actividad is not None else None,
    )
