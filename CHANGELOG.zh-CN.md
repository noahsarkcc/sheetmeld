# 更新日志

[English](CHANGELOG.md) · **中文**

SheetMeld 的所有重要变更都记录在这里，格式大致遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/) 风格。

## 未发布

更可靠的表格合并，更清楚的冲突处理。

**数据安全**
- SVN 更新后保留用户选择的合并结果，更新失败时提供恢复副本。
- 保存时正确处理 UTF-16 文件和富文本内容，并在覆盖原文件前拦截无效输出。

**合并行为**
- “保留我的”“用最新”“跳过”按选择执行，支持子目录文件；跳过的文件仍会提示有待更新内容。
- 正确合并新启用的数据列；遇到暂不支持的工作表结构变化时，在保存前明确提示并停止。

**显示与语言**
- 删除行后仍显示其他行的修改，安全展示表名和表头，工作表拦截提示支持中英文。
- 切换界面语言时，已经显示的工作表拦截提示会即时更新。

**内部 / API**
- 对不支持的工作表变化返回结构化错误码和受影响的 Sheet 名，由前端选择对应语言。
- 按文件自己的 BASE 检查远端变化，并在原子替换前校验 XML。

**测试**
- 新增 XML/API、真实本地 SVN 仓库和前端回归测试并接入 CI；完整套件共 122 个用例。

## v1.5.1（2026-08-25）

项目从 SmartDiff 重命名为 SheetMeld，降低同名竞争、提高搜索辨识度。

**变更**
- 所有用户可见引用（窗口标题、UI 文字、托盘提示、exe 名称）已改为 "SheetMeld"
- GitHub 仓库地址更新为 `noahsarkcc/sheetmeld`
- 构建产出重命名：`SheetMeld.exe`
- 自动更新模块的资源名和 API 目标同步更新

**未变更**
- 内部 localStorage key（`smartdiff_locale`、`smartdiff_diff_view`）保持不变，兼容已有用户偏好

## v1.5.0（2026-06-17）

SVN 冲突语义合并流程重做 + 系统托盘运行。

**SVN 冲突语义合并流程重做**
- 真三方对比：检测到 SVN 文本冲突时，改用 `svn info --xml` 暴露的 `.r<old>` / `.mine` / `.r<new>` 旁路文件作为 BASE / MINE / THEIRS。修复此前工作副本已被 `<<<<<<<` 标记污染、`svn cat -r BASE` 返回更新后的 HEAD 而非真正祖先版本、导致三方对比完全错位的问题
- 统一更新队列：语义合并成为冲突文件的第 4 种选择（与 keep mine / take theirs / skip 并列），点「确认更新」后交由更新流程托管成语义合并队列逐个引导，最后统一执行一次 `svn update`，已合并文件以 `--accept working` 解析。修复此前点语义合并就跳走、`svn update` 从未真正执行、落后远端的纯远程文件停在旧版本的问题
- 聚焦待办：merge 模式只列出当前处于 SVN 冲突的 `.xml`（红色状态点高亮，`get_modified_files` 返回 conflicted 状态）；「只看待解决」默认开启，并永久屏蔽 4 类纯本地噪音行（`added_mine` / `added_both_same` / `removed_mine` / `removed_both`）
- auto 决议可逐个 override：`auto_mine` / `auto_theirs` / `auto_both` 三种自动决议状态的 cell 也显示「保留我的 / 用远端 / 自定义」按钮（高亮当前默认侧），主要防止本地有未提交脏改时 `auto_mine` 把远端正确值默默丢掉；只有被改动过默认值的 cell 才会回传
- 「无语义差异」引导：仅格式 / 空白 / 属性顺序差异（0 冲突 0 自动合并）的文件不再用带省略号的「正在合并…」让人误以为还在跑，而是给出行动式 banner + 绿色引导卡，一键完成 `svn resolve` 并推进队列
- 更新流程各关键节点与 applyMerge 完成后主动重查远端版本，避免顶部 banner / 更新按钮卡在「检查中」

