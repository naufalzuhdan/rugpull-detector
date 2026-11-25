"""
app.py  —  Flask Web App: Sistem Deteksi Rug Pull Token Solana
"""

import os
import re
import sys
import json
import logging
import subprocess

from flask import Flask, render_template, request, jsonify, redirect, url_for
from dotenv import load_dotenv

# ── Setup path ──
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

load_dotenv(os.path.join(BASE_DIR, ".env"))

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ── Flask init ──
app = Flask(__name__)
secret_key = os.environ.get("SECRET_KEY", "")
if not secret_key or secret_key == "ganti-dengan-random-secret-key-yang-kuat":
    logger.warning("⚠ SECRET_KEY tidak diset atau masih default. Ganti di .env sebelum production!")
    secret_key = "rugpull-detector-dev-UNSAFE"
app.secret_key = secret_key

# ── Base58 validator ──
_BASE58_RE = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')

def is_valid_solana_address(addr: str) -> bool:
    return bool(_BASE58_RE.match(addr))

# ── Auto-train model if not exists ──
MODEL_PATH = os.path.join(BASE_DIR, "model", "random_forest_model.pkl")

def ensure_model_trained():
    if not os.path.exists(MODEL_PATH):
        logger.info("Model belum ada. Memulai training otomatis...")
        try:
            result = subprocess.run(
                [sys.executable, os.path.join(BASE_DIR, "model", "train_model.py")],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                logger.info("Training model berhasil.")
            else:
                logger.error(f"Training gagal: {result.stderr}")
        except Exception as e:
            logger.error(f"Error saat training: {e}")

ensure_model_trained()

# ── Load predictor ──
from model.predictor import get_metrics, predict_token
from utils.data_fetcher import extract_features, MODEL_FEATURES


APP_VERSION = "v11.3-FastFetch"

# ══════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════

@app.route("/")
def index():
    """Halaman utama / Dashboard"""
    metrics = get_metrics()
    return render_template("index.html", metrics=metrics)


@app.route("/detect", methods=["GET", "POST"])
def detect():
    """Halaman deteksi token"""
    if request.method == "GET":
        return render_template("detect.html")

    # POST: proses deteksi
    address = request.form.get("address", "").strip()
    if not address:
        return jsonify({"error": "Alamat token tidak boleh kosong"}), 400

    if len(address) < 32 or len(address) > 44:
        return jsonify({"error": "Format alamat token Solana tidak valid (harus 32–44 karakter)"}), 400

    if not is_valid_solana_address(address):
        return jsonify({"error": "Format alamat tidak valid. Gunakan Base58 (tidak boleh mengandung karakter 0, O, I, atau l)"}), 400

    try:
        logger.info(f"Menganalisis token: {address}")
        features = extract_features(address)  # DexScreener + RugCheck + Solscan, no key needed

        if features.get("error"):
            return jsonify({"error": features["error"]}), 404

        result = predict_token(features)
        result["raw_features"] = features
        result["app_version"]  = APP_VERSION

        logger.info(f"Hasil: {result['label']} ({result['rug_probability']}%) — {address[:8]}...")
        return jsonify({"success": True, "result": result})

    except Exception as e:
        logger.exception(f"Error saat analisis token {address}")
        return jsonify({"error": f"Terjadi kesalahan: {str(e)}"}), 500


@app.route("/model")
def model_info():
    """Halaman informasi model & evaluasi"""
    metrics = get_metrics()
    return render_template("model.html", metrics=metrics)


@app.route("/dataset")
def dataset_view():
    """Halaman dataset preview"""
    import pandas as pd
    csv_path = os.path.join(BASE_DIR, "data", "dataset.csv")
    
    # Ambil parameter pagination dari query string
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 100, type=int)
    show_all = request.args.get('show_all', 'false').lower() == 'true'
    
    # Batasi per_page maksimal untuk keamanan
    if per_page > 10000:
        per_page = 10000
    
    if os.path.exists(csv_path):
        df = pd.read_csv(csv_path)
        rug_count   = int(df["label"].sum())
        legit_count = int((df["label"] == 0).sum())
        total = len(df)
        
        # Jika show_all, tampilkan semua data
        if show_all:
            sample = df.to_dict("records")
            total_pages = 1
            current_page = 1
        else:
            # Pagination
            start_idx = (page - 1) * per_page
            end_idx = start_idx + per_page
            sample = df.iloc[start_idx:end_idx].to_dict("records")
            total_pages = (total + per_page - 1) // per_page  # Ceiling division
            current_page = page
        
        # Pastikan kolom metadata ada
        has_meta = "token_name" in df.columns and "contract_address" in df.columns
    else:
        sample      = []
        total       = 0
        rug_count   = 0
        legit_count = 0
        has_meta    = False
        total_pages = 0
        current_page = 1

    return render_template("dataset.html",
                           sample=sample,
                           total=total,
                           rug_count=rug_count,
                           legit_count=legit_count,
                           features=MODEL_FEATURES,
                           has_meta=has_meta,
                           current_page=current_page,
                           total_pages=total_pages,
                           per_page=per_page,
                           show_all=show_all)


