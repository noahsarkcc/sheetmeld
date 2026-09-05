# Contributing to SheetMeld

**English** · [中文](#贡献指南)

Thanks for taking the time to consider contributing! SheetMeld is a small, focused tool — contributions, bug reports, and discussions are all welcome.

## How to contribute

1. **Open an issue first** for non-trivial changes, so we can align on direction before you write code. Trivial fixes (typos, obvious bugs) can go straight to a PR.
2. **Fork** the repository and create a feature branch from `main`:
   ```bash
   git checkout -b feature/my-change
   ```
3. **Run the test suite** and make sure it passes:
   ```bash
   python tests/test_merger.py     # 29 cases, no SVN required
   python tests/test_differ.py     # 11 cases, no SVN required
   python tests/test_updater.py    # 23 cases, mocked network
   python tests/test_api_merge.py  # 28 cases, mock SVN
   python tests/test_regressions.py # 10 XML/API cases
   python tests/test_svn_update.py  # 9 cases, requires svn + svnadmin
   node --test tests/test_frontend.js # 12 cases, requires Node.js 22+
   ```
4. **Add tests** for any new behavior. Python tests use plain `assert` + `print` or standard-library `unittest`; frontend tests use Node's built-in test runner. No pytest or additional test framework is required. The full suite has 122 cases; real SVN tests use disposable local repositories and explicitly skip when SVN tools are unavailable.
5. **Open a PR** against `main`. CI runs the Python suites on Python 3.8 / 3.10 / 3.12 and frontend tests with Node.js 22.

## Code style

- **Backend (Python)**: keep the runtime dependencies in `requirements.txt` minimal. Don't pull in new packages without discussion.
- **Frontend (`static/`)**: vanilla JS / CSS, no build step, no framework. New code follows the existing patterns in [static/js/app.js](static/js/app.js).
- **Comments**: explain non-obvious intent, trade-offs, or constraints — not what the code literally does.

## Reporting bugs

Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.yml). Please include:

- OS + Python version
- Whether SVN CLI is installed (`svn --version`)
- A minimal reproduction file if the issue is parser/diff related (a tiny `.xml` snippet usually suffices)
- The full error traceback if any

## Discussions vs Issues

- **Issues**: confirmed bugs, concrete feature requests
- **Discussions**: questions, ideas, "is this the right approach?", showing off your config workflow

---

# 贡献指南

[English](#contributing-to-sheetmeld) · **中文**

感谢你愿意为 SheetMeld 做贡献！这是一个小而专注的工具，欢迎 bug 报告、PR 和讨论。

## 贡献流程

1. **非琐碎改动请先开 issue** 沟通方向，再写代码；typo / 明显 bug 可以直接发 PR。
2. **Fork** 仓库，从 `main` 拉一个功能分支：
   ```bash
   git checkout -b feature/my-change
   ```
3. **跑测试**：
   ```bash
   python tests/test_merger.py     # 29 用例，无需 SVN
   python tests/test_differ.py     # 11 用例，无需 SVN
   python tests/test_updater.py    # 23 用例，mock 网络
   python tests/test_api_merge.py  # 28 用例，mock SVN
   python tests/test_regressions.py # 10 个 XML/API 用例
   python tests/test_svn_update.py  # 9 用例，需要 svn + svnadmin
   node --test tests/test_frontend.js # 12 用例，需要 Node.js 22+
   ```
4. **新功能配新测试**。Python 测试使用 `assert` + `print` 或标准库 `unittest`；前端使用 Node 内置测试运行器，不引入 pytest 等额外测试框架。完整套件共 122 个用例；真实 SVN 测试只使用临时本地仓库，缺少 SVN 工具时明确跳过。
5. **提 PR** 到 `main`。CI 在 Python 3.8 / 3.10 / 3.12 上运行 Python 套件，并使用 Node.js 22 运行前端测试。

## 代码风格

- **后端（Python）**：`requirements.txt` 中的运行依赖保持最小化，新增依赖请先讨论。
- **前端 (`static/`)**：原生 JS / CSS，无构建步骤，无框架。新代码遵循 [static/js/app.js](static/js/app.js) 中的既有模式。
- **注释**：解释非显而易见的意图、权衡或约束，不要把代码字面意思再说一遍。

## Bug 报告

请使用 [bug 报告模板](.github/ISSUE_TEMPLATE/bug_report.yml)，并附：

- 操作系统 + Python 版本
- 是否安装了 SVN CLI（`svn --version`）
- 涉及解析 / Diff 时，附最小复现 XML（一小段就够了）
- 完整的错误堆栈（如果有）

## Discussions 与 Issues 怎么选

- **Issues**：确认的 bug、具体的功能请求
- **Discussions**：提问、想法、"我这个用法对不对？"、分享你的配置工作流
