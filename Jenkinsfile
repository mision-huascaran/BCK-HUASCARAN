// Pipeline de BCK-HUASCARAN (Jenkins multibranch del curso).
//
//   development → despliegue
//   qa, uat     → pruebas (pytest + cobertura), SonarQube, Quality Gate y despliegue
//   main        → todavía no despliega
//
// El .env de cada entorno está en Jenkins como credencial "Secret file":
// HUASCARAN_BACKEND_DEV, _QA y _UAT. Nunca se imprime en el log.
// El contenedor no publica puertos: el proxy del servidor lo alcanza por la red
// externa proxy_net con el nombre <entorno>-bck-huascaran (ver docker-compose.yml).

pipeline {
    agent any

    options {
        buildDiscarder(logRotator(numToKeepStr: '5'))
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
    }

    post {
        always {
            // El .env solo hace falta para levantar el contenedor.
            sh 'rm -f .env'
        }
    }
}
