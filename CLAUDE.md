# 财报处理项目

本文件所在目录就是独立项目根目录。会话开始时读取本目录 `AGENTS.md` 和 `harness/financial-report/SKILL.md`，按共享规则调用本目录的 `run.ps1`。所有必需规则、参考 SQL 和脚本均在本目录内，不依赖父目录的历史文件。

首次运行缺少脚本依赖或迁移后执行 `setup.ps1`。PDF 解析只调用 MinerU 在线 API，不安装本地 MinerU 或下载模型。MinerU 使用独立 `MINERU_API_TOKEN`，Claude 的调用由宿主负责。财报业务规则只维护在共享 Harness 中。

可委派给 `financial-report` 子智能体，明确传入 PDF、处理范围和独立输出目录。

任务结果写入 `runs/<任务名>/`，源码压缩包写入 `dist/`。本机 `example/` 是历史运行样例，不自动作为已复核数据或编码规则依据。不要将本地环境、PDF、任务结果或凭据提交到 Git。
