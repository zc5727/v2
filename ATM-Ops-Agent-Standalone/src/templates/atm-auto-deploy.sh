#!/usr/bin/env bash
set -euo pipefail

CONFIG_FILE="${1:-./atm-deploy.env}"

if [ ! -f "$CONFIG_FILE" ]; then
  echo "配置文件不存在：$CONFIG_FILE"
  exit 1
fi

source "$CONFIG_FILE"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "缺少命令：$1"
    exit 1
  fi
}

require_var() {
  local name="$1"
  if [ -z "${!name:-}" ]; then
    echo "缺少配置：$name"
    exit 1
  fi
}

ssh_exec() {
  local host="$1"
  local command="$2"

  expect -c "
    set timeout 900
    spawn ssh -o StrictHostKeyChecking=no ${SSH_USER}@${host} \"$command\"
    expect {
      \"*assword:*\" { send \"${SSH_PASSWORD}\\r\"; exp_continue }
      eof
    }
    catch wait result
    exit [lindex \$result 3]
  "
}

scp_file() {
  local src="$1"
  local host="$2"
  local dest="$3"

  expect -c "
    set timeout 900
    spawn scp -o StrictHostKeyChecking=no \"$src\" ${SSH_USER}@${host}:\"$dest\"
    expect {
      \"*assword:*\" { send \"${SSH_PASSWORD}\\r\"; exp_continue }
      eof
    }
    catch wait result
    exit [lindex \$result 3]
  "
}

pull_code() {
  require_cmd git
  require_var REPO_URL
  require_var BRANCH
  require_var WORK_DIR

  if [ -d "$WORK_DIR/.git" ]; then
    echo "更新代码：$WORK_DIR"
    git -C "$WORK_DIR" fetch --all --prune
    git -C "$WORK_DIR" checkout "$BRANCH"
    git -C "$WORK_DIR" pull --ff-only origin "$BRANCH"
  else
    echo "克隆代码：$REPO_URL -> $WORK_DIR"
    mkdir -p "$(dirname "$WORK_DIR")"
    git clone -b "$BRANCH" "$REPO_URL" "$WORK_DIR"
  fi
}

build_backend() {
  require_cmd mvn

  echo "构建后端服务"
  cd "$WORK_DIR"
  mvn clean package -DskipTests -P"${MAVEN_PROFILE}"
}

build_frontend() {
  require_cmd pnpm

  echo "构建前端 atm-ui"
  cd "$WORK_DIR/atm-ui"
  pnpm install --frozen-lockfile
  ${UI_BUILD_CMD}
}

prepare_jars() {
  echo "复制后端 jar 到 Docker 构建目录"
  cp "$WORK_DIR/atm-auth/target/atm-auth.jar" "$WORK_DIR/docker/atm-pre-services/atm-auth.jar"
  cp "$WORK_DIR/atm-modules/atm-system/target/atm-modules-system.jar" "$WORK_DIR/docker/atm-pre-services/atm-system.jar"
  cp "$WORK_DIR/atm-modules/atm-service/target/atm-service.jar" "$WORK_DIR/docker/atm-pre-services/atm-service.jar"
  cp "$WORK_DIR/atm-modules/atm-openapi/target/atm-openapi.jar" "$WORK_DIR/docker/atm-pre-services/atm-openapi.jar"
  cp "$WORK_DIR/atm-gateway/target/atm-gateway.jar" "$WORK_DIR/docker/atm-gateway-pre/atm-gateway.jar"
}

package_artifacts() {
  echo "整理部署产物"
  rm -rf /tmp/atm-deploy-artifacts
  mkdir -p /tmp/atm-deploy-artifacts/services
  mkdir -p /tmp/atm-deploy-artifacts/gateway
  mkdir -p /tmp/atm-deploy-artifacts/bigdata

  prepare_jars

  cp -R "$WORK_DIR/docker/atm-pre-services" /tmp/atm-deploy-artifacts/services-compose
  cp -R "$WORK_DIR/docker/atm-gateway-pre" /tmp/atm-deploy-artifacts/gateway-compose

  if [ -d "$WORK_DIR/docker/bigdata-pre" ]; then
    cp -R "$WORK_DIR/docker/bigdata-pre" /tmp/atm-deploy-artifacts/bigdata/bigdata-pre
  fi

  if [ -d "$WORK_DIR/atm-ui/dist" ]; then
    tar -czf /tmp/atm-deploy-artifacts/atm-ui-dist.tgz -C "$WORK_DIR/atm-ui" dist
  else
    echo "前端 dist 不存在：$WORK_DIR/atm-ui/dist"
    exit 1
  fi

  tar -czf /tmp/atm-deploy-artifacts/atm-backend.tgz -C /tmp/atm-deploy-artifacts services-compose gateway-compose

  if [ -d /tmp/atm-deploy-artifacts/bigdata/bigdata-pre ]; then
    tar -czf /tmp/atm-deploy-artifacts/bigdata-pre.tgz -C /tmp/atm-deploy-artifacts/bigdata bigdata-pre
  fi
}

deploy_backend_and_gateway() {
  echo "上传并部署后端与网关到 ${APP_HOST}"
  scp_file /tmp/atm-deploy-artifacts/atm-backend.tgz "$APP_HOST" /tmp/atm-backend.tgz

  ssh_exec "$APP_HOST" "
    set -e
    mkdir -p /data/atm-auto-release/backend
    tar -xzf /tmp/atm-backend.tgz -C /data/atm-auto-release/backend

    mkdir -p ${REMOTE_SERVICES_DIR}
    mkdir -p ${REMOTE_GATEWAY_DIR}

    cp -R /data/atm-auto-release/backend/services-compose/. ${REMOTE_SERVICES_DIR}/
    cp -R /data/atm-auto-release/backend/gateway-compose/. ${REMOTE_GATEWAY_DIR}/

    cd ${REMOTE_SERVICES_DIR}
    docker compose up -d --build

    cd ${REMOTE_GATEWAY_DIR}
    docker compose up -d --build
  "
}

