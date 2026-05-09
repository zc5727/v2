# 阿里云服务器部署合并最终版

更新时间：2026-04-21

## 重要说明

前面实际部署、登录、验证过的服务器如下：

| 服务器 | 内网 IP | 账号 | 主要用途 |
| --- | --- | --- | --- |
| `8.161.226.223` | `172.20.71.211` | `root` | ATM pre 应用服务器、Nacos、Gateway、前端 Nginx |
| `8.161.227.173` | `172.20.71.210` | `root` | XXL-Job Admin、DolphinScheduler、ClickHouse、Postgres、Spark/Hadoop |
| `8.130.65.254` | 未核验 | `root` | MySQL 数据库服务器 |

注意：

- 前面出现过 `8.161.227.223`、`8.161.226.173` 的说法，但实际登录和部署核验的是 `8.161.226.223` 与 `8.161.227.173`。
- 密码、token 等敏感信息不再写入项目模板，统一按运行时注入或外部保管处理。

## 8.161.226.223: ATM Pre 应用服务器

### 运行中应用

| 应用 | 容器/服务 | 端口 | 说明 |
| --- | --- | --- | --- |
| Nacos | `nacos` | `8848`、`9848`、`9849`、`8080` | Nacos 最新版 |
| Gateway | `atm-gateway-pre` | `9300` | Nacos 配置文件 `atm-gateway-pre.yml`，namespace `pd-pre` |
| Auth | `atm-auth-pre` | `9200` | ATM pre 后端服务 |
| System | `atm-system-pre` | `9201` | ATM pre 后端服务 |
| Service | `atm-service-pre` | `9202`、`9998` | `9998` 为 XXL-Job executor 端口 |
| OpenAPI | `atm-openapi-pre` | `9203` | ATM pre 后端服务 |
| ATM UI | 宿主机 Nginx 静态站点 | `9400` | 根目录 `/data/atm-ui-pre/current` |
| Nginx 默认站点 | 宿主机 Nginx | `80` | 系统默认 Nginx |

### 关键目录

- `/data/atm-gateway-pre`
- `/data/atm-pre-services`
- `/data/atm-ui-pre`
- `/data/atm-ui-pre/current -> /data/atm-ui-pre/releases/20260417111421/atm-ui/dist`
- `/data/atm-pre-compose-network`
- `/data/atm-pre-standardize`
- `/data/atm-service-pre-redeploy`
- `/data/atm-ui`
- `/data/atm-ui-release-fix`
- `/data/docker`

其中 `/data/docker` 是 Docker 数据目录，不建议手动修改。

### Nacos

- 容器名：`nacos`
- 镜像：`nacos/nacos-server:latest`
- 控制台：`http://8.161.226.223:8848/nacos`
- namespace：`pd-pre`
- namespace ID：`ba92e967-e1b2-4891-afa4-ad59385b1dbb`
- Gateway Data ID：`atm-gateway-pre.yml`
- Service Data ID：`atm-service-pre.yml`

### ATM 后端微服务

- 部署目录：`/data/atm-pre-services`
- 运行方式：Docker Compose
- Compose 文件：`/data/atm-pre-services/docker-compose.yml`
- Docker 网络：`atm-pre-network`
- Compose 项目名：`atm-pre-services`

服务清单：

- `atm-auth-pre` 认证服务 `9200`
- `atm-system-pre` 系统服务 `9201`
- `atm-service-pre` 业务服务 `9202`
- `atm-openapi-pre` 开放接口服务 `9203`

### atm-service-pre 与 XXL-Job 执行器

- 执行器端口：`9998`
- 执行器 AppName：`atm-job-chengdu`
- 推荐注册地址：`http://172.20.71.211:9998`
- 调度中心：`http://172.20.71.210:7000/xxl-job-admin`

当前已固化的执行器参数：

- `xxl.job.admin.addresses = http://172.20.71.210:7000/xxl-job-admin`
- `xxl.job.executor.appname = atm-job-chengdu`
- `xxl.job.executor.address = http://172.20.71.211:9998`
- `xxl.job.executor.ip = 172.20.71.211`
- `xxl.job.executor.port = 9998`

敏感项如 access token 应通过外部安全存储注入，不在知识库中固化明文。

### ATM 网关

- 部署目录：`/data/atm-gateway-pre`
- 运行方式：Docker Compose
- Compose 文件：`/data/atm-gateway-pre/docker-compose.yml`
- 容器名：`atm-gateway-pre`
- 镜像：`atm-gateway:pre`
- 服务端口：`9300`
- Docker 网络：`atm-pre-network`
- Compose 项目名：`atm-gateway-pre`

