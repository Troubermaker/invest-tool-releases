# 发布流程操作指南

> 一条命令完成构建、Cython 保护、打包、签名校验、发布说明、push 清单、上传 zip 到 Gitee Releases。

---

## 🚀 TL;DR — 最常用命令

```powershell
# 完整发布：升 patch + 全套构建 + 自动 push latest.json + 自动上传 zip + 打 git tag
venv\Scripts\python.exe release.py --bump patch --publish --git-tag
```

跑完会看到：

```
✓ latest.json push 到公开仓库
✓ zip 上传到 Gitee Releases（API）

🎉 全自动发布完成！用户 app 下次启动即检测到 v0.1.x
```

---

## 🧰 一次性配置（首次发版前必做）

### 1️⃣ 配置发布仓库信息

编辑 [`update_config.py`](../update_config.py)：

```python
GITEE_USER = "luckyforever666"        # 你的 Gitee 用户名
RELEASE_REPO = "invest-tool-releases"  # 公开发布仓库名
RELEASE_BRANCH = "master"
RELEASE_REPO_LOCAL_PATH = r"D:\Project\invest-tool-releases"  # 本地 clone 路径
```

### 2️⃣ Gitee 上创建公开发布仓库

去 https://gitee.com/projects/new 新建一个**公开**仓库 `invest-tool-releases`（勾选「使用 Readme 文件初始化」）。

### 3️⃣ 本地 clone 这个公开仓库

```powershell
cd D:\Project
git clone https://gitee.com/luckyforever666/invest-tool-releases.git
```

