# Antigravity 用量看板

在 Antigravity 桌面端查看当前会话的 Token、缓存命中率、生成速率和上下文窗口。悬停顶部「用量看板」可查看当前会话和近 7 天合计的美元、人民币 API 参考费用。点击后可在原窗口内按日期查用量、费用、汇率和模型额度。

## 安装

适用于 Windows x64，已在 Antigravity 桌面端 2.32.1 验证。先从 GitHub 的 **Releases** 下载 `antigravity-usage-*-win-x64.zip`，完整解压后双击 **`Install.cmd`**。发布包包含 Python 运行环境，不需要额外安装依赖或使用管理员权限。

以后使用原来的入口打开 Antigravity，插件会由 Antigravity 自动启动。如果安装时 Antigravity 已打开且没有立即出现用量栏，请退出后重开一次。安装成功后可以删除解压出的副本。升级时下载新版 ZIP 并再运行一次 **`Install.cmd`**。卸载时在保留的发布包或重新下载的发布包中运行 **`Uninstall.cmd`**。

插件安装在 `%USERPROFILE%\.gemini\config\plugins\agy-usage`，通过 [官方 Sidecar 机制](https://antigravity.google/docs/sidecars/) 随 Antigravity 启动。桌面界面通过客户端本机调试接口接入，客户端大版本更新后可能需要适配。日志位于 `%LOCALAPPDATA%\AntigravityUsage\composer.log`。

## 用量口径

- 输入框底部的三组指标随留白伸缩。悬停或键盘聚焦看明细，点击可固定，按 Esc 关闭。
- 上下文圆环用最近一次匹配调用的预估 Token 除以当前模型目录返回的窗口。调用记录里的上下文预算单列。切换模型后，在该模型有对应调用前，只显示窗口。5 小时和周额度按客户端实际返回的周期展示。
- 历史统计覆盖本机可读的 Antigravity 会话。累计 Token 含输入、缓存读写和输出；缓存命中率按缓存读取除以输入 Token 计算。
- 费用按 `prices.json` 中的公开 API 价格估算，无法匹配价格的模型只计 Token。悬停卡片的近 7 天按北京时间统计，含今天及之前 6 天的本机用量。启动时从 [Frankfurter](https://frankfurter.dev/) 读取最新 USD/CNY 参考汇率，用于悬停卡片的人民币金额。历史看板按用量发生日期折算。汇率请求只包含币种和必要的日期范围。

## 从源码构建

使用 Python 3.10 或更新版本运行 `python build_release.py`，生成的 ZIP 和 SHA-256 校验文件位于 `dist/`。脚本从 Python.org 和 PyPI 取固定版本运行环境并校验 SHA-256。

上传源码到 GitHub 后，可在 Actions 手动运行 Windows release 工作流取得构建产物，或推送和 `plugin.json` 一致的版本标签（例如 `v1.1.1`）发布到 Releases。

界面整理参考 [unslop](https://heydamir.design/unslop)，作者 Damir Sokolovsky，CC BY 4.0。运行时许可与来源见 [THIRD_PARTY.md](THIRD_PARTY.md)。
