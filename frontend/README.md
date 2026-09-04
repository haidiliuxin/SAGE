# SAGE-Pass Frontend

面向第一阶段联调的前端框架，覆盖创建任务、PRIR 分析、策略规划、Mock 执行、状态轮询和结果展示。

## 启动

```powershell
npm ci
npm run dev
```

默认访问 `http://127.0.0.1:5173`。开发服务器会把 `/api` 转发到 `http://127.0.0.1:8000`。

## 联调配置

复制 `.env.example` 为 `.env.local`：

```env
VITE_API_BASE_URL=
```

- 留空时由 Vite 将 `/health` 和 `/api` 代理到 `http://127.0.0.1:8000`。
- 也可设置为 `http://127.0.0.1:8000` 让浏览器直连；后端默认允许 5173 端口跨域访问。
- 前端始终使用真实后端数据。后端不可用时直接显示接口错误，不会生成本地模拟任务或结果。
- 文件上传暂按 `POST /api/files`、字段名 `file` 对接；如后端接口不同，只需修改 `src/api/client.ts` 中的 `uploadFile`。

## 已覆盖接口

- `POST /api/tasks`
- `POST /api/tasks/{task_id}/analyze`
- `POST /api/tasks/{task_id}/plan`
- `POST /api/tasks/{task_id}/execute`
- `GET /api/runs/{run_id}/status`
- `GET /api/runs/{run_id}/result`
- `POST /api/files`（依据现有 README 补充的适配入口）

本界面仅用于经过授权的离线口令安全评测。

## 回归验证

```powershell
npm ci
npm test
npm run build
```

`npm test` 使用 Node 测试运行器执行 6 项前端回归测试，覆盖原始文本输入、提交解析、非法年份、实际结果摘要和断网错误处理。测试依赖仅用于开发，不参与生产运行。

- 关键词和年份支持中文/英文逗号；输入过程中保留原文，提交时去除首尾空白和空条目。
- 年份必须为正整数，非法值会显示错误，不发送创建任务请求。
- 结果页只展示后端返回的统计值，不推断未经计算的策略优劣。

合并交接说明见 `../docs/handoff/week1-frontend.md`。
