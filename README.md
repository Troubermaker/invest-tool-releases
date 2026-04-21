# Invest Tool - A智能量化投研助手

这是一个基于 Python (PyWebview) 和 Vue 3 开发的桌面端量化投研分析工具。支持 A 股核心指数实时行情、热门板块分析以及 AI 驱动的盘面解读。

## 项目结构

- `app.py` / `main.py`: 后端入口与窗口管理
- `fetcher.py`: 数据抓取逻辑 (API 接入)
- `api.py`: 前后端通信桥梁
- `db.py`: 本地数据库缓存管理
- `ai_service.py`: AI 分析服务集成
- `frontend/`: 基于 Vite + Vue 3 的前端代码

## 环境安装

### 1. 后端环境 (Python)

建议使用 Python 3.8+ 版本。

```bash
# 创建并激活虚拟环境 (可选)
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 2. 前端环境 (Node.js)

需要安装 Node.js (推荐 v18+)。

```bash
cd frontend
npm install
```

## 运行项目

### 开发模式

1. 启动前端开发服务器：
   ```bash
   cd frontend
   npm run dev
   ```

2. 运行 Python 应用：
   ```bash
   python main.py
   ```

### 运行说明

- 首次运行会自动创建 `invest_data.db` 本地数据文件。
- AI 功能需要在 `ai_service.py` 中配置您的 API Key。
- 市场数据通过 Sina/Tencent/EastMoney 等公开接口获取。

## 技术栈

- **后端**: Python, PyWebview, AkShare (行情数据), SQLite (缓存), Schedule (定时任务)
- **前端**: Vue 3, Vite, TailwindCSS, Lightweight Charts (专业金融图表)
- **AI**: 支持 OpenAI 兼容格式的模型接口

## 许可证

MIT License
