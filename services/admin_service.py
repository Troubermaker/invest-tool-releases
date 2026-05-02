"""
管理员模式服务。

普通用户：基础 K 线 + 自选 + 持仓 + 行情页（够日常使用）
管理员：以上全部 + 复杂指标（三维启动 / 主升 / 缺口 / 共振 等）+ 找候选 / 找发车扫描器
       + pytdx 数据源（绕过 EM/腾讯反爬，扫多少不怕封）

解锁方式（B + C 组合）：
  - 已激活的普通用户输入"管理员密码"即可升级为管理员（B 模式）
  - 前端可通过隐藏快捷键 Ctrl+Shift+A 唤起密码输入框（C 模式）

存储：管理员状态持久化在 user_preferences 表（key=admin.enabled，值为 'true' / 缺失）

⚠ 安全边界：
  ADMIN_PASSWORD_HASH 内嵌客户端，反编译可见；本方案是"软门禁"，挡掉一般用户即可。
  真正商业部署请改成服务端 HTTP 校验。
"""
import hashlib
import hmac
import logging

from services import watchlist_service

logger = logging.getLogger(__name__)


# ============================================
# ⚠ 发布前必改：把下面的 ADMIN_PASSWORD 改成你自己定的密码，
# 然后把上面那行 hashlib 算出来的 hex 填到 ADMIN_PASSWORD_HASH。
# 命令：
#     python -c "import hashlib; print(hashlib.sha256('你的密码'.encode()).hexdigest())"
# 当前默认密码是 'invest_admin_2026'（请改）
# ============================================
ADMIN_PASSWORD_HASH = (
    'b3a7b2c81e9d4f6a5c2e8f1d4b7a3e6c9d1a5b8e7f3c6d2a4b9e1c7f5d3a8b2e'  # 占位
)
# 真实计算：
ADMIN_PASSWORD_HASH = hashlib.sha256(b'admins_666').hexdigest()

_PREF_KEY = 'admin.enabled'


def verify_password(password: str) -> bool:
    """常量时间比较密码哈希，避免 timing attack。"""
    if not password:
        return False
    h = hashlib.sha256(password.encode('utf-8')).hexdigest()
    return hmac.compare_digest(h, ADMIN_PASSWORD_HASH)


def is_admin() -> bool:
    """当前会话是否处于管理员模式。"""
    val = watchlist_service.get_preference(_PREF_KEY)
    return str(val).lower() == 'true'


def unlock(password: str) -> bool:
    """输入密码升级为管理员；密码错误返回 False。"""
    if not verify_password(password):
        logger.warning('管理员解锁失败：密码错误')
        return False
    watchlist_service.set_preference(_PREF_KEY, 'true')
    logger.info('管理员模式已开启')
    return True


def disable():
    """退出管理员模式（开发调试 / 误开后撤销）。"""
    watchlist_service.set_preference(_PREF_KEY, None)
    logger.info('管理员模式已关闭')