**数据安全（高危修复）**
- 修复语义合并队列结束后整目录 `svn update` 把 `<<<<<<<` / `=======` / `>>>>>>>` 标记冻进工作副本的严重数据损坏：SVN 会对这些文件重做三方 merge 再生成标记，而 `--accept working` 按设计不会清理标记，导致带标记的损坏 XML 被认定为「已解决」。新流程在整目录 update **之前**，对每个 semantic_file 单独 `svn resolve --accept working` + `svn update --accept working <file>` 把 BASE 推到 HEAD，整目录 update 不再 touch 它们
- SVN 外部状态漂移防御：`/api/merge/preview` 返回 `merge_signature`（`is_conflict` + `theirs_revision` + 文件大小 / 纳秒 mtime 指纹），`applyMerge` 时回传，后端发现指纹不一致即返回 HTTP 409 + `stale: true`，前端提示后自动重跑 preview；merge 模式额外 30s 自动轮询冲突列表。覆盖命令行 `svn resolve` / `svn update` / 外部编辑器保存等场景
- 语义合并收尾现在会把每个文件的精确目标版本传给 `/api/svn/update`，用 `svn update -r <theirs_revision> --accept working <file>` 推进单文件 BASE；如果单文件 promote 失败，会在整目录 update 前停止并返回错误；相对 `.mine` / `.rN` 旁路路径也会按冲突文件所在目录解析
- 队列中一旦有文件已经写回，前端不再允许在最终 promote/update 前取消队列，避免已合并文件停留在 SVN 仍可能重新注入冲突标记的状态
- 工作副本含 `<<<<<<<` / `=======` / `>>>>>>>` 标记、或 `.mine` 旁路被外部清掉时，给出清晰中文错误，而非 ElementTree 的 `not well-formed` ParseError
- applyMerge 重入保护（`mergeApplyInFlight` + 按钮 disabled + 成功后清空 `mergeData` / `activeSheet`），避免队列前进 / svn update 期间重复 apply

**系统托盘运行**
- 默认通过 `pythonw` / `--noconsole` 隐藏控制台，主进程跑 pystray 托盘图标，右键菜单：打开浏览器 / 显示日志 / 打开工作目录 / 退出
- 托盘图标改用初音像素形象（脸部裁剪），与网页内 UI 一致，复用 `static/img/miku.svg`、无新依赖；解析失败回退内置占位图
- 服务器输出重定向到 `logs/server.log`（轮转 3 × 1 MB）；需要实时控制台用新增的 `start_console.bat`（等价 `python server.py --console`）
- 托盘「显示日志」改用浏览器打开内置 `/log` 查看器（最新在顶、5 秒自动刷新、深色等宽顶栏带路径 / 行数 / 字节 / 修改时间），`webbrowser.open` 失败时回退记事本 / xdg-open
- svn / netstat / taskkill 子进程加 `subprocess.CREATE_NO_WINDOW`（仅 Windows）消除闪黑窗
- 修复托盘模式下 Flask 启动横幅崩溃：`_StreamToLogger.write` 兼容 bytes / bytearray 并暴露 `encoding` / `isatty` / `fileno` / `writelines`，避免 `click.echo` 走 bytes 路径打挂后台 Flask 线程（端口起不来）

**可用性 & 可观测性**
- 切换工作区新增全屏遮罩进度条，分「提交切换请求 → 加载文件列表 → 检查 SVN 状态」三步；三个 SVN load 用 `Promise.allSettled` 等待全部完成，单个失败也不卡住
- 侧栏底部新增可折叠的状态点颜色图例（红 / 绿 / 红 / 橙 / 灰 = SVN 冲突 / 新增 / 删除 / 数据变更 / 仅元数据变更）
- apply 弹窗改用 `total_changes`（真正写入文件的行级 ops 数）展示，文案从「决议」改为「变更」，不再因只统计手动 resolutions 而误显示「0 项」
- 关键 API 补 INFO/WARNING 业务日志便于从 `/log` 回溯：`/api/workspaces/switch|add|remove`、`/api/merge/preview|apply|svn-mark-resolved`、`/api/svn/update`（入口 skip/theirs/mine/semantic 计数 + 出口 updated/errors 计数）
- 日志不再被 `[SVN] Using: svn` 刷屏：`is_available()` 改用 `_svn_announced` 标志只在首次探测成功时打印一次

