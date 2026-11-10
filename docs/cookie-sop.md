# Cookie 写入 SOP（由你本人执行）

> Cookie 相当于你的微博登录凭证，等同于账号密码。只在本机使用，不要在对话里明文粘贴，不要截图外发，不要提交到 Git。
> 本工具走 weibo.com PC 端接口，所以 Cookie 必须来自 **weibo.com（PC 网页版）已登录** 的状态。

## 一、导出 Cookie

### 方式 A：浏览器插件（推荐，最简单）

1. Chrome / Edge 安装扩展「Cookie-Editor」（或同类）。
2. 浏览器登录 `https://weibo.com`，确认能看到 @比格钱钱 的主页。
3. 停留在 `https://weibo.com` 页面，点扩展图标 → Export（导出为 Header String / 请求头格式）。
4. 复制整串，形如 `SUB=...; SUBP=...; XSRF-TOKEN=...`。关键是 **SUB 和 SUBP** 两个字段必须都在。

### 方式 B：开发者工具手动复制（Chrome）

1. 登录 `https://weibo.com`。
2. 按 F12 打开 DevTools → 切到 Network 面板。
3. 刷新页面，在请求列表点任意一个 `weibo.com` 的 `ajax` 请求（如 statuses/mymblog）。
4. 右侧 Headers → Request Headers → 找到 `Cookie:` 那一行，从 `SUB=` 开始整行复制到结尾。

> 注意：不要用控制台里 `document.cookie` 的返回值——它不含 HttpOnly 的 `SUB`/`SUBP`，会登录失效。

### 方式 C：开发者工具（Safari）

1. Safari 菜单「开发」→「显示网页检查器」（若没有「开发」菜单，先到 Safari 设置 → 高级 → 勾选「在菜单栏中显示开发菜单」）。
2. 登录并打开 `https://weibo.com`，切到「网络」标签，刷新。
3. 点一个 `weibo.com` 的 `ajax` 请求 → 请求标头 → 找到 `Cookie`，整行复制。

## 二、写入 .env

1. 终端执行（在项目目录内）：

   ```bash
   cd code/weibo-beagle-qianqian
   cp .env.example .env
   ```

2. 用编辑器打开 `.env`，把 Cookie 整串粘到 `WEIBO_COOKIE=` 后面（同一行）。
3. 确认 `WEIBO_UID=3866429224`（钱钱的 UID）。
4. 保存。`.env` 已被 `.gitignore` 忽略，不会进版本库。

## 三、验证

```bash
python fetch_weibo.py --mode probe
```

- 输出「Cookie 有效。首页微博数：N」即成功。
- 输出「登录态失效（ok=0/-100）」：Cookie 过期或没复制全，重新登录 weibo.com 再导出。
- 输出「被风控/拒绝（HTTP 403/418/429/432）」：停止操作，等几小时再试，不要反复重试。

## 四、失效与更换

- 微博改密码、退出登录、账号被风控后 Cookie 会失效，需重新导出。
- 换设备或换 IP 登录可能触发重新验证，届时重新走一遍本 SOP 即可。