完了应该有 `D:\Project\invest-tool-releases\` 目录，跟 `update_config.RELEASE_REPO_LOCAL_PATH` 一致。

### 4️⃣ 生成 Gitee Personal Access Token

去 https://gitee.com/profile/personal_access_tokens：

1. 点「生成新令牌」
2. 描述填 `invest_tool release`（或随便）
3. 权限**只勾选**：✅ `projects`（创建/编辑 release 需要）
4. 点生成 → **立即复制 token**（只显示一次！）

### 5️⃣ 把 token 填进 release_secret.py

```powershell
copy release_secret.py.example release_secret.py
notepad release_secret.py
```

把 `GITEE_TOKEN = "REPLACE_..."` 改成你的真实 token。文件已 `.gitignore`，不会进版本库。

> 或用环境变量（优先级更高）：
> ```powershell
> [System.Environment]::SetEnvironmentVariable('GITEE_TOKEN', 'xxxxx', 'User')
> # 重启终端生效
> ```

---

## 📦 标准发版流程

### 步骤 1：发版前自检

- [ ] 所有改动已 commit（`git status` 应该 clean）
- [ ] 本机 `python main.py` 跑通主要功能
- [ ] [`services/license_service.py`](../services/license_service.py) 的 `SECRET` 已是发布用值（绝不是 `"3146b6a8..."` 占位符）
- [ ] `vendor/openssl/` 目录里有 4 个文件（如缺：`python vendor_openssl.py`）

### 步骤 2：选升版级别

| 改动类型 | 命令 | 例子 |
|---|---|---|
| 修 bug、改文案、性能优化 | `--bump patch` | 0.1.5 → 0.1.6 |
| 加新功能、新 tab、新接口 | `--bump minor` | 0.1.5 → 0.2.0 |
| 数据库 schema 变 / 收费策略变 | `--bump major` | 0.1.5 → 1.0.0 |

### 步骤 3：跑发版命令

```powershell
venv\Scripts\python.exe release.py --bump patch --publish --git-tag
```

它会**自动**做：

| # | 动作 | 用了什么 |
|---|---|---|
| 1 | 升 `version.py` 版本号 | bump_version |
| 2 | 调用 `build.py` 全套构建 | 前端 + Cython + PyInstaller + OpenSSL 替换 |
| 3 | 把 `dist/invest_tool/` 打成 zip | zipfile |
| 4 | 算 SHA256 | hashlib |
| 5 | 从 git log 抓自上个 tag 起的 commit 生成发布说明（自动过滤 merge / chore / wip 噪音）| collect_recent_commits |
| 6 | 生成 `release/latest.json` | write_latest_json |
| 7 | **复制 latest.json 到本地公开仓库** + git pull → add → commit → push | publish_manifest_to_release_repo |
| 8 | **通过 Gitee API 创建 release（tag = vX.Y.Z）+ 上传 zip 附件** | upload_zip_to_gitee_release |
| 9 | 主源码仓库打 git tag `vX.Y.Z`（本地，不 push） | make_git_tag |

### 步骤 4：检查产物

```powershell
ls release\
type release\invest_tool_v0.1.6_RELEASE_NOTES.txt
```

如果发布说明里 commit 列表写得不理想（你 commit message 写得乱），可以**手动改** `RELEASE_NOTES.txt` —— 它就是给最终用户看的。

⚠️ 但 `latest.json` 已经 push 到公开仓库了。要改用户能看到的发布说明，需要**手动**改 `D:\Project\invest-tool-releases\latest.json` 的 `release_notes` 字段，再 push。

### 步骤 5：把版本号 commit 推上去

`release.py --bump` 改了 `version.py` 但**不会自动 commit 主仓库**。手动：

```powershell
git add version.py
git commit -m "chore: bump to v0.1.6"
git push origin master
git push origin v0.1.6     # 推 tag
```

### 步骤 6：验证用户侧能收到更新

- 等 1-2 分钟（Gitee CDN 缓存）
- 启动一个**旧版本** invest_tool.exe
- 5 秒内顶部应该弹出"🔔 新版本可用 v0.1.5 → v0.1.6"
- 点查看详情 → 立即更新 → 走完整下载 + 重启流程

---

## ⚡ 各种场景的命令组合

### 场景：日常发版（一条命令搞定）

```powershell
venv\Scripts\python.exe release.py --bump patch --publish --git-tag
```

### 场景：不自动上传，只生成产物

```powershell
venv\Scripts\python.exe release.py --bump patch --git-tag
```

之后手动操作（参考"手动发布"章节）。

### 场景：改了一行代码想验证打包能跑通

```powershell
# 不升版本（避免污染版本号），跳过前端节省时间
venv\Scripts\python.exe release.py --skip-front
```

### 场景：dist/ 已经构建好了，只想重打 zip

```powershell
venv\Scripts\python.exe release.py --no-build
```

适用：发现 zip 损坏，或手动改了 dist/ 里某个文件想重新签。

### 场景：开发自测（不想跑 Cython 编译）

```powershell
venv\Scripts\python.exe release.py --skip-cython
```

⚠️ **绝对不要发布 `--skip-cython` 的产物**！它的 SECRET / Cookies 是明文 .pyc，立即可解。

### 场景：不知道怎么开始，最稳的命令

```powershell
venv\Scripts\python.exe release.py
```

不升版、不打 tag、不 publish、用当前版本号完整打一次。安全无副作用。

---

## 🛠 手动发布（不走 --publish 时）

如果你出于调试 / 想审视产物的原因不用 `--publish`：

### A. 手动 push latest.json

```powershell
cd D:\Project\invest-tool-releases
git pull
copy ..\invest_tool\release\latest.json .
git add latest.json
git commit -m "release v0.1.6"
git push
```

### B. 手动上传 zip 到 Gitee Releases

去 https://gitee.com/luckyforever666/invest-tool-releases/releases

1. 点「创建发布版本」
2. tag：`v0.1.6`（必须跟 `latest.json` 里的 `version` 一致，前面加 `v`）
3. 标题：`v0.1.6`
4. 描述：把 `release/invest_tool_v0.1.6_RELEASE_NOTES.txt` 内容粘贴
5. 附件：拖入 `release/invest_tool_v0.1.6_*.zip`
6. 点创建

---

## 🆘 常见错误 & 处理

### Q1：跑了 `--bump patch` 但构建失败

`version.py` 已经升级，但产物没生成。**回退**：
```powershell
git checkout version.py
```
然后修构建错误，再跑。

### Q2：`--publish` 提示「Gitee 上 v0.1.6 release 已存在」

```powershell
# 去 https://gitee.com/.../releases 网页删除该 release，再重跑：
venv\Scripts\python.exe release.py --no-build --publish
```

### Q3：`--publish` 提示「未找到 Gitee token」

参考"一次性配置 → 4️⃣ + 5️⃣"——配置 `release_secret.py` 或环境变量。

### Q4：`upload_zip_to_gitee_release` 上传超时

zip 太大（>50MB）+ 国内网慢可能超时（脚本超时给的 5 分钟）。重试一次：
```powershell
# 先到网页删掉那个 release（已创建但 zip 没传上去）
venv\Scripts\python.exe release.py --no-build --publish
```

或手动到网页补传 zip。

### Q5：git tag 已经存在

```powershell
git tag -d v0.1.6                   # 删本地 tag
git push origin :refs/tags/v0.1.6   # 删远程 tag（如已推送）
# 然后再次运行 release.py --git-tag
```

### Q6：发布说明的 commit 列表是空的

意味着自上个 git tag 起没有新 commit。要么忘了 commit，要么真没改东西。

### Q7：每次发布都要跑完整 build 吗？太慢

完整 build 大约 1-3 分钟（前端 30s + Cython 10s + PyInstaller 1-2min）。
- 前端没改：`--skip-front` 省 30s
- 后端没改：先单独 `cd frontend && npm run build`，再用 `release.py --skip-front`

### Q8：Token 泄露了怎么办

立刻去 https://gitee.com/profile/personal_access_tokens 撤销那个 token，重新生成一个，更新 `release_secret.py`。

---

## 🗂 相关脚本一览

| 文件 | 用途 | 调用方式 |
|---|---|---|
| [`release.py`](../release.py) | **发布主入口** | 你运行这个 |
| [`build.py`](../build.py) | 构建产物（前端 + Cython + PyInstaller）| 内部调用，也可单独跑 |
| [`setup_cython.py`](../setup_cython.py) | 编译关键模块为 .pyd | 由 build.py 调用 |
| [`vendor_openssl.py`](../vendor_openssl.py) | 一次性下载 python.org 的 OpenSSL DLL | 装机后跑一次 |
| [`version.py`](../version.py) | 版本号唯一真相源 | 由 release.py 读写 |
| [`update_config.py`](../update_config.py) | Gitee 仓库配置（公开 URL）| 一次性配置 |
| `release_secret.py` | Gitee Personal Access Token（gitignore）| 一次性配置 |
| [`gen_license.py`](../gen_license.py) | 签发激活码 | 按需运行 |

---

## 🎯 一句话总结

**日常发布只需一条命令**：

```powershell
venv\Scripts\python.exe release.py --bump patch --publish --git-tag
```

剩下都是它自动跑。出错的话查上面的 Q1-Q8。
