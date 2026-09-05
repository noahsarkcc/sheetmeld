# 开发文档

[English](DEVELOPMENT.md) · **中文**

---

## 目录

- [项目架构](#项目架构)
- [技术栈](#技术栈)
- [模块详解](#模块详解)
  - [xml_parser.py / xlsx_parser.py — 工作簿解析器](#xml_parserpy--xlsx_parserpy--工作簿解析器)
  - [xml_differ.py — 语义 Diff 引擎](#xml_differpy--语义-diff-引擎)
  - [xml_merger.py — 三方语义合并引擎](#xml_mergerpy--三方语义合并引擎)
  - [svn_helper.py — SVN 集成](#svn_helperpy--svn-集成)
  - [updater.py — 应用内自动更新](#updaterpy--应用内自动更新)
  - [server.py — Flask REST API](#serverpy--flask-rest-api)
  - [static/ — 前端 SPA](#static--前端-spa)
- [扩展指南](#扩展指南)
- [测试](#测试)
- [后续开发计划](#后续开发计划)

---

## 项目架构

```
sheetmeld/
├── server.py            # Flask 后端，REST API 入口
├── xml_parser.py        # SpreadsheetML 2003 解析器
├── xlsx_parser.py       # XLSX (Office Open XML) 解析器
├── xml_differ.py        # 语义 Diff 引擎
├── xml_merger.py        # 三方语义合并引擎（BASE/MINE/THEIRS）
├── svn_helper.py        # SVN CLI 集成
├── updater.py           # 应用内自动更新（GitHub Releases + 加速代理）
├── tray.py              # 系统托盘集成（pystray + PIL）
├── config.json          # 工作区配置（运行时生成，已 .gitignore）
├── requirements.txt     # Python 依赖
├── start.bat            # Windows 一键启动（托盘模式，pythonw 静默）
├── start_console.bat    # 控制台模式启动（--console，便于排错）
├── build.bat            # PyInstaller 本地打包脚本（与 release.yml 对齐）
├── .github/workflows/
│   ├── test.yml         # CI：3 个 Python 版本跑全量测试
│   └── release.yml      # 推 v* tag 自动构建并上传 SheetMeld.exe
├── static/              # 前端 SPA（index.html + css + js + img）
├── tests/
│   ├── TESTING.md / TESTING.zh-CN.md   # 测试指南
│   ├── test_merger.py                  # xml_merger 单元测试（29 用例）
│   ├── test_differ.py                  # xml_differ 单元测试（11 用例）
│   ├── test_updater.py                 # updater + /api/update/* 测试（23 用例）
│   ├── test_api_merge.py               # HTTP API + mock SVN 端到端（28 用例）
│   ├── setup_demo_svn.bat              # 一键搭建 SVN 演示仓库供手工 UI 测试
│   └── data/                           # 三方测试数据：base.xml / mine.xml / theirs.xml
├── README.md / README.zh-CN.md
└── DEVELOPMENT.md / DEVELOPMENT.zh-CN.md
```

---

## 技术栈

| 层 | 技术 | 说明 |
|---|---|---|
| 后端 | Python 3 + Flask | 轻量 Web 框架，无需数据库 |
| 前端 | Vanilla JS + CSS | 零依赖 SPA，无需构建工具 |
| XML 解析 | `xml.etree.ElementTree` | 标准库 |
| XLSX 解析 | openpyxl | `read_only` + `data_only` 模式 |
| 版本控制 | SVN CLI (subprocess) | 调用 svn 命令行获取版本信息 |
| 打包 | PyInstaller | 通过 `build.bat` 打包独立 exe |

---

## 模块详解

### `xml_parser.py` / `xlsx_parser.py` — 工作簿解析器

两个解析器对外暴露相同的输出结构，下游不需要关心实际格式。

**`xml_parser.py`** — SpreadsheetML 2003：

- 命名空间：`urn:schemas-microsoft-com:office:spreadsheet`
- 处理 `ss:Index`（非连续编号）和 `ss:MergeAcross` / `ss:MergeDown`（跳过合并单元格的占位）
- 仅在单元格有实际值时才推进 `max_col`，末尾空列被丢弃
- 末尾连续空表头自动裁剪

**`xlsx_parser.py`** — Office Open XML：

- 使用 `openpyxl` 的 `read_only=True, data_only=True` 模式（只读值，不加载公式 / 样式）
- 整数浮点数归一化：`1.0` → `"1"`，与 `xml_parser` 字符串输出一致
- 两种输入：`parse_file(path)` 和 `parse_bytes(data)`，后者用于 SVN 历史版本

**共用输出结构**：

```python
{
    "sheets": {
        "Sheet1": {
            "headers": ["ID", "名称", "数值"],
            "rows": [
                {"_row": 1, "cells": {"A": "ID", "B": "名称", "C": "数值"}},
                {"_row": 2, "cells": {"A": "1001", "B": "道具", "C": "100"}},
            ],
            "row_count": 10,
            "col_count": 3,
        }
    },
    "_parse_ms": 12.5,
}
```

### `xml_differ.py` — 语义 Diff 引擎

对比两个解析后的工作簿，产出结构化的变更信息。

1. **自动 ID 列检测**（`_auto_detect_id_column`）：扫描表头中包含 `ID / 编号 / Key / 序号 / 索引` 的列，要求 ≥ 50% 非空且唯一；回退到前 3 列检查唯一性。对 old / new 两个 sheet 检测取一致结果。唯一性判断只看「表头起始行」（`header_row`，随解析结果携带）之后的数据行，obj/type/desc/key 等元信息行不参与。
2. **有效列过滤**（`valid_cols`）：只比较有非空表头的列；没有表头的列视为注释数据。
3. **三轮行匹配**（`_diff_sheet`）：
   - Pass 1：按 ID 列值匹配
   - Pass 2：按内容哈希 + 位置就近优先（解决插入 / 删除导致的行号偏移）
   - Pass 3：按行号匹配（兜底：内容变更 + 位置偏移的行）
4. **空行**（`_is_empty_row`）从 ID 检测和所有匹配 pass 中跳过。

### `xml_merger.py` — 三方语义合并引擎

对 SpreadsheetML 2003 (`.xml`) 工作簿做单元格级 + 行级三方合并（BASE / MINE / THEIRS），输出可交互的合并预览结构，并将用户决议写回 MINE 的 XML 文件。

**核心函数**：

- `three_way_diff(base, mine, theirs, id_column=None)`：返回结构化对比；每行 cells 字典提供 `base / mine / theirs / status / resolved`。
- `apply_resolutions(result, resolutions)`：写入用户选择（`mine / theirs / base / custom` + 行级决议），校验是否还有未解决冲突，返回 `{ok, unresolved, applied}`。
- `write_merged_xml(source_path, result, output_path)`：基于 MINE 原始 XML AST 做增量修改（Cell/Data 文本更新、Row 节点克隆 / 移除），重新输出文件。

**单元格三方语义**：

| BASE | MINE | THEIRS | 结果 |
|---|---|---|---|
| X | X | X | `unchanged` |
| X | X | Y | `auto_theirs` |
| X | Y | X | `auto_mine` |
| X | Y | Y | `auto_both` |
| X | Y | Z | `conflict`（待用户决议） |

**行级语义**（依赖 ID 列匹配）：

- `added_mine` / `added_theirs`：单方新增
- `added_both_same` / `added_both_diff`：双方加同 ID；不同则需要决议（`keep_mine` / `accept_theirs` / `merge`）
- `removed_mine` / `removed_theirs` / `removed_both`：单方或双方删除
- `mine_del_theirs_mod` / `mine_mod_theirs_del`：删除 vs 修改的强冲突

**XML 写回保真**：原文件的 BOM、`<?xml ?>` 声明、`<?mso-application ?>` PI、文档内部的 `<!-- -->` 注释（解析时启用 `insert_comments`）、命名空间风格（默认 ns 或 `ss:` 前缀）都会保留。新增 Row / Cell 显式设置 `ss:Index` 避免破坏隐式编号；修改单元格时只重写 `<Data>` 文本节点，`<Cell>` 的样式属性（如 `ss:StyleID`）和内联注释保持不变。

**写回安全**：

- **原子写入**：先写同目录临时文件，再 `os.replace()` 一次性替换目标文件。中途失败（磁盘满、进程被杀）不会留下半截文件，本地未提交修改不会丢失。
- **行列数声明维护**（`_update_table_extent`）：若 `Table` 带 `ss:ExpandedRowCount` / `ss:ExpandedColumnCount`，写回前按实际内容范围增长（只增不减），避免 Excel 因实际行列数超出声明值而拒绝打开文件。

### `svn_helper.py` — SVN 集成

封装 SVN CLI 调用，处理编码问题。

- 自动检测 `svn` 路径（PATH 中的 svn、TortoiseSVN 路径）
- `_decode_output`：依次尝试 UTF-8、GBK、UTF-8(replace) 解码（仅用于命令行消息；文件内容统一走 raw 字节）
- 所有操作都有超时保护
- **远程 URL 策略**：`get_log` / `get_dir_log` / `get_file_at_revision` / `get_changed_files_between_revisions` 优先使用远程 URL（通过 `get_svn_info`），无需 `svn update` 即可看到最新版本历史
- **URL 解码**：SVN 输出的 URL 对非 ASCII 文件名做百分号编码，`get_log` / `get_changed_files_between_revisions` 统一 `unquote` 后再与本地路径比较，保证中文文件名的冲突检测与历史过滤正确
- **版本过滤**：`--stop-on-copy` 避免显示拷贝前的历史；`get_log` 额外按路径过滤；`get_changed_files_between_revisions` 跳过当前目录之外的文件
- **二进制支持**：`_run_raw` / `get_file_at_revision_raw` / `get_base_content_raw` 返回原始字节流。XML 文件内容也走 raw 路径，由 ElementTree 按 XML 声明自动解码（支持 UTF-16 等编码）
- **`smart_update`**：先校验工作区内路径并固定更新版本；semantic/mine 在更新前保存干净内容，更新后恢复，再标记文本冲突已解决。theirs 先 revert 再 update；skip 从更新目标中排除并保留 BASE。后续目录更新避开这些文件，失败时保留恢复副本并报告错误。
- **`get_remote_update_status`**：使用 `svn status --xml --show-updates` 按文件自己的 BASE 检查更新，工作副本包含多个版本时也能继续发现此前跳过的文件。
- **`get_conflict_info(filepath)`** 解析 `svn info --xml` 暴露的 `<conflict type="text">`，返回 BASE / MINE / THEIRS 三个旁路文件路径（`.r<old>` / `.mine` / `.r<new>`）和对应版本号；用于让 `/api/merge/preview|apply` 在文件已被 SVN 写入 `<<<<<<<` 标记的情况下，绕开被污染的工作副本读到正确的三方内容。非冲突文件返回 `None`，merge 接口自动 fallback 到 `svn cat -r BASE` / 工作副本 / `svn cat HEAD` 的常规路径

### `updater.py` — 应用内自动更新

纯标准库（urllib）实现的检查更新 / 下载 / 自替换模块。

- **代理回退**（`_fetch`）：先直连（超时 8s），失败后用 `PROXY_PREFIX + url`（`github.2436666.xyz`）重试；本次会话记住可用通道（`_use_proxy`），后续请求与下载直接复用
- **`check_update(current)`**：请求 GitHub `releases/latest`，版本号按 int 元组比较（`v1.3.7` → `(1,3,7)`，位数不齐补零）；从 assets 中找 `SheetMeld.exe` 资产，没有则 `asset_url=None`（前端退化为「打开发布页」）
- **下载状态机**：模块级单例 `{status: idle|downloading|ready|error, percent, downloaded, total, error, path}`；`start_download` 启动后台线程流式写入 `SheetMeld.exe.new.part`，完成后 `os.replace` 改名 `.new`（沿用原子写思路）
- **`apply_update()`**（仅 frozen）：在 exe 目录生成自删除的 `sheetmeld_update.bat`（循环 `del` 等待旧 exe 解锁 → `move` 替换 → `start` 新 exe），以 `DETACHED_PROCESS` 启动后延迟 `os._exit(0)` 退出旧进程。处理了两个 Windows 坑：启动脚本前会剔除 PyInstaller 引导器环境变量（`_PYI_*` / `_MEIPASS2`），否则重启的新 exe 会误认为自己是已解压的子阶段而启动即崩；脚本等待用 `ping` 而非 `timeout`（后者在 stdin 重定向时可能立即退出）
- **源码模式**：check 可用；download / apply 返回「请用 git pull」提示，不做任何文件操作
- `config.json` 在 exe 同目录，替换 exe 不影响用户配置

### `server.py` — Flask REST API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/config` | 配置 + SVN 状态 + 工作区列表 |
| GET, POST | `/api/workspaces`, `/workspaces/{switch,add,remove}` | 工作区 CRUD |
| POST, GET | `/api/pick-dir`, `/api/browse-dir?path=` | 目录选择（原生 tkinter，fallback 到自制浏览器） |
| GET | `/api/file-mtime?file=` | 文件最后修改时间（自动刷新用） |
| GET | `/api/files` | 列出工作区文件（XML + XLSX） |
| GET | `/api/svn/modified[-classify]` | 本地修改文件；`classify` 区分 `data`（实质数据变更）vs `meta`（仅格式） |
| GET | `/api/svn/log`, `/api/svn/dir-log`, `/api/svn/changed-files` | 版本历史（文件 / 目录 / 两版本之间） |
| GET | `/api/svn/remote-revision` | 远程 HEAD vs 本地 BASE 版本号对比 |
| POST | `/api/svn/update` | 智能 SVN 更新（`check_only` 检测冲突；body 接受 `skip_files / theirs_files / mine_files / semantic_files`） |
| GET  | `/api/svn/conflicted` | 列出当前处于 SVN 冲突状态的工作区文件（merge 模式默认过滤源） |
| GET | `/api/parse?file=` | 解析文件（自动识别 XML/XLSX） |
| POST | `/api/diff/{local,revisions,overview,batch}` | Diff 接口 |
| POST | `/api/merge/preview` | 三方合并预览（仅 .xml） |
| POST | `/api/merge/apply` | 应用合并决议并写回 |
| POST | `/api/merge/svn-mark-resolved` | 调用 `svn resolve --accept working` |
| GET | `/api/update/check` | 检查新版本（缓存 1 小时，`?force=1` 跳过） |
| POST | `/api/update/download` | 启动后台下载新版 exe |
| GET | `/api/update/progress` | 下载进度 / 状态 |
| POST | `/api/update/apply` | 自替换并重启（仅 frozen，源码模式 400） |

所有接收 `file` 参数的端点都经过 `_safe_workspace_path` 校验：路径 join 后取 `realpath`，必须落在当前工作区目录内，`..` 与绝对路径穿越一律返回 400。

### `static/` — 前端 SPA

**state 管理**：全局 `state` 对象，包含当前模式、文件列表、diff 结果等。所有 UI 更新通过 `render*` 函数从 state 重新渲染。

**渲染优化**：分批渲染（`BATCH_SIZE = 150` 行），用 `requestAnimationFrame` 追加后续行；每个 tbody 有唯一 ID，支持多表格同时展开。

**Inline Diff**（`inlineDiff`）：基于 LCS 算法的字符级 diff，在修改单元格内高亮变更字符。短字符串（≤ 200 字符）逐字符对比，长字符串先按分隔符分词。输出 `<del>` / `<ins>` 标签，样式参照 GitHub Dark 配色。

**自动刷新与远程版本检测**：`/api/file-mtime` 每 3 秒轮询一次，文件变更时自动重新加载 diff（本地变更 / 浏览模式）；`/api/svn/remote-revision` 每 30 秒轮询一次，远程 HEAD 领先于本地 BASE 时顶部显示黄色横幅，点击"更新"触发智能更新流程（冲突检测 + 分类处理）。切换工作区会重置所有轮询状态。

**语义合并模式**：第四个模式 tab；侧边栏自动过滤为仅 `.xml`。选中文件后调 `/api/merge/preview`，按 Sheet → Row → Cell 三层渲染：

- 顶部 sticky：sheet tabs + 版本来源对比条（BASE ← 本地 · 远程）
- 每行是一张卡片，包含 header（行 ID / 状态徽章 / 行级决议按钮 / 折叠开关）、始终显示的行数据表（根据 `row_decision` 自动选择显示来源 mine / theirs / base / 已合并；删除决议带 strike-through）、可折叠的"变更详情"面板（modified 行展示每个变更单元格的 BASE / 本地 / 远程 + 决议按钮组；`added_both_diff` 展示双方对比）
- 单元格 badge：`本`（蓝）、`远`（紫）、`=`（绿）、`✎`（黄）、`!`（红，未决议）
- 工具栏：自动 / 冲突 / 待解决统计 + 进度条 + "只看待解决"过滤 + "全部展开 / 折叠 / 智能"三态切换；智能模式（默认）展开需要手动决议的行，折叠自动决议的行
- **应用合并** 调 `/api/merge/apply`，前端通过 `collectResolutions()` 同时下发单元格选择和行级选择
- SVN 冲突弹窗的 `.conflict-item` 上对 `.xml` 文件额外渲染**语义合并**按钮：点击只把该文件标记为 `choice='semantic'`，不再立刻跳走，与 `mine / theirs / skip` 并列。点击"确认更新"后，前端 `state.updateContext` 把所有 `semantic` 文件组成队列，逐个 `setMode('merge')` + `selectFile()` 引导用户决议；每个文件 `applyMerge` 完成后写回工作副本并由后端自动 `svn resolve --accept working`；整个队列结束后统一发一次 `/api/svn/update`，让其他冲突按 mine/theirs/skip 处理、semantic 文件作为 `semantic_files` 走 `--accept working`
- merge 模式的侧边栏默认只列出当前处于 SVN 冲突状态的 `.xml`（由 `/api/svn/conflicted` 提供），顶部有"只看冲突 / 全部 XML"切换；conflicted 状态显示红色色点

### 运行模式：系统托盘 vs 控制台

- **托盘模式（默认）**：`start.bat` 用 `pythonw` 启动 → 无控制台窗口 → 主线程跑 pystray 图标（菜单：打开浏览器 / 显示日志 / 打开工作目录 / 退出），Flask 在后台线程运行；标准输出/错误重定向到 `logs/server.log`（`RotatingFileHandler`，3 × 1 MB）
- **控制台模式**：`start_console.bat` 或 `python server.py --console` → 保留原始行为，控制台实时显示日志，托盘不启动；用于开发/排错
- 打包：`SheetMeld.spec` 与 `build.bat` / `.github/workflows/release.yml` 均使用 `--noconsole` + `--hidden-import pystray._win32 / PIL.Image / PIL.ImageDraw`。当 pystray/PIL 不可用时（如未安装依赖），server 自动降级回控制台模式

**国际化（i18n）**：`static/js/i18n.js` 提供轻量 i18n 框架。`I18N.messages` 存放完整的 `zh` / `en` 双语词典。全局函数 `t(key, ...args)` 根据当前语言查表并执行 `{0}`、`{1}` 占位符替换。页面加载时 `I18N.init()` 从 `localStorage`（`sheetmeld_lang`）或 `navigator.language` 检测语言。点击顶栏切换按钮调用 `I18N.setLocale()`，保存偏好后对静态 DOM 应用 `data-i18n` / `data-i18n-title` / `data-i18n-placeholder` 属性，并调用 `reRenderAll()` 重新渲染所有动态 UI。原来的静态常量对象（`ROW_STATUS_LABELS`、`CELL_STATUS_LABELS`）已改为 getter 函数（`getRowStatusLabel`、`getCellStatusLabel`），确保标签在渲染时才求值。

**其它细节**：

- 修改文件圆点指示器（橙 = 实质数据变更、灰 = 仅元数据、绿 = 新增、红 = 删除）通过 `/api/svn/modified-classify` 周期性分类
- 宽表格两侧浮动滚动提示 `← 左侧有 N 处变更` / `右侧有 N 处变更 →`，点击滚动到最近变更列
- 总览面板高度根据视口动态适配（200~600 px clamp）确保水平滚动条始终可见；展开 / 折叠用局部 DOM 操作保留滚动位置
- 版本对比默认选最近两个 SVN 版本；0 / 1 个版本的文件有降级 UI 提示
- 目录选择优先 `tkinter.filedialog.askdirectory()`，PyInstaller 打包环境下 fallback 到自制弹窗（支持路径输入 / 面包屑 / 单击返回上级）

---

## 扩展指南

### 添加新的合规规则

在 `xml_differ.py` 中扩展 `diff_workbooks`，或新增独立的 `validate_workbook` 函数：

```python
def validate_workbook(parsed: dict, rules: list) -> list:
    """对解析后的工作簿执行合规检查。
    rules: [{"type": "range_check", "sheet": "...", "col": "C", "min": 0, "max": 100}, ...]
    returns: [{"sheet": "...", "row": N, "col": "C", "message": "..."}]
    """
```

然后在 `server.py` 中添加 `/api/validate` 端点。

### 添加新的数据源

如果需要支持其他表格格式（参考 `xlsx_parser.py` 的实现）：

1. 在 `xml_parser.py` 同级创建新的解析器
2. 保持相同的输出格式（`sheets` > `headers` + `rows`）
3. 在 `server.py` 的 `_parse_file` / `_parse_content` 调度函数中按扩展名分发
4. 二进制格式使用 `svn_helper` 的 `_raw` 系列函数获取原始字节

### PyInstaller 打包与发版

本地打包直接运行 `build.bat`，等价于：

```bash
pip install -r requirements.txt pyinstaller
pyinstaller --onefile --noconsole --add-data "static;static" --hidden-import pystray._win32 --hidden-import PIL.Image --hidden-import PIL.ImageDraw --name SheetMeld server.py
```

生成的 `dist/SheetMeld.exe` 可独立运行，无需 Python 环境。`config.json` 会在首次启动时自动生成，无需打包进去。

**发版流程**：先在 `CHANGELOG.zh-CN.md` / `CHANGELOG.md` 写好该版本的 `## vX.Y.Z` 小节，然后推送 `v*` tag（如 `git tag v1.4.0 && git push origin v1.4.0`）。`.github/workflows/release.yml` 会在 Windows runner 上跑全量测试 → PyInstaller 构建 → 用 `.github/release_notes.py` 生成 release 正文 → 把 `SheetMeld.exe` 上传到该 tag 的 GitHub Release。客户端的应用内更新（`updater.py`）即从该资产下载、release 正文即为更新说明。

**Release 正文约定**（`.github/release_notes.py`）：

- 只取英文 `CHANGELOG.md` 的对应小节；中文版仅保留在仓库内
- 发布页正文是精简 highlight：版本一句话简介 + 最多 5 条非技术 bullet（每个 changelog 小节最多 2 条）
- 标题命中黑名单（Tests / API / infrastructure / internal / CI）的粗体子节不上发布页
- 跳版本发布时（中间版本没单独发过 release），中间版本各以一行 `Also includes vX.Y.Z: <小节首行简介>` 带过，因此每个版本小节标题下的第一行应是一句话总结
- 正文末尾固定为 `See CHANGELOG for details.`；技术细节留在 `CHANGELOG.md`

---

## 测试

```powershell
# 1) 合并引擎单元测试（无需 SVN）
python tests\test_merger.py     # 29 用例

# 2) Diff 引擎单元测试（无需 SVN）
python tests\test_differ.py     # 11 用例

# 3) 更新模块 + /api/update/* 测试（mock 网络）
python tests\test_updater.py    # 23 用例

# 4) HTTP API 端到端（mock SVN）
python tests\test_api_merge.py  # 28 用例

# 5) 手工 UI 测试（需要 svn CLI）
tests\setup_demo_svn.bat        # 在 %TEMP%\xmldev_demo_svn\ 搭一个三方对齐的演示仓库
start.bat                       # 然后在头部添加 wc 目录作为工作区
```

详见 [tests/TESTING.zh-CN.md](tests/TESTING.zh-CN.md)：每条测试数据预期结果、UI 操作步骤、SVN 冲突集成验证、常见问题。

---

## 后续开发计划

| 功能 | 说明 | 涉及模块 |
|---|---|---|
| 合规引擎 | 可配置的规则系统：数值范围检查、必填字段验证、跨表引用验证（如表 A 的某列值必须存在于表 B） | 新增 `validator.py`、`/api/validate` |
| 文件收藏夹 | 常用文件快速访问，每个工作区独立配置 | `config.json` 扩展 |

完整的版本变更历史见 [CHANGELOG.zh-CN.md](CHANGELOG.zh-CN.md)。
