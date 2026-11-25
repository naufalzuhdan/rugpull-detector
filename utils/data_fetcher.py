"""
utils/data_fetcher.py — RugScan v11 FINAL + v11.3 LP DETECTION AGGRESSIVE
══════════════════════════════════════════════════════════════════════════════
v11.3 Improvements:
  ✅ Check 50 holders (tidak hanya 20) untuk catch LP di ranking lebih bawah
  ✅ Aggressive pattern matching untuk Raydium/Orca addresses
  ✅ Extended keyword detection untuk burn/LP pools
  ✅ Detailed logging setiap LP detection
══════════════════════════════════════════════════════════════════════════════
"""

import os
import re
import time
import logging
import threading
from typing import Optional

import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Load .env SEBELUM membaca env vars ────────────────────────────
try:
    from dotenv import load_dotenv as _load_dotenv
    _env_path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), ".env")
    _load_dotenv(_env_path, override=False)
except ImportError:
    pass

logger = logging.getLogger(__name__)

# ── Baca konfigurasi ────────────────────────────────────────────────
def _get_helius_key() -> str:
    return os.environ.get("HELIUS_API_KEY", "").strip()

def _get_custom_rpc() -> str:
    return os.environ.get("CUSTOM_RPC_URL", "").strip()

DEXSCREENER_BASE     = "https://api.dexscreener.com"
RUGCHECK_BASE        = "https://api.rugcheck.xyz/v1"
SOLANAFM_BASE        = "https://api.solana.fm/v0"
PUMPFUN_BASE         = "https://frontend-api.pump.fun"

_PUBLIC_RPC_ENDPOINTS = [
    "https://api.mainnet-beta.solana.com",
    "https://solana.publicnode.com",
    "https://rpc.ankr.com/solana",
    "https://endpoints.omniatech.io/v1/sol/mainnet/public",
    "https://solana-api.projectserum.com",
]

def _build_rpc_endpoints() -> list:
    helius_key = _get_helius_key()
    custom_rpc = _get_custom_rpc()
    eps = []
    if helius_key:
        eps.append(f"https://mainnet.helius-rpc.com/?api-key={helius_key}")
    if custom_rpc:
        eps.append(custom_rpc)
    eps += _PUBLIC_RPC_ENDPOINTS
    return eps

_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; RugScan/1.0; Research)",
    "Accept":     "application/json",
})

_cache: dict = {}
_cache_lock = threading.Lock()

# ══════════════════════════════════════════════════════════════════
#  GET API Calls
# ══════════════════════════════════════════════════════════════════

def _get(url: str, params: dict = None, retries: int = 2,
         timeout: int = 15, cache_key: str = None) -> Optional[dict]:
    if cache_key:
        with _cache_lock:
            if cache_key in _cache:
                return _cache[cache_key]
    for attempt in range(retries):
        try:
            r = _session.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 404:
                return None
            if r.status_code >= 500:
                time.sleep(1)
                continue
            r.raise_for_status()
            data = r.json()
            if cache_key:
                with _cache_lock:
                    _cache[cache_key] = data
            return data
        except Exception as e:
            logger.debug(f"GET {url[:40]}: {e}")
    return None

# ══════════════════════════════════════════════════════════════════
#  RPC Calls
# ══════════════════════════════════════════════════════════════════

def _rpc_call(url: str, method: str, params: list,
              timeout_sec: int = 15, max_retries: int = 3) -> Optional[dict]:
    for attempt in range(max_retries):
        try:
            r = _session.post(
                url,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                headers={"Content-Type": "application/json"},
                timeout=timeout_sec,
            )
            if r.status_code != 200:
                logger.warning(f"  RPC HTTP {r.status_code} [{method}]")
                return None
            resp = r.json()
            if "error" in resp:
                err  = resp["error"]
                code = err.get("code", 0) if isinstance(err, dict) else 0
                msg  = err.get("message", "") if isinstance(err, dict) else str(err)
                if code == -32603 and "overload" in str(msg).lower():
                    if attempt < max_retries - 1:
                        wait = 3 * (attempt + 1)
                        logger.warning(f"  Helius overloaded, retry in {wait}s...")
                        time.sleep(wait)
                        continue
                logger.warning(f"  RPC error [{method}]: {str(msg)[:100]}")
                return None
            return resp.get("result")
        except requests.exceptions.Timeout:
            logger.warning(f"  RPC timeout [{method}]")
            if attempt < max_retries - 1:
                time.sleep(2)
        except Exception as e:
            logger.debug(f"  RPC: {e}")
            return None
    return None

