"""
model/predictor.py
Load Random Forest model dan lakukan prediksi token baru.
Smart cache: reload metrics hanya jika file berubah.
"""

import joblib, json, os
import numpy as np
import pandas as pd
import logging
from typing import Dict

MODEL_DIR = os.path.dirname(os.path.abspath(__file__))
logger = logging.getLogger(__name__)

_model   = None
_metrics = None
_metrics_mtime = None  # ← Track file modification time

# Coba load Random Forest dulu, fallback ke Decision Tree lama
_MODEL_FILES = [
    "random_forest_model.pkl",
    "decision_tree_model.pkl",
]


def load_model():
    global _model, _metrics
    loaded = False
    for fname in _MODEL_FILES:
        path = os.path.join(MODEL_DIR, fname)
        if os.path.exists(path):
            _model = joblib.load(path)
            loaded = True
            break

    if not loaded:
        raise FileNotFoundError(
            "Model belum ditraining. Jalankan: python model/train_model.py"
        )

    metrics_path = os.path.join(MODEL_DIR, "metrics.json")
    if os.path.exists(metrics_path):
        with open(metrics_path) as f:
            _metrics = json.load(f)

    return _model, _metrics


def get_model():
    global _model
    if _model is None:
        load_model()
    return _model


def get_metrics():
    """
    Load metrics dengan smart cache.
    Reload hanya jika file metrics.json berubah (timestamp check).
    Jika file unchanged, gunakan cache (untuk performance).
    """
    global _metrics, _metrics_mtime
    
    metrics_path = os.path.join(MODEL_DIR, "metrics.json")
    
    # Jika file tidak ada, return empty
    if not os.path.exists(metrics_path):
        _metrics = None
        _metrics_mtime = None
        return {}
    
    # Check file modification time
    current_mtime = os.path.getmtime(metrics_path)
    
    # Reload jika: (1) cache kosong, atau (2) file berubah
    if _metrics is None or _metrics_mtime is None or _metrics_mtime != current_mtime:
        try:
            with open(metrics_path) as f:
                _metrics = json.load(f)
            _metrics_mtime = current_mtime
            logger.info(f"Metrics reloaded from {metrics_path}")
        except Exception as e:
            logger.error(f"Failed to load metrics: {e}")
            _metrics = None
            _metrics_mtime = None
            return {}
    
    return _metrics or {}


