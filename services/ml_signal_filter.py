"""
Phase 2 Step 2-3：LightGBM 训练 + SHAP 诊断（命令行工具）。

用法：
    python -m services.ml_signal_filter            # 训练 + 诊断 + 保存模型
    python -m services.ml_signal_filter --shap-only  # 只诊断不训练（用已有模型）
    python -m services.ml_signal_filter --label t14Profitable

数据流：
    data/ml_dataset/*.ndjson  →  pandas DataFrame  →  LightGBM 分类/回归
    →  models/signal_filter_v{N}.pkl  +  reports/shap_summary_*.png

要求：pip install lightgbm shap pandas scikit-learn matplotlib
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from datetime import datetime
from glob import glob

warnings.filterwarnings('ignore', category=UserWarning)

# 项目根目录
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATASET_DIR = os.path.join(_ROOT, 'data', 'ml_dataset')
_MODELS_DIR  = os.path.join(_ROOT, 'models')
_REPORTS_DIR = os.path.join(_ROOT, 'reports')

# 特征列（必须跟 useBacktest.js extractTradeFeatures 对齐）
# null 列（s2Type, gradeLetter）作为 categorical 处理
_NUMERIC_FEATURES = [
    's1Length', 's1Amplitude',
    's2BarsFromS1', 's3BarsFromS2',
    's2VolRatio', 's3VolRatio',
    'preBreakoutVolBars', 'breakoutVsS1Upper',
    'gradeScore',
    'weeklyConfirmed',
    'ma20Dist', 'ma50Dist', 'ma250Dist',
    'vol5dMean', 'vol20dMean', 'vol60dMean',
    'volRatio5v20', 'volRatio5v60',
    'atr20Pct',
    'breakoutBodyPct', 'breakoutUpperShadowPct', 'breakoutLowerShadowPct',
    'breakoutChangePct',
    'limitUps30d', 'daysFromLastLimitUp',
    'dayOfWeek', 'month',
    # Week 1：环境因子（关键新增）
    'sectorStrength',
    # Week 2 Day 2：主力潜伏特征（4 维）
    'volAcceleration', 'upperShadowDensity', 'closeStability', 'resistanceTests',
    # P2 龙虎榜（4 维，业内实测最强 alpha 维度）
    'lhbInWindow', 'lhbCount', 'lhbNetBuySum', 'daysSinceLastLhb',
]
_CATEGORICAL_FEATURES = ['s2Type', 'gradeLetter', 'signalSource', 'breakoutConfirm']
# marketRegime 不入训练特征：训练数据里全是"扫描当下的 regime"，无方差。
# 运行时仍通过 getStrategyOverrides 用 regime 做硬过滤（弱市/破位市暂停策略）。
_ALL_FEATURES = _NUMERIC_FEATURES + _CATEGORICAL_FEATURES

# 标签候选
_LABEL_CHOICES = {
    't3Profitable':  ('classification', 't3Return',  lambda r: r > 0),
    't7Profitable':  ('classification', 't7Return',  lambda r: r > 0),
    't14Profitable': ('classification', 't14Return', lambda r: r > 0),
    't21Profitable': ('classification', 't21Return', lambda r: r > 0),
    't7Return':      ('regression',     't7Return',  None),
    't14Return':     ('regression',     't14Return', None),
}


def _ensure_dirs():
    os.makedirs(_MODELS_DIR, exist_ok=True)
    os.makedirs(_REPORTS_DIR, exist_ok=True)


def load_dataset():
    """读 data/ml_dataset/*.ndjson 拼成 DataFrame。"""
    import pandas as pd
    files = sorted(glob(os.path.join(_DATASET_DIR, '*.ndjson')))
    if not files:
        raise FileNotFoundError(f'没找到训练数据：{_DATASET_DIR}/*.ndjson —— 先跑一次 bt.runDump()')
    dfs = []
    for fp in files:
        df = pd.read_json(fp, lines=True)
        df['__source'] = os.path.basename(fp)
        dfs.append(df)
        print(f'  loaded {len(df):>6d} rows from {os.path.basename(fp)}')
    full = pd.concat(dfs, ignore_index=True)
    print(f'total: {len(full):,} rows · {len(files)} files')
    return full


def prepare_xy(df, label_name):
    """切分 X / y / 编码 categoricals。"""
    import pandas as pd
    task, src_col, transform = _LABEL_CHOICES[label_name]

    # 标签：去 null
    valid = df.dropna(subset=[src_col]).copy()
    if transform:
        valid[label_name] = valid[src_col].apply(transform).astype(int)
    else:
        valid[label_name] = valid[src_col].astype(float)

    y = valid[label_name].values

    # 老数据兼容：没有 signalSource 列时默认填 'threeStage'
    if 'signalSource' not in valid.columns:
        valid['signalSource'] = 'threeStage'

    # 特征
    X = valid[_ALL_FEATURES].copy()
    # categorical → category dtype（LightGBM 原生支持）
    for c in _CATEGORICAL_FEATURES:
        X[c] = X[c].astype('category')

    print(f'\nlabel={label_name} ({task})  X.shape={X.shape}  y dist: ', end='')
    if task == 'classification':
        pos = int(y.sum()); neg = len(y) - pos
        print(f'pos={pos} ({pos/len(y)*100:.1f}%)  neg={neg}')
    else:
        print(f'mean={y.mean():.2f}  median={pd.Series(y).median():.2f}  std={y.std():.2f}')
    return X, y, task


def train(X, y, task):
    """LightGBM 训练 + 时序 walk-forward 验证。"""
    import lightgbm as lgb
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.metrics import roc_auc_score, accuracy_score, mean_absolute_error

    cv = TimeSeriesSplit(n_splits=5)
    metrics = []
    last_model = None

    for fold, (tr, te) in enumerate(cv.split(X)):
        Xtr, Xte = X.iloc[tr], X.iloc[te]
        ytr, yte = y[tr], y[te]
        if task == 'classification':
            mdl = lgb.LGBMClassifier(
                n_estimators=400, max_depth=5, num_leaves=31,
                learning_rate=0.05, min_child_samples=20,
                reg_alpha=0.1, reg_lambda=0.1,
                random_state=42, verbose=-1,
            )
            mdl.fit(Xtr, ytr, eval_set=[(Xte, yte)], callbacks=[lgb.early_stopping(30, verbose=False)])
            yp = mdl.predict_proba(Xte)[:, 1]
            try:
                auc = roc_auc_score(yte, yp)
            except ValueError:
                auc = float('nan')
            acc = accuracy_score(yte, (yp > 0.5).astype(int))
            metrics.append({'fold': fold, 'auc': auc, 'acc': acc, 'n_train': len(tr), 'n_test': len(te)})
        else:
            mdl = lgb.LGBMRegressor(
                n_estimators=400, max_depth=5, num_leaves=31,
                learning_rate=0.05, min_child_samples=20,
                random_state=42, verbose=-1,
            )
            mdl.fit(Xtr, ytr, eval_set=[(Xte, yte)], callbacks=[lgb.early_stopping(30, verbose=False)])
            yp = mdl.predict(Xte)
            mae = mean_absolute_error(yte, yp)
            metrics.append({'fold': fold, 'mae': mae, 'n_train': len(tr), 'n_test': len(te)})
        last_model = mdl

    print('\n=== walk-forward 验证 ===')
    for m in metrics:
        if task == 'classification':
            print(f"  fold {m['fold']}: AUC={m['auc']:.3f}  acc={m['acc']:.3f}  n_train={m['n_train']:>5d}  n_test={m['n_test']:>5d}")
        else:
            print(f"  fold {m['fold']}: MAE={m['mae']:.2f}%  n_train={m['n_train']:>5d}  n_test={m['n_test']:>5d}")

    # 最终模型用全量数据 refit
    if task == 'classification':
        final = lgb.LGBMClassifier(
            n_estimators=300, max_depth=5, num_leaves=31,
            learning_rate=0.05, min_child_samples=20,
            reg_alpha=0.1, reg_lambda=0.1,
            random_state=42, verbose=-1,
        )
    else:
        final = lgb.LGBMRegressor(
            n_estimators=300, max_depth=5, num_leaves=31,
            learning_rate=0.05, min_child_samples=20,
            random_state=42, verbose=-1,
        )
    final.fit(X, y)
    return final, metrics


def diagnose_shap(model, X, label_name, max_samples=2000):
    """SHAP 特征重要性 + summary plot 保存到 reports/。"""
    import shap
    import matplotlib
    matplotlib.use('Agg')   # 无 GUI 模式
    import matplotlib.pyplot as plt

    # 大样本抽样，加速 SHAP（>2000 没必要算全量）
    if len(X) > max_samples:
        Xs = X.sample(max_samples, random_state=42)
    else:
        Xs = X

    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(Xs)
    # 二分类 sklearn LightGBM 0.5+: shap_values is ndarray (n, n_features)
    # 旧版：list[2]，取正类
    if isinstance(shap_values, list):
        shap_values = shap_values[1]

    # 特征重要性表
    import numpy as np
    imp = np.abs(shap_values).mean(axis=0)
    order = np.argsort(imp)[::-1]
    print('\n=== SHAP 特征重要性 Top 15 ===')
    print(f'{"feature":<28} {"mean|SHAP|":>10}')
    print('-' * 42)
    for i in order[:15]:
        print(f'{_ALL_FEATURES[i]:<28} {imp[i]:>10.4f}')

    # summary plot
    _ensure_dirs()
    plt.figure(figsize=(10, 8))
    shap.summary_plot(shap_values, Xs, feature_names=_ALL_FEATURES, show=False, max_display=20)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_path = os.path.join(_REPORTS_DIR, f'shap_summary_{label_name}_{ts}.png')
    plt.tight_layout()
    plt.savefig(out_path, dpi=130, bbox_inches='tight')
    plt.close()
    print(f'\n[PLOT] SHAP summary saved: {out_path}')

    # JSON 摘要（方便程序读）
    summary = {
        'label':       label_name,
        'n_samples':   int(len(Xs)),
        'features_by_importance': [
            {'name': _ALL_FEATURES[i], 'mean_abs_shap': float(imp[i])} for i in order
        ],
        'generated_at': datetime.now().isoformat(),
    }
    json_path = os.path.join(_REPORTS_DIR, f'shap_summary_{label_name}_{ts}.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f'[JSON] SHAP summary: {json_path}')
    return summary


def save_model(model, label_name, metrics):
    """保存模型到 models/signal_filter_{label}_{ts}.pkl + sidecar JSON。"""
    import pickle
    _ensure_dirs()
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    model_path = os.path.join(_MODELS_DIR, f'signal_filter_{label_name}_{ts}.pkl')
    with open(model_path, 'wb') as f:
        pickle.dump({'model': model, 'features': _ALL_FEATURES, 'label': label_name}, f)
    sidecar = {
        'label':       label_name,
        'features':    _ALL_FEATURES,
        'metrics':     metrics,
        'generated_at': datetime.now().isoformat(),
    }
    with open(model_path + '.json', 'w', encoding='utf-8') as f:
        json.dump(sidecar, f, ensure_ascii=False, indent=2)
    print(f'\n[SAVED] model: {model_path}')
    return model_path


def do_train(dataset_files=None, label='t7Profitable', skip_shap=True):
    """
    可在 service 层调用的训练入口（非 CLI）。

    Args:
        dataset_files: None 表示拼接 _DATASET_DIR/*.ndjson 全部；
                       否则只用指定的文件名列表（'dataset_phase4_v1.ndjson'）
        label:         预测标签
        skip_shap:     是否跳过 SHAP（UI 触发时跳过，省 30-60s）

    Returns:
        dict: {
            ok: bool,
            model_path: str,
            label: str,
            metrics: list[dict],     # walk-forward CV 每折
            avg_auc: float,
            n_samples: int,
            error: str (if not ok)
        }
    """
    import pandas as pd
    try:
        # 加载指定 / 全部 dataset
        if dataset_files:
            dfs = []
            for fname in dataset_files:
                fp = os.path.join(_DATASET_DIR, fname)
                if not os.path.exists(fp):
                    return {'ok': False, 'error': f'dataset not found: {fname}'}
                df = pd.read_json(fp, lines=True)
                df['__source'] = fname
                dfs.append(df)
            full = pd.concat(dfs, ignore_index=True)
        else:
            full = load_dataset()

        if len(full) < 100:
            return {'ok': False, 'error': f'样本太少 ({len(full)} 条)，至少需要 100 条'}

        X, y, task = prepare_xy(full, label)
        model, metrics = train(X, y, task)
        model_path = save_model(model, label, metrics)

        # 平均 AUC (classification) / MAE (regression)
        if task == 'classification':
            aucs = [m.get('auc') for m in metrics if m.get('auc') is not None]
            avg_auc = sum(aucs) / len(aucs) if aucs else None
        else:
            avg_auc = None   # regression 不用 AUC

        return {
            'ok':         True,
            'model_path': model_path,
            'label':      label,
            'metrics':    metrics,
            'avg_auc':    avg_auc,
            'task':       task,
            'n_samples':  int(len(full)),
        }
    except ImportError as e:
        return {'ok': False, 'error': f'缺少依赖: {e}（pip install lightgbm scikit-learn pandas）'}
    except Exception as e:
        import traceback
        return {'ok': False, 'error': f'{type(e).__name__}: {e}', 'trace': traceback.format_exc()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--label', default='t7Profitable', choices=list(_LABEL_CHOICES.keys()),
                    help='预测标签（默认 t7Profitable）')
    ap.add_argument('--shap-only', action='store_true', help='只跑 SHAP 诊断，不训练新模型（需先有模型）')
    args = ap.parse_args()

    print(f'=== ML signal filter @ {datetime.now().isoformat(timespec="seconds")} ===')
    print(f'  data dir: {_DATASET_DIR}')
    print(f'  label:    {args.label}')

    df = load_dataset()
    X, y, task = prepare_xy(df, args.label)

    model, metrics = train(X, y, task)
    save_model(model, args.label, metrics)

    print('\n=== 诊断阶段（SHAP）===')
    diagnose_shap(model, X, args.label)

    print(f'\n[DONE] Next steps:')
    print(f'   1. inspect reports/shap_summary_{args.label}_*.png for top features')
    print(f'   2. codify SHAP top-3 rules back into detector')
    print(f'   3. or wire model into frontend for predict-time filtering (Step 4)')


if __name__ == '__main__':
    try:
        main()
    except FileNotFoundError as e:
        print(f'\n[ERROR] {e}', file=sys.stderr)
        sys.exit(1)
    except ImportError as e:
        print(f'\n[ERROR] missing dep: {e}', file=sys.stderr)
        print('   pip install lightgbm shap pandas scikit-learn matplotlib', file=sys.stderr)
        sys.exit(2)
