"""
ML 模型健康监控（P2 alpha 衰减监控）。

核心指标：
  1. 当前生产模型：名字 / 训练时间 / 训练时 AUC
  2. IC（Information Coefficient）—— 当前模型对历史 dataset 的预测能力
     IC > 0.05  ✓ 有效
     IC 0.02-0.05  ⚠ 衰减中
     IC < 0.02  ❌ 失效，建议重训
  3. 跨 dataset 的 IC 趋势：能看到 alpha 衰减时间线

接口：
  get_current_model_info()   当前加载的模型 + sidecar metadata
  list_models()              models/ 下所有 .pkl
  list_datasets()            data/ml_dataset/ 下所有 NDJSON（按时间倒序）
  compute_dataset_ic(name)   单个数据集的 IC（用当前模型预测）
  recent_ic_trend(n)         最近 N 个数据集的 IC 时间线
"""
import glob
import json
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODELS_DIR  = os.path.join(_ROOT, 'models')
_DATASET_DIR = os.path.join(_ROOT, 'data', 'ml_dataset')


def get_current_model_info():
    """当前生产模型信息 + 状态。"""
    from services import ml_predict_service
    status = ml_predict_service.status()
    info = {
        'ready':    status.get('ready', False),
        'path':     status.get('path'),
        'features': status.get('features') or [],
        'label':    status.get('label'),
        'error':    status.get('error'),
    }
    if info['path'] and os.path.exists(info['path']):
        st = os.stat(info['path'])
        info['size_kb']  = round(st.st_size / 1024, 1)
        info['mtime']    = datetime.fromtimestamp(st.st_mtime).isoformat()
        info['age_days'] = (datetime.now() - datetime.fromtimestamp(st.st_mtime)).days
        # sidecar JSON
        sidecar = info['path'] + '.json'
        if os.path.exists(sidecar):
            try:
                with open(sidecar, encoding='utf-8') as f:
                    info['sidecar'] = json.load(f)
            except Exception:
                pass
    return info


def list_models():
    """所有训练过的模型（按时间倒序）。"""
    if not os.path.isdir(_MODELS_DIR):
        return []
    files = sorted(glob.glob(os.path.join(_MODELS_DIR, '*.pkl')))
    out = []
    for fp in files:
        st = os.stat(fp)
        item = {
            'filename': os.path.basename(fp),
            'path':     fp,
            'size_kb':  round(st.st_size / 1024, 1),
            'mtime':    datetime.fromtimestamp(st.st_mtime).isoformat(),
            'age_days': (datetime.now() - datetime.fromtimestamp(st.st_mtime)).days,
        }
        sidecar = fp + '.json'
        if os.path.exists(sidecar):
            try:
                with open(sidecar, encoding='utf-8') as f:
                    s = json.load(f)
                    item['label']   = s.get('label')
                    item['metrics'] = s.get('metrics') or []
            except Exception:
                pass
        out.append(item)
    out.sort(key=lambda x: x['mtime'], reverse=True)
    return out


def list_datasets():
    """data/ml_dataset/ 下所有 NDJSON（按时间倒序）。"""
    if not os.path.isdir(_DATASET_DIR):
        return []
    files = []
    for fn in os.listdir(_DATASET_DIR):
        if not fn.endswith('.ndjson'): continue
        fp = os.path.join(_DATASET_DIR, fn)
        st = os.stat(fp)
        files.append({
            'filename': fn,
            'path':     fp,
            'size_kb':  round(st.st_size / 1024, 1),
            'mtime':    datetime.fromtimestamp(st.st_mtime).isoformat(),
            'age_days': (datetime.now() - datetime.fromtimestamp(st.st_mtime)).days,
        })
    files.sort(key=lambda x: x['mtime'], reverse=True)
    return files


