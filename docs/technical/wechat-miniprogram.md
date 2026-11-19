# 微信小程序迁移与部署

## 当前架构

```text
微信原生小程序
  -> wx.cloud.callContainer
  -> 微信云托管 FastAPI
  -> 微信云托管 MySQL

本地抓取 / 审核 / 运营
  -> SQLite
  -> mysql_import.py
```

正式小程序不通过公网域名访问 FastAPI。云托管服务应关闭公网访问。

## 只读接口

- `GET /api/v1/bootstrap`：入口、排序和收录时间范围。
- `GET /api/v1/posts`：微博列表，支持 `feed`、`meme`、分页和排序。
- `GET /api/v1/search`：搜索与相关度排序。
- `GET /api/v1/posts/{id}`：详情、分类、来源微博和媒体元数据。
- `GET /api/v1/posts/{id}/video`：按需解析游客视频地址。
- `GET /api/v1/media/image?u=`：小体积图片回退，单次最多 900 KiB。
- `GET /api/v1/memes`：分类条目。
- `GET /api/v1/healthz`：当前数据库后端和微博数量。

接口响应是 `{"ok": true, "data": ...}`。错误由 FastAPI 返回 `detail`。

## 本地测试

```bash
python -m unittest discover -s tests -v
node --test miniprogram/tests/*.test.js
uvicorn app.main:app --host 127.0.0.1 --port 8000
python scripts/smoke_miniapp_api.py http://127.0.0.1:8000
```

`scripts/smoke-test.sh` 已同时覆盖 Web 页面和小程序 API。

## SQLite 到 MySQL

先做只读检查：

```bash
python mysql_import.py --sqlite data/weibo.db --snapshot-label data-YYYYMMDDTHHMMSSZ
```

确认数量和字段后执行导入：

```bash
python mysql_import.py \
  --sqlite data/weibo.db \
  --snapshot-label data-YYYYMMDDTHHMMSSZ \
  --apply
```

连接参数使用 `deploy/cloudrun.env.example` 中的变量。默认只检查，不传 `--apply` 不会写 MySQL。导入会替换目标库中的小程序镜像表，并逐表核对导入前后数量。

## 云托管配置

1. 用微信小程序账号创建云托管环境。
2. 开通 Serverless MySQL，创建只读账号供 FastAPI 查询。
3. 用独立写入账号运行 `mysql_import.py --apply`，不要把写入账号放进云托管。
4. 使用现有 `Dockerfile` 部署 FastAPI，监听 8000 端口。
5. 设置 `MINIAPP_DB_BACKEND=mysql` 和其余 `MINIAPP_DB_*` 环境变量。
6. 关闭云托管公网访问。
7. 在小程序后台把基础库最低版本设为 2.23.0。
8. 把 AppID 写入 `miniprogram/project.config.json`。
9. 把环境 ID 和服务名称写入 `miniprogram/config.js`。

## 小程序本地入口

微信开发者工具直接导入 `miniprogram/`。当前 `project.config.json` 使用 `touristappid`，只能用于项目文件检查；真机、体验版和正式版必须换成实际 AppID。

## 已知联调卡点

- 需要实际小程序 AppID。
- 需要云托管环境 ID 和服务名称。
- 需要确认主体可开通云托管和 MySQL。
- 微博图床直链、视频直链是否能在真机加载，必须在体验版验证。
