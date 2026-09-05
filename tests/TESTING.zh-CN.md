# 单元格语义合并 — 测试指南

[English](TESTING.md) · **中文**

本指南面向 v1.3 新增的"单元格级三方语义合并"功能（仅 `.xml` SpreadsheetML 2003）。

---

## 目录

- [一、快速验证](#一快速验证推荐-5-分钟跑完)
- [二、覆盖范围速查](#二覆盖范围速查)
- [三、手工 UI 测试](#三手工-ui-测试完整体验流程)
- [四、回归测试覆盖矩阵](#四回归测试覆盖矩阵)
- [五、常见问题](#五常见问题)
- [六、给我反馈](#六给我反馈)

---

## 一、快速验证（推荐 5 分钟跑完）

### 1. 运行自动化测试

在项目根目录（`C:\xmldev`）：

```powershell
# 让 PowerShell 正确显示中文
chcp 65001

# 1) 合并引擎单元测试（不需 SVN，秒级完成）
python tests\test_merger.py

# 2) HTTP API 端到端测试（mock SVN，无需真实仓库）
python tests\test_api_merge.py
python tests\test_differ.py
python tests\test_updater.py
python tests\test_regressions.py
python tests\test_svn_update.py
node --test tests/test_frontend.js
```

新增回归覆盖 XML 编码/结构、真实 SVN 更新行为及前端转义/行渲染。SVN 测试只创建临时本地 file:// 仓库，需要 PATH 中有 `svn` 和 `svnadmin`，缺失时明确跳过；前端测试需要 Node.js 22+。完整本地套件当前共 119 个测试。

预期输出末尾：

```
== 全部通过：29/29 ==     （test_merger.py）
== 全部通过：28/28 ==     （test_api_merge.py）
```

任意一项失败请把整段输出发给我。

---

## 二、覆盖范围速查

`tests/data/` 下的 `base.xml` / `mine.xml` / `theirs.xml` 故意构造了所有可能的合并场景，每一行都对应一个明确的预期状态：

| ID | BASE | MINE（本地） | THEIRS（远程） | 期望状态 | 期望结果（默认决议） |
|---|---|---|---|---|---|
| **1001** | 原始道具 / 10 | 原始道具 / 10 | 原始道具 / 10 | `unchanged`（隐藏） | 行原样保留 |
| **1002** | 仅本地改名称 / 20 | **本地新名称** / 20 | 仅本地改名称 / 20 | `modified` + B = `auto_mine` | 取本地名称 |
| **1003** | 仅远程改数值 / 30 | 仅远程改数值 / 30 | 仅远程改数值 / **33** | `modified` + C = `auto_theirs` | 数值改成 33 |
| **1004** | 双方同改 / 40 | **双方同改后** / 40 | **双方同改后** / 40 | `modified` + B = `auto_both` | 名称取共识 |
| **1005** | 双方改不同列 / 50 | **本地改名称** / 50 | 双方改不同列 / **55** | `modified` + B = `auto_mine` + C = `auto_theirs` | B/C 各自接受 |
| **1006** | 同列冲突 / 60 | **本地改后** / 66 | **远程改后** / 66 | `modified` + **B = conflict** | ⚠ 需要手选 B；C = auto_both |
| **1007** | 本地删除 / 70 | _(删除)_ | 本地删除 / 70 | `removed_mine` | 默认确认删除 |
| **1008** | 远程删除 / 80 | 远程删除 / 80 | _(删除)_ | `removed_theirs` | 默认接受删除 |
| **1009** | 双方删除 / 90 | _(删除)_ | _(删除)_ | `removed_both` | 默认确认删除 |
| **1010** | 本删远改 / 100 | _(删除)_ | 本删远改 / 100 / **远程修改** | ⚠ `mine_del_theirs_mod` | **必须手选**：保留删除 vs 恢复并接受远程 |
| **1011** | 本改远删 / 110 | 本改远删 / **119** / **本地修改** | _(删除)_ | ⚠ `mine_mod_theirs_del` | **必须手选**：保留我的修改 vs 接受远程删除 |
| **2001** | _(不存在)_ | 本地新增 / 201 | _(不存在)_ | `added_mine` | 默认保留新增 |
| **2002** | _(不存在)_ | _(不存在)_ | 远程新增 / 202 | `added_theirs` | 默认接受新增 |
| **2003** | _(不存在)_ | 双方都加 / 203 | 双方都加 / 203 | `added_both_same` | 自动保留 |
| **2004** | _(不存在)_ | **本地版本** / 204 / **本地说** | **远程版本** / 204 / **远程说** | ⚠ `added_both_diff` | **必须手选**：保留我的 / 用远程 / 按单元格合并 |

汇总：
- **3 个行级强冲突**：1010、1011、2004
- **1 个单元格冲突**：1006.B（数值列同改不同值）
- **其余全部自动决议**

---

## 三、手工 UI 测试（完整体验流程）

适合验证：合并界面交互、决议按钮、写回正确性、SVN 集成等。

### 3.1 准备一个本地 SVN 演示仓库

可以用我准备好的脚本（在 `tests/setup_demo_svn.bat`）一键完成：

```cmd
cd C:\xmldev\tests
setup_demo_svn.bat
```

执行后会在 `%TEMP%\xmldev_demo_svn\` 下生成：

```
%TEMP%\xmldev_demo_svn\
├── repo\           # SVN 仓库（svnadmin create）
└── wc\             # 工作副本，已经处于"本地修改"状态
    └── items.xml
```

**它做了什么**：
1. `svnadmin create repo` 创建本地仓库
2. 用 `base.xml` 作为 r1 提交
3. 用 `theirs.xml` 覆盖并提交为 r2（模拟"远程已经有人提交了 theirs"）
4. `svn update -r 1 wc` 把工作副本回退到 r1（模拟"本地 BASE 是 r1"）
5. 用 `mine.xml` 覆盖工作副本中的 items.xml（模拟"本地未提交的修改"）

最终状态完美对齐合并三方：
- **SVN BASE** = `base.xml`
- **MINE（工作副本）** = `mine.xml`
- **THEIRS（HEAD）** = `theirs.xml`

### 3.2 启动应用

```cmd
cd C:\xmldev
start.bat
```

或直接 `python server.py`。

### 3.3 切换到演示工作区

1. 点 Header 的 **+** 按钮，选择 `%TEMP%\xmldev_demo_svn\wc` 目录
2. 工作区列表会切到这个目录，文件列表里出现 `items.xml`

### 3.4 测试入口 A：独立"语义合并"模式

1. 点 Header 顶部 tab 切到 **语义合并**
2. 左侧只显示 `.xml` 文件
3. 点击 `items.xml`
4. 主区域加载三方对比，工具栏显示：

   ```
   items.xml  对比版本: HEAD  [刷新]
   自动合并 N，冲突 4，待解决 4    [应用合并并保存]（禁用）
   ```

5. 按下表逐个解决冲突：

| 冲突项 | 期望操作 | 验证 |
|---|---|---|
| **行 1010**（红边） | 点"恢复并接受远程修改" | 红边消失，待解决 -1 |
| **行 1011**（红边） | 点"接受远程删除" | 红边消失，待解决 -1 |
| **行 2004**（红边） | 点"用远程版本" | 红边消失，待解决 -1 |
| **行 1006 → 单元格 B（红底）** | 点"用远程" | 红底变绿，下方显示"→ 远程改后 [远程]"，待解决 -1 |

6. 工具栏 stats 变绿："已全部解决"，"应用合并并保存"按钮亮起
7. 点 **应用合并并保存**
8. 弹窗"合并完成，已写回 N 项决议"
9. 自动跳回"本地变更"模式

### 3.5 验证写回结果

在文件管理器打开 `%TEMP%\xmldev_demo_svn\wc\items.xml`（或继续在 UI 看本地变更），应该能确认：

- ✅ 1003 数值变成 33（远程改的）
- ✅ 1005 名称是"本地改名称"，数值是 55（双方各自接受）
- ✅ 1006 名称变成"远程改后"（用户选了 theirs）
- ✅ 1010 行被恢复，备注是"远程修改"
- ✅ 1011 行被删除
- ✅ 2002 远程新增的行出现了
- ✅ 2004 名称是"远程版本"

### 3.6 测试入口 B：SVN 冲突弹窗集成

1. 切回"本地变更"模式
2. 顶部 SVN 更新横幅会显示"有 1 个新版本可用"（因为 wc 还是 r1）
3. 点 **更新** → 弹出"发现冲突文件"对话框
4. `items.xml` 会同时出现 4 个按钮：`保留我的` / `用最新` / `跳过` / **`语义合并`**（紫色）
5. 点击 **语义合并** → 自动关闭弹窗，跳转到合并模式，加载三方对比
6. 完成决议 → 应用合并 → 系统自动调 `svn resolve --accept working`
7. 检查：弹窗提示包含 "SVN 冲突已标记为已解决"

### 3.7 边界场景验证

| 场景 | 操作 | 期望 |
|---|---|---|
| 文件无任何变更 | 选一个未改动的 .xml | 显示"该 Sheet 无任何变更" |
| 选 .xlsx 文件 | 切到合并 tab，再切回看左侧 | .xlsx 不出现（已被过滤） |
| 直接通过 URL 调 .xlsx | `curl -X POST -d '{"file":"x.xlsx"}'` | 返回 400 错误 |
| 未解决就点应用 | 不解决冲突直接点按钮 | alert "还有未解决的冲突..." |
| 自定义值 | 1006.B 点"自定义..." | 弹出 prompt，输入任意值后生效 |

---

## 四、回归测试覆盖矩阵

下表列出了已经被自动化测试覆盖的场景：

| 维度 | 覆盖项 | 在哪个测试 |
|---|---|---|
| 单元格状态 | unchanged / auto_mine / auto_theirs / auto_both / conflict | `test_merger.py` §1 |
| 行级状态 | 9 种全部覆盖 | `test_merger.py` §2 |
| summary 统计 | 行级 + 单元格冲突计数准确 | `test_merger.py` §3 |
| 决议 API | 未决议、全决议、自定义值 | `test_merger.py` §4 |
| XML roundtrip | 写回 → 重新解析 → 内容比对 | `test_merger.py` §5 |
| Preamble 保留 | XML 声明 / mso-application PI | `test_merger.py` §5 |
| 命名空间风格 | 默认 ns 不被改成 ss: 前缀 | `test_merger.py` §5 |
| HTTP 端点 | preview / apply / svn-mark-resolved | `test_api_merge.py` §1-3 |
| 错误处理 | xlsx 拒绝、文件缺失、未决议 400 | `test_api_merge.py` |
| 文件列表 | `/api/files` 递归列出子目录 `.xls` 文件 | `test_api_merge.py` §4 |
| SVN 冲突检测 | check_only 按工作区相对路径匹配子目录文件 | `test_api_merge.py` §4 |
| 幂等性 | apply 后再 preview 无残留冲突 | `test_api_merge.py` §5 |

---

## 五、常见问题

**Q1：PowerShell 输出全是乱码？**
A：先跑 `chcp 65001`，或者用 Windows Terminal / VS Code 集成终端。

**Q2：自动化测试报 `ImportError`？**
A：必须从项目根目录运行：`cd C:\xmldev` 然后 `python tests\test_merger.py`，不能进入 tests 目录运行。

**Q3：`setup_demo_svn.bat` 报"svn not found"？**
A：安装 TortoiseSVN 时勾选"command line client tools"；或单独安装 SlikSVN，把它的 bin 目录加 PATH。验证：`svn --version`。

**Q4：我想清理演示数据**
A：删 `%TEMP%\xmldev_demo_svn\` 整个目录即可。

**Q5：写回的 XML 和原文件有什么差异？**
A：我们尽量保留原 XML 的 BOM / declaration / mso-application PI / 默认命名空间。新增的行会带上 `ss:Index="N"` 显式索引。Excel 重新打开 → 另存为时可能轻微调整，但功能上完全等价。

---

## 六、给我反馈

任何不符合预期的地方，请记录：

1. 出现问题的步骤（最好截图）
2. `tests/test_merger.py` 和 `tests/test_api_merge.py` 的输出
3. 浏览器 DevTools Console 的报错（合并 UI 操作时按 F12）
4. 需要时打包 `%TEMP%\xmldev_demo_svn\wc\items.xml` 给我
