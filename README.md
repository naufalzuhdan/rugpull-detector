# RugScan — Sistem Deteksi Rug Pull Token Solana
## Tugas Akhir: SISTEM DETEKSI POTENSI RUG PULL TOKEN PADA BLOCKCHAIN SOLANA MENGGUNAKAN METODE DECISION TREE

---

## ✅ Tidak Memerlukan API Key

Sistem menggunakan dua API publik yang **100% gratis tanpa registrasi**:

| API | Endpoint | Key Required |
|-----|----------|:---:|
| **DexScreener** | https://api.dexscreener.com | ❌ Tidak |
| **Solana RPC** | https://api.mainnet-beta.solana.com | ❌ Tidak |

---

## Cara Menjalankan

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. (Opsional) Konfigurasi .env
```bash
cp .env.example .env
# Hanya perlu mengubah SECRET_KEY jika untuk produksi
```

### 3. Training Model
```bash
python model/train_model.py
```

### 4. Jalankan Aplikasi
```bash
python app.py
```

Buka browser: **http://localhost:5000**

---

## Struktur Project
```
rugpull_detector/
├── app.py                      # Flask web application utama
├── requirements.txt            # Dependencies Python
├── .env                        # Konfigurasi (tanpa API key)
├── model/
│   ├── train_model.py          # Script training Decision Tree
│   ├── predictor.py            # Load model & prediksi
│   ├── random_forest_model.pkl # Model terlatih (auto-generate)
│   └── metrics.json            # Hasil evaluasi model
├── utils/
│   └── data_fetcher.py         # DexScreener + Solana RPC fetcher
├── data/
│   └── dataset.csv             # Dataset training
├── templates/                  # HTML templates Flask
│   ├── base.html
│   ├── index.html              # Dashboard
│   ├── detect.html             # Halaman deteksi token
│   ├── model.html              # Evaluasi model
│   ├── dataset.html            # Preview dataset
│   └── settings.html           # Info sumber data API
└── static/
    ├── css/style.css
    ├── js/main.js
    └── img/                    # Chart evaluasi (auto-generate)
```

---

## Alur Pengambilan Data

```
Token Address (input)
      │
      ├─► DexScreener API ──► harga, market cap, likuiditas, volume,
      │                        perubahan harga, umur token, pair count
      │
      └─► Solana RPC ────────► mint authority, freeze authority,
                               total supply, top holders, LP burned
                                      │
                                      ▼
                              12 Fitur On-Chain
                                      │
                                      ▼
                         Decision Tree Classifier
                                      │
                                      ▼
                     Rug Pull / Legitimate + Probabilitas
```

---

## 12 Fitur yang Digunakan Model

| Fitur | Sumber | Keterangan |
|---|---|---|
| top10_holder_pct | Solana RPC | % supply 10 holder terbesar |
| top1_holder_pct | Solana RPC | % supply holder terbesar (whale) |
| creator_balance_pct | Estimasi | % supply milik developer |
| mint_authority_enabled | Solana RPC | Dev masih bisa cetak token baru |
| freeze_authority_enabled | Solana RPC | Dev bisa freeze wallet holder |
| lp_burned_pct | Solana RPC | % LP yang sudah dibakar/dikunci |
| liq_to_mc_ratio | DexScreener | Rasio likuiditas / market cap |
| vol_to_liq_ratio | DexScreener | Rasio volume / likuiditas |
| token_age_days | DexScreener | Umur token dalam hari |
| liquidity | DexScreener | Total likuiditas USD |
| unique_holders | Solana RPC | Jumlah holder unik |
| price_change_24h | DexScreener | Perubahan harga 24 jam (%) |

---

## Contoh Token untuk Testing
- **USDC** (legitimate): `EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v`
- **BONK** (meme coin): `DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263`
- **WIF** (dogwifhat): `EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm`

---

## Training dengan Data Real (DexScreener + Solana RPC)

### Cara Cepat (semuanya otomatis):
```bash
python model/collect_real_data.py --train-after
```

### Cara Bertahap:
```bash
# Langkah 1: Kumpulkan data real (±2-3 menit untuk 40 token)
python model/collect_real_data.py

# Langkah 2: Training model dengan data real
python model/train_model.py
```

### Opsi Tambahan:
```bash
# Jeda lebih lama antar request (hindari rate limit)
python model/collect_real_data.py --delay 2.0

# Simpan dataset real tanpa mengganti dataset.csv
python model/collect_real_data.py --no-replace
```

### Menambah Token Baru ke Dataset:
Edit file `model/collect_real_data.py`, tambahkan ke:
- `CONFIRMED_LEGITIMATE` untuk token yang sudah terbukti aman
- `CONFIRMED_RUG_PULL` untuk token yang sudah terkonfirmasi scam

Format: `("alamat_token", "Nama Token", "SIMBOL")`

### Alur Lengkap Training Real Data:
```
model/collect_real_data.py
        │
        ├─► Fetch DexScreener (harga, liq, vol, age)
        ├─► Fetch Solana RPC (authority, supply, holders)
        ├─► Ekstraksi 12 fitur per token
        └─► Simpan ke data/dataset.csv
                │
                ▼
    model/train_model.py
        │
        ├─► Load dataset.csv
        ├─► Train Decision Tree (80% train, 20% test)
        ├─► 10-Fold Cross Validation
        ├─► Generate confusion matrix, ROC, feature importance
        └─► Simpan model ke model/random_forest_model.pkl
```

---

## 🤖 Auto Collect — Fetch Otomatis Tanpa Daftar Token

Script baru `model/auto_collect.py` menggantikan sistem daftar token manual.

### Cara Kerja
```
DexScreener API          RugCheck.xyz API      Solana RPC
(Trending + New Pairs)   (Risk Score)          (On-chain Data)
        │                      │                      │
        └──── DISCOVER ────────┼──── AUTO-LABEL ─────┤
                               │                      │
                               └──── EXTRACT 12 FEATURES ────→ DATASET
```

### Perintah
```bash
# Mode preview — test koneksi 5 token
python model/auto_collect.py --preview

# Collect 1000 baris (default)
python model/auto_collect.py

# Collect 1500 baris + langsung training
python model/auto_collect.py --target 1500 --train-after

# Lanjutkan jika terputus
python model/auto_collect.py --resume

# Jeda lebih lambat (internet lambat)
python model/auto_collect.py --delay 2.0
```

### Logika Auto-Labeling
| Kondisi | Label |
|---|---|
| RugCheck score ≥ 500 | 🚨 Rug Pull (1) |
| RugCheck score < 200 | ✅ Legitimate (0) |
| RugCheck score 200-499 + `mint_auth=1` & `top10>80%` & `lp<10%` | 🚨 Rug Pull (1) |
| RugCheck score 200-499 + kondisi aman | ✅ Legitimate (0) |

### Keunggulan vs Sistem Lama
| | Sistem Lama | Auto Collect |
|---|---|---|
| Sumber token | Daftar tetap ~500 token | Dinamis dari DexScreener |
| Mentok di 60? | Ya (banyak token mati) | Tidak — terus discover baru |
| Labeling | Manual/sintetis | Otomatis via RugCheck.xyz |
| Resume | Ya | Ya |
| Token terbaru | Tidak | Ya — selalu fresh |