**内部 / API**
- 新增 `svn_helper.get_conflict_info()` 解析 `svn info --xml` 的 `<conflict>` 旁路文件信息
- 新增 `/api/svn/conflicted` 接口；`/api/svn/update` 接受 `semantic_files` 参数
- 前端新增 `state.updateContext` 队列与 `_processNextSemantic` / `_finishSemanticQueue` / `cancelSemanticQueue`
- `static/js/app.js` 的 `api()` helper 在错误路径上把 HTTP status / JSON body 挂到 Error（`err.status` / `err.body`），兼容现有 `alert(e.message)` 的同时让 409 stale 等可恢复错误可被特殊处理

**测试**
- `test_api_merge.py` 扩充到 28 个用例，新增数据损坏路径回归（`test_smart_update_semantic_files_promoted_first`、`test_apply_rejects_poisoned_working_copy`）、semantic promote 失败 / 相对 sidecar 路径回归，以及 4 项 SVN 外部漂移回归（冲突→已解决、已解决→冲突、mtime 变化、`.mine` 旁路消失）

## v1.4.2（2026-06-12）

自动更新重启修复。

**问题修复**
- 应用内更新完成后程序现在会自动重启；此前新版 exe 已替换成功，但程序不会重新拉起，需要手动再次打开
- GitHub release 标题不再重复版本号；CI 发布现在自动生成"vX.Y.Z - 一句话摘要"形式的标题

**内部改进**
- 根因：更新脚本继承了垂死旧进程的 PyInstaller 引导器环境变量（`_PYI_*` / `_MEIPASS2`），重启的新 exe 误认为自己是已解压的子阶段，指向已删除的 `_MEIxxxx` 临时目录导致启动即崩；`apply_update` 现在会剔除这些变量
- 更新脚本等待改用 `ping` 实现（`timeout` 在 stdin 重定向下可能立即退出），放弃分支重启也补上了工作目录参数
- 新增 3 个更新模块测试：引导器环境变量剔除、脚本模板守卫、锁文件换包集成测试（Windows）

## v1.4.1（2026-06-11）

Diff 表格显示修复。

**问题修复**
- 分行视图：修改单元格的旧值（红）/ 新值（绿）背景现在始终铺满整格高度，相邻单元格的红绿分界线不再因内容长短不一而错位露底
- Diff 表格长文本默认完整换行显示，不再需要把鼠标悬停到单元格上才展开

## v1.4.0（2026-06-10）

应用内自动更新。

**新功能**
- 应用内检查更新：启动 5 秒后静默检查 GitHub Releases，发现新版本时设置按钮（齿轮）显示红点提示
- 设置弹窗新增「版本与更新」区：显示当前版本、手动检查更新、查看新版本说明
- 一键更新（exe 模式）：后台下载新版 `SheetMeld.exe` → 显示进度条 → 自动替换并重启 → 页面自动刷新到新版本；`config.json` 等用户配置不受影响
- GitHub 直连失败时自动走加速代理（`github.2436666.xyz`）重试，检查与下载均支持；会话内记住可用通道
- 源码模式运行时提示使用 `git pull` 更新，并提供发布页直连/加速双链接

**发版基建**
- 新增 `.github/workflows/release.yml`：推送 `v*` tag 自动在 Windows 上跑全量测试、PyInstaller 构建并把 `SheetMeld.exe` 上传到对应 release（此前 release 无 exe 资产，一键更新自此版本起闭环）
- 修正 `build.bat`：依赖改为安装 `requirements.txt`（补齐 openpyxl/xlrd），构建参数与 CI 对齐

