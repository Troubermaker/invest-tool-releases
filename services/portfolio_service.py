"""
持仓账户管理服务。

功能：
- 账户 CRUD + 拖拽重排（多账户分组）
- 账户内持仓 CRUD
- A 股限制：持股必须是 100 的倍数（后续扩港股再放开）

数据存储：sqlite 两张表 portfolio_accounts / portfolio_positions
"""
import logging

import db

logger = logging.getLogger(__name__)


# =========== 账户 Accounts =========== #

def get_accounts():
    """返回所有账户（含持仓数 count、总市值辅助字段不在此计算），按 sort_order 升序。"""
    conn = db.get_db()
    c = conn.cursor()
    c.execute('''
        SELECT a.id, a.name, a.sort_order, a.created_at,
               COALESCE(COUNT(p.id), 0) AS count
        FROM portfolio_accounts a
        LEFT JOIN portfolio_positions p ON p.account_id = a.id
        GROUP BY a.id, a.name, a.sort_order, a.created_at
        ORDER BY a.sort_order ASC, a.id ASC
    ''')
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_account(name):
    name = (name or '').strip()
    if not name:
        raise ValueError("账户名称不能为空")
    conn = db.get_db()
    c = conn.cursor()
    c.execute('SELECT COALESCE(MAX(sort_order), -1) AS m FROM portfolio_accounts')
    next_order = c.fetchone()['m'] + 1
    c.execute('INSERT INTO portfolio_accounts (name, sort_order) VALUES (?, ?)', (name, next_order))
    new_id = c.lastrowid
    conn.commit()
    conn.close()
    return new_id


def rename_account(account_id, new_name):
    new_name = (new_name or '').strip()
    if not new_name:
        raise ValueError("账户名称不能为空")
    conn = db.get_db()
    c = conn.cursor()
    c.execute('UPDATE portfolio_accounts SET name = ? WHERE id = ?', (new_name, account_id))
    conn.commit()
    conn.close()


def delete_account(account_id):
    """删除账户。ON DELETE CASCADE 会一并清掉账户下的持仓。"""
    conn = db.get_db()
    c = conn.cursor()
    c.execute('PRAGMA foreign_keys = ON')
    c.execute('DELETE FROM portfolio_accounts WHERE id = ?', (account_id,))
    conn.commit()
    conn.close()


def reorder_accounts(ordered_ids):
    conn = db.get_db()
    c = conn.cursor()
    for i, aid in enumerate(ordered_ids):
        c.execute('UPDATE portfolio_accounts SET sort_order = ? WHERE id = ?', (i, aid))
    conn.commit()
    conn.close()


# =========== 持仓 Positions =========== #

def _validate_shares(shares):
    """A 股规则：持股必须为 100 的正整数倍。"""
    try:
        n = int(shares)
    except (TypeError, ValueError):
        raise ValueError("持股数量必须为整数")
    if n <= 0:
        raise ValueError("持股数量必须大于 0")
    if n % 100 != 0:
        raise ValueError("A 股持股数量必须为 100 的整数倍")
    return n


def _validate_cost_price(cost_price, *, require_positive=False):
    """
    成本价校验。
    - 新建持仓（add_position）→ require_positive=True，必须 > 0
    - 更新持仓（update_position）→ require_positive=False，允许负数或零，
      因为摊薄成本法下 "高位卖出后剩余股份的摊薄成本" 可以变负数（= 已全部回本还有盈余）
    """
    try:
        p = float(cost_price)
    except (TypeError, ValueError):
        raise ValueError("成本价必须为数字")
    if require_positive and p <= 0:
        raise ValueError("成本价必须大于 0")
    return p