def _rpc_batch_call(url: str, calls: list, timeout_sec: int = 15) -> dict:
    payload = [
        {"jsonrpc": "2.0", "id": c["id"], "method": c["method"], "params": c["params"]}
        for c in calls
    ]
    try:
        r = _session.post(url, json=payload,
                          headers={"Content-Type": "application/json"},
                          timeout=timeout_sec)
        if r.status_code != 200:
            logger.warning(f"  RPC batch HTTP {r.status_code}")
            return {}
        responses = r.json()
        if not isinstance(responses, list):
            return {}
        out = {}
        for resp in responses:
            if resp is None or not isinstance(resp, dict):
                continue
            if "error" in resp:
                continue
            rid = resp.get("id")
            result = resp.get("result")
            if rid is not None and result is not None:
                out[rid] = result
        return out
    except Exception as e:
        logger.debug(f"  RPC batch: {e}")
    return {}

def _rpc_batch_all(mint: str) -> dict:
    cache_key = f"rpc_batch:{mint}"
    with _cache_lock:
        if cache_key in _cache:
            return _cache[cache_key]

    helius_key = _get_helius_key()
    custom_rpc = _get_custom_rpc()

    calls = [
        {"id": 1, "method": "getAccountInfo",
         "params": [mint, {"encoding": "jsonParsed"}]},
        {"id": 2, "method": "getTokenSupply",
         "params": [mint]},
        {"id": 3, "method": "getTokenLargestAccounts",
         "params": [mint]},
    ]

    def _parse_batch(raw: dict) -> dict:
        out = {}
        if 1 in raw:
            out["accountInfo"] = raw[1]
        if 2 in raw:
            out["tokenSupply"] = raw[2]
        if 3 in raw:
            out["largestAccounts"] = raw[3]
        return out

    primary_url = None
    if helius_key:
        primary_url = f"https://mainnet.helius-rpc.com/?api-key={helius_key}"
    elif custom_rpc:
        primary_url = custom_rpc

    if primary_url:
        logger.info(f"  RPC batch → {primary_url[:45]}...")
        raw = _rpc_batch_call(primary_url, calls, timeout_sec=15)
        
        if not raw:
            logger.warning("  RPC batch gagal, individual calls...")
            out = {}
            ai = _rpc_call(primary_url, "getAccountInfo", 
                          [mint, {"encoding": "jsonParsed"}], timeout_sec=15)
            if ai:
                out["accountInfo"] = ai
            ts = _rpc_call(primary_url, "getTokenSupply", [mint], timeout_sec=15)
            if ts:
                out["tokenSupply"] = ts
        else:
            out = _parse_batch(raw)

        la_value = (out.get("largestAccounts") or {}).get("value") if isinstance(out.get("largestAccounts"), dict) else None
        has_la_data = bool(la_value)

        if not has_la_data:
            logger.info("  getTokenLargestAccounts...")
            la = _rpc_call(primary_url, "getTokenLargestAccounts",
                           [mint, {"commitment": "finalized"}], timeout_sec=15)
            if la is None:
                la = _rpc_call(primary_url, "getTokenLargestAccounts",
                               [mint], timeout_sec=15)

            la_value = (la.get("value") if isinstance(la, dict) else la) if la else None
            if la_value:
                out["largestAccounts"] = la
                logger.info(f"  getTokenLargestAccounts: OK ✅ ({len(la_value)} akun)")
            else:
                logger.info("  Helius getTokenAccounts...")
                ta_accounts = _helius_get_token_accounts(mint, primary_url, limit=50)
                if ta_accounts:
                    out["largestAccounts"] = {"context": {}, "value": ta_accounts}
                    out["_holder_via_getTokenAccounts"] = True
                    logger.info(f"  getTokenAccounts: OK ✅ ({len(ta_accounts)} akun)")

        if out:
            out["_rpc_url"] = primary_url  # ← Store RPC URL for account info batch queries
            with _cache_lock:
                _cache[cache_key] = out
            return out

    public_eps = [
        "https://api.mainnet-beta.solana.com",
        "https://solana.publicnode.com",
        "https://rpc.ankr.com/solana",
    ]

    logger.info("  RPC → public endpoints...")
    out = {}
    used_url = None

    for url in public_eps:
        raw = _rpc_batch_call(url, calls[:2], timeout_sec=12)
        if raw:
            out.update(_parse_batch(raw))
            used_url = url
            break

    for url in public_eps:
        res = _rpc_call(url, "getTokenLargestAccounts", [mint], timeout_sec=12)
        if res is not None:
            out["largestAccounts"] = res
            used_url = url
            break

    if out:
        if used_url:
            out["_rpc_url"] = used_url  # ← Store RPC URL
        with _cache_lock:
            _cache[cache_key] = out
    return out

