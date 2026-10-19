# ElasticWeave

**[English README](README.md)**

ElasticWeave 让编程 agent 协作处理长期优化、研究、算法分析和定理证明等任务。顶层 supervisor 委派工作，引入其他模型提供建议和独立 review，再根据结果决定下一步。

![ElasticWeave 的 Naive、Default 和 Fast 三种模式](docs/assets/system-overview.png)

## 快速开始

### 1. 准备 Linux 环境

通过系统包管理器安装 Git、Python 3、curl 和 unzip，再安装 agent 命令行工具及 Bun：

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
curl -fsSL https://claude.ai/install.sh | bash
curl https://cursor.com/install -fsS | bash
curl -fsSL https://bun.com/install | bash
```

按安装器提示配置 PATH，运行 `codex`、`claude` 和 `agent login`，完成所选账户的认证。具体配置参见官方的 [Codex](https://developers.openai.com/codex/cli/)、[Claude Code](https://code.claude.com/docs/en/setup)、[Cursor](https://cursor.com/docs/cli/installation) 和 [Bun](https://bun.sh/docs/installation) 文档。

[包内模型策略](.claude/skills/default-mode/assets/project/config/workflows/models.json)当前优先使用 **GPT-6 Astra + Claude Opus 5**，包含 Codex 和 Anthropic 模型，以及通过 Cursor 调用的配置。告诉 agent 你能使用哪些账户和模型；它会在调用前检查可用性，并报告不可用的选择。其他组合需要显式配置；目前普通运行方式中，OpenCode 不能直接替代 Cursor。无需修改全局 Claude 设置，也无需安装 Humanize。

建议增加Codex 允许的并行 Agent 数量（如果你的 Codex 的计费方式是按 API 或者中转站计费的）.
```toml
[agents]
enabled = true
max_concurrent_threads_per_session = 20
```

### 2. 将 ElasticWeave 加入项目

```bash
git clone git@github.com:ElasticWeave/ElasticWeave.git "$HOME/elasticweave"
mkdir -p /path/to/your-project
cd /path/to/your-project
git init  # 已经是 Git 仓库时跳过。
```

如果项目尚未安装 skill，复制技能包并让 Codex 能发现它：

```bash
(
  set -eu
  # 已有任一技能目录时，在复制前停止。
  test ! -e .claude/skills
  test ! -L .claude/skills
  test ! -e .agents/skills
  test ! -L .agents/skills
  mkdir -p .claude .agents
  cp -a "$HOME/elasticweave/.claude/skills" .claude/skills
  ln -s ../.claude/skills .agents/skills
  for entry in '/.claude/skills/' '/.agents/skills'; do
    grep -qxF "$entry" .gitignore 2>/dev/null || printf '\n%s\n' "$entry" >> .gitignore
  done
)
```

项目已有 skill 时，用下方的更新提示让 agent 合并。保留你自己的 skill 和 agent 设置，不必复制整个仓库的隐藏目录。

### 3. 开始任务

在项目目录运行 `codex`，选择一种模式：

| 模式 | 谁提出计划？ | 适用场景 |
| --- | --- | --- |
| `$default-mode` | Agent 自行提出和优化计划，并按需 review。 | 自主研究、实现与优化。 |
| `$fast-mode` | 你给出方向或计划，agent 执行短周期任务并反馈。 | 快速实验，以及让 agent 对齐你的意图。 |
| `$naive-mode` | 一个隔离 agent 根据完整的任务描述工作。 | 消融实验和基线对比。 |

使用 Default Mode 时，替换尖括号中的内容并粘贴：

```text
$default-mode
目标：<任务及可衡量的成功标准>。
仓库和分支：<路径或地址、分支>；<允许 push / 不要 push>。
验收：<由 agent 验证的内容，以及需要我 review 的内容>。
约束与资源：<规则、数据、计算资源、时间或费用预算>。
模型：<已认证的 CLI 和模型，或检查权限后使用包内推荐配置>。
上下文：<相关文件和已有 session 的标识，如有>。
持续推进，直到达成约定目标，或遇到确实需要我处理的阻碍。
```

使用 Fast Mode 时，保留同样的任务信息，开头换成 `$fast-mode`，并给出你的计划：

```text
$fast-mode
我的计划：<具体改动或实验>。按这个计划实现、验证，并先向我展示结果，再决定下一步方向。
```

进行基线对比时，在提示中提供完整输入：

```text
$naive-mode
任务：<独立完整的问题、输入、约束和预期输出>。
```

Naive Mode 在临时目录中运行，不读取项目代码、skill、配置或历史 session。输出返回当前 session，不修改你的项目。

新信息和纠正意见直接发在主 session。需要时可以询问进度或是否有事情需要你处理；支持 [`/side`](https://developers.openai.com/codex/cli/slash-commands/) 的 Codex 版本可以在工作继续时使用 `/side 当前进展如何？`。

## 工作机理

Default Mode 中，顶层 agent 负责监督规划、委派实现、实验和结果分析。独立 reviewer 质疑重要计划并检查产出；结果意外或进展停滞时，可以向更强模型寻求建议。规划、建议和分析可以交织进行，你不需要自己选择 workflow template。

![Default Mode：监督、review 与自我改良](docs/assets/default-mode-loop.png)

- **并发执行：** 独立 worker 和 workflow 可以并行运行。图中 controller 串行执行的说明指的是单个 workflow 内部的分支，不是 supervisor 委派的不同任务。
- **自我改良：** Supervisor 根据实际结果调整 workflow，并保留经过 review 的改进供后续使用。改变的是工作过程，而非底层模型权重。
- **按需上下文：** Worker 只接收当前任务需要的信息和 skill，supervisor 则持续跟踪整体目标。

Fast Mode 按你的方向推进，减少对 idea 本身的讨论，仍保留实现验证和独立 review。你可以先用 Fast Mode 确认 agent 理解了意图，再切换到 Default Mode 自主优化。

切换时，**在同一个 session** 发送 `$default-mode` 或 `$fast-mode` 及下一步指令即可。Agent 会先处理好在途工作，更新并重新读取 `AGENTS.md` 和 `CLAUDE.md` 中的受管部分，再继续任务。你自己的指令和任务历史会保留。

![同 session 切换模式，并按需加载上下文](docs/assets/mode-switching-context.png)

## 更新版本

更新技能包的本地克隆：

```bash
git -C "$HOME/elasticweave" pull --ff-only
```

然后在**已有项目 session** 中发送以下提示，替换来源路径和当前模式：

```text
将 <技能包本地克隆的绝对路径> 中的 ElasticWeave 安装或更新到当前项目。
先读其中的 .claude/skills/default-mode/references/installed-project-layout.md。
把包内 skill 合并到 .claude/skills，并通过 .agents/skills 提供发现入口。
保留我自己的 skill、设置、本地修改、任务状态和记忆；逐项处理冲突和已退役的包内文件。
只在 Git 中忽略实际安装的包文件。
按当前的 <default / fast> 模式刷新 AGENTS.md 和 CLAUDE.md 的受管部分，
重新读取更新后的指令和相关任务上下文，在同一个 session 中继续当前任务。
```

Agent 会一起处理技能包变更和指令刷新，不需要另开顶层 session。

## 引用

如果 ElasticWeave 用于研究，请按以下格式引用：

```bibtex
@misc{li2026elasticweave,
  author       = {Li, Muheng},
  title        = {ElasticWeave},
  year         = {2026},
  publisher    = {GitHub},
  journal      = {GitHub repository},
  howpublished = {\url{https://github.com/ElasticWeave/ElasticWeave}}
}
```

## Sponsorship

如有赞助咨询事宜，请联系 muhengli2005@gmail.com。

## 许可证

[MIT](LICENSE)。
