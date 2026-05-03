# 小红书数据看板（作品集 Demo）

Streamlit 看板：上传笔记 CSV 展示表格与封面，并调用 **DeepSeek** 生成内容画像与选题建议。  
可选功能：在本机安装浏览器组件后，支持从主页链接抓取笔记（见下文）。

**在线试用**：代码托管在 GitHub，公开链接由 [Streamlit Community Cloud](https://streamlit.io/cloud) 部署生成（形如 `https://<app名>.streamlit.app`），**不是** GitHub 直接提供网页。

---

## 本地运行

```bash
cd xiaohongshu_dashboard
python -m venv .venv
# Windows: .\.venv\Scripts\activate
pip install -r requirements.txt
# 需要「浏览器抓取」时再执行：
pip install -r requirements-local.txt
```

复制 `.env.example` 为 `.env`，填入 `DEEPSEEK_API_KEY`，然后：

```bash
streamlit run app.py
```

---

## 部署到公网（GitHub + Streamlit Cloud）

### 1. 推到 GitHub

1. 在 GitHub 新建空仓库（不要勾选自动添加 README，避免推送冲突）。
2. 在本项目目录执行（把 `YOUR_USER` 和 `YOUR_REPO` 换成你的）：

```bash
cd xiaohongshu_dashboard
git init
git add .
git commit -m "Initial commit: Xiaohongshu dashboard portfolio"
git branch -M main
git remote add origin https://github.com/YOUR_USER/YOUR_REPO.git
git push -u origin main
```

**注意**：`.gitignore` 已排除 `.env` 与 `.venv`，**切勿**把真实 API Key 提交进仓库。

### 2. 连接 Streamlit Community Cloud

1. 打开 [share.streamlit.io](https://share.streamlit.io) ，用 GitHub 登录。
2. **New app** → 选择你的仓库、`main` 分支、主文件 **`app.py`**。
3. **Advanced settings** 里 Python 版本可选 3.11–3.12（与本地一致即可）。
4. 部署完成后，控制台会给出 **`https://xxx.streamlit.app`** 链接，可写进作品集。

### 3. 配置 Secrets（否则 AI 无法调用）

在 Streamlit Cloud 应用页面：**Settings（齿轮）→ Secrets**，粘贴 TOML，例如：

```toml
DEEPSEEK_API_KEY = "sk-xxxxxxxx"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"
```

保存后 **Reboot app**。应用内会从 `st.secrets` 读取（与本地 `.env` 二选一）。

### 4. 云端与本地差异

| 功能 | Streamlit Cloud | 本机（装齐依赖） |
|------|-----------------|------------------|
| 上传 CSV + 表格展示 | ✅ | ✅ |
| DeepSeek 选题 | ✅（需配置 Secrets） | ✅（`.env`） |
| 浏览器抓取主页 | ❌ 未安装 DrissionPage | ✅ `pip install -r requirements-local.txt` |

---

## CSV 列名说明

至少包含 **「标题」或「封面/封面链接」** 之一；可选：点赞、收藏、评论等（支持常见中英文列名）。

---

## 免责声明

抓取与数据使用须遵守小红书平台规则与当地法律；本项目仅供学习展示。
