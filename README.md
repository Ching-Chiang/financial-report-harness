# 财报 Harness 独立项目

克隆或解压项目后，用 Claude Code 打开本 README 所在的 `financial-report-harness` 根目录。换电脑时复制整个文件夹并打开复制后的根目录，无需父目录的历史文件。

根目录 `CLAUDE.md` 是 Claude Code 的项目指引，`AGENTS.md` 提供项目规则，`.claude/agents/financial-report.md` 定义可委派的财报子智能体。共享业务规则、三份原始 SQL、字段映射、脚本和测试均在 `harness/financial-report/` 中。

## 首次使用

目标电脑需已安装并登录 Claude Code，并提供 Python 3.10+。在本目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
powershell -ExecutionPolicy Bypass -File .\run.ps1 doctor
```

初始化只安装小型 Python 辅助依赖，不安装 MinerU、本地模型或 CUDA。在线解析需要独立的 `MINERU_API_TOKEN`，通过进程环境提供；不能使用 Claude 的 API Key 代替。`.env.example` 仅为配置示例，脚本不会自动加载 `.env`。

打开项目后，可直接交给 Claude Code：

> 按本项目 Harness 处理我提供的财报 PDF，抽取合并资产负债表、利润表和现金流量表的本期与上期数据。调用 MinerU 在线 API，按包内 SQL 映射，输出页码证据、质量报告和待复核问题。未知业务编码不要编造。

PDF 由任务提供，可放入本文件夹，也可传入外部文件的完整路径。本包不附带父目录中的历史财报或旧任务结果。

## 目录约定

| 路径 | 用途 | 纳入 Git |
| --- | --- | --- |
| `CLAUDE.md`、`AGENTS.md` | 宿主入口和项目规则 | 是 |
| `.claude/agents/` | 财报子智能体定义 | 是 |
| `harness/financial-report/` | 共享规则、三份 SQL、映射、脚本和测试 | 是 |
| `inputs/` | 用户提供的 PDF，可按需创建 | 否 |
| `runs/<任务名>/` | 解析、候选、质量检查与导出结果 | 否 |
| `example/` | 本机已有的运行样例，仅本地保留 | 否 |
| `dist/` | 可迁移源码 ZIP，由打包命令生成 | 否 |
| `.venv/` | 本机辅助 Python 环境 | 否 |

任务输出目录统一指定到 `runs/<任务名>/`；分享源码时无需包含运行结果。已有本地样例未在本次仓库整理中进行数据正确性复核，不作为官方验收基线。

## 迁移和打包

复制整个项目到另一位置后，运行 `setup.ps1` 重建辅助环境；分享时可排除 `.venv/`、`runs/`、`tmp/` 和本地凭据。规则文件随项目携带，宿主登录和 MinerU Token 需由目标电脑提供。

生成仅含源码、规则和参考资料的压缩包：

```powershell
.\.venv\Scripts\python.exe -I -X utf8 harness\financial-report\scripts\package_harness.py
```

压缩包生成到 `dist/financial-report-harness.zip`，不再使用 `output/harness/`。GitHub 仓库保存源码，ZIP 在需要分享文件包时本地生成。

## 验证

```powershell
.\.venv\Scripts\python.exe -I -X utf8 harness\financial-report\tests\test_harness.py
```

离线测试使用模拟 API 响应。真实 MinerU 在线解析需要有效 Token；离线测试通过不代表已验证在线服务或财报识别准确率。

完整命令说明见 [Harness 使用说明](harness/financial-report/README.md)，实际测试边界见 [验证记录](harness/financial-report/verification.md)。
