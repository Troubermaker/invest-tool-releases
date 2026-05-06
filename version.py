"""
版本号 — 唯一真相源。

升版规则（语义化版本 SemVer）：
    主.次.补丁  例如 1.2.3
    - 补丁 (patch)：bug 修复、文档、性能小优化
    - 次版本 (minor)：新增功能、向后兼容
    - 主版本 (major)：破坏性变更（数据库迁移 / API 不兼容 / 收费模式变更等）

不要手动改这里——用 `python release.py --bump patch/minor/major` 升版，
release.py 会自动更新。如果手动改也无所谓，只是要保持唯一性。
"""
__version__ = "0.2.27"