@app.route("/api/retrain", methods=["POST"])
def retrain():
    """Re-train model (bisa dipanggil via API)"""
    try:
        from model.train_model import train_and_evaluate
        _, metrics = train_and_evaluate()
        return jsonify({"success": True, "metrics": metrics})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/auto-collect", methods=["POST"])
def api_auto_collect():
    """Jalankan auto_collect di background thread"""
    import threading
    from model.auto_collect import _collect_progress

    # Cegah double-start jika sudah berjalan
    if _collect_progress.get("running"):
        return jsonify({
            "success": False,
            "message": "Auto-collect sudah berjalan. Tunggu sampai selesai atau cek /api/auto-collect/status."
        }), 409

    data     = request.get_json() or {}
    target   = int(data.get("target", 1000))
    delay    = float(data.get("delay", 1.5))
    balance  = bool(data.get("balance_classes", True))
    require  = bool(data.get("require_complete", True))

    def run_collect():
        try:
            sys.path.insert(0, BASE_DIR)
            from model.auto_collect import run as auto_run, save_dataset, _collect_progress as prog
            df = auto_run(target=target, delay=delay,
                          balance_classes=balance,
                          require_complete_data=require)
            save_dataset(df, replace=True)
            logger.info(f"Auto-collect selesai: {len(df)} baris")
        except Exception as e:
            from model.auto_collect import _collect_progress as prog
            prog["running"] = False
            prog["error"]   = str(e)
            logger.error(f"Auto-collect error: {e}")

    t = threading.Thread(target=run_collect, daemon=True)
    t.start()
    return jsonify({
        "success": True,
        "message": f"Auto-collect dimulai (target={target}, require_complete={require}). "
                   f"Pantau progress di /api/auto-collect/status"
    })


@app.route("/api/auto-collect/status")
def api_auto_collect_status():
    """Status progress auto-collect yang sedang berjalan."""
    try:
        from model.auto_collect import _collect_progress
        return jsonify(_collect_progress)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/cache/clear", methods=["POST"])
def api_cache_clear():
    """Hapus in-memory cache data_fetcher."""
    try:
        from utils.data_fetcher import clear_cache
        n = clear_cache()
        return jsonify({"success": True, "cleared": n})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/cache/stats")
def api_cache_stats():
    """Statistik cache data_fetcher."""
    try:
        from utils.data_fetcher import cache_stats
        return jsonify(cache_stats())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/debug-connectivity")
