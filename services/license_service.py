"""
本地激活码服务（HMAC-SHA256 签名 + 离线校验）。

激活码结构：
    code = SERIAL(8 字符) + SIG(8 字符)
    SIG  = HMAC-SHA256(SECRET, SERIAL) 的前 5 字节经 32 字符表编码

显示格式：XXXX-XXXX-XXXX-XXXX（4 段，每段 4 字符，全大写）

校验流程：
    用户输入 → 去横线/空格 + 大写 → 拆 8+8 → 重算 HMAC 比对

⚠️  安全边界
    SECRET 内嵌在客户端，反编译 / 解包 PyInstaller 后可见，
    所以本方案只能阻挡一般用户。后期升级到服务器签发即可，
    把 verify_code 内部换成 HTTP 校验，外部 API 保持不变。

签发激活码：见 gen_license.py（开发者本地用）
"""
import base64
import hmac
import hashlib
import secrets
import re

from services import watchlist_service


# ============================================
# ⚠️  发布前必改：用下面命令生成 64 字符 hex，然后替换这里
#     python -c "import secrets; print(secrets.token_hex(32))"
#     妥善保管这个值；私钥泄露 = 任何人能签发激活码
# ============================================
SECRET = bytes.fromhex(
    "3146b6a8d3a4731284de89851b87efc06eb1c873028610afbbe0739ea175d77e"
)

# 自定义 32 字符表：去掉容易混淆的 0/1/I/O，剩下刚好 32 个
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_PREF_KEY = 'license.activation_code'


# ---------------- 编码工具 ---------------- #

def _enc32(data: bytes, length: int) -> str:
    """
    把字节流按 5 比特一组从字母表里取字符。
    标准 base32 自带 I/O 容易混淆，所以走自定义表。
    """
    out = []
    bits = 0
    nbits = 0
    for b in data:
        bits = (bits << 8) | b
        nbits += 8
        while nbits >= 5 and len(out) < length:
            shift = nbits - 5
            out.append(_ALPHABET[(bits >> shift) & 0x1F])
            nbits -= 5
            bits &= (1 << nbits) - 1
        if len(out) >= length:
            break
    return ''.join(out)


def _normalize(code: str) -> str:
    """去横线 / 空格 / 大写化。允许用户输入时随意带横线。"""
    return re.sub(r'[\s\-]', '', code or '').upper()


def _format(raw16: str) -> str:
    """16 字符 → XXXX-XXXX-XXXX-XXXX 显示格式。"""
    return f"{raw16[0:4]}-{raw16[4:8]}-{raw16[8:12]}-{raw16[12:16]}"


def _sig_for(serial: str) -> str:
    """对 8 字符 serial 计算 8 字符签名。"""
    digest = hmac.new(SECRET, serial.encode(), hashlib.sha256).digest()
    return _enc32(digest, 8)


# ---------------- 对外 API ---------------- #

def generate_code() -> str:
    """生成一个新激活码（开发者签发用）。"""
    rnd = secrets.token_bytes(5)        # 5 字节 = 40 比特 = 8 字符
    serial = _enc32(rnd, 8)
    sig = _sig_for(serial)
    return _format(serial + sig)


def verify_code(code: str) -> bool:
    """校验激活码格式 + 签名是否正确。"""
    raw = _normalize(code)
    if len(raw) != 16:
        return False
    if any(c not in _ALPHABET for c in raw):
        return False
    serial, sig = raw[:8], raw[8:]
    return hmac.compare_digest(sig, _sig_for(serial))


def is_activated() -> bool:
    """当前是否已激活。每次都重新校验签名，防止有人手动塞假码。"""
    code = watchlist_service.get_preference(_PREF_KEY)
    if not code:
        return False
    return verify_code(code)


def activate(code: str) -> bool:
    """尝试激活；成功返回 True，失败返回 False。"""
    if not verify_code(code):
        return False
    watchlist_service.set_preference(_PREF_KEY, _format(_normalize(code)))
    return True


def deactivate():
    """开发调试用：重置激活状态。"""
    watchlist_service.set_preference(_PREF_KEY, None)
