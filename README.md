# Antigravity 用量看板

在 Antigravity 桌面端查看当前会话的 Token、缓存命中率、生成速率和上下文窗口。悬停顶部「用量看板」可查看当前会话和近 7 天合计的美元、人民币 API 参考费用。点击后可在原窗口内按日期查用量、费用、汇率和模型额度。看板按用量分析布局组织：顶部是 2×2 的指标卡（参考费用、请求数、Token 总量、缓存命中率），与下方的用量柱状图共用一张卡片，中间的折叠钮可以把趋势图收起；柱状图可按金额/Token/次数切换（悬停高亮单柱并显示该时段明细），纵轴按数值大小使用万、亿单位、按费用排序且名字—条形—金额三列对齐的模型分布，以及每日、模型、调用、会话、额度五个明细视图（Tab 上标出各自条数）。范围可切到当天、24 小时、7 / 14 / 30 天或全部；24 小时与当天按小时聚合，柱状图横轴直接显示小时，明细表首列随之变成「时刻」。明细视图可切每日、模型、调用、会话、额度，各自标出条数；切换时间范围时视图回到「每日」，不会残留上一个视图的列。

## 安装

适用于 Windows x64，已在 Antigravity 桌面端 2.32.1 验证。先从 GitHub 的 **Releases** 下载 `antigravity-usage-*-win-x64.zip`，完整解压后双击 **`Install.cmd`**。发布包包含 Python 运行环境，不需要额外安装依赖或使用管理员权限。

以后使用原来的入口打开 Antigravity，插件会由 Antigravity 自动启动。如果安装时 Antigravity 已打开且没有立即出现用量栏，请退出后重开一次。安装成功后可以删除解压出的副本。卸载时在保留的发布包或重新下载的发布包中运行 **`Uninstall.cmd`**。

**已安装过的用户**下载新版 ZIP，解压后双击新版里的 **`Install.cmd`** 即可原地升级。安装程序会替换插件文件，原有会话、用量记录和设置会继续使用。

插件安装在 `%USERPROFILE%\.gemini\config\plugins\agy-usage`，通过 [官方 Sidecar 机制](https://antigravity.google/docs/sidecars/) 随 Antigravity 启动。桌面界面通过客户端本机调试接口接入，客户端大版本更新后可能需要适配。日志位于 `%LOCALAPPDATA%\AntigravityUsage\composer.log`。

## 用量口径

- 输入框底部的三组指标随留白保持单行。生成中出现停止按钮或侧栏变窄时，会改用简写。只够放图标时，可以悬停或键盘聚焦查看明细，点击可固定，按 Esc 关闭。
- 上下文圆环用最近一次匹配调用的预估 Token 除以当前模型目录返回的窗口。调用记录里的上下文预算单列。切换模型后，在该模型有对应调用前，只显示窗口。5 小时和周额度按客户端实际返回的周期展示。
- 历史统计覆盖本机可读的 Antigravity 会话。累计 Token 含输入、缓存读写和输出；缓存命中率按缓存读取除以输入 Token 计算。
- 会话顶部的「X 轮 Y 步」同时给出本会话的上下文压缩次数，看板的会话卡片与每日汇总里是同一个数字；压缩次数按会话库里的 checkpoint 步骤统计，不需要解析步骤载荷。
- 费用按 `prices.json` 中的公开 API 价格估算，无法匹配价格的模型只计 Token，并在看板里单列未计价次数与平均单次成本。编排器与执行器的模型别名（如 `gemini-pro-orchestrator-argon`、`gemini-flash-worker-agy`、`gemini-4-hydrogen`）按对应基础模型的公开价格折算。悬停卡片的近 7 天按北京时间统计，含今天及之前 6 天的本机用量。启动时从 [Frankfurter](https://frankfurter.dev/) 读取最新 USD/CNY 参考汇率，用于悬停卡片的人民币金额。历史看板按用量发生日期折算。汇率请求只包含币种和必要的日期范围；接口不可用时回落到最近一次成功获取的汇率或内置离线值，并在看板上标注当前口径。

## 从源码构建

使用 Python 3.10 或更新版本运行 `python build_release.py`，生成的 ZIP 和 SHA-256 校验文件位于 `dist/`。脚本从 Python.org 和 PyPI 取固定版本运行环境并校验 SHA-256。

上传源码到 GitHub 后，可在 Actions 手动运行 Windows release 工作流取得构建产物，或推送和 `plugin.json` 一致的版本标签（例如 `v1.1.4`）发布到 Releases。

界面整理参考 [unslop](https://heydamir.design/unslop)，作者 Damir Sokolovsky，CC BY 4.0。运行时许可与来源见 [THIRD_PARTY.md](THIRD_PARTY.md)。