def debug_connectivity():
    """
    Endpoint debug — hanya bisa diakses dari localhost.
    Buka http://localhost:5000/api/debug-connectivity
    untuk melihat status koneksi semua API yang digunakan sistem.
    """
    # Batasi akses hanya dari localhost
    remote = request.remote_addr or ""
    if remote not in ("127.0.0.1", "::1", "localhost"):
        return jsonify({"error": "Endpoint ini hanya bisa diakses dari localhost"}), 403

    import requests as req
    from concurrent.futures import ThreadPoolExecutor, as_completed as caf

    addr_test = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # USDC
    results   = {}

    # 1. DexScreener
    try:
        r = req.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{addr_test}",
            timeout=10
        )
        pairs = [p for p in r.json().get("pairs", []) if p.get("chainId") == "solana"]
        results["DexScreener"] = {
            "status": r.status_code,
            "ok":     len(pairs) > 0,
            "info":   f"{len(pairs)} pairs ditemukan"
        }
    except Exception as e:
        results["DexScreener"] = {"status": "ERROR", "ok": False, "info": str(e)}

    # 2. Solana RPC (parallel)
    rpc_endpoints = [
        "https://api.mainnet-beta.solana.com",
        "https://solana.publicnode.com",
        "https://rpc.ankr.com/solana",
        "https://endpoints.omniatech.io/v1/sol/mainnet/public",
        "https://solana-api.projectserum.com",
    ]
    payload = {"jsonrpc": "2.0", "id": 1, "method": "getTokenSupply",
               "params": [addr_test]}

    def _test_rpc(url):
        try:
            r = req.post(url, json=payload,
                         headers={"Content-Type": "application/json"}, timeout=8)
            if r.status_code == 200:
                d = r.json()
                supply = d.get("result", {}).get("value", {}).get("uiAmount")
                if supply is not None:
                    return url, True, f"supply={supply:.2e}"
            return url, False, f"HTTP {r.status_code}"
        except Exception as e:
            return url, False, str(e)[:60]

    rpc_results = {}
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(_test_rpc, u): u for u in rpc_endpoints}
        for f in caf(futures, timeout=12):
            url, ok, info = f.result()
            rpc_results[url] = {"ok": ok, "info": info}

    results["Solana_RPC"] = rpc_results
    any_rpc_ok = any(v["ok"] for v in rpc_results.values())
    results["Solana_RPC_summary"] = {
        "ok":   any_rpc_ok,
        "info": f"{sum(v['ok'] for v in rpc_results.values())}/{len(rpc_endpoints)} endpoint OK"
    }

    # 3. RugCheck
    try:
        r = req.get(
            f"https://api.rugcheck.xyz/v1/tokens/{addr_test}/report/summary",
            timeout=10
        )
        if r.status_code == 200:
            d = r.json()
            results["RugCheck"] = {
                "status": 200, "ok": True,
                "info": f"score={d.get('score')} totalHolders={d.get('totalHolders')}"
            }
        else:
            results["RugCheck"] = {"status": r.status_code, "ok": False, "info": r.text[:100]}
    except Exception as e:
        results["RugCheck"] = {"status": "ERROR", "ok": False, "info": str(e)}

    # 4. SolanaFM
    try:
        r = req.get(f"https://api.solana.fm/v0/tokens/{addr_test}", timeout=10)
        results["SolanaFM"] = {
            "status": r.status_code,
            "ok":     r.status_code == 200,
            "info":   "OK" if r.status_code == 200 else r.text[:100]
        }
    except Exception as e:
        results["SolanaFM"] = {"status": "ERROR", "ok": False, "info": str(e)}

    overall = {
        "semua_ok":      all([
            results["DexScreener"]["ok"],
            any_rpc_ok,
        ]),
        "dexscreener_ok": results["DexScreener"]["ok"],
        "rpc_ok":         any_rpc_ok,
        "rugcheck_ok":    results["RugCheck"]["ok"],
        "solanafm_ok":    results["SolanaFM"]["ok"],
        "rekomendasi": (
            "✅ Semua OK!" if results["DexScreener"]["ok"] and any_rpc_ok
            else "⚠️ Solana RPC gagal — holder data mungkin tidak tersedia"
            if results["DexScreener"]["ok"] and not any_rpc_ok
            else "❌ Masalah koneksi internet"
        )
    }
    results["_summary"] = overall

    return jsonify(results)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "False").lower() == "true"
    logger.info(f"Server berjalan di http://localhost:{port} (debug={debug})")
    app.run(host="0.0.0.0", port=port, debug=debug)