def get_account_positions(account_id):
    conn = db.get_db()
    c = conn.cursor()
    c.execute('''
        SELECT id, account_id, code, name, shares, cost_price, remark, sort_order, added_at
        FROM portfolio_positions
        WHERE account_id = ?
        ORDER BY sort_order ASC, id ASC
    ''', (account_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_position(account_id, code, name='', shares=None, cost_price=None, remark=''):
    code = (code or '').strip()
    if not code:
        raise ValueError("股票代码不能为空")
    shares = _validate_shares(shares)
    # 新建持仓必须是正成本价（你不可能免费或"负价"买入股票）
    cost_price = _validate_cost_price(cost_price, require_positive=True)

    conn = db.get_db()
    c = conn.cursor()
    c.execute('SELECT id FROM portfolio_accounts WHERE id = ?', (account_id,))
    if not c.fetchone():
        conn.close()
        raise ValueError(f"账户 {account_id} 不存在")
    # 同账户同股票不允许重复（UNIQUE 约束），用户应该走"编辑"
    c.execute('SELECT id FROM portfolio_positions WHERE account_id = ? AND code = ?', (account_id, code))
    if c.fetchone():
        conn.close()
        raise ValueError(f"当前账户已持有 {code}，请直接编辑持仓")

    c.execute('SELECT COALESCE(MAX(sort_order), -1) AS m FROM portfolio_positions WHERE account_id = ?',
              (account_id,))
    next_order = c.fetchone()['m'] + 1
    c.execute(
        '''INSERT INTO portfolio_positions
           (account_id, code, name, shares, cost_price, remark, sort_order)
           VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (account_id, code, name or '', shares, cost_price, remark or '', next_order)
    )
    conn.commit()
    conn.close()


def update_position(account_id, code, *, name=None, shares=None, cost_price=None,
                    remark=None, added_at=None):
    """更新持仓：仅改传入的非 None 字段。shares/cost_price 仍走校验。"""
    updates, params = [], []
    if name is not None:
        updates.append('name = ?'); params.append(name)
    if shares is not None:
        updates.append('shares = ?'); params.append(_validate_shares(shares))
    if cost_price is not None:
        updates.append('cost_price = ?'); params.append(_validate_cost_price(cost_price))
    if remark is not None:
        updates.append('remark = ?'); params.append(remark)
    if added_at is not None:
        updates.append('added_at = ?'); params.append(added_at)
    if not updates:
        return
    params.extend([account_id, code])
    conn = db.get_db()
    c = conn.cursor()
    c.execute(f'UPDATE portfolio_positions SET {", ".join(updates)} WHERE account_id = ? AND code = ?',
              params)
    conn.commit()
    conn.close()


def remove_position(account_id, code):
    conn = db.get_db()
    c = conn.cursor()
    c.execute('DELETE FROM portfolio_positions WHERE account_id = ? AND code = ?', (account_id, code))
    conn.commit()
    conn.close()


def reorder_positions(account_id, ordered_codes):
    conn = db.get_db()
    c = conn.cursor()
    for i, code in enumerate(ordered_codes):
        c.execute(
            'UPDATE portfolio_positions SET sort_order = ? WHERE account_id = ? AND code = ?',
            (i, account_id, code)
        )
    conn.commit()
    conn.close()


# =========== 汇总（给 Step 3 "汇总 tab" 预留接口）=========== #

def get_all_positions_merged():
    """
    跨账户合并：同 code 按成本价加权平均、持股累加。
    返回字段：code, name, shares, cost_price, account_names(|分隔)
    """
    conn = db.get_db()
    c = conn.cursor()
    c.execute('''
        SELECT p.code,
               MIN(p.name) AS name,
               SUM(p.shares) AS shares,
               SUM(p.shares * p.cost_price) / SUM(p.shares) AS cost_price,
               GROUP_CONCAT(a.name, '|') AS account_names
        FROM portfolio_positions p
        JOIN portfolio_accounts a ON a.id = p.account_id
        GROUP BY p.code
        ORDER BY MIN(p.added_at) ASC
    ''')
    rows = c.fetchall()
    conn.close()
    results = []
    for r in rows:
        d = dict(r)
        d['accounts'] = d.pop('account_names', '').split('|') if d.get('account_names') else []
        results.append(d)
    return results
