# 发布冷却层纹演示

正式地址为 [alexxpowered.top/layer-time-smoother/](https://alexxpowered.top/layer-time-smoother/)。源仓库的 Publish demo 工作流重建并测试页面，生成名为 `layer-time-smoother-static` 的正式静态文件包。更新主站时，从指定成功构建下载该包，将内容同步到 `Alexx-Powered/alexxpowered-site` 的 `layer-time-smoother/` 目录，提交到主站的 `main` 分支。主站由该分支的根目录发布。源仓库自身的 Pages 首页负责跳转，保留查询参数和片段。

访问统计使用 `deploy/counter/` 中的 Worker 与 D1。部署后端更新时，在此目录执行 `wrangler d1 migrations apply layer-time-smoother-visits --remote`，再执行 `wrangler deploy`。页面每次打开或刷新计一次，重试复用同一个随机 ID。数据库只保存该 ID 和累计次数。
