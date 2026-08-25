---
title: The Designer Changed 3 Numbers. The Diff Said "Binary Files". So I Built SheetMeld
published: false
description: Our game studio versions Excel config tables in SVN. Reviewing changes meant eyeballing two spreadsheets side by side — so I built a diff tool that actually understands tables.
tags: showdev, opensource, python, productivity
cover_image: https://raw.githubusercontent.com/noahsarkcc/sheetmeld/main/assets/1.png
---

If you've ever worked at a game studio, you know the drill: half the game isn't in code. It's in **config tables** — items, skills, drop rates, level curves — maintained as Excel spreadsheets and checked into SVN next to the source.

Here's what reviewing a config change looked like for us:

A designer tweaks 3 numbers in a 5,000-row item table. They commit. You open the diff and get one of two things:

- `Cannot display: file marked as a binary type.` — thanks, SVN, very helpful. (If you're on git, you know this one as `Binary files differ`.)
- Or, if the file happens to be XML-based: **two thousand changed lines** of `<Cell>`, `<Style>`, column widths, window positions, and the cursor location the file was saved with. Somewhere in there are the 3 numbers that actually matter.

So the actual review process was: open the old version in Excel, open the new version in Excel, put them side by side, and *eyeball it*. For every commit. Forever.

At some point I snapped and built [SheetMeld](https://github.com/noahsarkcc/sheetmeld) — an open-source, local-first diff and merge tool that understands spreadsheets as **tables of data**, not as lines of text.

## Why line-based diff will never work for spreadsheets

It's not that diff tools are bad. It's that they're answering the wrong question. A spreadsheet file is a *serialization* of a table, and the serialization is full of things that aren't data:

- **Format noise.** Styles, column widths, pane positions, selection state — Excel rewrites these constantly. They produce diffs that look huge but mean nothing.
- **Positional matching.** Text diff matches by line number. Insert one row in the middle of a table and every row below it is "changed". The one real edit drowns in a cascade of false positives.
- **Merging is all-or-nothing.** Two people edit the same file? With binary spreadsheets your version control offers you a coin flip: keep yours or take theirs. Someone's afternoon gets discarded.

Every one of these is a *semantic* problem. You can't fix it with a better text algorithm — the tool has to know it's looking at a table.

## What SheetMeld does instead

SheetMeld parses the spreadsheet (`.xml` SpreadsheetML, `.xlsx`, `.xls`) into a table model, throws away everything that isn't data, and diffs *that*.

This is what the same "3 numbers changed in a 5,000-row table" looks like now:

![Local changes view — only real data changes are highlighted](https://raw.githubusercontent.com/noahsarkcc/sheetmeld/main/assets/1.png)

Green rows are added, red rows are deleted, yellow cells are modified — with old → new shown right in the cell, and token-level highlighting so you can see *which number* in a long formula-ish value actually moved.

Need to review a whole revision range instead of one file? There's a GitHub-style "Files changed" overview:

![Overview mode — every changed file between two revisions](https://raw.githubusercontent.com/noahsarkcc/sheetmeld/main/assets/2.png)

And when two people really do edit the same table, instead of the coin flip you get a **cell-level three-way merge**: non-conflicting edits from both sides are merged automatically, and only genuine conflicts — the same cell changed to two different values — ask for a human decision:

![Semantic merge — cell-level three-way resolution](https://raw.githubusercontent.com/noahsarkcc/sheetmeld/main/assets/3.png)

The merge result is written back to the original XML, preserving comments, processing instructions, and namespaces, so the file stays diff-able and Excel-friendly.

## The interesting technical bits

A few problems turned out to be more fun than expected:

**Row matching without trusting row numbers.** SheetMeld auto-detects the ID column (most config tables have one) and matches rows by identity across versions. No usable ID column? It falls back to content hashing, then to row position as a last resort. The result: inserting or deleting a row produces exactly *one* diff entry, not five thousand.

**Knowing what to ignore.** Columns without a header are treated as designer annotations and excluded from the diff — they never ship to the game anyway. Styles, pane state, and column widths are dropped at parse time, so they can't generate noise by construction.

**Three-way merge at the cell level.** Classic merge thinks in lines; SheetMeld thinks in `(row ID, column)` coordinates. With BASE, MINE, and THEIRS parsed into tables, most "conflicts" dissolve: you edited the price column, your colleague edited the description column of the same row — both survive, automatically. Only true same-cell disagreements need resolution.

**SVN integration, because that's where the pain was.** The tool polls the repo, shows a banner when new revisions land, and when an update would conflict it offers per-file choices — keep mine, take theirs, or drop into the semantic merge view. (No SVN installed? Browse mode still works as a clean table viewer, but the diff and merge features are built around SVN working copies.)

## The stack, briefly

- **Backend:** Python + Flask, talking to the SVN CLI
- **Frontend:** zero-dependency vanilla JS single-page app — no build step, no node_modules
- **Distribution:** PyInstaller single `.exe` with in-app auto-update, for the "I am not installing Python for this" crowd
- 76 automated tests across the diff engine, merge engine, and API

Nothing exotic — the value is in the table model, not the framework.

## Try it

The project is open source: **[github.com/noahsarkcc/sheetmeld](https://github.com/noahsarkcc/sheetmeld)**

- Grab the standalone `SheetMeld.exe` from [Releases](https://github.com/noahsarkcc/sheetmeld/releases) (no Python needed), or
- `pip install -r requirements.txt && python server.py` and it opens in your browser.

Point it at an SVN working copy full of spreadsheet configs and see what your last "binary" commit actually changed.

---

I'd genuinely love to hear: **how does your team review spreadsheet or config-table changes?** Side-by-side eyeballing? Export to CSV and pray? A commercial tool that actually works? Tell me in the comments — especially if you've solved the `.xlsx`-merge problem differently.

<!--
═══════════════════════════════════════════════════════════════
发布操作清单(此 HTML 注释不会在 dev.to 渲染,发布前可整段删除)
═══════════════════════════════════════════════════════════════

【标题候选】(front matter 中已用推荐项,可替换;均不再提 git)
1. 推荐:The Designer Changed 3 Numbers. The Diff Said "Binary Files". So I Built SheetMeld
2. 备选:How I Stopped Eyeballing 5,000-Row Excel Diffs at Our Game Studio
3. 备选:I Built a Diff Tool That Understands Spreadsheets (Because Line-Based Diff Never Will)

【发布步骤】
1. dev.to 右上角 Create Post → 左上角切到 Markdown 模式,整篇粘贴本文件内容
2. front matter 的 published: false 表示存为草稿;用 Preview 检查 3 张截图是否正常加载
3. 确认无误后把 published: false 改为 true 再保存,即正式发布

【封面图】
- 已做好成品:c:\xmldev\devto-cover.png(1000x420,dev.to 标准尺寸)
- 用法:在 dev.to 编辑器正文区点击图片上传按钮上传 devto-cover.png,
  把它生成的 URL 替换 front matter 中的 cover_image(当前临时指向 assets/1.png 作兜底)
- 也可直接用编辑器顶部的 "Add a cover image" 按钮上传,会覆盖 front matter 设置

【标签】showdev, opensource, python, productivity
- showdev:官方"展示你的项目"标签,本文最契合
- opensource / python / productivity:热门大标签,与内容真实匹配
- 不要堆砌不相关热门标签(如 ai/webdev),内容不符会被 mod 移除标签甚至降权

【发布时间】
- 美区工作日上午流量最高:北京时间周二~周四 21:00–24:00 发布最佳
- 避开周五晚和周末

【发布后 24~48 小时(关键窗口)】
- 每条评论都回复,算法对互动率加权,评论越活跃曝光越多
- 文末提问就是为了引评论,认真回应每个分享工作流的人
- 可在 X/Twitter、Reddit r/gamedev 或 r/programming 转发(Reddit 注意各版自宣规则)

【后续导流】
- 若反响好,写第二篇技术深挖(三遍行匹配算法 / 单元格级三方合并实现),
  与本篇组成 series,dev.to 的 series 功能会在两篇文章间互相导流
- README 顶部可加 dev.to 文章链接,形成双向流量
═══════════════════════════════════════════════════════════════
-->