### ATM 前端

- 运行方式：宿主机 Nginx
- 前端访问端口：`9400`
- 前端根目录：`/data/atm-ui-pre/current`
- 当前软链目标：`/data/atm-ui-pre/releases/20260417111421/atm-ui/dist`
- 发布包：`/data/atm-ui-pre/dist.tgz`
- 发布脚本：`/data/atm-ui-pre/release.sh`
- Nginx 配置来源：`/etc/nginx/nginx.conf`

Nginx 前端代理配置：

- `http://8.161.226.223:9400/` -> `/data/atm-ui-pre/current`
- `http://8.161.226.223:9400/prod-api/` -> `http://127.0.0.1:9300/`
- `http://8.161.226.223:9400/webSocket/` -> `http://127.0.0.1:9202/`
- `http://8.161.226.223:9400/atm/` -> `/home/atm/uploadPath/`

## 8.161.227.173: 调度与大数据服务器

### 运行中应用

| 应用 | 容器/服务 | 端口 | 说明 |
| --- | --- | --- | --- |
| XXL-Job Admin | `atm-job` | `7000` | 管理台 |
| DolphinScheduler | `dolphinscheduler-standalone` | 多端口 | standalone 模式 |
| DolphinScheduler Postgres | `dolphinscheduler-postgres` | `5432` | 本机 Postgres |
| ClickHouse | `clickhouse-server` | `8123`、`9000` | `9000` 为 Native TCP |
| Hadoop/HDFS | `hadoop-namenode-pre`、`hadoop-datanode-pre` | `8020`、`9870`、`9864` | Compose 目录 `/data/bigdata-pre` |
| Spark | `spark-master-pre`、`spark-worker-1-pre` | `7077`、`8081`、`8082` | Compose 目录 `/data/bigdata-pre` |

当前主要监听端口：

- `7000` XXL-Job Admin
- `5432` DolphinScheduler Postgres
- `8123` ClickHouse HTTP
- `9000` ClickHouse Native TCP
- `8020` HDFS NameNode RPC
- `9870` HDFS NameNode Web UI
- `9864` HDFS DataNode Web UI
- `7077` Spark Master RPC
- `8081` Spark Master Web UI
- `8082` Spark Worker Web UI
- `12345` DolphinScheduler 相关端口
- `5678` DolphinScheduler 相关端口
- `25333` DolphinScheduler Java 进程端口
- `50052` DolphinScheduler Java 进程端口

### atm-job / XXL-Job Admin

- 部署目录：`/data/atm-job`
- 运行方式：Docker 容器
- 容器名：`atm-job`
- 镜像：`atm-job:pre`
- 对外端口：`7000`
- Spring Profile：`pre`
- 数据库：`8.130.65.254:3306/atm_job`
- 公网地址：`http://8.161.227.173:7000/xxl-job-admin/`
- 内网地址：`http://172.20.71.210:7000/xxl-job-admin/`
- 管理台默认账号：`admin`

调度 token 不在知识库中存明文，运行时注入。

### DolphinScheduler

- 部署目录：`/data/dolphinscheduler`
- 运行方式：Docker / Docker Compose
- 当前运行容器：`dolphinscheduler-standalone`
- 镜像：`hub.rat.dev/apache/dolphinscheduler:latest`
- 数据目录挂载：`/data/dolphinscheduler/data -> /opt/soft/dolphinscheduler/data`
- 日志目录挂载：`/data/dolphinscheduler/logs -> /opt/soft/dolphinscheduler/logs`
- 当前数据库：本机 Postgres 容器 `127.0.0.1:5432`
- 数据库名：`dolphinscheduler`
- 数据库用户：`dolphinscheduler`
- Compose 文件：`/data/dolphinscheduler/docker-compose.yml`

说明：

- `/data/dolphinscheduler` 下还存在一份 `docker-compose.yaml`，内容是 master、worker、api、zookeeper 拆分部署方案，使用远端 MySQL。
- 当前实际运行的是 standalone 方案，不是拆分容器方案。

### ClickHouse

- 容器名：`clickhouse-server`
- 镜像：`clickhouse/clickhouse-server:latest`
- 端口映射：`8123:8123`、`9000:9000`
- 数据目录：`/data/clickhouse -> /var/lib/clickhouse`
- 用户配置挂载：`/data/clickhouse/config/users.xml -> /etc/clickhouse-server/users.xml`
- HTTP 地址：`http://8.161.227.173:8123`
- Native TCP 地址：`8.161.227.173:9000`

