# 发布流程操作指南

> 一条命令完成构建、保护、打包、签名校验、生成发布说明。

---

## TL;DR — 最常用命令

```powershell
# 完整发布：升 patch + 构建 + 打 zip + 自动 push latest.json + git tag
venv\Scripts\python.exe release.py --bump patch --publish --git-tag

# 不自动 publish，只生成 release/ 产物（之后手动 push）
venv\Scripts\python.exe release.py --bump patch --git-tag

# 不升版本，重新打包一次
venv\Scripts\python.exe release.py

# 跳过前端构建省 30s
venv\Scripts\python.exe release.py --skip-front
```

**`--publish` 是什么**：自动把 `release/latest.json` 复制到你预先 clone 好的公开发布仓库，然后 `git pull/add/commit/push` 一条龙。配置见 [`update_config.py`](../update_config.py) 的 `RELEASE_REPO_LOCAL_PATH`。zip 仍需手动到 Gitee 网页上传到 Releases（zip 太大不走 git）。

发布完毕后产物在 `release/` 下：
```
release/
  invest_tool_v0.1.1_2026-04-27.zip          ← 用户拿到的
  invest_tool_v0.1.1_2026-04-27.zip.sha256   ← 校验和
  invest_tool_v0.1.1_RELEASE_NOTES.txt       ← 发版说明
```

---

## 完整发布流程（推荐顺序）

### 1. 发布前自检

- [ ] 所有改动已 commit（`git status` 应该 clean）
- [ ] 在本机 `venv\Scripts\python.exe main.py` 跑通所有功能
- [ ] 激活码逻辑可用（[docs/LICENSE_KEY_SETUP.md](LICENSE_KEY_SETUP.md)）
- [ ] [services/license_service.py](../services/license_service.py) 的 SECRET 已是发布用值
- [ ] CHANGELOG（如果有）已更新

### 2. 选择升版级别

按你这次改动的性质决定：

| 改动类型 | 命令 | 例子 |
|---|---|---|
| 修 bug、改文案、性能优化 | `--bump patch` | 0.1.0 → 0.1.1 |
| 加新功能、新 tab、新接口 | `--bump minor` | 0.1.0 → 0.2.0 |
| 数据库迁移 / API 不兼容 / 收费策略大变 | `--bump major` | 0.1.0 → 1.0.0 |

### 3. 跑发布命令

```powershell
venv\Scripts\python.exe release.py --bump patch --git-tag
```

它会自动：

1. 把 `version.py` 的版本号改成新的
2. 调 `build.py`（前端 → Cython 保护 → PyInstaller）
3. 把 `dist/invest_tool/` 打成 zip
4. 算 SHA256
5. 从 git log 抓"自上个 tag 起的所有提交"生成发布说明
6. 创建 `git tag v0.1.1`（本地，未推送）

### 4. 检查产物

```powershell
ls release/
type release\invest_tool_v0.1.1_RELEASE_NOTES.txt   # 看一下发布说明对不对
```

如果发布说明里的 commit 列表不理想（信息不清），可以在 `release/` 里**手动改**那个 `.txt` 文件——它本来就是给最终用户看的。

### 5. 推送 git tag（可选）

如果用 GitHub Releases 之类的发布渠道，推 tag：

```powershell
git push origin v0.1.1
git push origin master
```

如果没用 GitHub 公开发布，本地 tag 只是个版本快照，按需保留。

### 6. 把 commits 也推上去

```powershell
git add version.py
git commit -m "chore: bump to v0.1.1"
git push origin master
```

> ⚠️ `release.py --bump` 只改了 `version.py` 但**不会自动 commit**，你要手动提一下。如果反悔（想撤销升版），直接 `git checkout version.py` 即可。

### 7. 分发

把 `release/invest_tool_vX.Y.Z_DATE.zip` 通过你选择的渠道发给用户：

