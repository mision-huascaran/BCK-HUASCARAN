# Imagen del backend en dos etapas sobre Alpine (Trivy: solo hallazgos LOW).
# La misma etiqueta de Alpine en las dos etapas; sin digest para recibir los parches de esa línea.

FROM python:3.11-alpine3.24 AS build

COPY requirements.txt .
# venv sin pip: el pip de esta etapa instala dentro del venv con --python (shebangs del venv)
RUN python -m venv --without-pip /opt/venv \
    && pip --python /opt/venv/bin/python install --no-cache-dir -r requirements.txt

FROM python:3.11-alpine3.24

LABEL proyecto="huascaran-bck"

# Ni el Python del sistema ni el venv quedan con pip, setuptools ni wheel.
# Usuario sin privilegios: el arranque no escribe en el disco del contenedor.
RUN python -m pip uninstall -y pip setuptools wheel \
    && rm -rf /usr/local/lib/python3.11/site-packages/setuptools \
              /usr/local/lib/python3.11/site-packages/pkg_resources \
              /usr/local/lib/python3.11/site-packages/_distutils_hack \
    && addgroup -S -g 10001 app \
    && adduser -S -D -H -u 10001 -G app app

COPY --from=build /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY . .

USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