def compute_dataset_ic(filename, label_field='t7Return'):
    """
    用当前生产模型对指定数据集做 predict，与实际 label 算 Spearman IC。

    Returns:
        {
            'filename': str,
            'n':            int,    # 有效样本数
            'ic':           float,  # Spearman 相关系数
            'ic_threestage': float, # 仅 threeStage 子集的 IC（更纯）
            'mean_pred':    float,
            'mean_label':   float,
            'computed_at':  iso,
            'verdict':      'healthy' | 'declining' | 'failed',
        }
        或 { error: ... }
    """
    fp = os.path.join(_DATASET_DIR, filename) if not os.path.isabs(filename) else filename
    if not os.path.exists(fp):
        return {'error': f'file not found: {fp}'}

    try:
        import pandas as pd
        import numpy as np
    except ImportError:
        return {'error': 'pandas/numpy not installed'}

    from services import ml_predict_service
    if not ml_predict_service.is_ready():
        return {'error': 'ML model not loaded'}

    try:
        # 读 NDJSON
        rows = []
        with open(fp, encoding='utf-8') as f:
            for line in f:
                rows.append(json.loads(line))
        if not rows:
            return {'error': 'empty dataset'}

        # 拼 features
        features = ml_predict_service._state['features']
        records = []
        labels = []
        sources = []
        for r in rows:
            if r.get(label_field) is None: continue
            records.append({f: r.get(f) for f in features})
            labels.append(float(r[label_field]))
            sources.append(r.get('signalSource') or 'unknown')

        if len(records) < 30:
            return {'error': f'too few samples: {len(records)}'}

        X = pd.DataFrame(records)
        # 分类列处理
        for c in ['s2Type', 'gradeLetter', 'signalSource', 'breakoutConfirm', 'marketRegime']:
            if c in X.columns:
                X[c] = X[c].astype('category')
        for c in X.columns:
            if c not in ('s2Type', 'gradeLetter', 'signalSource', 'breakoutConfirm', 'marketRegime'):
                X[c] = pd.to_numeric(X[c], errors='coerce')

        # Predict
        model = ml_predict_service._state['model']
        preds = model.predict_proba(X)[:, 1]
        labels_arr = np.array(labels)
        sources_arr = np.array(sources)

        # 全样本 IC
        ic_all = _spearman_corr(preds, labels_arr)
        # threeStage 子集 IC
        ts_mask = sources_arr == 'threeStage'
        ic_ts = _spearman_corr(preds[ts_mask], labels_arr[ts_mask]) if ts_mask.sum() >= 30 else None

        # 检测 in-sample：模型 mtime > dataset mtime → 模型见过这些数据 → IC 不可信
        # 显式 bool() 转 native Python，避免 numpy.bool_ JSON 序列化失败
        model_path = ml_predict_service._state.get('path')
        is_in_sample = False
        if model_path and os.path.exists(model_path):
            model_mtime = os.path.getmtime(model_path)
            dataset_mtime = os.path.getmtime(fp)
            is_in_sample = bool(model_mtime >= dataset_mtime)

        ic_main = ic_ts if ic_ts is not None else ic_all
        if is_in_sample:
            verdict = 'in_sample'   # 不能作为衰减判定（训练时见过）
        elif ic_main is None or pd.isna(ic_main):
            verdict = 'unknown'
        elif ic_main >= 0.05:
            verdict = 'healthy'
        elif ic_main >= 0.02:
            verdict = 'declining'
        else:
            verdict = 'failed'

        return {
            'filename':       os.path.basename(fp),
            'n':              len(records),
            'n_threestage':   int(ts_mask.sum()),
            'ic':             round(float(ic_all), 4) if ic_all is not None else None,
            'ic_threestage':  round(float(ic_ts), 4) if ic_ts is not None else None,
            'mean_pred':      round(float(preds.mean()), 4),
            'mean_label_pos': round(float((labels_arr > 0).mean()), 4),
            'is_in_sample':   is_in_sample,
            'computed_at':    datetime.now().isoformat(),
            'verdict':        verdict,
        }
    except Exception as e:
        logger.exception('compute_dataset_ic failed')
        return {'error': str(e)}


def _spearman_corr(x, y):
    """Spearman 秩相关系数。手写避免引 scipy 依赖。"""
    try:
        import pandas as pd
        s1 = pd.Series(x)
        s2 = pd.Series(y)
        return s1.corr(s2, method='spearman')
    except Exception:
        return None