### Spark / Hadoop 当前 Docker 部署

- 部署目录：`/data/bigdata-pre`
- 本地模板：`/Users/chenzhuo/project/cd-new/atm-cd/docker/bigdata-pre`
- Compose 文件：`/data/bigdata-pre/docker-compose.yml`
- Docker 网络：`bigdata-pre-network`

容器与镜像：

- `hadoop-namenode-pre` `docker.1ms.run/sbloodys/hadoop:3.3.6`
- `hadoop-datanode-pre` `docker.1ms.run/sbloodys/hadoop:3.3.6`
- `spark-master-pre` `docker.1ms.run/apache/spark:3.4.3`
- `spark-worker-1-pre` `docker.1ms.run/apache/spark:3.4.3`

HDFS 配置：

- `fs.defaultFS = hdfs://hadoop-namenode:8020`
- `dfs.replication = 1`
- `dfs.namenode.name.dir = file:///data/hdfs/namenode`
- `dfs.datanode.data.dir = file:///data/hdfs/datanode`
- `dfs.namenode.rpc-address = hadoop-namenode:8020`
- `dfs.namenode.http-address = 0.0.0.0:9870`
- `dfs.datanode.http.address = 0.0.0.0:9864`
- `dfs.permissions.enabled = false`

Spark 配置：

- `spark.master = spark://spark-master:7077`
- `spark.app.name = SparkOnHDFSPre`
- `spark.hadoop.fs.defaultFS = hdfs://hadoop-namenode:8020`
- `spark.eventLog.enabled = true`
- `spark.eventLog.dir = hdfs://hadoop-namenode:8020/spark-logs`
- `spark.sql.warehouse.dir = hdfs://hadoop-namenode:8020/user/hive/warehouse`

已创建的 HDFS 目录：

- `/spark-logs`
- `/tmp`
- `/user`
- `/user/root`
- `/user/hive/warehouse`

最新验证结果：

- `docker compose ps`：4 个容器均 Up
- `hdfs dfs -ls /`：正常返回 `/spark-logs`、`/tmp`、`/user`
- NameNode UI：`http://8.161.227.173:9870/` 返回 `302`
- Spark Master UI：`http://8.161.227.173:8081/` 返回 `200`
- Spark Worker UI：`http://8.161.227.173:8082/` 返回 `200`
- SparkPi 示例运行成功

当前部署的是 HDFS + Spark standalone，不是 YARN 集群。HDFS RPC 使用 `8020`，避免和 ClickHouse 的 `9000` 冲突。

### Spark / Hadoop 之前未运行的原因

原来只有配置目录，没有真正运行中的 Hadoop/Spark 服务：

- `docker ps -a` 没有 Hadoop 或 Spark 容器
- `docker images` 没有 Hadoop 或 Spark 镜像
- `pgrep hadoop`、`pgrep yarn`、`pgrep spark` 没有发现进程
- `/data/hadoop/hadoop-3.3.4.tar.gz` 是 `0` 字节
- `/data/hadoop` 没有完整 Hadoop 二进制安装目录
- `/data/spark` 没有 Spark 二进制安装目录
- 原 Spark 配置指向 `hdfs://8.161.227.173:9000`，但 `9000` 实际被 ClickHouse 占用
- 原 Hadoop 配置里的 `fs.defaultFS`、数据目录路径不一致

结论：原状态属于“配置目录存在，但服务没有安装/启动”，现已通过 `/data/bigdata-pre` Docker Compose 修复。

## 8.130.65.254: MySQL 数据库服务器

- 公网 IP：`8.130.65.254`
- 登录用户：`root`
- MySQL 地址：`8.130.65.254:3306`
- MySQL 用户：`root`
- 主要数据库：`atm_job`

数据库密码不在知识库中存明文。

## XXL-Job 调度关系

- `atm-job` 是调度中心，只负责管理和触发任务
- `atm-cd` 里的 `atm-service-pre` 是执行器，真正执行 Java 代码中的任务
- XXL-Job 管理台里的执行器 AppName 应为：`atm-job-chengdu`
- 管理台里任务的 `JobHandler` 必须和代码里的 `@XxlJob("...")` 名称一致

历史异常：

- 旧注册表里曾出现容器内网地址 `http://172.21.0.2:9998/`
- 期望注册地址是 `http://172.20.71.211:9998/`

