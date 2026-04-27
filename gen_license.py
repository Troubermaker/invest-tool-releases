"""
开发者本地工具：批量生成激活码。

用法：
    python gen_license.py             # 生成 1 个
    python gen_license.py 10          # 生成 10 个
    python gen_license.py 50 --csv    # 生成 50 个并输出 CSV（带序号）

发码后建议保存到一个本地 Excel / 记事本里，记录"激活码 → 用户邮箱 / 微信 / 付款时间"，
方便售后查询。
"""
import sys

from services import license_service


def main():
    args = sys.argv[1:]
    n = 1
    csv = False
    for a in args:
        if a == '--csv':
            csv = True
        elif a.isdigit():
            n = int(a)

    if csv:
        print('serial,code,issued_at')
        from datetime import datetime
        ts = datetime.now().isoformat(timespec='seconds')
        for i in range(1, n + 1):
            code = license_service.generate_code()
            print(f'{i},{code},{ts}')
    else:
        for _ in range(n):
            print(license_service.generate_code())


if __name__ == '__main__':
    main()