def predict_token(features: dict) -> dict:
    """
    Prediksi apakah token berpotensi rug pull.
    Args:    features dict dari extract_features()
    Returns: dict hasil prediksi lengkap
    """
    from utils.data_fetcher import MODEL_FEATURES

    model = get_model()

    feat_values = [float(features.get(f, 0) or 0) for f in MODEL_FEATURES]
    X    = pd.DataFrame([feat_values], columns=MODEL_FEATURES)

    pred_class = int(model.predict(X)[0])
    pred_proba = model.predict_proba(X)[0]

    rug_prob   = round(float(pred_proba[1]) * 100, 1)
    legit_prob = round(float(pred_proba[0]) * 100, 1)

    # ── Override: jika RugCheck sudah konfirmasi rugged → paksa 99% ──
    is_confirmed_rugged = features.get("rugcheck_rugged", False)
    if is_confirmed_rugged:
        pred_class = 1
        rug_prob   = max(rug_prob, 99.0)
        legit_prob = round(100.0 - rug_prob, 1)
        logger.info("  ⚠ rugcheck_rugged=True → override prediksi ke RUG PULL 99%")

    # ── Boost berdasarkan rugcheck_score tinggi (tanpa replace model sepenuhnya) ──
    rc_score = int(features.get("rugcheck_score", -1) or -1)
    if rc_score >= 700 and rug_prob < 70:
        rug_prob   = max(rug_prob, 70.0)
        legit_prob = round(100.0 - rug_prob, 1)
        pred_class = 1
        logger.info(f"  ⚠ RugCheck score={rc_score} tinggi → boost rug_prob ke {rug_prob}%")

    # Risk level
    if rug_prob >= 80:
        risk_level, risk_color, risk_icon = "SANGAT TINGGI", "#e74c3c", "🚨"
    elif rug_prob >= 60:
        risk_level, risk_color, risk_icon = "TINGGI",        "#e67e22", "⚠️"
    elif rug_prob >= 40:
        risk_level, risk_color, risk_icon = "SEDANG",        "#f1c40f", "⚡"
    elif rug_prob >= 20:
        risk_level, risk_color, risk_icon = "RENDAH",        "#2ecc71", "✅"
    else:
        risk_level, risk_color, risk_icon = "SANGAT RENDAH", "#27ae60", "✅"

    # Analisis faktor risiko
    risk_factors     = []
    positive_factors = []
    v = features

    def fv(key): return float(v.get(key, 0) or 0)
    def fi(key): return int(v.get(key, 0) or 0)

    if fv("top10_holder_pct") > 70:
        risk_factors.append({"feature": "Konsentrasi Holder",
            "detail": f"Top 10 holder memiliki {fv('top10_holder_pct'):.1f}% supply — sangat terkonsentrasi",
            "severity": "high"})
    elif fv("top10_holder_pct") < 40:
        positive_factors.append("Distribusi holder relatif merata")

    if fv("top1_holder_pct") > 30:
        risk_factors.append({"feature": "Whale Wallet",
            "detail": f"1 wallet menguasai {fv('top1_holder_pct'):.1f}% supply — risiko dump tinggi",
            "severity": "high"})

    if fi("mint_authority_enabled") == 1:
        risk_factors.append({"feature": "Mint Authority Aktif",
            "detail": "Developer masih bisa mencetak token baru tanpa batas — sangat berbahaya",
            "severity": "critical"})
    else:
        positive_factors.append("Mint authority sudah dinonaktifkan")

    if fi("freeze_authority_enabled") == 1:
        risk_factors.append({"feature": "Freeze Authority Aktif",
            "detail": "Developer bisa membekukan wallet holder",
            "severity": "high"})

    if fv("lp_burned_pct") < 20:
        risk_factors.append({"feature": "Likuiditas Tidak Terkunci",
            "detail": f"Hanya {fv('lp_burned_pct'):.1f}% LP dibakar/dikunci — dev bisa tarik likuiditas",
            "severity": "critical"})
    elif fv("lp_burned_pct") > 70:
        positive_factors.append(f"LP burned tinggi ({fv('lp_burned_pct'):.1f}%)")

    if fv("token_age_days") < 7:
        risk_factors.append({"feature": "Token Sangat Baru",
            "detail": f"Token berumur {fv('token_age_days'):.1f} hari — belum terbukti",
            "severity": "medium"})
    elif fv("token_age_days") > 180:
        positive_factors.append(f"Token sudah berumur {fv('token_age_days'):.0f} hari")

    if fv("creator_balance_pct") > 20:
        risk_factors.append({"feature": "Dev Wallet Besar",
            "detail": f"Developer memegang {fv('creator_balance_pct'):.1f}% supply",
            "severity": "high"})

    if fv("liq_to_mc_ratio") < 0.02:
        risk_factors.append({"feature": "Likuiditas Sangat Rendah",
            "detail": f"Rasio liq/mcap hanya {fv('liq_to_mc_ratio'):.4f}",
            "severity": "medium"})

    if fi("unique_holders") < 100:
        risk_factors.append({"feature": "Holder Sedikit",
            "detail": f"Hanya {fi('unique_holders')} holder unik",
            "severity": "medium"})
    elif fi("unique_holders") > 1000:
        positive_factors.append(f"Komunitas cukup besar ({fi('unique_holders'):,} holder)")

    # Ambil info model
    metrics      = get_metrics()
    algo_name    = metrics.get("algorithm", "Random Forest")
    n_estimators = metrics.get("n_estimators", 200)

    return {
        "prediction":           pred_class,
        "label":                "Rug Pull" if pred_class == 1 else "Legitimate",
        "rug_probability":      rug_prob,
        "legit_probability":    legit_prob,
        "risk_level":           risk_level,
        "risk_color":           risk_color,
        "risk_icon":            risk_icon,
        "risk_factors":         risk_factors,
        "positive_factors":     positive_factors,
        "feature_values":       {f: features.get(f, 0) for f in MODEL_FEATURES},
        "token_name":           features.get("token_name", "Unknown"),
        "token_symbol":         features.get("token_symbol", "???"),
        "address":              features.get("address", ""),
        "algorithm":            algo_name,
        "n_estimators":         n_estimators,
        "rugcheck_confirmed":   is_confirmed_rugged,
        "rugcheck_score":       rc_score,
    }