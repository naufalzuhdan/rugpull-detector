"""
model/train_model.py
Training model RANDOM FOREST untuk deteksi rug pull token Solana.
Menggantikan Decision Tree — akurasi lebih tinggi, tetap interpretable
via feature importance.
"""

import numpy as np
import pandas as pd
import joblib
import os
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns

from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split, cross_val_score, StratifiedKFold
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score,
    confusion_matrix, classification_report, roc_auc_score, roc_curve
)
from sklearn.inspection import permutation_importance

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.data_fetcher import MODEL_FEATURES

MODEL_DIR  = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(os.path.dirname(MODEL_DIR), "static", "img")
os.makedirs(STATIC_DIR, exist_ok=True)

# ── Nama file model (ganti dari decision_tree → random_forest) ──
MODEL_FILE   = "random_forest_model.pkl"
MODEL_PATH   = os.path.join(MODEL_DIR, MODEL_FILE)
METRICS_PATH = os.path.join(MODEL_DIR, "metrics.json")

# Backward compat: juga simpan alias lama agar predictor lama tidak error
MODEL_ALIAS  = os.path.join(MODEL_DIR, "decision_tree_model.pkl")


# ══════════════════════════════════════════════════════════════
#  SYNTHETIC DATASET
# ══════════════════════════════════════════════════════════════

def generate_synthetic_dataset(n_samples: int = 1500,
                                random_state: int = 42) -> pd.DataFrame:
    """Dataset sintetis realistis — dipakai jika dataset.csv belum ada."""
    rng = np.random.default_rng(random_state)
    records = []

    for i in range(n_samples):
        is_rug = i < n_samples // 2

        if is_rug:
            top10       = rng.uniform(70, 99)
            top1        = rng.uniform(40, 90)
            creator_pct = rng.uniform(20, 80)
            mint_auth   = rng.choice([0, 1], p=[0.2, 0.8])
            freeze_auth = rng.choice([0, 1], p=[0.35, 0.65])
            lp_burned   = rng.uniform(0, 20)
            liq_mc      = rng.uniform(0.001, 0.05)
            vol_liq     = rng.uniform(0.5, 20.0)
            age_days    = rng.uniform(0, 30)
            liquidity   = rng.uniform(500, 50_000)
            holders     = rng.integers(10, 500)
            price_chg   = rng.uniform(-80, 500)
            sym  = f"RUG{i:04d}"
            name = f"Rug Token {i}"
            addr = f"SYNTHETIC_RUG_{i:06d}AAAAAAAAAAAAAAAAAAA"
        else:
            top10       = rng.uniform(15, 60)
            top1        = rng.uniform(2, 25)
            creator_pct = rng.uniform(0, 12)
            mint_auth   = rng.choice([0, 1], p=[0.88, 0.12])
            freeze_auth = rng.choice([0, 1], p=[0.92, 0.08])
            lp_burned   = rng.uniform(35, 100)
            liq_mc      = rng.uniform(0.05, 0.5)
            vol_liq     = rng.uniform(0.05, 3.0)
            age_days    = rng.uniform(30, 730)
            liquidity   = rng.uniform(10_000, 5_000_000)
            holders     = rng.integers(500, 50_000)
            price_chg   = rng.uniform(-30, 100)
            sym  = f"LEGIT{i:04d}"
            name = f"Legitimate Token {i}"
            addr = f"SYNTHETIC_LEGIT_{i:06d}AAAAAAAAAAAAAAAAAA"

        records.append({
            "contract_address":         addr,
            "token_name":               name,
            "token_symbol":             sym,
            "top10_holder_pct":         round(top10, 2),
            "top1_holder_pct":          round(min(top1, top10), 2),
            "creator_balance_pct":      round(creator_pct, 2),
            "mint_authority_enabled":   int(mint_auth),
            "freeze_authority_enabled": int(freeze_auth),
            "lp_burned_pct":            round(lp_burned, 2),
            "liq_to_mc_ratio":          round(liq_mc, 4),
            "vol_to_liq_ratio":         round(vol_liq, 4),
            "token_age_days":           round(age_days, 2),
            "liquidity":                round(liquidity, 2),
            "unique_holders":           int(holders),
            "price_change_24h":         round(price_chg, 2),
            "label":                    1 if is_rug else 0,
        })

    df = pd.DataFrame(records).sample(frac=1, random_state=random_state).reset_index(drop=True)
    return df


