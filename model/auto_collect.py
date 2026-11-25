"""
model/auto_collect.py
══════════════════════════════════════════════════════════════════════
Auto Dataset Collector — Fetch Mandiri Tanpa Daftar Token Tetap

Cara kerja:
  1. DISCOVER  : Ambil token dari DexScreener (trending + new pairs Solana)
  2. FILTER    : Deduplikasi, validasi alamat, cek minimal data tersedia
  3. LABEL     : RugCheck.xyz API (risk score) + validasi 12 kriteria on-chain
  4. COLLECT   : Ekstraksi 12 fitur via DexScreener + Solana RPC
  5. REPEAT    : Terus jalan sampai target terpenuhi

Labeling Logic:
  ─ RUGCHECK score  ≥ 500  → Rug Pull (label=1)
  ─ RUGCHECK score  < 200  → Legitimate (label=0)
  ─ RUGCHECK score 200-499 → On-chain check:
      mint_auth=1 AND top10>80% AND lp_burned<10% → Rug Pull
      else                                         → Legitimate

Cara pakai:
  python model/auto_collect.py                    # target 1000, jeda 2.5s
  python model/auto_collect.py --target 1500      # target 1500
  python model/auto_collect.py --delay 2.0        # jeda lebih lambat
  python model/auto_collect.py --resume           # lanjut sesi terputus
  python model/auto_collect.py --train-after      # langsung training setelah selesai
══════════════════════════════════════════════════════════════════════
"""

import os, sys, time, json, shutil, argparse, logging, random
from datetime import datetime
from collections import defaultdict

import requests
import pandas as pd

# ── Load .env sebelum import data_fetcher ─────────────────────────
# data_fetcher membaca HELIUS_API_KEY saat import; dotenv harus jalan dulu
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    from dotenv import load_dotenv as _ld
    _ld(os.path.join(_BASE, ".env"), override=False)
except ImportError:
    pass

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.data_fetcher import (
    extract_features, MODEL_FEATURES,
    DexScreenerFetcher, SolanaRpcFetcher,
    RugCheckFetcher, SolanaFmFetcher, PumpFunFetcher
)

# ── Logging ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)

# ── Progress tracker (dibaca dari Flask route /api/auto-collect/status) ─────
_collect_progress: dict = {
    "running":      False,
    "records":      0,
    "target":       0,
    "skipped":      0,
    "label_counts": {"0": 0, "1": 0},
    "round":        0,
    "error":        None,
    "started_at":   None,
    "finished_at":  None,
}

