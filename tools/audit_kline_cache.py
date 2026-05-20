"""
K 线缓存完整性审计 —— 独立 CLI 工具。

扫描 kline_cache.db 里每只票的 K 线数组，检测相邻两根 K 的日期间隔，
如果跳跃超过阈值（默认 12 个日历日）则视为"空洞"。

用法（项目根目录下）：
    python tools/audit_kline_cache.py                  # 用默认阈值跑
    python tools/audit_kline_cache.py --max-gap 10     # 自定义阈值
    python tools/audit_kline_cache.py --fix            # 把有空洞的 code 列表写到 _gaps.txt
    python tools/audit_kline_cache.py --verbose        # 打印每个空洞详情

正常 A 股最长空缺：
    周末   = 3 日历日（周五→周一）
    国庆   = 9-10 日历日
    春节   = 9-10 日历日（含调休）
    建议阈值 12（春节再多 2 天容错）

输出：
    总统计 + Top 10 空洞最多的票 + 可选导出 .txt 给"强制重拉"用
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import date, timedelta

# 让脚本能在项目根目录被 python 直接调
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import db   # noqa: E402  —— 复用 db._CACHE_DB_PATH 的逻辑


def _kline_cache_path():
    """跟 services.kline_cache_service 同路径。"""
    return os.path.join(os.path.dirname(db.DB_PATH), 'kline_cache.db')


def _extract_date(t):
    """K 线 time 字段 → date 对象。无法识别返 None。"""
    if isinstance(t, str) and len(t) >= 10:
        try:
            y, m, d = int(t[0:4]), int(t[5:7]), int(t[8:10])
            return date(y, m, d)
        except ValueError:
            return None
    if isinstance(t, dict) and t.get('year'):
        try:
            return date(int(t['year']), int(t.get('month', 1)), int(t.get('day', 1)))
        except (TypeError, ValueError):
            return None
    return None


def audit_one(code, klines_json, max_gap_days):
    """
    对单只票的 K 线 JSON 做空洞检测。
    返回 {
        'bars': N,
        'gaps': [{'from': 'YYYY-MM-DD', 'to': 'YYYY-MM-DD', 'days': N}],
        'first': 'YYYY-MM-DD' or None,
        'last':  'YYYY-MM-DD' or None,
    }
    """
    try:
        klines = json.loads(klines_json)
    except Exception:
        return None

    if not isinstance(klines, list) or len(klines) < 2:
        return {'bars': len(klines) if isinstance(klines, list) else 0,
                'gaps': [], 'first': None, 'last': None}

    dates = []
    for k in klines:
        if isinstance(k, dict):
            d = _extract_date(k.get('time'))
            if d is not None:
                dates.append(d)

    gaps = []
    for i in range(1, len(dates)):
        diff = (dates[i] - dates[i - 1]).days
        if diff > max_gap_days:
            gaps.append({
                'from': dates[i - 1].isoformat(),
                'to':   dates[i].isoformat(),
                'days': diff,
            })

    return {
        'bars': len(klines),
        'gaps': gaps,
        'first': dates[0].isoformat() if dates else None,
        'last':  dates[-1].isoformat() if dates else None,
    }


def main():
    parser = argparse.ArgumentParser(description='K 线缓存完整性审计')
    parser.add_argument('--max-gap', type=int, default=12,
                        help='相邻两根 K 最大允许日历日间隔（默认 12，含春节调休容错）')
    parser.add_argument('--timeframe', default='日K',
                        help='只检查这个 timeframe（默认 日K）')
    parser.add_argument('--fix', action='store_true',
                        help='把有空洞的 code 列表导出到 tools/_gaps.txt')
    parser.add_argument('--verbose', action='store_true',
                        help='打印每个空洞详情')
    args = parser.parse_args()

    cache_path = _kline_cache_path()
    if not os.path.exists(cache_path):
        print(f'❌ 缓存文件不存在：{cache_path}')
        sys.exit(1)

    print(f'📂 审计目标：{cache_path}')
    print(f'⚙️  timeframe={args.timeframe}  max_gap={args.max_gap} 日历日\n')

    conn = sqlite3.connect(cache_path)
    rows = conn.execute(
        'SELECT code, klines_json FROM kline_cache WHERE timeframe = ? ORDER BY code',
        (args.timeframe,),
    ).fetchall()
    conn.close()

    total = len(rows)
    empty_cache = 0
    no_gap = 0
    with_gap = []   # list of (code, audit_result)
    short_cache = []  # < 60 根的票（pre-IPO / 次新）

    for code, klines_json in rows:
        result = audit_one(code, klines_json, args.max_gap)
        if result is None:
            continue
        if result['bars'] == 0:
            empty_cache += 1
            continue
        if result['bars'] < 60:
            short_cache.append((code, result['bars']))
            # 短缓存也不查 gap（次新本来数据就少）
            continue
        if not result['gaps']:
            no_gap += 1
            continue
        with_gap.append((code, result))

    # ---------------- 汇总 ----------------
    print(f'📊 总览：{total} 只票缓存中')
    print(f'   ✓ 完整无空洞：{no_gap}')
    print(f'   ⚠ 有空洞：    {len(with_gap)}')
    print(f'   - 短缓存(<60根)：{len(short_cache)}（次新/新上市，不计）')
    print(f'   - 空缓存：       {empty_cache}（pre-IPO 确认无数据，不计）')

    if not with_gap:
        print('\n🎉 缓存完整，没有发现任何空洞。')
        return

    # ---------------- Top 10 空洞最严重的票 ----------------
    print('\n🔥 Top 10 空洞最多的票：')
    with_gap.sort(key=lambda x: (len(x[1]['gaps']), max(g['days'] for g in x[1]['gaps'])), reverse=True)
    for code, r in with_gap[:10]:
        biggest = max(g['days'] for g in r['gaps'])
        print(f'   {code}  {r["bars"]} 根  {len(r["gaps"])} 处空洞  最大 {biggest} 天 '
              f'(范围 {r["first"]} → {r["last"]})')

    # ---------------- 详细模式 ----------------
    if args.verbose:
        print('\n📋 详细空洞列表：')
        for code, r in with_gap:
            print(f'\n  {code}（{r["bars"]} 根，{r["first"]} → {r["last"]}）')
            for g in r['gaps']:
                print(f'    {g["from"]} → {g["to"]}  跳了 {g["days"]} 天')

    # ---------------- 导出 fix 清单 ----------------
    if args.fix:
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_gaps.txt')
        with open(out, 'w', encoding='utf-8') as f:
            for code, _ in with_gap:
                f.write(code + '\n')
        print(f'\n💾 有空洞的 code 列表已导出：{out}')
        print('   下一步可以写脚本读这个文件，对每只票强制全量重拉（不走 incremental）。')


if __name__ == '__main__':
    main()