如果管理台仍显示旧地址，重启 `atm-service-pre` 后重新检查注册表。

## atm-spark 模块依赖结论

- `atm-spark` 模块明确使用 Spark
- `pom.xml` 声明了 `spark-core_2.12:3.4.3` 和 `spark-sql_2.12:3.4.3`
- 代码中使用了 `SparkSession`、`Dataset<Row>`、`SaveMode`、`StorageLevel` 等 Spark API

说明：

- `atm-spark` 没有直接声明 `org.apache.hadoop:hadoop-*` 依赖
- Hadoop/HDFS 更像是 Spark 的运行时存储环境，通过 Spark 的 Hadoop 配置间接使用

## 常用运维命令

查看 `8.161.227.173` 容器：

```bash
ssh root@8.161.227.173
docker ps -a
```

查看 `atm-job`：

```bash
ssh root@8.161.227.173
docker ps | grep atm-job
docker logs --tail 100 atm-job
```

查看 DolphinScheduler：

```bash
ssh root@8.161.227.173
cd /data/dolphinscheduler
docker ps | grep dolphinscheduler
docker logs --tail 100 dolphinscheduler-standalone
docker logs --tail 100 dolphinscheduler-postgres
```

查看 ClickHouse：

```bash
ssh root@8.161.227.173
docker ps | grep clickhouse
docker logs --tail 100 clickhouse-server
curl http://127.0.0.1:8123/ping
```

查看 Spark/Hadoop：

```bash
ssh root@8.161.227.173
cd /data/bigdata-pre
docker compose ps
docker exec hadoop-namenode-pre hdfs dfs -ls /
curl -I http://127.0.0.1:9870/
curl -I http://127.0.0.1:8081/
curl -I http://127.0.0.1:8082/
```

重启 Spark/Hadoop：

```bash
ssh root@8.161.227.173
cd /data/bigdata-pre
docker compose restart
```

测试 Spark 任务：

```bash
ssh root@8.161.227.173
docker exec spark-master-pre /opt/spark/bin/spark-submit \
  --master spark://spark-master:7077 \
  --class org.apache.spark.examples.SparkPi \
  /opt/spark/examples/jars/spark-examples_2.12-3.4.3.jar 2
```

查看 `8.161.226.223` 容器：

```bash
ssh root@8.161.226.223
docker ps
```

查看后端微服务：

```bash
ssh root@8.161.226.223
cd /data/atm-pre-services
docker compose ps
docker logs --tail 100 atm-service-pre
```

重启后端微服务：

```bash
ssh root@8.161.226.223
cd /data/atm-pre-services
docker compose restart atm-auth-pre atm-system-pre atm-service-pre atm-openapi-pre
```

查看和重启网关：

```bash
ssh root@8.161.226.223
cd /data/atm-gateway-pre
docker compose ps
docker compose restart atm-gateway-pre
```

查看和重载 Nginx：

```bash
ssh root@8.161.226.223
nginx -T
systemctl status nginx
nginx -t
systemctl reload nginx
```

查询 XXL-Job 执行器注册表：

```bash
ssh root@8.130.65.254
docker exec mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -s -e "USE atm_job; SELECT registry_group,registry_key,registry_value FROM xxl_job_registry;"
```

测试执行器端口：

```bash
ssh root@8.161.227.173
curl http://172.20.71.211:9998/
```

如果返回类似 `invalid request, HttpMethod not support.`，说明执行器端口是通的。

## 本地相关文件

- `atm-job` 项目：`/Users/chenzhuo/project/cd-new/atm-job-chengdu`
- `atm-job pre 配置`：`/Users/chenzhuo/project/cd-new/atm-job-chengdu/xxl-job-admin/src/main/resources/application-pre.properties`
- `atm-cd` 项目：`/Users/chenzhuo/project/cd-new/atm-cd`
- `atm-cd pre 服务 Docker 配置`：`/Users/chenzhuo/project/cd-new/atm-cd/docker/atm-pre-services/docker-compose.yml`
- `atm-cd pre 网关 Docker 配置`：`/Users/chenzhuo/project/cd-new/atm-cd/docker/atm-gateway-pre/docker-compose.yml`
- `atm-ui pre 部署配置`：`/Users/chenzhuo/project/cd-new/atm-cd/docker/atm-ui-pre`
- `Spark/Hadoop pre Docker 配置`：`/Users/chenzhuo/project/cd-new/atm-cd/docker/bigdata-pre`
- 本地服务器密码记录：`/Users/chenzhuo/file/aliyun-servers.md`
