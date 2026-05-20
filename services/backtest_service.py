"""
回测结果持久化服务。

每次前端跑 bt.runAll / bt.gridSearch / bt.runV0 后，调 save_run 存一份快照。
后续 Backtest.vue 通过 list_runs / get_run 展示历史，方便对比"调参前后"的成绩。

设计原则：
- 只存 summary（聚合统计），不存原始 trades —— trades 量大且可重跑
- gridSearch 的所有 combo 结果都塞 summary JSON，一行一个 run
- runAll / runV0 的多维切片（byGrade / byWeekly / byExitReason / ...）整体放 summary JSON
"""
import json
import logging
from datetime import datetime

import db

logger = logging.getLogger(__name__)


def save_run(run_type, sample_size, hold_days, boards=None,
             detector_opts=None, summary=None, top_combos=None, notes='',
             produced_dataset=None, produced_model=None):
    """
    保存一次回测结果。返回新行的 id。

    Args:
        run_type:        'runAll' | 'gridSearch' | 'runV0' | 'runExitExperiment'
        sample_size:     扫描的股票数
        hold_days:       持有期
        boards:          list 或 None
        detector_opts:   dict / None。gridSearch 时塞 params 网格定义
        summary:         dict / None。聚合统计
        top_combos:      list / None。仅 gridSearch 用，Top 10 combos
        notes:           可选备注
        produced_dataset: 该 run 产生的 dataset 文件名（智能重训用，落 data/ml_dataset/*.ndjson）
        produced_model:   该 run 训练出的 model 路径（智能重训用，models/*.pkl）
    """
    conn = db.get_db()
    c = conn.cursor()
    c.execute('''
        INSERT INTO backtest_runs
            (run_at, run_type, sample_size, hold_days, boards,
             detector_opts, summary, top_combos, notes,
             produced_dataset, produced_model)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        datetime.now().isoformat(),
        run_type,
        sample_size,
        hold_days,
        json.dumps(boards) if boards else None,
        json.dumps(detector_opts) if detector_opts else None,
        json.dumps(summary) if summary else None,
        json.dumps(top_combos) if top_combos else None,
        notes,
        produced_dataset,
        produced_model,
    ))
    run_id = c.lastrowid
    conn.commit()
    conn.close()
    logger.info(f'saved backtest run #{run_id} type={run_type} samples={sample_size}')
    return run_id


def update_artifacts(run_id, produced_dataset=None, produced_model=None):
    """
    更新 run 的关联文件（智能重训 step 2 训练完成时回填 produced_model）。
    传 None 表示不动该字段，传字符串表示设置。
    """
    if produced_dataset is None and produced_model is None:
        return False
    conn = db.get_db()
    c = conn.cursor()
    sets = []
    args = []
    if produced_dataset is not None:
        sets.append('produced_dataset = ?')
        args.append(produced_dataset)
    if produced_model is not None:
        sets.append('produced_model = ?')
        args.append(produced_model)
    args.append(run_id)
    c.execute(f'UPDATE backtest_runs SET {", ".join(sets)} WHERE id = ?', args)
    ok = c.rowcount > 0
    conn.commit()
    conn.close()
    return ok


def list_runs(limit=50):
    """
    列出最近 N 条回测 run（按 run_at 倒序）。
    返回轻量元数据 —— 不解 summary JSON（list 页面用不到，详情才解）。
    """
    conn = db.get_db()
    c = conn.cursor()
    c.execute('''
        SELECT id, run_at, run_type, sample_size, hold_days, boards, notes,
               produced_dataset, produced_model
        FROM backtest_runs
        ORDER BY run_at DESC
        LIMIT ?
    ''', (limit,))
    rows = c.fetchall()
    conn.close()
    return [
        {
            'id':              r[0],
            'run_at':          r[1],
            'run_type':        r[2],
            'sample_size':     r[3],
            'hold_days':       r[4],
            'boards':          json.loads(r[5]) if r[5] else None,
            'notes':           r[6] or '',
            'produced_dataset': r[7],
            'produced_model':   r[8],
        }
        for r in rows
    ]


def get_run(run_id):
    """获取单条 run 的完整数据（含 summary / top_combos JSON 解出来）。"""
    conn = db.get_db()
    c = conn.cursor()
    c.execute('''
        SELECT id, run_at, run_type, sample_size, hold_days, boards,
               detector_opts, summary, top_combos, notes,
               produced_dataset, produced_model
        FROM backtest_runs
        WHERE id = ?
    ''', (run_id,))
    r = c.fetchone()
    conn.close()
    if not r:
        return None
    return {
        'id':              r[0],
        'run_at':          r[1],
        'run_type':        r[2],
        'sample_size':     r[3],
        'hold_days':       r[4],
        'boards':          json.loads(r[5]) if r[5] else None,
        'detector_opts':   json.loads(r[6]) if r[6] else None,
        'summary':         json.loads(r[7]) if r[7] else None,
        'top_combos':      json.loads(r[8]) if r[8] else None,
        'notes':           r[9] or '',
        'produced_dataset': r[10],
        'produced_model':   r[11],
    }


def update_notes(run_id, notes):
    """更新 run 的备注（用户后期标注）。"""
    conn = db.get_db()
    c = conn.cursor()
    c.execute('UPDATE backtest_runs SET notes = ? WHERE id = ?', (notes or '', run_id))
    ok = c.rowcount > 0
    conn.commit()
    conn.close()
    return ok


def delete_run(run_id, delete_files=False):
    """
    删一条 run。

    Args:
        run_id:       要删的 run id
        delete_files: 是否一并删除该 run 关联的 dataset / model 文件
                      （智能重训产生的文件，常规 runAll 没有关联文件就是 no-op）

    Returns: {ok: bool, deleted_files: [path...], file_errors: [str...]}
    """
    # 先查 run，拿到关联文件路径
    run = get_run(run_id)
    if not run:
        return {'ok': False, 'error': f'run #{run_id} 不存在'}

    deleted_files = []
    file_errors = []

    if delete_files:
        # 删 dataset（如果有）
        ds_name = run.get('produced_dataset')
        if ds_name:
            from services import ml_health_service
            ds_res = ml_health_service.delete_dataset(ds_name)
            if ds_res.get('ok'):
                deleted_files.extend(ds_res.get('deleted', []))
            else:
                file_errors.append(f'dataset 删除失败: {ds_res.get("error")}')
        # 删 model（如果有，注意激活模型会被拒）
        mdl_path = run.get('produced_model')
        if mdl_path:
            from services import ml_health_service
            mdl_res = ml_health_service.delete_model(mdl_path)
            if mdl_res.get('ok'):
                deleted_files.extend(mdl_res.get('deleted', []))
            else:
                file_errors.append(f'model 删除失败: {mdl_res.get("error")}')

    # 删 DB 行
    conn = db.get_db()
    c = conn.cursor()
    c.execute('DELETE FROM backtest_runs WHERE id = ?', (run_id,))
    ok = c.rowcount > 0
    conn.commit()
    conn.close()
    return {
        'ok':             ok,
        'deleted_files':  deleted_files,
        'file_errors':    file_errors,
    }
