<div align="center">

# founder-os

**为同时运行多个 Claude Code 会话的产品开发提供记忆、协同与强制约束。**

[![version](https://img.shields.io/badge/version-1.0.0-black)](https://github.com/avanturer/claude-bestpractice/releases)
[![tests](https://img.shields.io/badge/tests-572%20passing-2ea44f)](#已验证)
[![doctor](https://img.shields.io/badge/doctor-25%20checks-2ea44f)](#已验证)
[![python](https://img.shields.io/badge/python-3.9%2B-blue)](#运行要求)
[![dependencies](https://img.shields.io/badge/dependencies-none-blue)](#运行要求)
[![context](https://img.shields.io/badge/常驻上下文-332%20tokens-blue)](#运行要求)
[![license](https://img.shields.io/badge/license-MIT-lightgrey)](../LICENSE)

[English](../README.md) · [Русский](README.ru.md) · **中文**

</div>

---

```sh
claude plugin marketplace add avanturer/claude-bestpractice
claude plugin install founder-os@founder-os
```

或者用这条——它会在注册任何东西**之前**先在你的机器上验证每个 gate：

```sh
curl -fsSL https://raw.githubusercontent.com/avanturer/claude-bestpractice/HEAD/install.sh | bash
```

这两条路径并不等价，差别在你使用的第一天就会遇到：

|  | `claude plugin install` | `install.sh` |
|---|---|---|
| gate 在会话中生效 | 是 | 是 |
| `founder-os` 在**你自己的终端**里可用 | 否 | 是（软链到 `~/.local/bin`）|
| 注册前先跑 doctor | 否 | 是 |

Claude Code 会自动把插件的 `bin/` 加进 Bash 工具的 PATH，所以走 marketplace 路径时，
下面这些命令在**会话内**可用，在你自己的 shell 里则是 `command not found`。这一点很重要，
因为 pre-push gate 正是由 `founder-os init` 安装的：跳过它，gate 就是关着的，而
`claude plugin list` 依然显示 `✓ enabled`。

之后在任意仓库中——在终端里运行，或者让 Claude 替你运行：

```sh
founder-os init      # 能从代码推导的自动推导，推导不出的才来问你
founder-os status    # 一次看全
```

安装器在注册任何东西**之前**先跑 doctor，只要有一个 gate 没有真正触发就拒绝安装。
悄无声息什么都不做的 gate，比没有 gate 更糟。

---

## 问题

你几乎完全通过 Claude 来构建产品。三到八个会话同时运行在各自的 worktree 里，
而你几乎不看 diff。出错的地方是被测量出来的，不是猜的：

| | |
|---|---|
| 智能体说"完成了"，代码却跑不通 | 提交率 **0.97**，而测试验证通过率只有 **0.65**。两种不同的防护提示词让这个差距移动了 **零** |
| 去改它根本没被要求碰的正确代码 | 在四个前沿模型上有 **60–90 %** 的运行如此，而正确做法是不动手 |
| 会话越长，规则越失效 | 违规率 0 % → 一次压缩后 **30 %** → 四次压缩后 **78 %** |
| 规则太多反而全面崩塌 | 10 条规则时 **93.8 %** 完全遵守 → 20 条降到 **75 %** → 40 条 **23.8 %** → 80 条 **0 %** |
| 过期的上下文比没有上下文更糟 | 仅用过期检索时，**15/17** 个样本产生了调用已废弃 API 的代码；完全不检索则是 **0/17** |

每个数字的来源见 [`EVIDENCE.md`](EVIDENCE.md)。

## 一句话设计原则

**凡是重要的，都不去"请求"模型。** 每一条必须成立的规则都由 harness 或 git 强制执行；
模型的上下文里只留下少数程序无法检查的东西。

---

## 你会得到什么

### 不会腐烂的记忆

三层结构，按*各自会因为什么而变成假的*来划分。

| 层 | 内容 | 为什么它保持为真 |
|---|---|---|
| **Derived（推导层）** | 符号图、测试结果、健康指标 | 从代码重新生成，并盖上来源 commit 的戳。过期的产物是一次构建失败，而不是一个自信的错误答案 |
| **Decided（已决层）** | 产品、非目标、实体、术语表、决策 | 不可变。一条决策是历史事实——只能由后来一条点名它的记录作废，绝不通过改写历史 |
| **Ephemeral（临时层）** | 会话、租约、认领、配额 | 按会话隔离，进 gitignore，带 TTL，会被回收 |

每一条断言都携带它所描述代码的 **git blob 哈希**——绝不用 mtime，因为 worktree 检出会把
mtime 整体重置。当断言的对象被改写时，该断言会被抑制并计数，而不是被悄悄删除。

每个实体都写明其规范标识符和所在文件。一次重命名会**让校验失败**，而不是留下一份
描述着已不存在之物的记忆。

### 会话彼此可见

```
OTHER LIVE SESSIONS (2) — do not edit files they hold:
  - a3f81c22 on feat/export  [ledger-export]  active 40s ago
      touched: src/billing.js, src/csv.js
      holds: src/billing.js
      task: Add CSV export to src/billing.js

IN FLIGHT:
  - 0004 Fix rounding in invoice totals  [b7d29e01]
NEXT:
  - 0005 Add client search
(12 done)

health: 3 live session(s), 1 reaped, 4 open item(s), 1 stale (suppressed)
```

去编辑另一个存活会话正持有的文件会被**拒绝**，并指名持有者。崩溃会话的租约和认领由
回收器释放——就在持有它们的那个 worktree 里——而不是永远挂在"进行中"。

安静下来的会话**不会**被当成死亡。按沉默回收意味着：创始人思考十五分钟回来，会话里
所有 gate 都已经不再强制任何东西。现在判定死亡需要进程真的消失，或其 pid 被复用。

### 能够合并的工作台账

`.claude/founder-os/plan/{next,doing,done}/` —— 一个任务一个文件，状态由目录编码，
所以状态转移就是一次 `git mv`。五个 worktree 产生五次干净的新增，而不是同一个 JSON
里五段互相冲突的 hunk。ID 的分配会参照所有兄弟 worktree，因为 worktree 在文件被提交
之前就已经共享同一个命名空间。

### 伪造"完成"的代价很高

Stop gate **丢弃智能体的自述文字**，自己去运行你的测试套件。它不做的事情是：把这当成
证明。诚实的说法值得直接写出来，因为六轮对抗式验证把先前那个说法拆开了四次：

> **智能体写你的代码、你的测试、你的测试命令和你的构建文件。任何读取 runner 输出的
> 检查，读到的都是智能体能写的东西。** 这个 gate 把一次假绿灯的代价从一行提高到若干
> 个有意为之的步骤，并为每一步留下持久记录。它并不能让假绿灯变得不可能。

被击穿的顺序是这样的：先是信任产物文件（手写 XML 就赢了），然后是退出码（Makefile
配方前加一个 `-`），然后是 "N failed" 这几个字（不打印就是了），然后是 "N passed"
这个计数（`@echo '2 passed in 0.03s'`）。每一次修复都只是把伪造往下挪一层，而不是挪
出去。

所以现在有一个信号取自这个循环之外：gate **自己去数你源码树里的测试声明**并做比较。
一次只执行了树中所声明的一小部分的运行，会被记为"未经验证"而不是绿色；而一棵掉了测试
的树无法清掉红色套件的记录——"删掉那个失败的测试"正是一个会拦人的 gate 最容易诱导出
的动作，而它此前是最便宜的出路。要挪动这个数字就得写真正的测试，这个代价本插件很乐意
收取。

在原型之后的阶段，它还会针对已提交树的干净检出再跑一次，用来抓"我这儿是绿的、别处是
红的"那一类问题——某个未提交的文件，或某个本地环境变量。

它也会升级而不是把人卡死：连续四次被拦截之后，它记录一次"未经验证的完成"并放行该轮，
因为一个永远挡住创始人工作流的 gate，就是一个会被卸载的 gate。

### 写给下一个模型读的代码

文档字符串必须承载无法从代码推导的信息。`Args:` / `Returns:` / `:param:` 被直接禁止——
类型已经说明了这些。签名变了而文档字符串没变，**提交会失败**。

> 如果你正准备写一句注释来说明某个值是什么，那就改写一个类型。

### 机械地抓住垃圾代码

吞掉的异常、只有一个调用方的抽象、没有消费者的兼容层、重复代码块、未使用的参数——
**永久预算为零**。复杂度和长度走棘轮：只设一次基线，此后只降不升。

### 严格程度自动伸缩

阶段由仓库自身推算得出，从不配置，并且棘轮只会收紧。

| 检测到的信号 | 随之启用 |
|---|---|
| CI 加上一个部署目标 | 出网规则、生产信号隔离闸 |
| 创建 users 表的迁移，或一个认证 SDK | 迁移管控、拒绝生产环境提升、每个 worktree 独立数据库与端口 |


### 检查在本地跑，不花别人的机时

`founder-os init` 会装上一个 **pre-push 钩子**，在任何东西离开这台机器之前先跑你自己的
`make check`——仓库没有的话就跑 doctor。免费，而且时机对：它拦下这次 push，而不是事后
给你发一封邮件。

之所以把它设为默认，是因为托管机时是计费的，而这种工作方式消耗它的速度和一个小团队一样：
三到八个会话整天在推送，账单却只有一份。

随附的 GitHub Actions 工作流**由一个仓库变量把守**，在你主动开启之前不花一分钱：

```sh
founder-os-ci status     # 什么在哪里跑
founder-os-ci github     # 打开托管 CI（通过 gh 设置该变量）
founder-os-ci off        # 移除 pre-push 钩子
```

两者可以并存：pre-push 钩子只约束装了它的机器，所以一个别人也会推送的仓库仍然需要托管
运行。想单次绕过就用 `git push --no-verify`——那是一个有痕迹的、刻意的动作，而被悄悄跳过
的托管运行没有痕迹。

### 它会接管与它冲突的东西

`founder-os adopt` 会找出争抢本插件所拥有事件的其他工具，把它们的 hook 条目移入一个
带标记的区块并留下备份，然后明确告诉你该禁用哪些竞争插件。可用 `--restore` 撤销。
它绝不会悄悄删除别的工具的配置。

---

## 命令

| | |
|---|---|
| `founder-os status` | 会话、计划、知识、记忆健康度、冲突、下一步动作 |
| `founder-os init` | 从你的代码推导出知识层 |
| `founder-os adopt` | 接管被其他工具争抢的事件 |
| `founder-os doctor` | 通过尝试一个已知的坏动作来证明每个 gate 有效 |
| `founder-os-plan` | 工作台账：`add`、`list`、`claim`、`done` |
| `founder-os-decide` | 采纳一条由你自己的纠正草拟出的决策 |
| `founder-os-ingest` | 把生产环境错误净化成带围栏的任务文件 |
| `founder-os-knowledge` | 校验已决层，刷新其索引 |
| `founder-os-reindex` | 丢弃并重建所有推导内容 |
| `founder-os-ci` | 检查在哪里跑：默认本地 pre-push，托管 CI 按需开启 |
| `founder-os-attempt` | 死路台账：试过什么、为什么没成 |
| `founder-os-options` | 把决策记成一次按指标打分的方案比较 |
| `founder-os-ship` | 这条分支交付了什么，写给不读代码的人（`--pr` 直接开 PR）|

在会话中：`/founder-os:status` · `/founder-os:plan` · `/founder-os:review`

## 各个 Gate

| Gate | 事件 | 失败姿态 | 作用 |
|---|---|---|---|
| `setup` | Setup | 失败放行 | 推导知识层，创建计划，播种阶段 |
| `session-start` | SessionStart | 失败放行 | 回收死会话，注册自身，注入看板 + 计划 + 阶段 |
| `prompt-capture` | UserPromptSubmit | 失败放行 | 逐字记录任务。**不注入任何东西** |
| `pre-tool` | PreToolUse | **失败拦截** | 调用上限、循环打断、写入前密钥扫描、租约、迁移与部署管控 |
| `review-commit` | `if: Bash(git commit:*)` | 异步唤醒 | 审查本轮的 diff；只在确实有话要说时才叫醒你 |
| `worktree-create` | WorktreeCreate | 失败放行 | 命名、播种信任、推导私有端口与数据库 |
| `subagent-brief` | SubagentStart | 失败放行 | 把非目标、实体和按查询偏置的代码图交给不继承任何规则的子智能体 |
| `checkpoint` | PreCompact | 失败放行 | 抽取式检查点，零模型调用，密钥已清洗 |
| `evidence-gate` | Stop | **失败拦截** | 范围漂移、测试证据、干净重跑；顺带收割决策草稿 |

九个条目，自设上限是十二个。常驻上下文 **约 329 tokens**，上限 400 ——
大约是 200k 窗口的 0.1 %。

---

## 已验证

```
make check    # lint · docs gate · slop gate · polyglot gate · knowledge · 572 个测试 · 25 项 doctor 检查 · budget
```

doctor 通过**真的去做那件坏事**来证明 gate 有效，而不是把配置读回来对一遍——
读回配置无法察觉语义上的变化。开发过程中出现的十七个真实 bug 全都无法靠阅读代码发现，
只有执行才抓得到，其中包括一个死锁：evidence gate 要求一份测试产物，然后又因为会话
产出了它而把会话拦下。

测试套件包含一整条项目生命周期，全程通过真实的 gate 可执行文件驱动：接管一个从未见过
的仓库、做计划、泄露一个凭据、一次范围越界、一次测试失败、一次绿色收尾，以及第二个
会话读取这段历史。

## 运行要求

Python 3.9+ 和 git。**没有任何其他依赖，这是硬约束**——这些 hook 在每一次工具调用时
都会运行，所以依赖树意味着延迟、额外的失败模式，以及一个供应链攻击面，而这个组件的
全部职责恰恰就是可信。此约束在 CI 中强制执行。

已在 Python 3.9、3.11 和 3.13 上测试。`claude plugin validate --strict` 在
Claude Code 2.1.220 上通过。

### 它会在你的仓库里留下什么，以及该怎么处理

两个目录，区别很重要：

**`.claude/founder-os/` —— 提交它。** 任务、决策、走过的死路、阶段标记。它被有意放在
仓库内部，因为它必须跟着分支走：在 `feat/billing` 上做出的决策就是关于 `feat/billing`
的，而一个切分支就丢失的任务清单比没有更糟。每个条目一个文件，所以五个 worktree 产生
五次干净的 add，而不是同一个 JSON 大对象里五段互相冲突的改动。里面没有任何逐次变化的
内容，所以它不会在每次 gate 触发时都冒出来变成一个 diff。

**`.git/founder-os/` —— 不用管，git 本来就看不见它。** 活跃会话、文件租约、循环计数器、
测试回执。它放在 git 公共目录里，因为那是唯一一个被同一个克隆的所有 worktree 共享、对
git 不可见、能扛过分支切换、并随克隆一起消亡的位置。它完全可重建：`founder-os reindex`
会从已提交的那一半重新生成它，而这条路径是被测试过的，不是假设的。

两者都不需要你手动编辑。如果想彻底清除，删掉这两个目录并卸载插件即可——你机器上别的
东西一律未被触碰。

### 上下文成本

Claude Code 的 `/plugin` 面板现在会显示每个插件每轮向上下文窗口添加多少内容，所以这是
一个可以横向比较的数字，而不是一句需要你相信的说法。

founder-os 的常驻上下文为 **约 332 tokens**，分布在四个组件上，自设上限为 400。
超出时 `make check` 直接让构建失败；而这条上限是靠精简描述守住的，不是靠抬高门槛——
已经这样做过两次。

其余一切都按需加载：skill 只在触发时加载，board 只在会话开始时加载，工作台账只在被
问到时加载。这个预算之所以存在，是因为成本会明明白白出现在你自己的用量视图里，而收益
是反事实的、看不见的——这笔交易理应由创始人自己来审。

---

## 它刻意不是什么

- **不是记忆引擎。** harness 负责存取记忆，本插件负责策展。
- **不是代码审查器。** 已有多条一方审查路径；选一条并集成即可。
- **不是任务管理器。** 原生任务系统被纳入并加以管控，而不是被替换。
- **不面向团队。** 每一个取舍都假定只有一个所有者、没有审查者。

## 它无法强制的四件事

先说清楚，因为这个领域的默认状态是虚假的安全感。

1. **测试语义。** 没有任何匹配器能区分正当的 skip 和作弊。
2. **品味。** 没有任何匹配器能区分好设计和坏设计。
3. **`claude --bare`。** 它会同时丢弃 managed hook 和插件 hook。这正是仓库层——
   真正的 git hook、CI、分支保护——存在的理由。
4. **拥有 root 的人。** 这是设计使然。

---

## 文档

| | |
|---|---|
| [`DESIGN.md`](DESIGN.md) | 论点、架构、记忆模型、存储基底、验证方式 |
| [`ENFORCEMENT.md`](ENFORCEMENT.md) | 强制 vs 建议、十条规则的预算、哪些约束根本立不住 |
| [`ECONOMICS.md`](ECONOMICS.md) | token 预算、prompt cache 不变式、限流下的准入控制 |
| [`EVIDENCE.md`](EVIDENCE.md) | 每一条量化断言及其来源与证据等级 |
| [`LIMITS.md`](LIMITS.md) | **一次假绿灯现在要付出什么** —— 八轮攻击，以及至今仍然奏效的那些 |
| [`ROADMAP.md`](ROADMAP.md) | 已交付的内容，以及只有执行才发现的那些 bug |

MIT。