# ── Paths ─────────────────────────────────────────────────────
BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR  = os.path.join(BASE_DIR, "data")
CACHE_DIR = os.path.join(DATA_DIR, "auto_cache")
os.makedirs(DATA_DIR,  exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

# ── API ───────────────────────────────────────────────────────
DEXSCREENER_BASE = "https://api.dexscreener.com"
RUGCHECK_BASE    = "https://api.rugcheck.xyz/v1"

HEADERS = {
    "User-Agent": "RugScan-DataCollector/1.0",
    "Accept":     "application/json",
}

# ── Labeling thresholds ───────────────────────────────────────
RUGCHECK_RUG_THRESHOLD   = 500   # score >= ini → Rug Pull
RUGCHECK_LEGIT_THRESHOLD = 200   # score <  ini → Legitimate
# 200-499 → gunakan on-chain heuristic

# On-chain heuristic untuk zona abu-abu (score 200-499)
HEURISTIC_RUG_CONDITIONS = {
    "mint_authority_enabled":   1,      # dev masih bisa cetak token
    "top10_holder_pct":         80.0,   # >80% terkonsentrasi
    "lp_burned_pct":            10.0,   # kurang dari 10% LP burned
    "token_age_days":           14.0,   # token < 14 hari
}


# ══════════════════════════════════════════════════════════════
#  1. DISCOVER — Ambil daftar token dari DexScreener
# ══════════════════════════════════════════════════════════════

def discover_tokens(max_per_source: int = 200) -> list[dict]:
    """
    Ambil token dari 3 sumber DexScreener:
      A) Token trending/boosted (established, banyak legitimate)
      B) New pairs Solana terbaru (banyak token muda, banyak rug pull)
      C) Volume tertinggi Solana (mix legitimacy)

    Return list of {"address": str, "name": str, "symbol": str}
    """
    all_tokens = {}  # address → metadata (deduplikasi otomatis)

    def add_token(address, name, symbol):
        if address and len(address) >= 32 and address not in all_tokens:
            all_tokens[address] = {
                "address": address,
                "name":    name or "Unknown",
                "symbol":  symbol or "???",
            }

    # ── A. Token Boosted / Trending ──────────────────────────
    logger.info("🔍 [A] Fetching trending/boosted tokens...")
    try:
        for endpoint in ["/token-boosts/top/v1", "/token-boosts/latest/v1"]:
            r = requests.get(f"{DEXSCREENER_BASE}{endpoint}",
                             headers=HEADERS, timeout=15)
            if r.status_code == 200:
                items = r.json() if isinstance(r.json(), list) else []
                for item in items:
                    if item.get("chainId") == "solana":
                        add_token(
                            item.get("tokenAddress", ""),
                            item.get("description", "Trending Token"),
                            item.get("tokenAddress", "")[:6],
                        )
                logger.info(f"   {endpoint}: +{len(items)} items")
            time.sleep(0.5)
    except Exception as e:
        logger.warning(f"   Boosted tokens error: {e}")

    # ── B. New Pairs Solana ───────────────────────────────────
    logger.info("🔍 [B] Fetching new Solana pairs...")
    try:
        r = requests.get(
            f"{DEXSCREENER_BASE}/token-profiles/latest/v1",
            headers=HEADERS, timeout=15
        )
        if r.status_code == 200:
            items = r.json() if isinstance(r.json(), list) else []
            for item in items:
                if item.get("chainId") == "solana":
                    add_token(
                        item.get("tokenAddress", ""),
                        item.get("header", "New Token"),
                        item.get("tokenAddress", "")[:6],
                    )
            logger.info(f"   token-profiles: +{len(items)} items")
        time.sleep(0.5)
    except Exception as e:
        logger.warning(f"   Token profiles error: {e}")

    # ── C. Search berbagai keyword untuk variasi ─────────────
    logger.info("🔍 [C] Searching Solana pairs by keyword...")
    keywords = [
        "sol", "meme", "pump", "moon", "doge", "pepe", "shib",
        "cat", "dog", "chad", "based", "rug", "safe", "gem",
        "ai", "gpt", "elon", "baby", "king", "god", "ape",
        "rocket", "diamond", "hands", "lfg", "hodl", "yolo",
        "alpha", "beta", "gamma", "omega", "meta", "verse",
        "rich", "poor", "bull", "bear", "lambo", "token",
        "coin", "degen", "bonk", "wojak", "npc", "wojack",
    ]
    random.shuffle(keywords)

    for kw in keywords[:28]:  # INCREASED: 25 → 28 keywords untuk menembus 500+
        try:
            r = requests.get(
                f"{DEXSCREENER_BASE}/latest/dex/search",
                params={"q": kw}, headers=HEADERS, timeout=15
            )
            if r.status_code == 200:
                pairs = r.json().get("pairs") or []
                sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
                # Randomize which pairs to take
                random.shuffle(sol_pairs)
                # TAKE MORE: ambil 12 pairs per keyword untuk menembus 500+
                for p in sol_pairs[:12]:
                    bt = p.get("baseToken", {})
                    add_token(
                        bt.get("address", ""),
                        bt.get("name", ""),
                        bt.get("symbol", ""),
                    )
            time.sleep(0.3)  # Lebih cepat untuk menambah throughput
        except Exception as e:
            logger.warning(f"   Search '{kw}' error: {e}")
    
    # ── D. Random single-letter searches (NEW!) ──────────────
    logger.info("🔍 [D] Random letter/number searches...")
    try:
        # Search dengan huruf/angka random untuk dapat token yang berbeda
        # INCREASED: 12 -> 22 characters untuk menembus 500+
        random_chars = random.sample("abcdefghijklmnopqrstuvwxyz0123456789", 22)
        for char in random_chars:
            r = requests.get(
                f"{DEXSCREENER_BASE}/latest/dex/search",
                params={"q": char}, headers=HEADERS, timeout=15
            )
            if r.status_code == 200:
                pairs = r.json().get("pairs") or []
                sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
                random.shuffle(sol_pairs)
                # 22 chars × 12 = 264 tokens
                for p in sol_pairs[:12]:
                    bt = p.get("baseToken", {})
                    add_token(
                        bt.get("address", ""),
                        bt.get("name", ""),
                        bt.get("symbol", ""),
                    )
            time.sleep(0.25)  # Lebih cepat
        logger.info(f"   random-chars: +{len(random_chars)} searches")
    except Exception as e:
        logger.warning(f"   Random chars error: {e}")
    
    # ── E. Pairs dari Solana dengan berbagai DEX (NEW!) ─────
    logger.info("🔍 [E] Fetching from specific Solana DEXs...")
    try:
        # Ambil pairs dari berbagai DEX di Solana
        dexes = ["raydium", "orca", "jupiter", "meteora", "phoenix"]
        for dex in dexes:
            r = requests.get(
                f"{DEXSCREENER_BASE}/latest/dex/search",
                params={"q": f"solana {dex}"}, headers=HEADERS, timeout=15
            )
            if r.status_code == 200:
                pairs = r.json().get("pairs") or []
                sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
                random.shuffle(sol_pairs)
                # TAKE MORE: 5 DEXs × 20 = 100 tokens
                for p in sol_pairs[:20]:
                    bt = p.get("baseToken", {})
                    add_token(
                        bt.get("address", ""),
                        bt.get("name", ""),
                        bt.get("symbol", ""),
                    )
            time.sleep(0.3)
        logger.info(f"   dex-specific: checked all {len(dexes)} DEXs")
    except Exception as e:
        logger.warning(f"   DEX-specific error: {e}")
    
    # ── F. Random two-char combinations (NEW!) ───────────────
    logger.info("🔍 [F] Two-character combo searches...")
    try:
        # Generate random 2-char combos untuk variasi ekstrem
        chars = "abcdefghijklmnopqrstuvwxyz0123456789"
        combos = []
        for _ in range(15):  # INCREASED: 10 → 15 combos
            combo = random.choice(chars) + random.choice(chars)
            combos.append(combo)
        
        for combo in combos:
            r = requests.get(
                f"{DEXSCREENER_BASE}/latest/dex/search",
                params={"q": combo}, headers=HEADERS, timeout=15
            )
            if r.status_code == 200:
                pairs = r.json().get("pairs") or []
                sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
                random.shuffle(sol_pairs)
                # TAKE MORE: 15 combos × 10 = 150 tokens
                for p in sol_pairs[:10]:
                    bt = p.get("baseToken", {})
                    add_token(
                        bt.get("address", ""),
                        bt.get("name", ""),
                        bt.get("symbol", ""),
                    )
            time.sleep(0.25)  # Lebih cepat
        logger.info(f"   2-char-combos: +{len(combos)} searches")
    except Exception as e:
        logger.warning(f"   2-char combo error: {e}")
    
    # ── G. Search specific popular memecoins/categories (NEW!) ──
    logger.info("🔍 [G] Popular memecoin & category searches...")
    try:
        # Search untuk kategori populer dan memecoin yang sering ada knock-off nya
        specific_terms = [
            "bonk", "wif", "popcat", "mew", "myro", "samo", "cope", "foxy",
            "billy", "silly", "michi", "ponke", "tremp", "boden", "jup",
            "drift", "kamala", "maga", "book", "bome", "wen", "harambe",
            "retardio", "npc", "gigachad", "sigma", "zyn", "giga",
        ]
        random.shuffle(specific_terms)
        
        for term in specific_terms[:22]:  # 22 terms untuk menembus 500+
            r = requests.get(
                f"{DEXSCREENER_BASE}/latest/dex/search",
                params={"q": term}, headers=HEADERS, timeout=15
            )
            if r.status_code == 200:
                pairs = r.json().get("pairs") or []
                sol_pairs = [p for p in pairs if p.get("chainId") == "solana"]
                random.shuffle(sol_pairs)
                # 22 terms × 10 = 220 tokens
                for p in sol_pairs[:10]:
                    bt = p.get("baseToken", {})
                    add_token(
                        bt.get("address", ""),
                        bt.get("name", ""),
                        bt.get("symbol", ""),
                    )
            time.sleep(0.25)
        logger.info(f"   popular-terms: +{len(specific_terms[:20])} searches")
    except Exception as e:
        logger.warning(f"   Popular terms error: {e}")
    
    # ── H. Volume-sorted Solana pairs (NEW!) ──────────────────
    logger.info("🔍 [H] High-volume Solana pairs...")
    try:
        # Ambil pairs dengan volume tertinggi dari berbagai timeframe
        for sort_param in ["h24", "h6", "h1"]:  # 24h, 6h, 1h volume
            r = requests.get(
                f"{DEXSCREENER_BASE}/latest/dex/pairs/solana",
                headers=HEADERS, timeout=15
            )
            if r.status_code == 200:
                data = r.json()
                pairs = data.get("pairs") or []
                
                # Sort by volume based on timeframe
                if sort_param == "h24":
                    pairs.sort(key=lambda x: float(x.get("volume", {}).get("h24", 0) or 0), reverse=True)
                elif sort_param == "h6":
                    pairs.sort(key=lambda x: float(x.get("volume", {}).get("h6", 0) or 0), reverse=True)
                else:  # h1
                    pairs.sort(key=lambda x: float(x.get("volume", {}).get("h1", 0) or 0), reverse=True)
                
                # Take top 30 from each timeframe
                for p in pairs[:30]:
                    bt = p.get("baseToken", {})
                    add_token(
                        bt.get("address", ""),
                        bt.get("name", ""),
                        bt.get("symbol", ""),
                    )
            time.sleep(0.4)
        logger.info(f"   volume-sorted: 3 timeframes checked")
    except Exception as e:
        logger.warning(f"   Volume-sorted error: {e}")

    result = list(all_tokens.values())
    # Randomize order untuk variasi saat pick nanti
    random.shuffle(result)
    logger.info(f"✅ Total token ditemukan (unik): {len(result)}")
    return result


# ══════════════════════════════════════════════════════════════
#  2. RUGCHECK — Auto-labeling via RugCheck.xyz
# ══════════════════════════════════════════════════════════════

_rugcheck_cache: dict[str, dict] = {}   # in-memory cache per sesi

def get_rugcheck_score(address: str, delay: float = 0.5) -> dict:
    """
    Ambil risk score dari RugCheck.xyz.
    Return: {"score": int, "risks": list, "is_rugged": bool, "status": str}
    """
    if address in _rugcheck_cache:
        return _rugcheck_cache[address]

    result = {"score": -1, "risks": [], "is_rugged": False, "status": "unknown"}

    try:
        url = f"{RUGCHECK_BASE}/tokens/{address}/report/summary"
        r   = requests.get(url, headers=HEADERS, timeout=12)

        if r.status_code == 200:
            data = r.json()
            result["score"]     = int(data.get("score", 0) or 0)
            result["risks"]     = data.get("risks", []) or []
            result["is_rugged"] = bool(data.get("rugged", False))
            result["status"]    = data.get("tokenMeta", {}).get("status", "unknown")

        elif r.status_code == 404:
            result["status"] = "not_found"
        else:
            result["status"] = f"http_{r.status_code}"

    except requests.exceptions.Timeout:
        result["status"] = "timeout"
    except Exception as e:
        result["status"] = f"error: {str(e)[:30]}"
    finally:
        time.sleep(delay)

    _rugcheck_cache[address] = result
    return result


def determine_label(features: dict, rugcheck: dict) -> tuple[int, str]:
    """
    Tentukan label (0=legitimate, 1=rug pull) berdasarkan:
      1. RugCheck score
      2. On-chain heuristic (fallback)

    Return: (label, reason_string)
    """
    score      = rugcheck.get("score", -1)
    is_rugged  = rugcheck.get("is_rugged", False)
    rc_status  = rugcheck.get("status", "unknown")

    # Hard rug — sudah terkonfirmasi
    if is_rugged:
        return 1, f"RugCheck: confirmed rugged (score={score})"

    # RugCheck score tersedia
    if score >= 0:
        if score >= RUGCHECK_RUG_THRESHOLD:
            return 1, f"RugCheck: high risk (score={score})"
        if score < RUGCHECK_LEGIT_THRESHOLD:
            return 0, f"RugCheck: low risk (score={score})"

    # Zona abu-abu (200-499) atau RugCheck tidak tersedia → on-chain heuristic
    mint  = int(features.get("mint_authority_enabled", 0) or 0)
    top10 = float(features.get("top10_holder_pct", 0) or 0)
    lp    = float(features.get("lp_burned_pct", 0) or 0)
    age   = float(features.get("token_age_days", 999) or 999)
    liq   = float(features.get("liquidity", 0) or 0)

    # Hitung risk score on-chain sederhana
    risk_pts = 0
    reasons  = []

    if mint == 1:
        risk_pts += 40
        reasons.append("mint_auth=aktif")
    if top10 > 80:
        risk_pts += 30
        reasons.append(f"top10={top10:.0f}%")
    elif top10 > 65:
        risk_pts += 15
    if lp < 10:
        risk_pts += 25
        reasons.append(f"lp_burned={lp:.0f}%")
    elif lp < 30:
        risk_pts += 10
    if age < 3:
        risk_pts += 20
        reasons.append(f"age={age:.1f}d")
    elif age < 14:
        risk_pts += 8
    if liq < 1000:
        risk_pts += 15
        reasons.append(f"liq=${liq:.0f}")
    elif liq < 5000:
        risk_pts += 5

    reason_str = f"OnChain heuristic (pts={risk_pts}): {', '.join(reasons) or 'no major flags'}"
    if score >= 0:
        reason_str = f"RugCheck={score} + {reason_str}"

    if risk_pts >= 70:
        return 1, reason_str
    elif risk_pts >= 40:
        # Ambiguous — gunakan RugCheck score sebagai tiebreaker
        if score >= 300:
            return 1, reason_str + " [RC tiebreak→rug]"
        return 0, reason_str + " [RC tiebreak→legit]"
    else:
        return 0, reason_str


def validate_required_data(features: dict) -> tuple[bool, str]:
    """
    Validasi KETAT: Token harus punya semua data wajib.
    Return: (is_valid, reason_if_invalid)
    
    Data WAJIB yang harus ada:
    1. Top holder data (top10 atau top1 atau unique_holders > 0)
    2. Authority data (mint & freeze harus ada, bukan None)
    3. Market data (price, liquidity, market_cap > 0)
    4. LP data (lp_burned_pct harus ada)
    5. Age data (token_age_days > 0)
    """
    
    # 1. HOLDER DATA - Minimal 1 dari 3 harus ada
    top10 = features.get("top10_holder_pct", 0)
    top1 = features.get("top1_holder_pct", 0)
    holders = features.get("unique_holders", 0)
    
    if top10 == 0 and top1 == 0 and holders == 0:
        return False, "NO_HOLDER_DATA"
    
    # 2. AUTHORITY DATA - Harus ada (0 atau 1, bukan None)
    mint_auth = features.get("mint_authority_enabled")
    freeze_auth = features.get("freeze_authority_enabled")
    
    if mint_auth is None or freeze_auth is None:
        return False, "NO_AUTHORITY_DATA"
    
    # 3. MARKET DATA - Price & liquidity harus > 0
    price = features.get("price_usd", 0)
    liquidity = features.get("liquidity", 0)
    market_cap = features.get("market_cap", 0)
    
    if price == 0:
        return False, "NO_PRICE_DATA"
    
    if liquidity == 0:
        return False, "NO_LIQUIDITY_DATA"
    
    if market_cap == 0:
        return False, "NO_MARKETCAP_DATA"
    
    # 4. LP DATA - lp_burned_pct harus ada (boleh 0, tapi bukan None)
    lp_burned = features.get("lp_burned_pct")
    if lp_burned is None:
        return False, "NO_LP_DATA"
    
    # 5. AGE DATA - Token age harus > 0
    age = features.get("token_age_days", 0)
    if age == 0:
        return False, "NO_AGE_DATA"
    
    # 6. VALIDASI LOGIKA - Deteksi data yang tidak masuk akal
    if top1 > 100:
        return False, "INVALID_TOP1_>100"
    
    if top10 > 100:
        return False, "INVALID_TOP10_>100"
    
    if top1 > top10 and top10 > 0:
        return False, "INVALID_TOP1>TOP10"
    
    # Semua validasi passed
    return True, "OK"


# ══════════════════════════════════════════════════════════════
#  3. COLLECT — Fetch fitur + label satu token
# ══════════════════════════════════════════════════════════════

def collect_one(token: dict, delay: float = 1.5,
                require_complete_data: bool = True) -> dict | None:
    """
    Fetch fitur + auto-label satu token.
    
    require_complete_data=True (DEFAULT) -> SKIP token tanpa data lengkap
    require_complete_data=False -> Tetap simpan meski data tidak lengkap (NOT RECOMMENDED)
    """
    address = token["address"]

    try:
        features = extract_features(address)
        if features.get("error"):
            return None

        # VALIDASI KETAT: Cek kelengkapan data
        if require_complete_data:
            is_valid, reason = validate_required_data(features)
            if not is_valid:
                logger.info(f"  SKIP ({reason}) [{address[:14]}]")
                return None

        rugcheck = {
            "score":     features.get("rugcheck_score", -1),
            "is_rugged": features.get("rugcheck_rugged", False),
            "status":    "ok" if features.get("rugcheck_score", -1) >= 0
                         else "not_found",
        }

        label, reason = determine_label(features, rugcheck)

        row = {
            "contract_address": address,
            "token_name":       features.get("token_name", token["name"]),
            "token_symbol":     features.get("token_symbol", token["symbol"]),
            "rugcheck_score":   rugcheck.get("score", -1),
            "label_reason":     reason[:120],
            "holder_source":    features.get("_holder_source", "none"),
        }
        for feat in MODEL_FEATURES:
            row[feat] = float(features.get(feat, 0) or 0)
        row["label"] = label

        # ── FIX: set holder_data_ok ───────────────────────────────────
        row["holder_data_ok"] = 1 if (
            float(features.get("top10_holder_pct", 0) or 0) > 0 or
            float(features.get("top1_holder_pct",  0) or 0) > 0 or
            int(features.get("unique_holders", 0) or 0) > 0
        ) else 0

        return row

    except Exception as e:
        logger.error(f"  ERROR {address[:12]}: {e}")
        return None
    finally:
        time.sleep(delay)


# ══════════════════════════════════════════════════════════════
#  4. MAIN LOOP — Discover → Collect → Repeat
# ══════════════════════════════════════════════════════════════

def run(target: int = 1000, delay: float = 1.5,
        resume: bool = False, balance_classes: bool = True,
        require_complete_data: bool = True):
    """
    Loop utama pengumpulan data otomatis.
    
    Parameters:
        target: Jumlah token target yang ingin dikumpulkan
        delay: Jeda antar request (detik)
        resume: Lanjutkan dari session sebelumnya
        balance_classes: Balance antara rug vs legitimate
        require_complete_data: SKIP token dengan data tidak lengkap (DEFAULT=True)
    
    Terus discover token baru jika stok habis sebelum target tercapai.
    """
    # ── Cache session ──────────────────────────────────────
    session_id   = datetime.now().strftime("%Y%m%d")
    cache_path   = os.path.join(CACHE_DIR, f"auto_{session_id}.json")
    done_set     = set()
    records      = []
    skipped      = 0

    # ── Init progress tracker ──────────────────────────────
    _collect_progress.update({
        "running":      True,
        "records":      0,
        "target":       target,
        "skipped":      0,
        "label_counts": {"0": 0, "1": 0},
        "round":        0,
        "error":        None,
        "started_at":   datetime.now().isoformat(),
        "finished_at":  None,
    })

    if resume and os.path.exists(cache_path):
        with open(cache_path) as f:
            cache    = json.load(f)
        done_set = set(cache.get("done", []))
        records  = cache.get("records", [])
        logger.info(f"Resume: {len(records)} baris dari cache, {len(done_set)} token sudah diproses")

    def save_cache():
        with open(cache_path, "w") as f:
            json.dump({"done": list(done_set), "records": records}, f)

    # ── Stats ──────────────────────────────────────────────
    label_counts = defaultdict(int, {r["label"]: 1 for r in records})

    def should_skip_label(lbl: int) -> bool:
        """Jika balance aktif: skip jika satu kelas sudah 60% dari total"""
        if not balance_classes:
            return False
        total = sum(label_counts.values())
        if total < 50:
            return False
        return label_counts[lbl] / total > 0.60

    print("=" * 68)
    print("  AUTO DATASET COLLECTOR — DexScreener + RugCheck.xyz")
    print("=" * 68)
    print(f"  Target      : {target} baris")
    print(f"  Jeda API    : {delay}s per token (min 2.0 direkomendasikan)")
    print(f"  Balance     : {'YA (max 60:40)' if balance_classes else 'TIDAK'}")
    print(f"  Resume      : {'YA' if resume else 'TIDAK'}")
    print(f"  RugCheck    : api.rugcheck.xyz/v1 (gratis)")
    print("=" * 68 + "\n")

    round_num = 0

    while len(records) < target:
        round_num += 1
        still_needed = target - len(records)
        _collect_progress["round"] = round_num  # ← update progress
        print(f"\n{'─'*68}")
        print(f"  ROUND {round_num} — sudah: {len(records)}/{target} baris, butuh: {still_needed}")
        print(f"  Label dist : Legit={label_counts[0]} | Rug={label_counts[1]}")
        print(f"{'─'*68}")

        # Discover token baru
        all_tokens = discover_tokens(max_per_source=300)
        new_tokens = [t for t in all_tokens if t["address"] not in done_set]
        random.shuffle(new_tokens)   # acak urutan agar beragam

        if not new_tokens:
            logger.warning("Tidak ada token baru dari DexScreener. Tunggu 30 detik...")
            time.sleep(30)
            continue

        print(f"  Token baru  : {len(new_tokens)} (dari {len(all_tokens)} total unik)")
        print()

        start_time = time.time()
        processed  = 0

        for token in new_tokens:
            if len(records) >= target:
                break

            addr = token["address"]
            done_set.add(addr)
            processed += 1

            logger.info(
                f"  [{len(records):04d}/{target}] "
                f"{token['symbol'][:10]:<12} | {addr[:14]}..."
            )

            row = collect_one(token, delay=delay, 
                            require_complete_data=require_complete_data)

            if row is None:
                skipped += 1
                continue

            lbl = row["label"]

            # Skip jika kelas ini sudah terlalu dominan
            if should_skip_label(lbl):
                logger.info(
                    f"    SKIP label={lbl} (balance: "
                    f"L={label_counts[0]} R={label_counts[1]})"
                )
                skipped += 1
                continue

            records.append(row)
            label_counts[lbl] += 1

            # ── Update progress tracker ────────────────────────────
            _collect_progress["records"]      = len(records)
            _collect_progress["skipped"]      = skipped
            _collect_progress["label_counts"] = {"0": label_counts[0], "1": label_counts[1]}

            # Progress bar
            pct  = len(records) / target * 100
            bar  = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
            ela  = time.time() - start_time
            rate = processed / ela if ela > 0 else 1
            eta  = (target - len(records)) / rate if rate > 0 else 0
            print(
                f"\r  [{bar}] {pct:5.1f}% | "
                f"L={label_counts[0]} R={label_counts[1]} | "
                f"ETA: {int(eta//60)}m{int(eta%60):02d}s   ",
                end="", flush=True
            )

            # Simpan cache setiap 25 token berhasil
            if len(records) % 25 == 0:
                save_cache()

        print()  # newline setelah progress bar
        save_cache()

        if len(new_tokens) < 20 and len(records) < target:
            logger.info("Token discovery mengembalikan sedikit hasil. Tunggu 15 detik...")
            time.sleep(15)

    # ── Selesai ────────────────────────────────────────────
    _collect_progress.update({
        "running":      False,
        "records":      len(records),
        "skipped":      skipped,
        "label_counts": {"0": label_counts[0], "1": label_counts[1]},
        "finished_at":  datetime.now().isoformat(),
    })
    print(f"\n{'='*68}")
    print(f"  ✅ SELESAI: {len(records)} baris terkumpul")
    print(f"  Label   : Legitimate={label_counts[0]} | Rug Pull={label_counts[1]}")

    # Statistik kelengkapan data holder
    has_holder  = sum(1 for r in records if r.get("holder_data_ok", 0) == 1)
    no_holder   = len(records) - has_holder
    holder_pct  = has_holder / len(records) * 100 if records else 0
    print(f"  Holder  : {has_holder} baris ada data ({holder_pct:.0f}%) | "
          f"{no_holder} baris kosong ({100-holder_pct:.0f}%)")
    if no_holder > 0 and not require_complete_data:
        print(f"  ⚠  {no_holder} baris top10/top1 = 0.0 karena public RPC tidak support")
        print(f"     getTokenLargestAccounts. Model akan kurang akurat untuk fitur ini.")
        if not os.environ.get("HELIUS_API_KEY", "").strip():
            print(f"  💡 Solusi: daftar GRATIS di https://dev.helius.xyz")
            print(f"             → isi HELIUS_API_KEY di .env → jalankan ulang")
    print(f"  Skipped : {skipped} token (error/tidak valid/balance)")
    print(f"{'='*68}\n")

    return pd.DataFrame(records)


# ══════════════════════════════════════════════════════════════
#  5. SAVE & REPLACE
# ══════════════════════════════════════════════════════════════

def save_dataset(df: pd.DataFrame, replace: bool = True) -> tuple[str, str | None]:
    """Simpan dataset dengan backup otomatis."""
    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    rug_n    = int(df["label"].sum())
    legit_n  = int((df["label"] == 0).sum())
    fname    = f"dataset_auto_{ts}_L{legit_n}_R{rug_n}_T{len(df)}.csv"
    real_path = os.path.join(DATA_DIR, fname)

    # Kolom untuk training (drop kolom debug)
    train_cols = ["contract_address", "token_name", "token_symbol"] + MODEL_FEATURES + ["label"]
    df_train   = df[[c for c in train_cols if c in df.columns]]
    df_train.to_csv(real_path, index=False)
    logger.info(f"Dataset disimpan: {real_path}")

    main_path = None
    if replace:
        main_path = os.path.join(DATA_DIR, "dataset.csv")
        if os.path.exists(main_path):
            bk = os.path.join(DATA_DIR, f"dataset_backup_{ts}.csv")
            shutil.copy(main_path, bk)
            logger.info(f"Backup lama: {bk}")
        df_train.to_csv(main_path, index=False)
        logger.info(f"Dataset utama diperbarui: {main_path}")

    # Simpan juga versi lengkap dengan debug info
    debug_path = os.path.join(DATA_DIR, fname.replace(".csv", "_debug.csv"))
    df.to_csv(debug_path, index=False)

    return real_path, main_path


# ══════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Auto-collect dataset token Solana dari DexScreener + RugCheck"
    )
    parser.add_argument("--target",      type=int,   default=1000,
                        help="Jumlah baris target (default: 1000)")
    parser.add_argument("--delay",       type=float, default=2.5,
                        help="Jeda antar request API (detik, default: 2.5)")
    parser.add_argument("--no-replace",  action="store_true",
                        help="Jangan ganti dataset.csv")
    parser.add_argument("--no-balance",      action="store_true",
                        help="Jangan balance kelas (biarkan imbalanced)")
    parser.add_argument("--allow-incomplete", action="store_true",
                        help="IZINKAN token dengan data tidak lengkap (NOT RECOMMENDED). "
                             "Default: SKIP token dengan missing data.")
    parser.add_argument("--resume",      action="store_true",
                        help="Lanjutkan sesi sebelumnya yang terputus")
    parser.add_argument("--train-after", action="store_true",
                        help="Langsung training setelah selesai")
    parser.add_argument("--preview",     action="store_true",
                        help="Preview 5 token saja (test koneksi)")
    args = parser.parse_args()

    # ── Preview mode ────────────────────────────────────────
    if args.preview:
        print("\n🔍 PREVIEW MODE — Test koneksi & labeling...\n")

        tokens = discover_tokens(max_per_source=50)
        print(f"Token ditemukan: {len(tokens)}")

        print("\n--- Test fetch + label 5 token pertama ---")
        for tok in tokens[:5]:
            print(f"\nToken: {tok['name']} ({tok['symbol']}) — {tok['address'][:16]}...")
            rc = get_rugcheck_score(tok["address"])
            print(f"  RugCheck: score={rc['score']}, rugged={rc['is_rugged']}, status={rc['status']}")
            feats = extract_features(tok["address"])
            if not feats.get("error"):
                lbl, reason = determine_label(feats, rc)
                print(f"  Label: {'🚨 RUG PULL' if lbl else '✅ LEGITIMATE'}")
                print(f"  Reason: {reason}")
                print(f"  Fitur: top10={feats.get('top10_holder_pct')}% | "
                      f"mint={feats.get('mint_authority_enabled')} | "
                      f"lp={feats.get('lp_burned_pct')}% | "
                      f"age={feats.get('token_age_days')}d | "
                      f"liq=${feats.get('liquidity'):,.0f}")
            else:
                print(f"  Error: {feats['error']}")
        sys.exit(0)

    # ── Full collection ──────────────────────────────────────
    print("\n" + "=" * 68)
    print("  INFORMASI AUTO-COLLECT")
    print("=" * 68)
    print(f"  Script akan terus berjalan sampai {args.target} baris terkumpul.")
    print(f"  Token diambil OTOMATIS dari DexScreener (trending + new pairs).")
    print(f"  Label OTOMATIS via RugCheck.xyz + validasi on-chain.")
    print(f"  Progress disimpan setiap 25 baris — bisa di-resume jika terputus.")
    print(f"  Data lama di-backup otomatis sebelum diganti.")
    print()
    print(f"  VALIDASI DATA:")
    if args.allow_incomplete:
        print(f"    Mode: ALLOW INCOMPLETE (token tanpa data lengkap tetap disimpan)")
        print(f"    WARNING: Data tidak lengkap akan menurunkan akurasi model!")
    else:
        print(f"    Mode: REQUIRE COMPLETE (DEFAULT)")
        print(f"    Token akan di-SKIP jika:")
        print(f"      - Holder data = 0 (top10, top1, unique_holders)")
        print(f"      - Authority data hilang (mint/freeze = None)")
        print(f"      - Price/liquidity/market cap = 0")
        print(f"      - LP burned data hilang")
        print(f"      - Token age = 0")
        print(f"      - Data tidak logis (top1>top10, top10>100%)")
    print("=" * 68)

    if args.delay < 1.5:
        print("  WARNING: delay < 1.5s berisiko 429 Rate Limit dari Solana RPC!")
        print("     Disarankan: --delay 2.5 atau lebih")
    
    # ── Startup test Helius ──────────────────────────────────────────
    _helius_key = os.environ.get("HELIUS_API_KEY", "").strip()
    if _helius_key:
        print()
        print("  Testing Helius connectivity...")
        _helius_url = f"https://mainnet.helius-rpc.com/?api-key={_helius_key}"
        
        # Simple test: try a basic RPC call
        try:
            import requests
            test_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getHealth"
            }
            resp = requests.post(_helius_url, json=test_payload, timeout=10)
            _helius_ok = resp.status_code == 200
        except Exception as e:
            print(f"     ⚠  Helius test failed: {e}")
            _helius_ok = False
        
        if _helius_ok:
            print("  ✅ Helius siap! Data holder akan dikumpulkan untuk semua token.")
        else:
            print("  ⚠  Helius tidak dapat diakses.")
            print("     Holder data mungkin tidak tersedia atau menggunakan fallback.")
    else:
        print("  ⚠  HELIUS_API_KEY tidak diset → holder data tidak akan tersedia!")
    print()

    try:
        confirm = input("\n  Mulai auto-collect? (y/n): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        confirm = "y"

    if confirm != "y":
        print("  Dibatalkan.")
        sys.exit(0)

    try:
        df = run(
            target                = args.target,
            delay                 = args.delay,
            resume                = args.resume,
            balance_classes       = not args.no_balance,
            require_complete_data = not args.allow_incomplete,  # DEFAULT: skip incomplete
        )

        real_path, main_path = save_dataset(df, replace=not args.no_replace)
        print(f"✅ Dataset auto : {real_path}")
        if main_path:
            print(f"✅ Dataset utama: {main_path}")

        if args.train_after:
            print("\n" + "="*68)
            print("  🌲 TRAINING MODEL...")
            print("="*68 + "\n")
            sys.path.insert(0, BASE_DIR)
            from model.train_model import train_and_evaluate
            train_and_evaluate()
            print("\n✅ Selesai! Buka http://localhost:5000/model")
        else:
            print(f"\n  → Jalankan training: python model/train_model.py")

    except KeyboardInterrupt:
        print("\n\n  ⚠️ Dihentikan. Progress tersimpan di cache.")
        print(f"  → Lanjutkan: python model/auto_collect.py --resume")
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)