# A 股板块前缀映射（跟 main.js BOARD_PREFIXES 同步）
_BOARD_PREFIXES = {
    'sh_main': ('600', '601', '603', '605'),
    'sz_main': ('000', '001', '003'),
    'sme':     ('002',),
    'gem':     ('300', '301'),
    'star':    ('688', '689'),
}

def _code_matches_boards(code, boards):
    """code 是否落在指定板块集合内。空 boards 表示不过滤。"""
    if not boards:
        return True
    code = str(code or '')
    for b in boards:
        prefixes = _BOARD_PREFIXES.get(b)
        if not prefixes:
            continue
        if any(code.startswith(p) for p in prefixes):
            return True
    return False


def compute_model_ic_on_dataset(model_path, dataset_filename,
                                 label_field='t7Return',
                                 signal_source=None,
                                 boards=None):
    """
    指定模型 × 指定数据集的 IC，可按 范围（signal_source / boards）切片。

    Args:
        model_path:     .pkl 路径
        dataset_filename: 数据集文件名
        label_field:    标签字段（t3Return / t7Return / t14Return / t21Return）
        signal_source:  仅评估指定 signalSource 子集（如 'threeStage'），None=全部
        boards:         仅评估指定板块（list[str]，如 ['sh_main','sme']），None=全部

    Returns:
        { model_path, dataset, n, ic, ic_threestage, verdict, scope, ... }
    """
    import pickle
    if not os.path.exists(model_path):
        return {'error': f'model not found: {model_path}'}

    fp = os.path.join(_DATASET_DIR, dataset_filename) if not os.path.isabs(dataset_filename) else dataset_filename
    if not os.path.exists(fp):
        return {'error': f'dataset not found: {fp}'}

    try:
        import pandas as pd
        import numpy as np
    except ImportError:
        return {'error': 'pandas/numpy not installed'}

    try:
        # 加载指定模型（不动当前激活模型）
        with open(model_path, 'rb') as f:
            pack = pickle.load(f)
        model    = pack['model']
        features = pack['features']

        # 读 NDJSON
        rows = []
        with open(fp, encoding='utf-8') as f:
            for line in f:
                rows.append(json.loads(line))
        if not rows:
            return {'error': 'empty dataset'}

        records, labels, sources = [], [], []
        for r in rows:
            if r.get(label_field) is None: continue
            # 按 signalSource 过滤（如指定）
            if signal_source and (r.get('signalSource') or 'threeStage') != signal_source:
                continue
            # 按 boards 过滤（如指定）
            if not _code_matches_boards(r.get('code'), boards):
                continue
            records.append({f: r.get(f) for f in features})
            labels.append(float(r[label_field]))
            sources.append(r.get('signalSource') or 'unknown')

        if len(records) < 30:
            return {'error': f'too few samples: {len(records)}（按当前筛选条件）'}

        X = pd.DataFrame(records)
        for c in ['s2Type', 'gradeLetter', 'signalSource', 'breakoutConfirm', 'marketRegime']:
            if c in X.columns:
                X[c] = X[c].astype('category')
        for c in X.columns:
            if c not in ('s2Type', 'gradeLetter', 'signalSource', 'breakoutConfirm', 'marketRegime'):
                X[c] = pd.to_numeric(X[c], errors='coerce')

        preds = model.predict_proba(X)[:, 1]
        labels_arr = np.array(labels)
        sources_arr = np.array(sources)

        ic_all = _spearman_corr(preds, labels_arr)
        ts_mask = sources_arr == 'threeStage'
        ic_ts = _spearman_corr(preds[ts_mask], labels_arr[ts_mask]) if ts_mask.sum() >= 30 else None

        # in-sample 检测（双重保险）—— 所有 bool 表达式显式 bool() 转 native Python，
        # 否则 numpy.bool_ 不能被 pywebview JSON 序列化
        # 1) mtime 检测：模型创建时间 >= 数据集 → 训练时大概率见过
        model_mtime = os.path.getmtime(model_path)
        dataset_mtime = os.path.getmtime(fp)
        mtime_in_sample = bool(model_mtime >= dataset_mtime)

        ic_main = ic_ts if ic_ts is not None else ic_all
        # 2) IC 异常高检测：正常股票预测 out-of-sample IC 是 0.05-0.15，
        #    超过 0.3 几乎肯定是 in-sample（开卷考试），即便 mtime 看着像 out-of-sample
        ic_too_high = bool(ic_main is not None and not pd.isna(ic_main) and abs(ic_main) > 0.30)
        is_in_sample = bool(mtime_in_sample or ic_too_high)

        if is_in_sample:
            verdict = 'in_sample'
        elif ic_main is None or pd.isna(ic_main):
            verdict = 'unknown'
        elif ic_main >= 0.05:
            verdict = 'healthy'
        elif ic_main >= 0.02:
            verdict = 'declining'
        else:
            verdict = 'failed'

        return {
            'model_path':     model_path,
            'dataset':        os.path.basename(fp),
            'n':              len(records),
            'n_threestage':   int(ts_mask.sum()),
            'ic':             round(float(ic_all), 4) if ic_all is not None else None,
            'ic_threestage':  round(float(ic_ts), 4) if ic_ts is not None else None,
            'is_in_sample':   is_in_sample,
            'verdict':        verdict,
            'scope': {
                'label_field':   label_field,
                'signal_source': signal_source,
                'boards':        list(boards) if boards else None,
            },
        }
    except Exception as e:
        logger.exception('compute_model_ic_on_dataset failed')
        return {'error': str(e)}


