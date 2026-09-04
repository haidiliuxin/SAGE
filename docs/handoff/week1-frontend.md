# 第一周前端合并交接

## 本次修复

- `frontend/src/App.tsx` 保留关键词、年份的原始文本状态，不在每次键入时切分，避免逗号被删除及条目拼接。
- `frontend/src/context-input.ts` 在提交时解析列表，支持中英文逗号、空列表和尾随分隔符；非法年份明确报错。
- 结果摘要移除固定的“Context 收益最高”结论，仅使用后端实际返回的候选数和恢复数；策略统计仍在列表中展示。
- 保留前一提交的修复：前端不再自动切换本地 Mock，后端离线直接显示错误；开发环境 CORS 支持 5173。

## 启动与验证

在仓库根目录启动后端：

```powershell
python -m uvicorn sage_pass.main:app --app-dir src --reload
```

在 `frontend` 目录运行：

```powershell
npm ci
npm test
npm run build
npm run dev
```

默认前端地址为 `http://127.0.0.1:5173`，通过 Vite 代理访问 8000 端口后端；可选直连配置见前端 README。

本次验证：前端 6 项回归测试通过，后端 15 项测试通过，TypeScript/Vite 生产构建和 Python 编译检查通过。测试工具会输出弃用提示，不影响当前测试结果。

## 验收要点

1. 在关键词中逐字输入 `alpha,beta`、年份中输入 `2024，2025`，分隔符保持可见；提交后的数组包含两个独立元素。
2. 清空上下文再运行任务，仅 S1 的结果不再声称 Context 收益最高。
3. 后端离线时界面显示错误，不展示本地伪造的成功结果。

本版本仍通过后端 Mock Executor 演示，不执行真实口令恢复；本次未增加策略收益排名算法。
