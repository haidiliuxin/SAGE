# 第 2 周甲任务交接

交接日期：2026-09-05
交付版本：0.2.0
范围：Hashcat 适配器（启动/停止/超时/结果解析）、ZIP（WinZip AES）真实接入、`real` 执行模式链路

## 交付结论

甲负责的第 2 周前半段任务已完成并经过检查：Hashcat 适配器支持启动、停止（取消）、时间预算自动停止与恢复结果解析；ZIP 文件目标通过 zip2john 提取 `$zip2$` 密文（WinZip AES，hashcat 13600）后进入真实执行；后端新增 `real` 执行模式，与既有 mock 链路共用任务状态机与 `StrategyRunModel`。外部工具为可配置依赖，自动化测试使用可控仿真可执行程序验证，不依赖本机安装真实 hashcat/zip2john。

## 主要交付物

- `src/sage_pass/hashcat_adapter.py`：`HashcatAdapter` / `HashcatHandle`。以 `--hash-type/--attack-mode 0/--runtime/--session/--outfile-format 1,2` 调用 hashcat，提供 `start/poll/wait/stop/close/live_progress`，解析恢复文件（含 `$HEX[...]`）与状态进度，统一为 `HashcatResult`；启动失败返回 `503`，非法输入返回 `422`。
- `src/sage_pass/zip_adapter.py`：`ZipHashExtractor`。调用 zip2john 从 ZIP 提取 WinZip AES 目标（正则收敛 `$zip2$...*$/zip2$`），传统 PKZIP 明确报不支持（提示 hashcat 13600），超时/未安装/无目标分别给出 `504/503/422`。
- `src/sage_pass/real_executor.py`：真实执行器与进程内 run registry。按策略计划逐策略切片候选并启动 Hashcat，后台线程执行/轮询/回收；提供 `status/result/cancel_task_runs/shutdown`；结束后回写 `StrategyRunModel` 并把任务推进到终态。
- `src/sage_pass/routes.py` + `main.py`：`POST /execute` 支持 `mode: real`；run status/result 按 registry 分派 mock/real；`PATCH /status cancelled` 会停止真实 Hashcat 进程；应用生命周期创建并关闭真实执行器。
- `src/sage_pass/analyzer.py`：ZIP 分析接入 zip2john 提取（成功 → `zip-aes`/medium；失败 → 降级 warning，不阻断）。
- `src/sage_pass/schemas.py`：`ExecutionRequest` 增加 `candidates/hashcat_mode/timeout`；`RunResult` 增加 `recovered_items/message`。
- `tests/test_adapters_week2.py`、`tests/test_week2_real_execution.py`、`tests/sim_binaries.py`：适配器与真实链路的自动化验证。
- `docs/API.md`、`docs/openapi.json`、`.env.example`：接口与配置文档同步更新。

## 与第一周的差异（行为变化）

- `POST /execute` 不再只允许 `mock`：`mode: real` 时运行真实 Hashcat。
- ZIP 分析不再停留在“第一周仅元数据”：安装 zip2john 后能识别 WinZip AES 并给出 `algorithm=zip-aes`；未安装时**仍然返回 200** 并在 `warnings` 中降级说明（不破坏 mock 链路）。
- `RunResult` 对真实执行返回 `recovered_items`（去重，`target`+`plaintext`）；mock 结果保持向后兼容。

## 环境配置

```text
SAGE_HASHCAT_PATH=hashcat      # 或完整路径
SAGE_ZIP2JOHN_PATH=zip2john    # 或完整路径
```

真实执行需要在本机安装并让后端能找到 hashcat（ZIP 任务还需要 John the Ripper 的 zip2john）。Windows 演示建议：

```powershell
# hashcat: 解压官方 release 后加入 PATH，或把 SAGE_HASHCAT_PATH 指到 hashcat.exe
# zip2john: 来自 John the Ripper jumbo（zip2john.exe），同样加入 PATH
```

## 乙/丙接入说明

1. 候选生成（乙 S1 Baseline、S2 Rule 等）的输出可直接作为 `execute` 请求的 `candidates`；候选预算语义与计划中每个策略的 `candidate_budget` 对齐（按顺序切片）。
2. 联调示例：`analyze → plan → execute(mode=real, candidates=[...]) → runs/{id}/status 轮询 → runs/{id}/result`。
3. `result` 中的 `recovered_items[].plaintext` 即为页面可直接展示的恢复口令。
4. 时间预算用尽或候选耗尽会自动停止，无需前端额外处理；`409 RUN_IN_PROGRESS` 表示还在执行。

## 验证结果

- `pytest`：39 项通过（原 16 项 + 适配器 16 项 + 真实链路 7 项）；
- `compileall`：源码与测试字节码编译通过；
- OpenAPI：`docs/openapi.json` 与运行时 `app.openapi()` 一致；
- 覆盖点：恢复解析（含 `$HEX[]`）、未恢复、候选预算截断、时间预算自动停止、取消停止、zip2john 成功/传统 PKZIP/空输出/未安装、真实执行 hash 与 ZIP 端到端、取消与 409 语义。

## 当前限制与下一步

- 真实运行的运行态保存在进程内存中；进程重启后未完成 run 无法恢复（第三周“任务状态持久化和异常恢复”处理）。
- ZIP 仅支持 WinZip AES（`$zip2$`，hashcat 13600）；传统 PKZIP（`$pkzip2$`）给出明确错误，PDF/Office 尚未接入真实解析。
- 候选生成（S1～S4 规则、PCFG、上下文词组合）、LLM 规划与动态调度由乙/丙在后续接入，本次未越界实现。
- 真实工具冒烟已在开发机完成：hashcat 7.1.2 与 John the Ripper 1.9.0-jumbo-1（zip2john）解压于 `F:\SA\tools`；后端 `real` 链路真实恢复 MD5（明文 `P@ssw0rd!2026`）与 WinZip-AES ZIP（密码 `SagePass2026`，并用恢复密码成功解包）。hashcat 需以其安装目录作为进程 cwd（OpenCL 内核目录按 cwd 解析），适配器已自动处理（见 `hashcat_adapter.py::_executable_dir`）。
- 本系统仅用于获得授权的离线安全评测。
