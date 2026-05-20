"""
ML 训练数据落盘服务（Phase 2 Step 1）。

设计：
- 每次 bt.runDump 跑完，前端把 features+labels 数组发过来
- 后端写到 data/ml_dataset/dataset_{timestamp}.ndjson（每行一个 JSON）
- 训练时 services/ml_signal_filter.py 读所有 ndjson 文件拼成 DataFrame

NDJSON 而非 SQLite 表的原因：
- 训练用 pandas.read_json(lines=True) 一行搞定
- 特征 schema 演化时不用做 migration（新增列自然为 NaN）
- 数据量级 1-10MB，append-friendly，无并发写需求
"""
import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

# data/ml_dataset/ 跟主项目并列，避免污染 invest_data.db
_BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'ml_dataset')


def _ensure_dir():
    os.makedirs(_BASE_DIR, exist_ok=True)


def save_dataset(rows, name=None):
    """
    把一批样本落盘成 ndjson 文件。

    Args:
        rows: list[dict]，每个 dict 至少含 features + mlLabels（前端 backtestStock 输出格式）
        name: 可选文件名后缀（不含扩展名），默认用 ISO 时间戳

    Returns:
        { 'path': str, 'rows': int }
    """
    if not isinstance(rows, list) or not rows:
        raise ValueError('rows 必须是非空 list')
    _ensure_dir()
    suffix = name or datetime.now().strftime('%Y%m%d_%H%M%S')
    # 清洗文件名（防止 path injection）
    suffix = ''.join(c for c in str(suffix) if c.isalnum() or c in '_-')
    fname = f'dataset_{suffix}.ndjson'
    path = os.path.join(_BASE_DIR, fname)

    with open(path, 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str))
            f.write('\n')

    logger.info(f'saved ml dataset: {path} ({len(rows)} rows)')
    return {'path': path, 'rows': len(rows), 'filename': fname}


def list_datasets():
    """列出所有已落盘的数据集文件（最新在前）。"""
    _ensure_dir()
    files = []
    for fn in os.listdir(_BASE_DIR):
        if fn.endswith('.ndjson'):
            fp = os.path.join(_BASE_DIR, fn)
            st = os.stat(fp)
            files.append({
                'filename': fn,
                'path':     fp,
                'size_kb':  round(st.st_size / 1024, 1),
                'mtime':    datetime.fromtimestamp(st.st_mtime).isoformat(),
            })
    files.sort(key=lambda x: x['mtime'], reverse=True)
    return files


def dataset_dir():
    """返回数据集目录的绝对路径（训练脚本用）。"""
    _ensure_dir()
    return _BASE_DIR