# ══════════════════════════════════════════════════════════════
#  TRAINING
# ══════════════════════════════════════════════════════════════

def train_and_evaluate():
    """Training Random Forest dan simpan semua artefak evaluasi."""
    print("=" * 65)
    print("  TRAINING MODEL: RANDOM FOREST — RUG PULL DETECTOR")
    print("=" * 65)

    # ── 1. Load / generate dataset ──────────────────────────
    csv_path = os.path.join(os.path.dirname(MODEL_DIR), "data", "dataset.csv")
    if os.path.exists(csv_path):
        print(f"[INFO] Memuat dataset: {csv_path}")
        df = pd.read_csv(csv_path)
    else:
        print("[INFO] Dataset tidak ditemukan. Membuat dataset sintetis (1500 sampel)...")
        df = generate_synthetic_dataset(n_samples=1500)
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        df.to_csv(csv_path, index=False)
        print(f"[INFO] Dataset sintetis disimpan: {csv_path}")

    # Deteksi tipe dataset
    rug_n   = int(df["label"].sum())
    legit_n = int((df["label"] == 0).sum())
    is_real = ("contract_address" in df.columns and
                not df["contract_address"].astype(str).str.startswith("SYNTHETIC").all())
    src_tag = "REAL (DexScreener+RPC+RugCheck)" if is_real else "SINTETIS"

    print(f"[INFO] Total: {len(df)} | Rug Pull: {rug_n} | Legitimate: {legit_n}")
    print(f"[INFO] Sumber : {src_tag}")

    non_feat = [c for c in df.columns if c not in MODEL_FEATURES + ["label"]]
    if non_feat:
        print(f"[INFO] Kolom metadata (diabaikan): {non_feat}")

    X = df[MODEL_FEATURES].fillna(0)
    y = df["label"]

    # ── 2. Train-test split ──────────────────────────────────
    # Handle dataset kecil: jika < 10 samples, training saja tanpa test split
    if len(df) < 10:
        print(f"[WARNING] Dataset terlalu kecil ({len(df)} samples). "
              f"Minimal 10 samples untuk split train/test.")
        print(f"[WARNING] Training tanpa test split (not recommended for production).")
        X_train, X_test = X, X.iloc[:0]  # Empty test set
        y_train, y_test = y, y.iloc[:0]
    else:
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )
    print(f"[INFO] Train: {len(X_train)} | Test: {len(X_test)}")

    # ── 3. Train Random Forest ───────────────────────────────
    model = RandomForestClassifier(
        n_estimators   = 200,          # jumlah pohon
        max_depth      = 15,           # kedalaman maks tiap pohon
        min_samples_split = 10,
        min_samples_leaf  = 5,
        max_features   = "sqrt",       # fitur acak per split (akar dari 12 = ~3-4)
        class_weight   = "balanced",   # tangani imbalanced data
        bootstrap      = True,
        oob_score      = True,         # out-of-bag score (bonus evaluasi)
        random_state   = 42,
        n_jobs         = -1,           # pakai semua CPU core
    )
    model.fit(X_train, y_train)
    print(f"[INFO] Random Forest (200 pohon) berhasil ditraining.")
    print(f"[INFO] OOB Score: {model.oob_score_:.4f}")

    # ── 4. Evaluasi ──────────────────────────────────────────
    if len(X_test) > 0:
        y_pred  = model.predict(X_test)
        y_proba = model.predict_proba(X_test)[:, 1]

        acc  = accuracy_score(y_test, y_pred)
        prec = precision_score(y_test, y_pred, zero_division=0)
        rec  = recall_score(y_test, y_pred, zero_division=0)
        f1   = f1_score(y_test, y_pred, zero_division=0)
        auc  = roc_auc_score(y_test, y_proba) if len(set(y_test)) > 1 else 0.0

        print(f"\n{'─'*45}")
        print(f"  Accuracy  : {acc*100:.2f}%")
        print(f"  Precision : {prec*100:.2f}%")
        print(f"  Recall    : {rec*100:.2f}%")
        print(f"  F1-Score  : {f1*100:.2f}%")
        print(f"  AUC-ROC   : {auc:.4f}")
        print(f"  OOB Score : {model.oob_score_:.4f}")
        print(f"{'─'*45}")
    else:
        # Dataset terlalu kecil, skip evaluation
        print(f"\n{'─'*45}")
        print(f"  [WARNING] No test set - evaluation skipped")
        print(f"  OOB Score : {model.oob_score_:.4f}")
        print(f"  [INFO] Collect more data (min 50 samples) for proper evaluation")
        print(f"{'─'*45}")
        
        # Set dummy metrics untuk save nanti
        acc = prec = rec = f1 = auc = 0.0
        y_pred = y_proba = y_test = []

    # Cross validation (hanya jika dataset cukup besar)
    if len(df) >= 10:
        n_splits = min(10, len(df)//2)
        cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
        cv_scores = cross_val_score(model, X, y, cv=cv, scoring="f1")
        cv_f1_mean = cv_scores.mean()
        cv_f1_std = cv_scores.std()
        print(f"  {n_splits}-Fold CV F1: {cv_f1_mean*100:.2f}% ± {cv_f1_std*100:.2f}%")
    else:
        cv_f1_mean = cv_f1_std = 0.0
        print(f"  [WARNING] Dataset too small for cross-validation")
    print(f"{'─'*45}\n")

    if len(X_test) > 0:
        print(classification_report(y_test, y_pred,
                                     target_names=["Legitimate", "Rug Pull"]))

    # Feature importance (built-in Random Forest: mean decrease impurity)
    feat_imp = dict(zip(MODEL_FEATURES,
                        [float(v) for v in model.feature_importances_]))
    feat_imp_sorted = dict(sorted(feat_imp.items(),
                                   key=lambda x: x[1], reverse=True))

    metrics = {
        "algorithm":      "Random Forest",
        "n_estimators":   200,
        "max_depth":      15,
        "oob_score":      round(float(model.oob_score_), 4),
        "accuracy":       round(acc * 100, 2),
        "precision":      round(prec * 100, 2),
        "recall":         round(rec * 100, 2),
        "f1_score":       round(f1 * 100, 2),
        "auc_roc":        round(auc, 4),
        "cv_f1_mean":     round(cv_f1_mean * 100, 2),
        "cv_f1_std":      round(cv_f1_std * 100, 2),
        "train_samples":  len(X_train),
        "test_samples":   len(X_test),
        "total_samples":  len(df),
        "feature_importance": feat_imp_sorted,
        "dataset_source": src_tag,
    }

    # ── 5. Charts (skip jika dataset terlalu kecil) ─────────
    if len(X_test) > 0:
        _plot_confusion_matrix(y_test, y_pred)
        _plot_roc_curve(y_test, y_proba, auc)
    else:
        print("[WARNING] Skipping confusion matrix & ROC curve (no test set)")
    
    _plot_feature_importance(feat_imp_sorted)
    _plot_tree_sample(model, X_train)

    # ── 6. Simpan model ──────────────────────────────────────
    joblib.dump(model, MODEL_PATH)
    joblib.dump(model, MODEL_ALIAS)   # backward compat
    with open(METRICS_PATH, "w") as f:
        json.dump(metrics, f, indent=2)

    print(f"[OK] Model disimpan : {MODEL_PATH}")
    print(f"[OK] Metrics disimpan: {METRICS_PATH}")
    print("[OK] Training selesai!\n")
    return model, metrics


# ══════════════════════════════════════════════════════════════
#  CHARTS
# ══════════════════════════════════════════════════════════════

def _plot_confusion_matrix(y_test, y_pred):
    cm = confusion_matrix(y_test, y_pred)
    fig, ax = plt.subplots(figsize=(6, 5))
    sns.heatmap(cm, annot=True, fmt='d', cmap='Blues',
                xticklabels=["Legitimate", "Rug Pull"],
                yticklabels=["Legitimate", "Rug Pull"],
                ax=ax, linewidths=0.5)
    ax.set_xlabel("Predicted Label", fontsize=12, labelpad=10)
    ax.set_ylabel("True Label", fontsize=12, labelpad=10)
    ax.set_title("Confusion Matrix — Random Forest", fontsize=14,
                 pad=15, fontweight='bold')
    plt.tight_layout()
    plt.savefig(os.path.join(STATIC_DIR, "confusion_matrix.png"),
                dpi=150, bbox_inches='tight')
    plt.close()


def _plot_feature_importance(feat_imp: dict):
    names  = list(feat_imp.keys())
    values = list(feat_imp.values())
    colors = ['#e74c3c' if v > 0.08 else '#3498db' for v in values]

    fig, ax = plt.subplots(figsize=(9, 6))
    bars = ax.barh(names[::-1], values[::-1],
                   color=colors[::-1], edgecolor='white', height=0.7)
    ax.set_xlabel("Feature Importance (Mean Decrease Impurity)", fontsize=11)
    ax.set_title("Fitur On-Chain — Random Forest Importance",
                 fontsize=13, fontweight='bold', pad=12)
    ax.set_xlim(0, max(values) * 1.18)

    for bar, val in zip(bars, values[::-1]):
        ax.text(bar.get_width() + 0.003,
                bar.get_y() + bar.get_height() / 2,
                f'{val:.4f}', va='center', fontsize=9, color='#2c3e50')

    plt.tight_layout()
    plt.savefig(os.path.join(STATIC_DIR, "feature_importance.png"),
                dpi=150, bbox_inches='tight')
    plt.close()


def _plot_roc_curve(y_test, y_proba, auc_score):
    fpr, tpr, _ = roc_curve(y_test, y_proba)
    fig, ax = plt.subplots(figsize=(6, 5))
    ax.plot(fpr, tpr, color='#27ae60', lw=2.5,
            label=f'Random Forest (AUC = {auc_score:.3f})')
    ax.plot([0, 1], [0, 1], color='#95a5a6', lw=1.5,
            linestyle='--', label='Random Classifier')
    ax.fill_between(fpr, tpr, alpha=0.08, color='#27ae60')
    ax.set_xlabel("False Positive Rate", fontsize=11)
    ax.set_ylabel("True Positive Rate", fontsize=11)
    ax.set_title("ROC Curve — Random Forest", fontsize=13,
                 fontweight='bold', pad=12)
    ax.legend(loc="lower right", fontsize=10)
    ax.set_xlim([0, 1])
    ax.set_ylim([0, 1.02])
    plt.tight_layout()
    plt.savefig(os.path.join(STATIC_DIR, "roc_curve.png"),
                dpi=150, bbox_inches='tight')
    plt.close()


def _plot_tree_sample(model, X_train):
    """Visualisasi salah satu pohon dari Random Forest (contoh tree ke-0)."""
    from sklearn.tree import plot_tree
    fig, ax = plt.subplots(figsize=(20, 8))
    plot_tree(
        model.estimators_[0],          # pohon pertama sebagai contoh
        feature_names=MODEL_FEATURES,
        class_names=["Legitimate", "Rug Pull"],
        filled=True, rounded=True,
        fontsize=7, ax=ax, max_depth=4,
        impurity=False, precision=2,
    )
    ax.set_title(
        "Contoh 1 Pohon dari Random Forest (tree ke-0, max_depth=4)\n"
        "— Random Forest menggunakan 200 pohon seperti ini —",
        fontsize=12, fontweight='bold', pad=12
    )
    plt.tight_layout()
    plt.savefig(os.path.join(STATIC_DIR, "random_forest.png"),
                dpi=100, bbox_inches='tight')
    # Simpan juga ke decision_tree.png untuk backward compatibility
    plt.savefig(os.path.join(STATIC_DIR, "decision_tree.png"),
                dpi=100, bbox_inches='tight')
    plt.close()


if __name__ == "__main__":
    train_and_evaluate()
