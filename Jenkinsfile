// Pipeline de BCK-HUASCARAN (Jenkins multibranch del curso).
//
//   development → despliegue
//   qa, uat     → pruebas (pytest + cobertura), SonarQube, Quality Gate y despliegue
//   qa          → además, Integration Tests y E2E Tests (Bruno) y OWASP ZAP API Scan
//                 contra el despliegue
//   main        → todavía no despliega
//
// El .env de cada entorno está en Jenkins como credencial "Secret file":
// HUASCARAN_BACKEND_DEV, _QA y _UAT. Nunca se imprime en el log.
// El contenedor no publica puertos: el proxy del servidor lo alcanza por la red
// externa proxy_net con el nombre <entorno>-bck-huascaran (ver docker-compose.yml).

pipeline {
    agent any

    options {
        // Solo se conserva el último build y sus artefactos (reportes de pruebas y de ZAP).
        buildDiscarder(logRotator(numToKeepStr: '1', artifactNumToKeepStr: '1'))
        disableConcurrentBuilds()
    }

    environment {
        ENTORNO  = "${env.BRANCH_NAME == 'development' ? 'dev' : env.BRANCH_NAME}"
        PROYECTO = "huascaran_bck_${env.BRANCH_NAME == 'development' ? 'dev' : env.BRANCH_NAME}"
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
                    sh '''
                        rm -f .env
                        cp "$ENV_FILE" .env
                        docker compose -p "$PROYECTO" up -d --build --remove-orphans
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
                                set -- "$@" -z "-config scanner.maxScanDurationInMins=30"
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
            // Limpia el workspace (reportes ya archivados, cachés de npm, venv): no
            // borra los artefactos del último build.
            deleteDir()
        }
    }
}
