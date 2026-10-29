// Pipeline de BCK-HUASCARAN (Jenkins multibranch del curso).
//
//   development → Build imagen, Trivy y despliegue
//   qa, uat     → pruebas (pytest + cobertura), SonarQube, Quality Gate, Build imagen,
//                 Trivy y despliegue
//   qa          → además, Integration Tests y E2E Tests (Bruno) y OWASP ZAP API Scan
//                 contra el despliegue
//   main        → todavía no despliega
//
// Trivy escanea la imagen candidata antes del despliegue: solo se permiten hallazgos LOW.
// Uno MEDIUM, HIGH, CRITICAL o UNKNOWN, o un escaneo que no termina, detiene el pipeline.
//
// El .env de cada entorno está en Jenkins como credencial "Secret file":
// HUASCARAN_BACKEND_DEV, _QA y _UAT. Nunca se imprime en el log.
// El contenedor no publica puertos: el proxy del servidor lo alcanza por la red
// externa proxy_net con el nombre <entorno>-bck-huascaran (ver docker-compose.yml).

pipeline {
    agent any

    options {
        // Solo se conserva el último build y sus artefactos (reportes de pruebas, Trivy y ZAP).
        buildDiscarder(logRotator(numToKeepStr: '1', artifactNumToKeepStr: '1'))
        disableConcurrentBuilds()
    }

    environment {
        ENTORNO  = "${env.BRANCH_NAME == 'development' ? 'dev' : env.BRANCH_NAME}"
        PROYECTO = "huascaran_bck_${env.BRANCH_NAME == 'development' ? 'dev' : env.BRANCH_NAME}"
        // Una imagen por entorno para que dev, qa y uat no se pisen. docker-compose.yml la
        // usa como ${IMAGEN}:actual; el build la deja como :candidata hasta pasar Trivy.
        IMAGEN   = "huascaran-bck-${ENTORNO}"
        // Trivy 0.76.1 fijado por digest. Nunca usar `latest` ni 0.69.4, 0.69.5 o 0.69.6:
        // fueron versiones comprometidas (GHSA-69fq-xp46-6x23).
        TRIVY_IMAGEN = 'aquasec/trivy@sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c'
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Pruebas') {
            when {
                // Sin esto, Jenkins levanta el contenedor del agente antes de
                // evaluar la condición y lo descarta enseguida en development.
                beforeAgent true
                anyOf {
                    branch 'qa'
                    branch 'uat'
                }
            }
            agent {
                docker {
                    image 'python:3.11-slim'
                    reuseNode true
                }
            }
            steps {
                // Las pruebas usan SQLite en memoria: no necesitan base de datos.
                sh '''
                    python -m venv venv
                    . venv/bin/activate
                    pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt
                    pytest tests/ --cov=app --cov-report=xml:coverage.xml
                '''
            }
        }

        stage('SonarQube') {
            when {
                anyOf {
                    branch 'qa'
                    branch 'uat'
                }
            }
            environment {
                scannerHome = tool 'SonarScanner'
            }
            steps {
                withSonarQubeEnv('SonarQube-Server') {
                    sh "${scannerHome}/bin/sonar-scanner"
                }
            }
        }

        stage('Quality Gate') {
            when {
                anyOf {
                    branch 'qa'
                    branch 'uat'
                }
            }
            steps {
                timeout(time: 15, unit: 'MINUTES') {
                    waitForQualityGate abortPipeline: true
                }
            }
        }

        stage('Build imagen') {
            when {
                anyOf {
                    branch 'development'
                    branch 'qa'
                    branch 'uat'
                }
            }
            steps {
                // --pull: cada build trae los últimos parches de la imagen base. No usa el .env.
                sh 'docker build --pull -t "$IMAGEN:candidata" .'
            }
        }

        // Trivy sobre la imagen candidata, antes del despliegue. Solo se permiten hallazgos
        // LOW: MEDIUM, HIGH, CRITICAL o UNKNOWN detienen el pipeline, sin excepciones (ni
        // .trivyignore ni filtros). Un escaneo que no termina también lo detiene.
        //
        // Como en ZAP, nada se monta desde el workspace: la imagen entra a un volumen con
        // nombre como tarball (Trivy no necesita el socket de Docker) y los reportes vuelven
        // con `docker cp`. Un solo escaneo deja el JSON completo; de ese mismo JSON salen el
        // HTML (con los LOW), la tabla del log y la regla.
        stage('Trivy - Imagen') {
            when {
                anyOf {
                    branch 'development'
                    branch 'qa'
                    branch 'uat'
                }
            }
            options {
                timeout(time: 20, unit: 'MINUTES')
            }
            environment {
                TRIVY_CONTENEDOR = "${PROYECTO}-trivy-${BUILD_NUMBER}"
                TRIVY_VOLUMEN    = "${PROYECTO}-trivy-${BUILD_NUMBER}-wrk"
                // Caché de la base de vulnerabilidades, una por entorno: unos cientos de MB que
                // no crecen con cada build (se actualizan en el lugar). Es una caché, no un
                // artefacto: no se borra en el post. Si queda inconsistente, Trivy falla y el
                // pipeline se detiene; nunca se salta el escaneo.
                TRIVY_CACHE      = "${PROYECTO}-trivy-cache"
            }
            steps {
                script {
                    def status = sh(
                        returnStatus: true,
                        script: '''
                            rm -rf trivy-reports
                            mkdir -p trivy-reports
                            docker volume create "$TRIVY_VOLUMEN" >/dev/null
                            docker volume create "$TRIVY_CACHE" >/dev/null

                            # Imagen candidata al volumen. Un fallo sale con 3 (error de ejecución),
                            # nunca con el 5 que se leería como "tiene vulnerabilidades". Si
                            # `docker save` fallara a mitad, el tarball queda incompleto y Trivy
                            # termina con error: el pipeline se detiene igual.
                            if ! docker image inspect "$IMAGEN:candidata" >/dev/null; then
                                echo "ERROR: no existe la imagen $IMAGEN:candidata"
                                exit 3
                            fi
                            if ! docker save "$IMAGEN:candidata" | docker run -i --rm -v "$TRIVY_VOLUMEN:/w" \
                                    --entrypoint sh "$TRIVY_IMAGEN" -c 'cat > /w/imagen.tar'; then
                                echo "ERROR: no se pudo pasar la imagen al volumen de Trivy"
                                exit 3
                            fi

                            # Jenkins corre `sh -xe`: sin el `||`, un fallo cortaría el script antes
                            # de copiar los reportes. Cada paso corre solo si el anterior salió con 0.
                            ESTADO=0
                            docker run --name "$TRIVY_CONTENEDOR" -v "$TRIVY_VOLUMEN:/w" -v "$TRIVY_CACHE:/root/.cache" \
                                "$TRIVY_IMAGEN" image --input /w/imagen.tar --scanners vuln,secret \
                                --format json --output /w/backend-trivy.json --no-progress || ESTADO=$?
                            if [ "$ESTADO" -eq 0 ]; then
                                docker run --rm -v "$TRIVY_VOLUMEN:/w" "$TRIVY_IMAGEN" convert --format template \
                                    --template "@contrib/html.tpl" --output /w/backend-trivy.html /w/backend-trivy.json || ESTADO=$?
                            fi
                            # Tabla completa en el log. --scanners solo agrega el resumen por target:
                            # sin él, una imagen sin hallazgos no deja nada en el log.
                            if [ "$ESTADO" -eq 0 ]; then
                                docker run --rm -v "$TRIVY_VOLUMEN:/w" "$TRIVY_IMAGEN" convert --format table \
                                    --scanners vuln,secret /w/backend-trivy.json || ESTADO=$?
                            fi
                            # La regla: 5 si hay algún hallazgo que no sea LOW (1 queda para errores de Trivy).
                            if [ "$ESTADO" -eq 0 ]; then
                                docker run --rm -v "$TRIVY_VOLUMEN:/w" "$TRIVY_IMAGEN" convert \
                                    --severity UNKNOWN,MEDIUM,HIGH,CRITICAL --exit-code 5 --format table \
                                    /w/backend-trivy.json || ESTADO=$?
                            fi

                            docker cp "$TRIVY_CONTENEDOR:/w/backend-trivy.html" trivy-reports/ || echo "AVISO: no se pudo copiar backend-trivy.html"
                            docker cp "$TRIVY_CONTENEDOR:/w/backend-trivy.json" trivy-reports/ || echo "AVISO: no se pudo copiar backend-trivy.json"
                            exit $ESTADO
                        '''
                    )

                    // 0 = solo hallazgos LOW (o ninguno) · 5 = algún hallazgo MEDIUM, HIGH,
                    // CRITICAL o UNKNOWN · cualquier otro = el escaneo no terminó.
                    if (status == 5) {
                        error('Trivy: la imagen tiene vulnerabilidades MEDIUM, HIGH, CRITICAL o UNKNOWN. No se despliega.')
                    } else if (status != 0) {
                        error("Trivy: el escaneo no terminó (${status}). No se despliega.")
                    }
                }
            }
            post {
                always {
                    // También si el timeout cortó el escaneo: no quedan contenedores ni volumen
                    // de trabajo. La caché (TRIVY_CACHE) se conserva.
                    sh '''
                        docker rm -f "$TRIVY_CONTENEDOR" >/dev/null 2>&1 || true
                        docker volume rm -f "$TRIVY_VOLUMEN" >/dev/null 2>&1 || true
                    '''
                    // Se archiva también cuando falla: ese HTML es el que hay que revisar.
                    archiveArtifacts(
                        artifacts: 'trivy-reports/**',
                        allowEmptyArchive: true
                    )
                }
            }
        }

        stage('Deploy') {
            when {
                anyOf {
                    branch 'development'
                    branch 'qa'
                    branch 'uat'
                }
            }
            steps {
                withCredentials([
                    file(credentialsId: "HUASCARAN_BACKEND_${env.ENTORNO.toUpperCase()}", variable: 'ENV_FILE')
                ]) {
                    // Solo se marca como "actual" y se despliega la imagen que pasó Trivy.
                    sh '''
                        rm -f .env
                        cp "$ENV_FILE" .env
                        docker tag "$IMAGEN:candidata" "$IMAGEN:actual"
                        docker compose -p "$PROYECTO" up -d --no-build --remove-orphans
                    '''
                }
            }
        }

        stage('Verificar despliegue') {
            when {
                anyOf {
                    branch 'development'
                    branch 'qa'
                    branch 'uat'
                }
            }
            steps {
                // Al arrancar, el contenedor migra la base y (si se pide) carga los
                // usuarios de prueba; recién después responde la API.
                sh '''
                    set +e
                    CONTENEDOR=$(docker compose -p "$PROYECTO" ps -aq backend)
                    RESPONDE=no

                    echo "Esperando hasta 120 s a que la API responda en / ..."
                    for i in $(seq 1 24); do
                        if docker compose -p "$PROYECTO" exec -T backend python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=3)" >/dev/null 2>&1; then
                            RESPONDE=si
                            break
                        fi
                        CORRIENDO=$(docker inspect -f '{{.State.Running}}' "$CONTENEDOR" 2>/dev/null)
                        RESTARTS=$(docker inspect -f '{{.RestartCount}}' "$CONTENEDOR" 2>/dev/null)
                        echo "  intento $i/24 -> Running=$CORRIENDO Restarts=$RESTARTS"
                        # Si el contenedor se detuvo o entra en bucle de reinicios
                        # (migración fallida, .env incompleto), no tiene sentido esperar.
                        if [ "$CORRIENDO" != "true" ] || [ "${RESTARTS:-0}" -gt 0 ]; then break; fi
                        sleep 5
                    done

                    docker compose -p "$PROYECTO" ps -a
                    docker compose -p "$PROYECTO" logs --tail=80 --no-color

                    if [ "$RESPONDE" != "si" ]; then
                        echo "ERROR: la API no responde. Revisar los logs de arriba (DATABASE_URL, migraciones)."
                        exit 1
                    fi
                    echo "OK: la API responde."
                '''
            }
        }

        // Colecciones Bruno contra el backend recién desplegado en qa (tests/bruno/README.md).
        // Solo qa: el seed de las cuentas E2E no corre en uat.
        stage('Integration Tests') {
            when {
                beforeAgent true
                branch 'qa'
            }
            agent {
                docker {
                    image 'node:22-bookworm-slim'
                    reuseNode true
                }
            }
            steps {
                // Si fallan, se marca el stage y se sigue con E2E Tests para tener ambos reportes.
                catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                    withCredentials([file(credentialsId: 'HUASCARAN_BACKEND_QA', variable: 'ENV_FILE')]) {
                        sh '''
                            set +x
                            # Bruno carga el .env de la raíz (el que dejó Deploy) y pisa las variables
                            # exportadas; además corta los valores en '#'. El despliegue ya terminó.
                            rm -f .env
                            . tests/bruno/credenciales-e2e.sh
                            export HOME="${WORKSPACE}/.bruno-home"
                            export npm_config_cache="${WORKSPACE}/.npm"
                            rm -f integracion.xml integracion.html
                            npx --yes @usebruno/cli@4.2.0 run tests/bruno/integracion --env qa \
                                --reporter-junit integracion.xml --reporter-html integracion.html \
                                --reporter-skip-request-body --reporter-skip-headers Authorization
                        '''
                    }
                }
            }
            post {
                always {
                    junit allowEmptyResults: true, testResults: 'integracion.xml'
                    archiveArtifacts artifacts: 'integracion.xml, integracion.html', allowEmptyArchive: true
                }
            }
        }

        stage('E2E Tests') {
            when {
                beforeAgent true
                branch 'qa'
            }
            agent {
                docker {
                    image 'node:22-bookworm-slim'
                    reuseNode true
                }
            }
            steps {
                withCredentials([file(credentialsId: 'HUASCARAN_BACKEND_QA', variable: 'ENV_FILE')]) {
                    sh '''
                        set +x
                        # Bruno carga el .env de la raíz (el que dejó Deploy) y pisa las variables
                        # exportadas; además corta los valores en '#'. El despliegue ya terminó.
                        rm -f .env
                        . tests/bruno/credenciales-e2e.sh
                        export HOME="${WORKSPACE}/.bruno-home"
                        export npm_config_cache="${WORKSPACE}/.npm"
                        rm -f e2e.xml e2e.html
                        npx --yes @usebruno/cli@4.2.0 run tests/bruno/e2e --env qa \
                            --reporter-junit e2e.xml --reporter-html e2e.html \
                            --reporter-skip-request-body --reporter-skip-headers Authorization
                    '''
                }
            }
            post {
                always {
                    junit allowEmptyResults: true, testResults: 'e2e.xml'
                    archiveArtifacts artifacts: 'e2e.xml, e2e.html', allowEmptyArchive: true
                }
            }
        }

        // OWASP ZAP API Scan (activo) de la API desplegada en qa, a partir de /openapi.json.
        //
        // Autenticado como el Supervisor E2E: zap/preparar.py hace el login y le entrega a
        // ZAP una copia del OpenAPI sin los endpoints que cerrarían su sesión, cambiarían
        // el estado de registros reales o enviarían PINs, y con ids inexistentes (999999)
        // para que nada se modifique. El token llega a ZAP por ZAP_AUTH_HEADER_VALUE: no
        // aparece en la línea de comando ni en el log. Va después de E2E Tests para que los
        // ataques no interfieran con esas pruebas.
        //
        // Como en el frontend, los archivos no se montan desde el workspace (Jenkins corre
        // en un contenedor y "$WORKSPACE" no existe en el host): van a un volumen de Docker
        // con nombre y vuelven con `docker cp`.
        stage('OWASP ZAP - Backend') {
            when {
                branch 'qa'
            }
            options {
                timeout(time: 60, unit: 'MINUTES')
            }
            environment {
                ZAP_API_BASE   = "http://${ENTORNO}-bck-huascaran:8000"
                // Misma versión fija que el frontend.
                ZAP_IMAGEN     = 'ghcr.io/zaproxy/zaproxy:2.17.0'
                ZAP_CONTENEDOR = "${PROYECTO}-zap-${BUILD_NUMBER}"
                ZAP_VOLUMEN    = "${PROYECTO}-zap-${BUILD_NUMBER}-wrk"
            }
            steps {
                withCredentials([file(credentialsId: 'HUASCARAN_BACKEND_QA', variable: 'ENV_FILE')]) {
                    script {
                        def status = sh(
                            returnStatus: true,
                            script: '''
                                set +x
                                rm -rf zap-reports
                                mkdir -p zap-reports
                                docker volume create "$ZAP_VOLUMEN" >/dev/null
                                # El volumen nace de root: el usuario zap (uid 1000) tiene que poder escribir.
                                docker run --rm -u root -v "$ZAP_VOLUMEN:/zap/wrk" --entrypoint chmod "$ZAP_IMAGEN" 0777 /zap/wrk

                                # Reglas y script de preparación al volumen. Un fallo sale con 3
                                # (error de ejecución), no con el 1 que se leería como "reglas incumplidas".
                                if ! { docker create --name "$ZAP_CONTENEDOR-archivos" -v "$ZAP_VOLUMEN:/zap/wrk" "$ZAP_IMAGEN" >/dev/null &&
                                       docker cp zap/reglas-backend.conf "$ZAP_CONTENEDOR-archivos:/zap/wrk/reglas-backend.conf" &&
                                       docker cp zap/preparar.py "$ZAP_CONTENEDOR-archivos:/zap/wrk/preparar.py"; }; then
                                    echo "ERROR: no se pudieron copiar los archivos de zap/ al volumen de ZAP"
                                    exit 3
                                fi
                                docker rm "$ZAP_CONTENEDOR-archivos" >/dev/null

                                if ! . tests/bruno/credenciales-e2e.sh; then exit 3; fi
                                if ! ZAP_AUTH_HEADER_VALUE=$(docker run --rm --network proxy_net -v "$ZAP_VOLUMEN:/zap/wrk" \
                                        -e ZAP_API_BASE -e E2E_SUPERVISOR_USER -e E2E_SUPERVISOR_PASS \
                                        --entrypoint python3 "$ZAP_IMAGEN" /zap/wrk/preparar.py preparar); then
                                    echo "ERROR: no se pudo preparar el escaneo (OpenAPI o login del Supervisor E2E)"
                                    exit 3
                                fi
                                export ZAP_AUTH_HEADER_VALUE

                                set -- zap-api-scan.py -t openapi-zap.json -f openapi -O "$ZAP_API_BASE"
                                # 5 hilos por host (por defecto 2): acorta el escaneo sin quitar reglas.
                                set -- "$@" -z "-config scanner.maxScanDurationInMins=30 -config scanner.threadPerHost=5"
                                set -- "$@" -c reglas-backend.conf
                                set -- "$@" -r backend-zap.html -J backend-zap.json
                                # Jenkins corre `sh -xe`: sin el `||`, un 1 o un 2 de ZAP cortaría
                                # el script antes de cerrar la sesión y copiar los reportes.
                                ESTADO=0
                                docker run --name "$ZAP_CONTENEDOR" --network proxy_net -v "$ZAP_VOLUMEN:/zap/wrk" \
                                    -e ZAP_AUTH_HEADER_VALUE "$ZAP_IMAGEN" "$@" || ESTADO=$?

                                docker run --rm --network proxy_net -v "$ZAP_VOLUMEN:/zap/wrk" -e ZAP_API_BASE -e ZAP_AUTH_HEADER_VALUE \
                                    --entrypoint python3 "$ZAP_IMAGEN" /zap/wrk/preparar.py cerrar || true
                                docker cp "$ZAP_CONTENEDOR:/zap/wrk/backend-zap.html" zap-reports/ || echo "AVISO: no se pudo copiar backend-zap.html"
                                docker cp "$ZAP_CONTENEDOR:/zap/wrk/backend-zap.json" zap-reports/ || echo "AVISO: no se pudo copiar backend-zap.json"
                                exit $ESTADO
                            '''
                        )

                        // Código de salida de zap-api-scan.py:
                        //   0 = sin alertas · 1 = al menos una regla en FAIL ·
                        //   2 = solo advertencias (WARN) · 3 u otro = el escaneo no terminó.
                        if (status == 1) {
                            error('ZAP Backend: reglas de seguridad incumplidas')
                        } else if (status == 2) {
                            unstable('ZAP Backend: advertencias detectadas')
                        } else if (status != 0) {
                            error("ZAP Backend: error de ejecución (${status})")
                        }
                    }
                }
            }
            post {
                always {
                    // También si el timeout cortó el escaneo: no quedan contenedores ni volumen.
                    sh '''
                        docker rm -f "$ZAP_CONTENEDOR" "$ZAP_CONTENEDOR-archivos" >/dev/null 2>&1 || true
                        docker volume rm -f "$ZAP_VOLUMEN" >/dev/null 2>&1 || true
                    '''
                    archiveArtifacts(
                        artifacts: 'zap-reports/**',
                        allowEmptyArchive: true
                    )
                }
            }
        }
    }

    post {
        always {
            // El .env solo hace falta para levantar el contenedor.
            sh 'rm -f .env'
            // Imágenes, para no llenar el disco del servidor (limpieza después del resultado:
            // estos `|| true` no ocultan nada del escaneo).
            //   - candidata: si también es "actual" solo pierde esa etiqueta; si Trivy la
            //     rechazó, se elimina.
            //   - prune sin -a: solo imágenes huérfanas (sin etiqueta) de este proyecto, como
            //     las "actual" anteriores. No toca las de otros equipos ni las "actual" de
            //     dev, qa o uat.
            //   - ${PROYECTO}-backend: imagen del esquema anterior (compose sin `image:`).
            //     Se puede quitar cuando ya no quede en el servidor.
            sh '''
                docker image rm "$IMAGEN:candidata" >/dev/null 2>&1 || true
                docker image prune -f --filter "label=proyecto=huascaran-bck" >/dev/null 2>&1 || true
                docker image rm "${PROYECTO}-backend" >/dev/null 2>&1 || true
            '''
            // Limpia el workspace (reportes ya archivados, cachés de npm, venv): no
            // borra los artefactos del último build.
            deleteDir()
        }
    }
}
