# 政策融合 LLM 服务

基于 FastAPI 的政策文件智能融合服务，调用 LLM 对多个政策 MD 文件进行语义融合。

## 快速启动

```bash
# 1. 安装依赖
cd backend\融合
pip install -r requirements.txt

# 2. 配置环境变量
copy .env.example .env
# 编辑 .env 文件，填入 LLM API 地址和密钥

# 3. 启动服务
python app.py

# 服务默认运行在 http://0.0.0.0:8001
# API 文档: http://localhost:8001/docs
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/fusion/health` | 健康检查 |
| GET | `/api/fusion/info` | 服务信息（模型、提示词列表） |
| POST | `/api/fusion/llm-fuse` | 执行 LLM 政策融合 |

### POST /api/fusion/llm-fuse

请求体：

```json
{
  "directoryPath": "D:\\kejiju\\metadata\\重点政策融合数据\\国家高新技术企业认定",
  "policyTitle": "国家高新技术企业认定（可选）",
  "promptName": "ronghe1.md"
}
```

成功响应：

```json
{
  "success": true,
  "data": {
    "content": "## 基本信息\n...（融合后的完整 Markdown）",
    "outputPath": "D:\\...\\【融合版】xxx.md",
    "title": "国家高新技术企业认定",
    "filesCount": 3,
    "model": "qwen-32B"
  }
}
```

## 部署

### 方式一：直接运行

```bash
python app.py
```

### 方式二：使用 uvicorn

```bash
uvicorn app:app --host 0.0.0.0 --port 8001
```

### 方式三：注册为 Windows 服务

使用 `nssm` 或 `winsw` 将 `python app.py` 注册为 Windows 服务。

## 目录结构

```
backend\融合\
├── app.py                 # FastAPI 应用主文件
├── requirements.txt       # Python 依赖
├── .env                   # LLM 配置（需手动创建）
├── .env.example           # 配置模板
├── README.md              # 本文件
└── prompts\               # 提示词文件
    ├── ronghe.md
    └── ronghe1.md
```