**API**
- 新增 `GET /api/update/check`（结果缓存 1 小时，`?force=1` 跳过）、`POST /api/update/download`、`GET /api/update/progress`、`POST /api/update/apply`

**测试**
- 新增 `tests/test_updater.py`（20 个用例）：版本号比较、代理回退、`check_update` 解析、下载状态机、四个端点行为；已接入 CI，全量 76 个用例通过

## v1.3.7（2026-06-10）

可靠性专项修复（来自全量代码评审，共 12 项）。

**数据安全（高危）**
- 语义合并写回改为原子操作：先写同目录临时文件再 `os.replace()` 替换，写入中途失败不再损坏工作副本、丢失未提交的本地修改
- 合并新增行后自动维护 `ss:ExpandedRowCount` / `ss:ExpandedColumnCount`，避免 Excel 因行列数超出声明值而拒绝打开文件
- 远程冲突检测与单文件版本历史对 SVN 百分号编码的 URL 统一解码，中文/非 ASCII 文件名不再漏判冲突或丢失历史记录

**健壮性（中危）**
- 所有接收文件名的 API 增加工作区路径前缀校验，拒绝 `..` 与绝对路径穿越
- 启动时端口清理改为精确匹配本地地址端口列，不再误杀 55660-55669 等相邻端口的进程
- SVN 智能更新改用 `svn status --xml` 判断冲突状态，不再依赖英文输出嗅探（中文 locale 下也正确）；更新文件计数排除 `Updating` / `At revision` 等非文件行
- 合并写回保留文档内部的 `<!-- -->` XML 注释（此前被 ElementTree 静默丢弃）

**其他改进（低危）**
- SVN 文件内容统一走原始字节读取，由 XML 声明自动解码，支持 UTF-16 等非 UTF-8 编码的表格文件
- ID 列自动检测尊重「表头起始行」设置，元信息行（obj/type/desc/key）不再干扰唯一性判断
- 配置读写加锁防并发竞争；`config.json` 保存失败时在 API 响应中返回 `warning` 而非静默忽略
- 打开工作区目录改用 `os.startfile`，移除模拟 Alt 按键的前台聚焦 hack

**测试**
- 新增 `tests/test_differ.py`（11 个用例）：插入/删除不级联、Pass 2/3 匹配、重复 ID、注释列过滤、header_row>1 的 ID 检测、UTF-16 解析；已接入 CI
- `test_merger.py` 新增 ExpandedRowCount 增长与 XML 注释保留回归用例（29 个）
- `test_api_merge.py` 新增路径穿越拒绝用例（16 个），全量 56 个用例通过

## v1.3.6（2026-06-09）

**单元格 Diff 可读性**
- 智能 token 级 diff：连续数字、英文字母作为整块对比，`738` -> `7074` 这样的值变化会整块高亮，不再被打散成零碎的红绿字符。对 `{5:738626,40,6:4235,400,...}` 这类紧凑结构化数据可读性大幅提升
- 新增「分行」视图：旧值、新值上下两行分开展示（旧值在上、新值在下），各自完整并高亮变化部分，一眼看清「原值 -> 新值」
- 工具栏新增「内联 / 分行」视图切换（本地变更、版本对比、版本总览三种模式均可用），偏好通过 localStorage 记忆
- 长值改为在 token 边界换行，不再从数字中间断开

## v1.3.5（2026-06-04）

**新功能**
- 可配置表头起始行：新增全局「表头起始行」设置，用户可指定表头所在行号（默认 1）。对于前几行是 obj/type/desc/key 等元信息的特殊表格，不再整列忽略，所有列都能被正确识别和对比
- 标题栏新增设置按钮（齿轮图标），可打开设置弹窗
- 设置持久化到 `config.json`，保存后立即在所有模式（本地变更、版本对比、浏览、版本总览、语义合并）中生效

## v1.3.4（2026-06-04）

