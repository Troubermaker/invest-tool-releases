# 激活码 SECRET 配置 & 签发操作指南

> 本文档面向**开发者本人**。每次发版前若要让历史激活码失效（如担心 SECRET 已泄露），按本指南重做一遍。

---

## 一、SECRET 是什么 / 为什么重要

激活码生成与校验都依赖一个 32 字节随机密钥（SECRET）：

- **签发**：开发者用 SECRET 对随机 serial 算 HMAC-SHA256 → 得到带签名的激活码
- **校验**：客户端 app 启动时用同一个 SECRET 重算 HMAC，签名匹配才算激活有效

**风险**：
- SECRET 一旦被攻击者拿到 → 任何人都能自行生成有效激活码 → 付费门禁失效
- SECRET 一旦被你自己改动 → 之前发出去的激活码**全部立即失效**（已付费用户也进不去）

> **决策提示**：除非确认 SECRET 已泄露，否则**绝不要轻易改**。改前一定先通知所有已付费用户重新发码。

---

## 二、首次设置 SECRET（发布前必做）

### 1. 生成新的随机 SECRET

打开 PowerShell（项目根目录）：

```powershell
venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
```

输出形如（每次都不同）：
```
7c1f9b3a2e5d8061f4a72e3c9b8f1d046a5b7c3e9f2d8a16b07c4e3f5a8d2b91
```

复制这一长串 64 字符 hex。

### 2. 替换源码里的占位 SECRET

打开 [services/license_service.py](../services/license_service.py)，找到（约第 30 行）：

```python
SECRET = bytes.fromhex(
    "8a4f3c9e1d6b7f2a5c0e8d3b9f1a4c6e2d8b5f7a3c9e1d6b7f2a5c0e8d3b9f1a"
)
```

把里面的 hex 字符串替换成第 1 步生成的新值，保存。

### 3. 重要：备份这个 SECRET

把 SECRET 存到 **三个不会一起丢的地方**：

- [ ] 1Password / Bitwarden 等密码管理器（推荐）
- [ ] 本地一个加密压缩包（带强密码）
- [ ] 离线 U 盘 / 纸质打印一份锁进抽屉

> **丢失 SECRET = 全部已发激活码失效，无法恢复**。

### 4. 验证替换是否生效

```powershell
# 重置当前激活态
venv\Scripts\python.exe -c "from services import license_service; license_service.deactivate()"

# 生成一个新激活码
venv\Scripts\python.exe gen_license.py

# 启动 app（dev 模式）
npm --prefix frontend run dev
# 另开一个终端：
venv\Scripts\python.exe main.py

# 用刚生成的激活码激活 → 应该成功进入主界面
```

---

## 三、批量签发激活码

### 命令

```powershell
# 生成 1 个
venv\Scripts\python.exe gen_license.py

# 生成 10 个
venv\Scripts\python.exe gen_license.py 10

# 生成 50 个 + CSV 格式（带序号、签发时间）
venv\Scripts\python.exe gen_license.py 50 --csv > codes_2026-04-27.csv
```

### CSV 输出示例

```
serial,code,issued_at
1,K3M9-H7XQ-4N2Q-PLR1,2026-04-27T16:23:12
2,B5T8-W2RZ-J6CV-NPK3,2026-04-27T16:23:12
3,XR4F-M8ND-Q2HP-LK7T,2026-04-27T16:23:12
...
```

### 推荐工作流

1. **批量生成**到 CSV（一次发 50-100 个备用）
2. **导入 Excel** 加几列：
   | serial | code | issued_at | 接收人 | 收款金额 | 收款时间 | 状态 |
   |--------|------|-----------|--------|----------|----------|------|
   | 1 | K3M9-H7XQ-4N2Q-PLR1 | 2026-04-27 | 张三/wx_xxx | ¥99 | 2026-04-28 | 已发出 |
   | 2 | B5T8-W2RZ-J6CV-NPK3 | 2026-04-27 | （未发） | | | 备用 |
3. **每次售出**：从未发列里挑一个，标记接收人、金额、时间
4. **售后查询**：用户问"我的激活码忘了"，按接收人查 Excel 即可

> 这份 Excel 是你的**资产档案**，比代码值钱。建议双备份（本地 + 云盘）。

---

## 四、安全清单（每次签发前自查）

- [ ] [.gitignore](../.gitignore) 已包含 `codes_*.csv`、`licenses/` 等规则
- [ ] 当前 git remote 是 **private repo**（用 `git remote -v` 确认）
- [ ] CSV 文件**不在** git status 里（用 `git status` 确认）
- [ ] SECRET 未推送到任何公开仓库 / 公开 gist / 截图
- [ ] 备份的 SECRET 至少在两个独立位置可访问

---

## 五、常见问题

### Q1：换了 SECRET，老用户怎么办？

**老激活码全部失效**。处理方式：
1. 提前通知所有已付费用户："因安全升级，需重新激活，您将收到新激活码"
2. 用新 SECRET 重新签发等量激活码
3. 一对一发给老用户
4. 在你的售后 Excel 里更新对应记录

### Q2：能用同一个 SECRET 签发不同价位 / 不同等级的激活码吗？

当前版本**不能**——所有激活码地位平等，激活后都解锁全部功能。如果以后要分等级（试用版 / 高级版），需要扩展激活码 payload（例如把 expiry 或 tier 编进 serial），改动会涉及 [license_service.py](../services/license_service.py) 的 `_sig_for` 和 `verify_code`。届时再说。

### Q3：用户能把激活码分享给朋友用吗？

**目前能**——没有机器绑定，一份码任意机器激活。后期想做硬限制，可以加：
- 客户端启动时上传机器指纹给你的服务器（需要服务器）
- 或者本地存"已激活的机器指纹"，第二次激活时检查（防同账户在多机切换，但治不了硬重装）

### Q4：用户激活后能撤销吗？

**当前版本不能**——一旦激活，本地永久解锁，离线也能用。这是单机离线方案的天然限制。要做"撤销 / 续费 / 退款"，必须上服务器签发模式。

### Q5：SECRET 泄露了怎么处理紧急公关？

1. **立刻**生成新 SECRET，发新版 exe
2. 老用户引导升级（强制）
3. 评估损失：泄露期间是否有非付费用户已用旧码激活
4. 复盘：泄露源头是哪儿（git push 误操作？协作者泄露？）

---

## 六、相关文件速查

| 文件 | 用途 |
|------|------|
| [services/license_service.py](../services/license_service.py) | 校验逻辑 + SECRET 配置 |
| [gen_license.py](../gen_license.py) | 命令行签发工具 |
| [frontend/src/components/ActivationGate.vue](../frontend/src/components/ActivationGate.vue) | 激活页 UI |
| [api.py](../api.py) | `api_endpoint` 装饰器中的门禁逻辑、`is_activated` / `activate_license` 端点 |
| [.gitignore](../.gitignore) | 防止激活码 CSV 被误提交 |

---

**最后一句话**：SECRET 之于激活码，就像私钥之于比特币。备份它，保护它，敬畏它。
