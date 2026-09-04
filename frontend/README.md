# SAGE-Pass Frontend

面向第一阶段联调的前端框架，覆盖创建任务、PRIR 分析、策略规划、Mock 执行、状态轮询和结果展示。

## 启动

```powershell
npm install
npm run dev
```

默认访问 `http://127.0.0.1:5173`。开发服务器会把 `/api` 转发到 `http://127.0.0.1:8000`。

## 联调配置

复制 `.env.example` 为 `.env.local`：

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
VITE_ENABLE_MOCK_FALLBACK=true
```

- 后端在线时，页面使用真实接口。
- 后端离线且 Mock fallback 开启时，页面在浏览器中模拟完整链路。
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