def list_models_with_ic(dataset_filename=None, max_models=10,
                         label_field='t7Return',
                         signal_source=None,
                         boards=None):
    """
    所有模型 × 最新（或指定）数据集 的 IC 表。

    Args:
        dataset_filename: None 表示用最新 dataset；否则用指定文件
        max_models: 最多评测多少个模型（按 mtime 倒序取最新的）

    Returns:
        [{filename, path, mtime, age_days, size_kb, label, ic, ic_threestage,
          verdict, is_active, n}, ...]
    """
    models = list_models()[:max_models]
    if not models:
        return []

    datasets = list_datasets()
    if not datasets:
        return [{**m, 'error': 'no dataset available'} for m in models]

    target_ds = dataset_filename or datasets[0]['filename']

    # 当前激活的模型
    active_path = None
    from services import ml_predict_service
    try:
        if ml_predict_service.is_ready():
            active_path = ml_predict_service._state.get('path')
    except Exception:
        pass

    out = []
    for m in models:
        ic_res = compute_model_ic_on_dataset(
            m['path'], target_ds,
            label_field=label_field,
            signal_source=signal_source,
            boards=boards,
        )
        # 显式 bool() 转 native Python 类型，避免 numpy.bool_ JSON 序列化失败
        is_active = bool(active_path and os.path.abspath(m['path']) == os.path.abspath(active_path))
        out.append({
            **m,
            'is_active': is_active,
            'evaluated_on': target_ds,
            **(ic_res if not ic_res.get('error') else {'error': ic_res['error']}),
        })

    # 排序：可信的 healthy/declining 在前（按 IC desc），再 in_sample，再 failed/error
    # 这样推荐顺序一眼能看到
    _PRIORITY = {'healthy': 0, 'declining': 1, 'failed': 2, 'in_sample': 3, 'unknown': 4}
    def _sort_key(row):
        if row.get('error'):
            return (9, 0)
        prio = _PRIORITY.get(row.get('verdict'), 5)
        ic = row.get('ic_threestage') if row.get('ic_threestage') is not None else row.get('ic')
        # IC 高在前（取负数让 sort asc 后变成 desc）
        ic_score = -(ic if ic is not None else -1)
        return (prio, ic_score)
    out.sort(key=_sort_key)

    # 给"最佳"模型加 recommended 标记：第一个 healthy / declining 的非 in_sample 模型
    # 如果所有都是 in_sample → 不推荐任何，返回提示让用户先 runDump 生成新 dataset
    for i, row in enumerate(out):
        if row.get('verdict') in ('healthy', 'declining') and not row.get('error'):
            row['recommended'] = True
            row['recommend_reason'] = (
                f"在当前范围（{label_field}"
                + (f" / {signal_source}" if signal_source else "")
                + (f" / {','.join(boards)}" if boards else "")
                + f"）out-of-sample IC = {row.get('ic_threestage') or row.get('ic'):.3f}，"
                + ("处于 ≥0.05 健康区间" if row.get('verdict') == 'healthy' else "处于衰减区但仍是当前最佳")
            )
            break
    return out