**问题修复**
- 版本总览：中文（非 ASCII）文件名不再报「Cannot get revision content」。SVN 在远程 URL 中对这类文件名做百分号编码，变更列表现在会解码，使文件名正确显示并能取到版本内容
- 版本总览改为通过仓库 URL 获取版本内容，本地未 checkout 的文件也能对比
- `get_file_at_revision` / `get_file_at_revision_raw` 支持直接传入仓库 URL

## v1.3.3（2026-06-04）

**国际化（i18n）**
- Web UI 新增中英文双语切换，Header 一键切换
- 首次访问自动检测浏览器语言（zh-* → 中文，其他 → 英文）
- 用户选择通过 localStorage (`sheetmeld_locale`) 持久化
- 覆盖范围：标题栏、侧栏、四种 Diff 模式、语义合并 UI、SVN 冲突弹窗、所有 alert 和错误提示

**文档**
- CHANGELOG 拆分为 `CHANGELOG.md`（英文）和 `CHANGELOG.zh-CN.md`（中文），与 README/DEVELOPMENT 双语风格一致

## v1.3.2（2026-05-27）

**文件列表**
- 修复子目录 `.xls` 在 SVN 变更列表 / 仅变更过滤中无法按相对路径匹配的问题
- 补充 `/api/files` 递归列出 `configs/items.xls` 与 `/api/svn/modified` 相对路径返回的回归测试

## v1.3.1（2026-05-15）

**SVN 冲突检测**
- `check_only` 改为按工作区相对路径匹配本地修改和远端变更，修复子目录文件被 `basename` 匹配漏判的问题
- 补充 API 回归测试，覆盖 `configs/items.xml` 这类子目录冲突场景

## v1.3.0（2026-05-15）

**单元格级三方语义合并**
- 新增 `xml_merger.py`，支持 `BASE / MINE / THEIRS` 三方 Diff、自动决议、手动决议和写回
- 新增 `/api/merge/preview`、`/api/merge/apply`、`/api/merge/svn-mark-resolved`
- SVN 冲突弹窗集成"语义合并"入口，XML 冲突可直接按单元格解决
- 新增自动化测试套件与演示 SVN 仓库脚本

<details>
<summary><b>历史版本（v1.0 – v1.2）</b></summary>

## v1.2.0（2026-05-14）

**Excel 二进制文件支持**
- 新增 `.xlsx` / `.xls` 解析，使用 `openpyxl` / `xlrd` 提取纯单元格数据
- SVN 历史版本读取支持二进制 raw bytes，版本 Diff 可用于 Excel 文件

## v1.1.0（2026-05-13）

**Diff 引擎**
- 重构三轮行匹配算法：Pass 1 按 ID 值匹配 → Pass 2 按内容哈希 + 位置就近匹配 → Pass 3 按行号兜底。插入 / 删除少量行时不再产生大量假修改
- SVN 日志过滤改进：正确包含通过目录拷贝（分支 / 标签）创建的文件版本

**版本总览（Overview）**
- 修复水平滚动条不可见与缩放后双 Y 轴滚动条问题（动态计算 diff 容器高度 + 外层容器显式禁用水平溢出）
- 内滚动条使用高对比度样式；动态插入 HTML 后强制重排确保立即渲染

**版本对比**
- 版本下拉不再包含"工作副本"，默认选中最近两个 SVN 版本
- 单版本文件显示"仅有 1 个版本，无法进行版本间对比"
- Scroll hints（浮动变更提示）支持 overview 模式，使用 `position: fixed` 精确定位

**其他**
- 打开工作区目录时 Explorer 窗口自动前台聚焦（Windows）
- 修正 `start.bat` 端口号显示（5000 → 5566）
- 新增 `__version__` 版本号管理，`/api/config` 接口返回版本信息

## v1.0.0

首个发布版本。包含本地变更、版本对比、浏览、版本总览四种模式，SVN 智能更新，inline diff，PyInstaller 打包等功能。

</details>
