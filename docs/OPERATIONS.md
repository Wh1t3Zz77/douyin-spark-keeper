# 备份、恢复、升级与卸载

## 备份

业务数据位于 Docker 卷 `spark-keeper_spark_data`。备份前应暂停会写入数据库的容器，避免复制到一半的 SQLite 文件：

```bash
docker compose stop api worker mailer
```

使用你信任的 Docker 卷备份工具导出该卷，完成后恢复服务：

```bash
docker compose up -d
```

备份中可能包含网站用户、抖音登录状态、Cookie 和运行记录，必须加密保存。

## 恢复

1. 停止应用。
2. 将备份恢复到同名或新的数据卷。
3. 保持原来的 `APP_SECRET`，否则加密数据可能无法读取。
4. 启动并检查 `/api/health`、登录、账号状态和下一次计划时间。

## 升级

```bash
docker compose pull
docker compose build --pull
docker compose up -d
docker compose ps
```

升级前先备份数据卷并阅读发布说明。不要使用 `docker compose down -v`，其中 `-v` 会删除业务数据卷。

## 卸载

仅停止并删除容器、保留数据：

```bash
docker compose down
```

彻底删除数据属于不可恢复操作，应先确认备份有效，再由操作者明确执行。项目文档不提供自动删除业务卷的一键脚本。