deploy_frontend() {
  echo "上传并部署前端到 ${APP_HOST}"
  scp_file /tmp/atm-deploy-artifacts/atm-ui-dist.tgz "$APP_HOST" /tmp/atm-ui-dist.tgz

  ssh_exec "$APP_HOST" "
    set -e
    mkdir -p ${REMOTE_UI_DIR}
    cp /tmp/atm-ui-dist.tgz ${REMOTE_UI_DIR}/dist.tgz
    cd ${REMOTE_UI_DIR}

    if [ -x ./release.sh ]; then
      ./release.sh dist.tgz
    else
      release_dir=${REMOTE_UI_DIR}/releases/\$(date +%Y%m%d%H%M%S)
      mkdir -p \"\$release_dir\"
      tar -xzf dist.tgz -C \"\$release_dir\"
      if [ -d \"\$release_dir/dist\" ]; then
        ln -sfn \"\$release_dir/dist\" ${REMOTE_UI_DIR}/current
      elif [ -d \"\$release_dir/atm-ui/dist\" ]; then
        ln -sfn \"\$release_dir/atm-ui/dist\" ${REMOTE_UI_DIR}/current
      else
        echo \"未找到 dist 目录\"
        exit 1
      fi
    fi

    nginx -t
    systemctl reload nginx
  "
}

deploy_bigdata() {
  if [ ! -f /tmp/atm-deploy-artifacts/bigdata-pre.tgz ]; then
    echo "没有 bigdata-pre 模板，跳过大数据部署"
    return 0
  fi

  echo "上传并部署 Spark/Hadoop 到 ${BIGDATA_HOST}"
  scp_file /tmp/atm-deploy-artifacts/bigdata-pre.tgz "$BIGDATA_HOST" /tmp/bigdata-pre.tgz

  ssh_exec "$BIGDATA_HOST" "
    set -e
    rm -rf ${REMOTE_BIGDATA_DIR}.new
    mkdir -p ${REMOTE_BIGDATA_DIR}.new
    tar -xzf /tmp/bigdata-pre.tgz -C ${REMOTE_BIGDATA_DIR}.new --strip-components=1

    if [ -d ${REMOTE_BIGDATA_DIR}/hdfs ]; then
      cp -a ${REMOTE_BIGDATA_DIR}/hdfs ${REMOTE_BIGDATA_DIR}.new/
    fi

    rm -rf ${REMOTE_BIGDATA_DIR}.bak
    if [ -d ${REMOTE_BIGDATA_DIR} ]; then
      mv ${REMOTE_BIGDATA_DIR} ${REMOTE_BIGDATA_DIR}.bak
    fi
    mv ${REMOTE_BIGDATA_DIR}.new ${REMOTE_BIGDATA_DIR}

    mkdir -p ${REMOTE_BIGDATA_DIR}/spark-work ${REMOTE_BIGDATA_DIR}/logs/spark
    chmod -R 777 ${REMOTE_BIGDATA_DIR}/spark-work ${REMOTE_BIGDATA_DIR}/logs

    cd ${REMOTE_BIGDATA_DIR}
    docker compose up -d
    docker exec hadoop-namenode-pre hdfs dfs -mkdir -p /spark-logs /tmp /user/root /user/hive/warehouse || true
    docker exec hadoop-namenode-pre hdfs dfs -chmod -R 777 /spark-logs /tmp /user || true
  "
}

health_check() {
  echo "检查 ATM 应用服务器"
  ssh_exec "$APP_HOST" "
    set -e
    docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | egrep 'atm-|nacos|NAMES'
    curl -I --max-time 10 http://127.0.0.1:9400/ || true
    curl -I --max-time 10 http://127.0.0.1:9300/ || true
  "

  echo "检查大数据服务器"
  ssh_exec "$BIGDATA_HOST" "
    set -e
    cd ${REMOTE_BIGDATA_DIR}
    docker compose ps
    docker exec hadoop-namenode-pre hdfs dfs -ls /
    curl -s -o /dev/null -w 'namenode=%{http_code}\n' http://127.0.0.1:9870/
    curl -s -o /dev/null -w 'spark-master=%{http_code}\n' http://127.0.0.1:8081/
    curl -s -o /dev/null -w 'spark-worker=%{http_code}\n' http://127.0.0.1:8082/
  "
}

usage() {
  cat <<EOF
用法：
  $0 [配置文件] all
  $0 [配置文件] code
  $0 [配置文件] build
  $0 [配置文件] backend
  $0 [配置文件] frontend
  $0 [配置文件] bigdata
  $0 [配置文件] check
EOF
}

main() {
  require_cmd expect
  require_cmd scp
  require_cmd ssh
  require_cmd tar

  local action="${2:-all}"

  case "$action" in
    all)
      pull_code
      build_backend
      build_frontend
      package_artifacts
      deploy_backend_and_gateway
      deploy_frontend
      deploy_bigdata
      health_check
      ;;
    code)
      pull_code
      ;;
    build)
      build_backend
      build_frontend
      package_artifacts
      ;;
    backend)
      package_artifacts
      deploy_backend_and_gateway
      health_check
      ;;
    frontend)
      build_frontend
      package_artifacts
      deploy_frontend
      health_check
      ;;
    bigdata)
      package_artifacts
      deploy_bigdata
      health_check
      ;;
    check)
      health_check
      ;;
    *)
      usage
      exit 1
      ;;
  esac
}

main "$@"
