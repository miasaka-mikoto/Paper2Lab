# Paper2Lab（论文复现实验工厂）

Paper2Lab 是一个独立的 Windows 优先桌面研究工作台，把合法获得的论文逐步转换为可追踪的实验工程：

`论文 → 章节 → Claim → Hypothesis → Blueprint → 实验骨架 → Mock 运行 → 结果比较 → 报告`

当前版本完全离线运行，开发阶段只使用 `RuleBasedExtractor` 和 `MockLLMProvider`，不会自动调用任何收费模型 API。
Provider registry 已为 `openai`、`anthropic`、`google`、`local-ollama`、`custom-http` 预留名称；这些入口在离线版本中会明确抛出未实现错误，不会偷偷发起网络请求。

## 快速启动

需要 Python 3.10+（Windows 上建议 3.12）。标准库即可运行；若希望解析复杂 PDF，可额外安装 `pypdf` 或 `PyMuPDF`。

```bash
python main.py
```

命令行端到端演示：

```bash
python scripts/run_demo.py
```

运行测试：

```bash
python -m pytest -q
```

服务层（不启动窗口）的烟测：

```bash
python -m paper2lab --service-self-test
```

完整验收并写出 JSON/Markdown 测试报告：

```bash
python scripts/run_acceptance.py
```

## Windows 打包

在 Windows PowerShell 中运行：

```powershell
./build_windows.ps1
```

脚本会使用 PyInstaller 生成 `dist/Paper2Lab.exe`。本 Linux 开发环境不能可靠地产生原生 Windows PE 文件，因此仓库同时提供可复现的 Windows 打包脚本，而非伪造一个不可执行的 `.exe`。

## 目录

```text
paper2lab/       核心模型、存储、解析、实验、报告
sample/          虚构 Sample Paper（无版权论文）
tests/           单元与端到端验收
artifacts/       测试报告和截图
scripts/         演示与辅助命令
main.py          桌面入口
```

核心服务也可以在无图形环境中调用：

```python
from paper2lab import Paper2LabService

service = Paper2LabService()
paper = service.create_sample_paper()
claims = service.extract_claims(paper.paper_id)
blueprint = service.create_blueprint(paper.paper_id, claims[0].claim_id)
service.generate_skeleton(paper.paper_id, blueprint.blueprint_id)
service.run_experiment(paper.paper_id, blueprint.blueprint_id)
service.generate_report(paper.paper_id, blueprint.blueprint_id)
```

生成的实验目录可以交给 `ExperimentRunner` 做真实子进程编排：
`start` / `wait` / `pause` / `resume` / `cancel` / `retry` 均为本地操作，
每次运行会保存 stdout、stderr、runtime、config、seed、result 和 environment。
桌面工作台底部的 Runs / Logs 面板提供对应的运行控制；内置验收流程默认使用
明确标注的 `MockExperimentRunner`，不会把 mock 数字冒充论文复现证据。

如果需要让生成的骨架真正经过子进程边界运行，可使用服务层的异步入口：

```python
run = service.start_experiment(paper.paper_id, blueprint.blueprint_id, seed=7)
# 运行期间可调用 service.pause_run(run.run_id)、resume_run 或 cancel_run
finished = service.wait_experiment(run.run_id, timeout=60)
```

`run_experiment()` 仍保留为快速、确定性的 Mock 流程；`start_experiment()`
则运行本地生成目录中的 `run.py`，并把每次尝试写入独立的 `runs/` 记录。

Paper Library 的 Title、Authors、Year、Venue、Abstract、Keywords、Notes、Tags
和 Local Path 都可通过服务层 `update_metadata()` 或桌面 Metadata JSON 面板编辑；
手动章节正文、Claim、Blueprint、公式/伪代码说明也会进入同一个可迁移项目文件。

所有项目数据默认保存到 Windows 用户数据目录下的 `Paper2Lab/`（可用
`PAPER2LAB_WORKSPACE` 覆盖）；也可以在界面中选择工作区。

## 设计边界

- 不声称自动还原论文中不存在的实现；代码骨架中缺失部分会标记 `TODO`。
- “论文结果”与“本地结果”始终分开保存，并显示绝对/相对差异。
- 可复现性分数只依据记录字段计算，不判断论文真实性。
- 示例论文是软件自行生成的虚构内容，便于完整验收。
- `artifacts/paper2lab_ui_preview.png` 是无显示器环境生成的布局预览；实际运行界面由 Tkinter 绘制。

