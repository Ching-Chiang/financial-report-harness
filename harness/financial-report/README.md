# 财报 Harness：直接在 Claude Code 中使用

把项目文件夹交给 Claude Code（或 Codex），让它读取 `SKILL.md` 并调用项目脚本即可。Claude 的推理、工具调用和账号由宿主负责。本包不另建母智能体服务，不引入 LangGraph/Agent SDK，也不安装 MinerU、本地模型或 CUDA。

## 需要准备的只有两项

1. Python 3.10+ 和少量脚本依赖。Windows 可运行项目根目录的 `setup.ps1` 自动建立 `.venv` 并安装锁定依赖；已有合适 Python 环境也可直接调用脚本。
2. **独立的 MinerU API Token**，通过启动 Claude Code 的进程环境传入 `MINERU_API_TOKEN`。这不是 Claude API Key。不要把真实 Token 提交到项目、粘贴到聊天或写入日志。

`.env.example` 只说明变量名称，程序不会自动读取 `.env`。可由操作系统凭据管理器、终端进程环境或团队约定的安全启动器注入。环境变量变更后需让 Claude Code 的新进程继承它。

## 使用

在项目文件夹运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
powershell -ExecutionPolicy Bypass -File .\run.ps1 doctor
```

然后对 Claude Code 说：

> 阅读 harness/financial-report/SKILL.md，调用 MinerU 在线 API 处理这份年报，提取合并三表的本期和上期数据。按项目 SQL 映射，返回原文页码证据、质量报告和异常清单，未知业务编码保持待审。

主智能体可以委派给 `.claude/agents/financial-report.md` 定义的子智能体。Codex 通过项目 `AGENTS.md` 找到共享入口。各任务用独立输出目录，不依赖共享聊天上下文。

## 工具接口

```powershell
.\run.ps1 doctor
.\run.ps1 parse --pdf .\report.pdf --output .\runs\report
.\run.ps1 validate --input .\runs\report\candidates.json --output .\runs\report\quality.json
.\run.ps1 export --input .\runs\report\candidates.json --output .\runs\report-export
```

`report.pdf` 是用户提供的输入文件占位名，本包不附带财报 PDF。`parse` 只完成解析及证据整理；`candidates.json` 由智能体依据解析表格和业务字典生成，再调用确定性校验。需要选择页面时增加 `--pages`，并先核实该 PDF 的物理页码。

超时且保存了任务 ID 时，使用相同 PDF 和输出目录追加 `--resume`，继续查询同一批次，不重新提交。默认轮询超时 900 秒、间隔 5 秒，可通过参数调整。

不通过 PowerShell 包装时：`python harness/financial-report/scripts/cli.py doctor`。详细数据结构及退出码见 [接口说明](references/io.md)。

## MinerU 在线调用

本包使用需要 Token 的精准解析 API，默认模型名称 `vlm`，也支持 `--model pipeline`：

1. `POST https://mineru.net/api/v4/file-urls/batch` 申请单文件上传地址。
2. `PUT` 上传选取的 PDF 页面，不携带 MinerU Authorization。
3. `GET https://mineru.net/api/v4/extract-results/batch/{batch_id}` 轮询结果。
4. 下载 ZIP、检查压缩包路径、提取结构化结果并映射回原 PDF 页码。

需要上传 PDF 的理由是使用在线解析服务。没有本地模型下载。官方文档另有免 Token 的轻量接口，但其输出仅 Markdown，不满足本项目对结构化结果及页码证据的要求，因此本版不使用它。

本包依据 2026-09-27 阅读的 [MinerU 官方 API 文档](https://mineru.net/apiManage/docs) 实现。在线服务版本、额度和返回格式由服务方控制；文件上限预检查按当日精准 API 文档的 200 MB / 200 页执行。

## 换电脑

复制项目文件夹，准备 Python，并在新电脑配置自己的 MinerU Token。执行 setup 即可重建小型辅助环境；不携带 `.venv` 更简单。原始 SQL 和业务规则已在包内，不依赖微信附件目录。

共享时排除 `.venv/`、`runs/`、`tmp/` 和任何本地凭据文件。Git 忽略规则已包含这些内容。宿主 Claude Code/Codex 仍需在目标电脑正常安装和登录。

## 放行与测试边界

缺少业务编码、映射歧义、证据不完整或必要勾稽失败时，仅导出待审结果，不生成正式 SQL/CSV。导出不连接数据库、不覆盖历史数据，也不验证与已有数据库记录的冲突。

`doctor` 只检查依赖和 Token 是否存在，不声称鉴权或网络已经验证。真实调用测试需要有效 MinerU Token。具体已执行测试见 [verification.md](verification.md)。