def recent_ic_trend(n=5):
    """最近 N 个数据集的 IC 时间线（用当前模型评测）。"""
    datasets = list_datasets()[:n]
    out = []
    for d in datasets:
        ic = compute_dataset_ic(d['filename'])
        out.append({
            'dataset_filename': d['filename'],
            'dataset_mtime':    d['mtime'],
            'dataset_age_days': d['age_days'],
            **ic,
        })
    return out


def delete_model(model_path):
    """
    删除模型文件 + sidecar JSON。
    安全：路径必须在 models/ 目录内；激活中的模型不允许删除（必须先切换）。

    Returns: {ok: bool, deleted: [path1, path2], error: str?}
    """
    if not model_path:
        return {'ok': False, 'error': '路径不能为空'}
    abs_path = os.path.abspath(model_path)
    # 路径必须在 models/ 下
    if not abs_path.startswith(os.path.abspath(_MODELS_DIR)):
        return {'ok': False, 'error': '路径必须在 models/ 目录内'}
    if not os.path.exists(abs_path):
        return {'ok': False, 'error': '文件不存在'}
    if not abs_path.endswith('.pkl'):
        return {'ok': False, 'error': '只能删 .pkl 文件'}

    # 激活中的模型不能删
    from services import ml_predict_service
    try:
        if ml_predict_service.is_ready():
            active_path = ml_predict_service._state.get('path')
            if active_path and os.path.abspath(active_path) == abs_path:
                return {'ok': False, 'error': '此模型正在激活，先切换到其他模型再删除'}
    except Exception:
        pass

    deleted = []
    try:
        os.remove(abs_path)
        deleted.append(abs_path)
        # 顺手删 sidecar .json
        sidecar = abs_path + '.json'
        if os.path.exists(sidecar):
            os.remove(sidecar)
            deleted.append(sidecar)
        logger.info(f'deleted model: {abs_path} + sidecar')
        return {'ok': True, 'deleted': deleted}
    except Exception as e:
        logger.exception('delete_model failed')
        return {'ok': False, 'error': f'{type(e).__name__}: {e}', 'deleted': deleted}


def delete_dataset(dataset_filename):
    """
    删除 dataset NDJSON 文件。
    安全：路径必须在 data/ml_dataset/ 目录内。

    Returns: {ok: bool, deleted: [path], error: str?}
    """
    if not dataset_filename:
        return {'ok': False, 'error': '文件名不能为空'}
    # 不允许路径分隔符（防路径遍历）
    if '/' in dataset_filename or '\\' in dataset_filename or '..' in dataset_filename:
        return {'ok': False, 'error': '文件名不能含路径分隔符'}
    fp = os.path.join(_DATASET_DIR, dataset_filename)
    abs_path = os.path.abspath(fp)
    if not abs_path.startswith(os.path.abspath(_DATASET_DIR)):
        return {'ok': False, 'error': '路径必须在 data/ml_dataset/ 目录内'}
    if not os.path.exists(abs_path):
        return {'ok': False, 'error': '文件不存在'}
    if not abs_path.endswith('.ndjson'):
        return {'ok': False, 'error': '只能删 .ndjson 文件'}

    try:
        os.remove(abs_path)
        logger.info(f'deleted dataset: {abs_path}')
        return {'ok': True, 'deleted': [abs_path]}
    except Exception as e:
        logger.exception('delete_dataset failed')
        return {'ok': False, 'error': f'{type(e).__name__}: {e}'}


def overview():
    """诊断总览：模型 + 最近 3 个 dataset 的 IC。"""
    return {
        'current_model': get_current_model_info(),
        'recent_ic':     recent_ic_trend(n=3),
        'total_models':  len(list_models()),
        'total_datasets': len(list_datasets()),
    }