def _helius_get_token_accounts(mint: str, helius_url: str,
                               limit: int = 50) -> Optional[list]:
    for attempt in range(3):
        try:
            r = _session.post(
                helius_url,
                json={"jsonrpc": "2.0", "id": "helius-ta",
                      "method": "getTokenAccounts",
                      "params": {"mint": mint, "limit": limit}},
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            if r.status_code != 200:
                return None
            resp = r.json()
            if "error" in resp:
                err = resp["error"]
                code = err.get("code", 0) if isinstance(err, dict) else 0
                msg = err.get("message", "") if isinstance(err, dict) else str(err)
                if code == -32603 and "overload" in str(msg).lower():
                    if attempt < 2:
                        time.sleep(3 * (attempt + 1))
                        continue
                return None
            result = resp.get("result", {})
            accounts = result.get("token_accounts") or result.get("value") or []
            return accounts if accounts else None
        except Exception as e:
            logger.debug(f"  Helius getTokenAccounts: {e}")
            return None
    return None

def _parse_ui_amount(obj: dict, decimals: int = 9) -> float:
    ui_str = obj.get("uiAmountString") or str(obj.get("uiAmount") or "")
    try:
        val = float(ui_str) if ui_str else 0.0
    except (ValueError, TypeError):
        val = 0.0
    if val == 0 and obj.get("amount"):
        try:
            val = int(obj["amount"]) / (10 ** decimals)
        except Exception:
            pass
    return val

# ══════════════════════════════════════════════════════════════════
#  Fetcher Classes
# ══════════════════════════════════════════════════════════════════

class DexScreenerFetcher:
    def get_token_pairs(self, address: str) -> Optional[list]:
        data = _get(f"{DEXSCREENER_BASE}/latest/dex/tokens/{address}",
                    cache_key=f"dex:{address}", timeout=15)
        if not data:
            return None
        pairs = [p for p in (data.get("pairs") or [])
                 if p.get("chainId") == "solana"]
        pairs.sort(key=lambda p: float(
            p.get("liquidity", {}).get("usd", 0) or 0), reverse=True)
        return pairs if pairs else None

class RugCheckFetcher:
    def get_summary(self, address: str) -> Optional[dict]:
        return _get(f"{RUGCHECK_BASE}/tokens/{address}/report/summary",
                    cache_key=f"rc:{address}", timeout=12, retries=2)

    def get_full(self, address: str) -> Optional[dict]:
        return _get(f"{RUGCHECK_BASE}/tokens/{address}/report",
                    cache_key=f"rc:full:{address}", timeout=12, retries=1)

class SolanaFmFetcher:
    def get_holders(self, address: str, limit: int = 10) -> Optional[dict]:
        return _get(f"{SOLANAFM_BASE}/tokens/{address}/holders",
                    params={"limit": limit, "page": 1},
                    cache_key=f"sfm:{address}", timeout=12, retries=1)

class PumpFunFetcher:
    def get_coin(self, address: str) -> Optional[dict]:
        return _get(f"{PUMPFUN_BASE}/coins/{address}",
                    cache_key=f"pf:{address}", timeout=10, retries=1)

class SolanaRpcFetcher:
    pass

# ══════════════════════════════════════════════════════════════════
#  BURN / LP DETECTION - v12 ON-CHAIN DETECTION
# ══════════════════════════════════════════════════════════════════

_BURN_ADDRESSES = {
    "1nc1nerator11111111111111111111111111111111",
    "11111111111111111111111111111111",
    "1111111111111111111111111111111111111111111",  # NEW: 43 char burn address
    "So11111111111111111111111111111111111111112",
    "TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM",
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",
    "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",
    "9w959DqTapjvU72kXgAAB3mH5dCzMt5tFJiHpUfLTnfp",
    "E7YMB5xtmgSuc5DkCRXMPoMRo2VXoQyvP9sjw1XSp1F1",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe8bv",
    "11111111111111111111111111111112",
    "SysvarC1ock11111111111111111111111111111111",
    "LBUZKhRxPF3XUpBCXVBPU15z6TG5V5ZNcvvBHp5DKwb",
    "oaSys2yNyckjXoXsoUZkxy7gHmbqKrSn9tguSmYipAw",
    # Common burn addresses
    "BURN1111111111111111111111111111111111111",
    "Dead11111111111111111111111111111111111111",
}

# Known LP Program IDs (Raydium, Orca, Marinade, etc)
_LP_PROGRAM_IDS = {
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # Raydium Legacy
    "5q546fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1",  # Raydium Fusion
    "9w959DqTapjvU72kXgAAB3mH5dCzMt5tFJiHpUfLTnfp",  # Orca Program
    "EhYXq3bK8sSH6u1EHzT1xErKz1H8sFJoEHhfT6MsLR9H",  # Orca Whirlpool
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",  # Marinade (stSOL pool)
    "SysvarC1ock11111111111111111111111111111111",    # Sysvar (burn marker)
    "11111111111111111111111111111112",               # System Program (burn marker)
}

def _get_account_info_batch(addresses: list[str], rpc_url: str, 
                           timeout_sec: int = 15, chunk_size: int = 5) -> dict[str, Optional[dict]]:
    """
    Batch fetch account info untuk multiple addresses via RPC.
    Uses small chunks (5 per batch) untuk avoid payload size limits (413 errors).
    Falls back to individual calls untuk reliability.
    Returns: {address: account_info_dict or None}
    """
    if not addresses:
        return {}
    
    result = {}
    
    # Process in small chunks untuk avoid 413 payload too large
    # Even better: use individual calls for reliability (avoid batch altogether)
    for addr in addresses:
        try:
            info = _rpc_call(rpc_url, "getAccountInfo",
                            [addr, {"encoding": "jsonParsed"}],
                            timeout_sec=min(timeout_sec, 8), max_retries=1)
            result[addr] = info
        except Exception as e:
            logger.debug(f"  Account info fetch failed for {addr[:10]}: {e}")
            result[addr] = None
        
        time.sleep(0.05)  # Small delay to avoid rate limiting
    
    return result

def _is_lp_account(account_info: dict) -> bool:
    """
    Tentukan apakah account adalah LP pool berdasarkan on-chain characteristics.
    
    Indicators:
    - Frozen state = True
    - Delegate address exists (LP locked)
    - Token owner (parsed.info.owner) = known LP program
    """
    if not account_info or not isinstance(account_info, dict):
        return False
    
    value = account_info.get("value")
    if not value or not isinstance(value, dict):
        return False
    
    # Extract parsed token account info
    data = value.get("data", {})
    if not isinstance(data, dict):
        return False
    
    parsed = data.get("parsed", {})
    if not isinstance(parsed, dict):
        return False
    
    info = parsed.get("info", {})
    if not isinstance(info, dict):
        return False
    
    # Check 1: Account is frozen
    is_frozen = info.get("state", "") == "frozen"
    if is_frozen:
        logger.debug(f"      ✅ Token account frozen → likely LP")
        return True
    
    # Check 2: Has delegate (LP locked indicator)
    delegate = info.get("delegate")
    if delegate and delegate != "":
        logger.debug(f"      ✅ Delegate exists → LP locked")
        return True
    
    # Check 3: Token owner is known LP program
    # NOTE: This is info.owner (token account owner), NOT value.owner (lamport account owner)
    token_owner = info.get("owner", "")
    if token_owner in _LP_PROGRAM_IDS:
        logger.debug(f"      ✅ Token owned by LP program ({token_owner[:10]}...) → LP")
        return True
    
    return False

def _is_burn_or_lp(addr: str, account_info: dict = None) -> bool:
    """
    Deteksi burn/LP dengan kombinasi:
    1. On-chain characteristics (jika account_info tersedia)
    2. Pattern matching (fallback)
    
    IMPROVED: Deteksi burn address lebih agresif + check owner is zero/closed
    """
    if not addr:
        return False
    
    # Check 1: Hardcoded burn addresses
    if addr in _BURN_ADDRESSES:
        logger.debug(f"      ✅ {addr[:20]}... → HARDCODED_BURN")
        return True
    
    # Check 2: All 1s (any length >= 20)
    if len(addr) >= 20 and all(c == "1" for c in addr):
        logger.debug(f"      ✅ {addr[:20]}... → ALL_1s_BURN")
        return True
    
    # Check 3: Check if account is CLOSED (owner = 0 or no data) - THIS IS KEY FOR BURNED LP
    if account_info:
        value = account_info.get("value")
        # If value is None, account doesn't exist (burned/closed)
        if value is None:
            logger.debug(f"      ✅ {addr[:20]}... → CLOSED_ACCOUNT (burned)")
            return True
        
        # Check if owner is system program or zero (indicates burn)
        if isinstance(value, dict):
            owner = value.get("owner", "")
            # System program ownership = burned
            if owner == "11111111111111111111111111111111":
                logger.debug(f"      ✅ {addr[:20]}... → SYSTEM_PROGRAM_OWNER (burned)")
                return True
            
            # Check if lamports = 0 (closed account)
            lamports = value.get("lamports", 0)
            if lamports == 0:
                logger.debug(f"      ✅ {addr[:20]}... → ZERO_LAMPORTS (burned)")
                return True
    
    # Check 4: On-chain LP characteristics (frozen, delegate, LP program)
    if account_info:
        if _is_lp_account(account_info):
            return True
    
    addr_lower = addr.lower()
    
    # Check 5: Known LP program addresses
    if addr in _LP_PROGRAM_IDS:
        logger.debug(f"      ✅ {addr[:20]}... → KNOWN_LP_PROGRAM")
        return True
    
    # Check 6: Keywords (including incinerator variants)
    keywords = ["burn", "locked", "treasury", "vault", "reward", "incinerator", "dead"]
    if any(kw in addr_lower for kw in keywords):
        logger.debug(f"      ✅ {addr[:20]}... → KEYWORD ({addr_lower})")
        return True
    
    logger.debug(f"      ❌ {addr[:20]}... → NOT_LP")
    return False

# ══════════════════════════════════════════════════════════════════
#  EXTRACT FEATURES - MAIN FUNCTION
# ══════════════════════════════════════════════════════════════════

def extract_features(address: str, birdeye_key: str = "", solscan_key: str = "") -> dict:
    dex      = DexScreenerFetcher()
    rugcheck = RugCheckFetcher()
    solanafm = SolanaFmFetcher()
    pumpfun  = PumpFunFetcher()

    features = {
        "address": address, "token_name": "Unknown", "token_symbol": "???",
        "error": None,
        "_rpc_ok": False, "_rc_ok": False,
        "_helius_active": bool(_get_helius_key()),
        "_holder_source": "none",
    }

    logger.info(
        f"Fetching {address[:16]}... "
        f"[Helius={'YES' if _get_helius_key() else 'NO'}]"
    )

    with ThreadPoolExecutor(max_workers=3) as ex:
        f_dex = ex.submit(dex.get_token_pairs, address)
        f_rpc = ex.submit(_rpc_batch_all, address)
        f_rc  = ex.submit(rugcheck.get_full, address)

        pairs    = f_dex.result(timeout=25)
        rpc_data = f_rpc.result(timeout=35)
        rc       = f_rc.result(timeout=20)

    if not rc:
        rc = rugcheck.get_summary(address)

    if not pairs:
        features["error"] = (
            "Token tidak ditemukan di DexScreener. "
            "Pastikan alamat valid dan token sudah memiliki liquidity pool."
        )
        return features

    best = pairs[0]
    bt, qt = best.get("baseToken", {}), best.get("quoteToken", {})
    if bt.get("address", "").lower() == address.lower():
        features["token_name"]   = bt.get("name", "Unknown")
        features["token_symbol"] = bt.get("symbol", "???")
    else:
        features["token_name"]   = qt.get("name", "Unknown")
        features["token_symbol"] = qt.get("symbol", "???")

    features["price_usd"]  = float(best.get("priceUsd") or 0)
    features["market_cap"] = float(best.get("marketCap") or best.get("fdv") or 0)
    features["fdv"]        = float(best.get("fdv") or 0)
    features["liquidity"]  = round(sum(
        float(p.get("liquidity", {}).get("usd", 0) or 0) for p in pairs), 2)
    features["volume_24h"] = round(sum(
        float(p.get("volume", {}).get("h24", 0) or 0) for p in pairs), 2)
    features["price_change_24h"] = float(
        best.get("priceChange", {}).get("h24") or 0)

    txns = best.get("txns", {}).get("h24", {})
    features["txns_24h_buys"]  = int(txns.get("buys",  0) or 0)
    features["txns_24h_sells"] = int(txns.get("sells", 0) or 0)
    features["sell_buy_ratio"] = round(
        features["txns_24h_sells"] / features["txns_24h_buys"], 3
    ) if features["txns_24h_buys"] > 0 else 0

    pair_ts = best.get("pairCreatedAt")
    if pair_ts:
        import time as _t
        features["token_age_days"] = round(
            max((_t.time() - float(pair_ts) / 1000) / 86400, 0), 2)
    else:
        features["token_age_days"] = 0

    features["pair_count"]       = len(pairs)
    features["liq_to_mc_ratio"]  = round(
        features["liquidity"] / features["market_cap"], 4
    ) if features["market_cap"] > 0 else 0
    features["vol_to_liq_ratio"] = round(
        features["volume_24h"] / features["liquidity"], 4
    ) if features["liquidity"] > 0 else 0

    # Parse RPC
    rpc_data    = rpc_data if isinstance(rpc_data, dict) else {}
    ai_result   = rpc_data.get("accountInfo", {})
    ts_result   = rpc_data.get("tokenSupply", {})
    la_result   = rpc_data.get("largestAccounts", {})

    mint_info = None
    if ai_result:
        value = ai_result.get("value") if isinstance(ai_result, dict) else None
        if value:
            data = value.get("data", {})
            if isinstance(data, dict) and "parsed" in data:
                mint_info = data["parsed"].get("info", {})

    if mint_info:
        features["_rpc_ok"] = True
        ma = mint_info.get("mintAuthority")
        fa = mint_info.get("freezeAuthority")
        features["mint_authority_enabled"]   = 0 if not ma else 1
        features["freeze_authority_enabled"] = 0 if not fa else 1
        features["total_supply"] = float(mint_info.get("supply", 0) or 0)
        features["decimals"]     = int(mint_info.get("decimals", 9) or 9)
        logger.info(
            f"  RPC OK: mint={'YES' if ma else 'NO'} freeze={'YES' if fa else 'NO'}"
        )
    else:
        features["mint_authority_enabled"]   = 0
        features["freeze_authority_enabled"] = 0
        features["total_supply"] = 0
        features["decimals"]     = 9

    total_supply_ui = 0.0
    decimals        = features["decimals"]
    if ts_result:
        sv = ts_result.get("value") if isinstance(ts_result, dict) else None
        if sv:
            total_supply_ui  = _parse_ui_amount(sv, decimals)
            features["decimals"] = int(sv.get("decimals", decimals) or decimals)
            decimals = features["decimals"]
            if not features["total_supply"]:
                features["total_supply"] = float(sv.get("amount", 0) or 0)

    # Top holders - CHECK 50 (not 20)
    rpc_holders_ok = False
    la_raw = []
    if isinstance(la_result, dict):
        la_raw = la_result.get("value", []) or []
    elif isinstance(la_result, list):
        la_raw = la_result

    la_accounts = []
    for acct in la_raw:
        if not isinstance(acct, dict):
            continue
        if "uiAmount" in acct or "uiAmountString" in acct:
            la_accounts.append(acct)
        elif "amount" in acct:
            decimals_val = features.get("decimals", 9)
            try:
                raw_amount = int(acct.get("amount", 0) or 0)
                ui = raw_amount / (10 ** decimals_val)
            except Exception:
                raw_amount, ui = 0, 0.0
            la_accounts.append({
                "address":        acct.get("address", ""),
                "amount":         str(raw_amount),
                "decimals":       decimals_val,
                "uiAmount":       ui,
                "uiAmountString": str(ui),
            })

    if la_accounts and total_supply_ui > 0:
        rpc_holders_ok = True
        top10 = top1 = lp_burned = 0.0
        
        # ✅ CHECK 50 HOLDERS (not 20)
        check_limit = min(50, len(la_accounts))
        logger.info(f"  ➜ Checking {check_limit} holders for LP/burn...")
        
        # NEW: Batch fetch account info untuk semua holders
        check_addresses = [acct.get("address", "") for acct in la_accounts[:check_limit]]
        check_addresses = [a for a in check_addresses if a]
        
        account_info_map = {}
        if check_addresses and rpc_data.get("_rpc_url"):
            account_info_map = _get_account_info_batch(
                check_addresses, rpc_data.get("_rpc_url"), timeout_sec=20
            )
            logger.info(f"  ✅ Fetched account info for {len([v for v in account_info_map.values() if v])} addresses")
        
        for i, acct in enumerate(la_accounts[:check_limit]):
            ui  = _parse_ui_amount(acct, decimals)
            pct = (ui / total_supply_ui * 100) if total_supply_ui > 0 else 0
            addr = acct.get("address", "")
            
            # Log first 20
            if i < 20:
                logger.info(f"    [{i+1}] {addr[:20]}... ({pct:.2f}%)")
            
            # Check ALL 50 with account info
            acct_info = account_info_map.get(addr)
            
            # DEBUG: Log account info status
            if i < 10:  # Log first 10 in detail
                if acct_info is None:
                    logger.debug(f"        Account info: None (fetch failed)")
                elif acct_info.get("value") is None:
                    logger.debug(f"        Account info: value=None (account closed/burned)")
                else:
                    value = acct_info.get("value", {})
                    owner = value.get("owner", "")[:10] if isinstance(value, dict) else "N/A"
                    lamports = value.get("lamports", 0) if isinstance(value, dict) else 0
                    logger.debug(f"        Account info: owner={owner}... lamports={lamports}")
            
            if _is_burn_or_lp(addr, acct_info):
                logger.info(f"    ✅ [{i+1}] DETECTED LP/BURN: {addr[:20]}... ({pct:.2f}%)")
                lp_burned += pct
            
            if i < 10:
                top10 += pct
            if i == 0:
                top1 = pct

        features["top1_holder_pct"]  = round(min(top1,  100), 2)
        features["top10_holder_pct"] = round(min(top10, 100), 2)
        features["lp_burned_pct"]    = round(min(lp_burned, 100), 2)  # ← FIX: cap at 100%
        features["_holder_source"]   = "Solana RPC"
        logger.info(
            f"  ✅ RPC Summary (top {check_limit}): "
            f"top10={features['top10_holder_pct']:.1f}% "
            f"top1={features['top1_holder_pct']:.1f}% "
            f"lp_burned={features['lp_burned_pct']:.1f}%"
        )
    elif la_accounts and total_supply_ui == 0:
        raw = [_parse_ui_amount(a, decimals) for a in la_accounts]
        raw = [v for v in raw if v > 0]
        if len(raw) >= 20:
            total_est = sum(raw)
            if total_est > 0:
                features["top1_holder_pct"]  = round(raw[0] / total_est * 100, 2)
                features["top10_holder_pct"] = round(
                    sum(raw[:10]) / total_est * 100, 2)
                rpc_holders_ok = True
                features["_holder_source"] = "Solana RPC (estimasi)"
    else:
        features["top1_holder_pct"]  = 0.0
        features["top10_holder_pct"] = 0.0
        features["lp_burned_pct"]    = 0.0
        logger.warning(
            f"  ⚠ RPC holder data gagal. Fallback ke RugCheck..."
        )

    # RugCheck
    if rc:
        features["_rc_ok"] = True
        logger.info(f"  RugCheck keys: {list(rc.keys())[:15]}")

        rc_holders = int(
            rc.get("totalHolders", 0) or 
            rc.get("holderCount", 0) or 
            rc.get("holders", 0) or 
            len(rc.get("topHolders") or []) or 0
        )
        if rc_holders > 0:
            features["unique_holders"] = rc_holders

        lp_locked = float(rc.get("lpLockedPct", 0) or 0)
        
        # IMPROVED: Check markets array if top-level lpLockedPct is 0
        if lp_locked == 0:
            markets = rc.get("markets", [])
            if markets:
                # Find market with highest lpLockedPct
                for market in markets:
                    lp_data = market.get("lp", {})
                    if isinstance(lp_data, dict):
                        market_lp_locked = float(lp_data.get("lpLockedPct", 0) or 0)
                        if market_lp_locked > lp_locked:
                            lp_locked = market_lp_locked
                            logger.info(f"  ✅ Found LP locked in market: {lp_locked:.1f}% (lpMint: {lp_data.get('lpMint', 'N/A')[:20]}...)")
        
        logger.info(f"  RugCheck lpLockedPct: {lp_locked:.1f}% (final)")
        
        # IMPROVED: Use RugCheck as primary source if significantly higher
        current_lp = features.get("lp_burned_pct", 0)
        
        # Case 1: RPC failed (0) → use RugCheck
        if lp_locked > 0 and current_lp == 0:
            features["lp_burned_pct"] = round(lp_locked, 2)
            features["_lp_source"] = "RugCheck (RPC failed)"
            logger.info(f"  ✅ Using RugCheck lpLockedPct: {lp_locked:.1f}%")
        
        # Case 2: RugCheck shows significantly higher → trust RugCheck (RPC might have missed burned addresses)
        elif lp_locked > current_lp + 20:  # >20% difference = RPC probably wrong
            logger.warning(f"  ⚠ RPC shows {current_lp:.1f}% but RugCheck shows {lp_locked:.1f}% → Using RugCheck")
            features["lp_burned_pct"] = round(lp_locked, 2)
            features["_lp_source"] = "RugCheck (override RPC)"
        
        # Case 3: Values close → keep RPC (more detailed)
        elif current_lp > 0:
            features["_lp_source"] = "Solana RPC"
            logger.info(f"  ✅ Using RPC value: {current_lp:.1f}% (RugCheck: {lp_locked:.1f}%)")

        creator_pct = float(rc.get("creatorPercentage", 0) or 0)
        if creator_pct > 0:
            features["creator_balance_pct"] = round(
                creator_pct * 100 if creator_pct <= 1 else creator_pct, 2)

        if not rpc_holders_ok:
            top_holders = rc.get("topHolders") or []
            logger.info(f"  RugCheck topHolders: {len(top_holders)} items")
            if top_holders:
                t10 = t1 = lpb = 0.0
                logger.info(f"  ➜ Checking RugCheck holders for LP/burn...")
                
                # Try to fetch account info for RugCheck holders too
                rc_addresses = [h.get("address", h.get("owner", "")) for h in top_holders[:10]]
                rc_addresses = [a for a in rc_addresses if a]
                rc_acct_info_map = {}
                
                if rc_addresses and rpc_data.get("_rpc_url"):
                    rc_acct_info_map = _get_account_info_batch(
                        rc_addresses, rpc_data.get("_rpc_url"), timeout_sec=15
                    )
                    logger.info(f"  ✅ Fetched account info for {len([v for v in rc_acct_info_map.values() if v])} RugCheck addresses")
                
                for i, h in enumerate(top_holders[:10]):
                    raw_pct = float(
                        h.get("pct") or h.get("percentage") or
                        h.get("ownerPercentage") or 0)
                    
                    if 0 < raw_pct <= 1.0:
                        pct = raw_pct * 100
                    elif raw_pct > 1.0:
                        pct = raw_pct
                    else:
                        pct = 0.0
                    
                    t10 += pct
                    if i == 0:
                        t1 = pct
                    addr = h.get("address", h.get("owner", ""))
                    logger.info(f"    [{i+1}] {addr[:20]}... ({pct:.2f}%)")
                    
                    acct_info = rc_acct_info_map.get(addr)
                    if _is_burn_or_lp(addr, acct_info):
                        lpb += pct
                
                if t10 > 0:
                    features["top1_holder_pct"]  = round(min(t1, 100),  2)
                    features["top10_holder_pct"] = round(min(t10, 100), 2)
                    if features.get("lp_burned_pct", 0) == 0:
                        features["lp_burned_pct"] = round(lpb, 2)
                    features["_holder_source"] = "RugCheck"
                    rpc_holders_ok = True
                    logger.info(
                        f"  ✅ RugCheck Summary: top10={features['top10_holder_pct']:.1f}% "
                        f"top1={features['top1_holder_pct']:.1f}% lp={features['lp_burned_pct']:.1f}%"
                    )

        if not features.get("_rpc_ok"):
            risks  = rc.get("risks", []) or []
            rnames = [r.get("name", "").lower() for r in risks]
            features["mint_authority_enabled"]   = \
                1 if any("mint"   in n for n in rnames) else 0
            features["freeze_authority_enabled"] = \
                1 if any("freeze" in n for n in rnames) else 0

        features["rugcheck_score"]  = int(rc.get("score", 0) or 0)
        features["rugcheck_rugged"] = bool(rc.get("rugged", False))
    else:
        features["rugcheck_score"]  = -1
        features["rugcheck_rugged"] = False

    # Fallbacks
    still_no_holders = (
        features.get("top10_holder_pct", 0) == 0 and
        features.get("top1_holder_pct",  0) == 0
    )
    if still_no_holders:
        sfm = solanafm.get_holders(address, limit=10)
        if sfm:
            hl = sfm.get("result", []) or sfm.get("holders", [])
            if hl and isinstance(hl, list):
                ts = total_supply_ui or features.get("total_supply", 0)
                if ts > 0:
                    t10 = t1 = 0.0
                    for i, h in enumerate(hl[:10]):
                        amt = float(h.get("amount", 0) or h.get("balance", 0) or 0)
                        if amt > 1e9:
                            amt /= (10 ** decimals)
                        pct = (amt / ts * 100) if ts > 0 else 0
                        t10 += pct
                        if i == 0:
                            t1 = pct
                    if t10 > 0:
                        features["top1_holder_pct"]  = round(t1,  2)
                        features["top10_holder_pct"] = round(min(t10, 100), 2)
                        features["_holder_source"]   = "SolanaFM"

    is_pumpfun = address.lower().endswith("pump") or any(
        p.get("dexId", "") == "pump" for p in pairs)
    if is_pumpfun or not features.get("unique_holders"):
        pf = pumpfun.get_coin(address)
        if pf:
            if not features.get("unique_holders"):
                features["unique_holders"] = int(pf.get("holder_count", 0) or 0)
            if not features.get("creator_balance_pct"):
                ts = float(pf.get("total_supply", 1) or 1)
                ct = float(pf.get("creator_token_supply", 0) or 0)
                features["creator_balance_pct"] = round(
                    ct / ts * 100, 2) if ts > 0 else 0

    if not features.get("creator_balance_pct"):
        top1 = features.get("top1_holder_pct", 0)
        age  = features.get("token_age_days", 999)
        features["creator_balance_pct"] = round(
            top1 * 0.8 if age < 7 and top1 > 30
            else top1 * 0.9 if top1 > 50
            else min(top1 * 0.5, 20.0), 2)

    for field in ["top1_holder_pct", "top10_holder_pct", "creator_balance_pct",
                  "mint_authority_enabled", "freeze_authority_enabled",
                  "lp_burned_pct", "unique_holders"]:
        if features.get(field) is None:
            features[field] = 0

    logger.info(
        f"✅ {features['token_name']} "
        f"[{features['_holder_source']}] "
        f"top10={features['top10_holder_pct']:.1f}% "
        f"top1={features['top1_holder_pct']:.1f}% "
        f"lp={features['lp_burned_pct']:.1f}% "
        f"age={features['token_age_days']:.1f}d"
    )
    return features

# ══════════════════════════════════════════════════════════════════
MODEL_FEATURES = [
    "top10_holder_pct", "top1_holder_pct", "creator_balance_pct",
    "mint_authority_enabled", "freeze_authority_enabled", "lp_burned_pct",
    "liq_to_mc_ratio", "vol_to_liq_ratio", "token_age_days",
    "liquidity", "unique_holders", "price_change_24h",
]

DATA_SOURCES = {
    "DexScreener":    {"url": DEXSCREENER_BASE,  "key_required": False},
    "Helius RPC":     {"url": "https://helius.dev", "key_required": True},
    "Solana RPC":     {"url": "https://api.mainnet-beta.solana.com", "key_required": False},
    "RugCheck.xyz":   {"url": RUGCHECK_BASE,     "key_required": False},
    "SolanaFM":       {"url": SOLANAFM_BASE,     "key_required": False},
    "Pump.fun":       {"url": PUMPFUN_BASE,       "key_required": False},
}

def clear_cache() -> int:
    """Hapus seluruh in-memory cache. Return jumlah entry yang dihapus."""
    with _cache_lock:
        n = len(_cache)
        _cache.clear()
    logger.info(f"Cache cleared: {n} entries removed")
    return n

def cache_stats() -> dict:
    """Return statistik cache saat ini."""
    with _cache_lock:
        return {"entries": len(_cache), "keys": list(_cache.keys())[:20]}