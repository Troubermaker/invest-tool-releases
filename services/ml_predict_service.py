"""
ML 模型预测服务（Phase 3 Step 2）。

启动时懒加载最新 pkl 模型，提供 predict 接口给 api.py。

模型选择策略：
- 优先 phase3only_*.pkl（threeStage 干净训练）
- 否则取 models/ 下最新 .pkl
- 都没有 → service.is_ready() == False，predict 抛错

容错：lightgbm 未装时 import 失败，predict 返回 None（前端降级显示）
"""
import glob
import logging
import os
import pickle
import threading

logger = logging.getLogger(__name__)

# Categorical 列清单必须跟 ml_signal_filter._CATEGORICAL_FEATURES 完全一致，
# 否则 LightGBM 报 "train and valid dataset categorical_feature do not match"
_CATEGORICAL_COLS = ('s2Type', 'gradeLetter', 'signalSource', 'breakoutConfirm')

_MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'models')

_lock = threading.Lock()
_state = {
    'model':    None,
    'features': None,
    'label':    None,
    'path':     None,
    'error':    None,
}


def _find_best_model():
    """优先 phase3only，否则取最新 .pkl。"""
    if not os.path.isdir(_MODELS_DIR):
        return None
    phase3 = sorted(glob.glob(os.path.join(_MODELS_DIR, 'signal_filter_phase3only_*.pkl')))
    if phase3:
        return phase3[-1]
    allp = sorted(glob.glob(os.path.join(_MODELS_DIR, '*.pkl')))
    return allp[-1] if allp else None


def _ensure_loaded():
    if _state['model'] is not None or _state['error']:
        return
    with _lock:
        if _state['model'] is not None or _state['error']:
            return
        try:
            path = _find_best_model()
            if not path:
                _state['error'] = 'no model file in models/'
                logger.warning(_state['error'])
                return
            with open(path, 'rb') as f:
                pack = pickle.load(f)
            _state['model']    = pack['model']
            _state['features'] = pack['features']
            _state['label']    = pack.get('label', 't7Profitable')
            _state['path']     = path
            logger.info(f'ml model loaded: {path} (features={len(_state["features"])})')
        except ImportError as e:
            _state['error'] = f'lightgbm not installed: {e}'
            logger.warning(_state['error'])
        except Exception as e:
            _state['error'] = f'model load failed: {e}'
            logger.exception('ml model load failed')


def reload():
    """清掉缓存，让下次 predict 时重新挑选模型（_find_best_model）。"""
    with _lock:
        _state['model']    = None
        _state['features'] = None
        _state['label']    = None
        _state['path']     = None
        _state['error']    = None
        _state.pop('_forced_path', None)
    logger.info('ml model cache cleared, will reload on next predict')


def activate(model_path):
    """
    切换激活模型（强制加载指定路径，不走 _find_best_model）。

    Args:
        model_path: 完整路径，必须在 models/ 下且存在

    Returns:
        dict: {ok: bool, path: str (if ok), error: str (if not)}
    """
    if not model_path or not os.path.exists(model_path):
        return {'ok': False, 'error': f'文件不存在: {model_path}'}
    if not model_path.endswith('.pkl'):
        return {'ok': False, 'error': '只能加载 .pkl 文件'}
    # 校验在 models/ 目录下（防路径遍历攻击）
    abs_path = os.path.abspath(model_path)
    if not abs_path.startswith(os.path.abspath(_MODELS_DIR)):
        return {'ok': False, 'error': '路径必须在 models/ 目录内'}
    with _lock:
        try:
            with open(abs_path, 'rb') as f:
                pack = pickle.load(f)
            _state['model']    = pack['model']
            _state['features'] = pack['features']
            _state['label']    = pack.get('label', 't7Profitable')
            _state['path']     = abs_path
            _state['error']    = None
            _state['_forced_path'] = abs_path
            logger.info(f'ml model activated: {abs_path}')
            return {'ok': True, 'path': abs_path}
        except Exception as e:
            logger.exception('activate failed')
            return {'ok': False, 'error': f'{type(e).__name__}: {e}'}


def is_ready():
    """预测服务是否可用。"""
    _ensure_loaded()
    return _state['model'] is not None


def status():
    """诊断用：返回当前加载状态。"""
    _ensure_loaded()
    return {
        'ready':    _state['model'] is not None,
        'path':     _state['path'],
        'features': _state['features'],
        'label':    _state['label'],
        'error':    _state['error'],
    }


def predict(features_dict):
    """
    对单个 feature 向量返回 [0,1] 概率（T+7 是否盈利）。

    Args:
        features_dict: {feature_name: value, ...}，缺失列填 None（LightGBM 原生支持 NaN）

    Returns:
        float | None  None 表示模型未加载 / 预测失败
    """
    _ensure_loaded()
    if _state['model'] is None:
        return None
    try:
        import pandas as pd
        row = {f: features_dict.get(f) for f in _state['features']}
        X = pd.DataFrame([row])
        # 强制把已知 categorical 转 category；数值列强转 numeric（None / 字符串 → NaN）
        for c in _CATEGORICAL_COLS:
            if c in X.columns:
                X[c] = X[c].astype('category')
        for c in X.columns:
            if c not in _CATEGORICAL_COLS:
                X[c] = pd.to_numeric(X[c], errors='coerce')
        proba = _state['model'].predict_proba(X)[0, 1]
        return float(proba)
    except Exception as e:
        logger.warning(f'predict failed: {e}')
        return None


def predict_batch(features_list):
    """批量预测。features_list: list[dict]。返回 list[float|None]。"""
    _ensure_loaded()
    if _state['model'] is None:
        return [None] * len(features_list)
    try:
        import pandas as pd
        feats = _state['features']
        rows = [{f: fd.get(f) for f in feats} for fd in features_list]
        X = pd.DataFrame(rows)
        for c in _CATEGORICAL_COLS:
            if c in X.columns:
                X[c] = X[c].astype('category')
        for c in X.columns:
            if c not in _CATEGORICAL_COLS:
                X[c] = pd.to_numeric(X[c], errors='coerce')
        probas = _state['model'].predict_proba(X)[:, 1]
        return [float(p) for p in probas]
    except Exception as e:
        logger.warning(f'predict_batch failed: {e}')
        return [None] * len(features_list)