- 早期：微信传文件 / 网盘链接
- 中期：阿里云 OSS / 腾讯云 COS（有 CDN，不限速）
- 后期：自建下载页 / GitHub Releases

附上 `RELEASE_NOTES.txt` 内容（或截图）给用户看版本说明。

---

## 各种场景的命令组合

### 场景：你改了一行代码想验证打包能跑通

```powershell
# 不升版本（避免污染版本号），跳过前端节省时间
venv\Scripts\python.exe release.py --skip-front
```

产物会带当前版本号，多个连续验证打包会覆盖前一个 zip（同名）。

### 场景：仅前端改动，后端 / Cython 模块不变

```powershell
venv\Scripts\python.exe release.py --bump patch
# 仍然会全量打包；如果你想加速可以先单独 cd frontend && npm run build，
# 然后 release.py 检测到 frontend/dist 已存在不会重复 npm run build
```

### 场景：dist/ 已经构建好了，只想重打 zip

```powershell
venv\Scripts\python.exe release.py --no-build
```

适用：发现 zip 损坏、或者你手动改了 dist/ 里的某个文件想重新签名。

### 场景：开发自测（不想跑 Cython 编译）

```powershell
venv\Scripts\python.exe release.py --skip-cython
```

⚠️ **绝对不要发布 `--skip-cython` 的产物**。它的 SECRET / Cookies 是明文 .pyc，立即可解。

### 场景：不知道怎么开始，最稳的命令

```powershell
venv\Scripts\python.exe release.py
```

不升版、不打 tag、用当前版本号完整打一次。安全无副作用。

---

## 常见问题

### Q1：跑了 `--bump patch` 但构建失败怎么办？

`version.py` 已经升级，但发布产物没生成。**手动回退**：

```powershell
git checkout version.py
```

然后修复构建错误，再重新跑。

### Q2：git tag 已经存在了？

```powershell
git tag -d v0.1.1                # 删本地 tag
git push origin :refs/tags/v0.1.1   # 删远程 tag（如已推送）
# 然后再次运行 release.py --git-tag
```

### Q3：发布说明的 commit 列表是空的？

意味着自上个 git tag 起没有新 commit。要么你忘了 commit，要么真的没改东西。

### Q4：每次发布都要跑完整 build 吗？太慢

完整 build 大约 1-3 分钟（前端 30s + Cython 10s + PyInstaller 1-2min）。
- 前端没改：`--skip-front` 省 30s
- 不要 `--skip-cython`（除非确认是开发自测）

如果你真的频繁迭代，可以单独跑 `build.py` 配 `--dev`（带 console 看错误）调试，等收尾再跑 `release.py`。

### Q5：可以同时支持 32/64 位 / Win10/11 吗？

当前只针对 64 位 Windows。32 位需要单独的 venv（且 Python 自己也是 32 位的）。
理论上这个流程在 32 位 Python 上也能跑，但没测过。Win10 / Win11 共用同一份 zip 没问题。

### Q6：能改 zip 文件名格式吗？

[release.py](../release.py) 里搜 `zip_name = ` 改即可：

```python
zip_name = f"invest_tool_v{version}_{today}.zip"
```

改成你喜欢的格式，比如 `投资工具_v{version}.zip`。

---

## 相关脚本

| 文件 | 用途 | 调用方式 |
|---|---|---|
| [release.py](../release.py) | **发布主入口**，包装 build.py | 你运行这个 |
| [build.py](../build.py) | 构建产物（前端 + Cython + PyInstaller）| 内部调用，也可单独跑 |
| [setup_cython.py](../setup_cython.py) | 编译关键模块为 .pyd | 由 build.py 调用 |
| [version.py](../version.py) | 版本号唯一真相源 | 由 release.py 读写 |
| [gen_license.py](../gen_license.py) | 签发激活码（开发者本地用） | 你按需运行 |

---

**一句话**：日常发布只需记 `python release.py --bump patch --git-tag`；想搞清细节看本文。
