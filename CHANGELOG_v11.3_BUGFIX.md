# RugScan v11.3 — Bugfix & Improvement Changelog

## Tanggal: 2026-07-02

---

## 🔴 Bug Kritis (sudah diperbaiki)

### 1. `lp_burned_pct` bisa melebihi 100% — `data_fetcher.py`
**Masalah:** Saat banyak holder terdeteksi sebagai LP/burn, nilai `lp_burned_pct`
tidak dibatasi sehingga bisa menjadi 120%, 150%, atau lebih. Ini merusak
prediksi model karena data training tidak pernah memiliki nilai > 100.

**Fix:** `features["lp_burned_pct"] = round(min(lp_burned, 100), 2)`

---

### 2. `holder_data_ok` tidak pernah diset — `auto_collect.py`
**Masalah:** Statistik akhir koleksi selalu menampilkan "0 baris ada data (0%)"
karena field `holder_data_ok` tidak pernah ditambahkan ke `row` di `collect_one()`.

**Fix:** Tambah field `holder_data_ok` di `collect_one()` berdasarkan nilai
`top10_holder_pct`, `top1_holder_pct`, atau `unique_holders`.

---

### 3. Loading step menyebut "Birdeye API" — `detect.html`
**Masalah:** Teks loading pertama masih `"Connecting to Birdeye API..."` meskipun
Birdeye sudah tidak digunakan sejak v11.

**Fix:** Diganti menjadi `"Menghubungkan ke DexScreener API..."`

---

### 4. Demo button mengisi address `DEMO1234567890` — `detect.html`
**Masalah:** Jika user klik Demo lalu tekan Scan secara tidak sengaja,
address palsu dikirim ke server dan menghasilkan error yang membingungkan.

**Fix:** Address dikosongkan saat demo, sehingga hanya data dummy yang ditampilkan
tanpa risiko mengirim request palsu ke server.

---

## 🔴 Masalah Keamanan (sudah diperbaiki)

### 5. HELIUS_API_KEY real ada di `.env` — `.env`
**Masalah:** File `.env` berisi API key Helius yang sungguhan ikut terdistribusi.

**Fix:** Key dihapus dan diganti dengan placeholder. **Penting: rotate key lama
di https://dev.helius.xyz segera jika sudah terlanjur di-share.**

---

### 6. `SECRET_KEY` Flask menggunakan default lemah — `app.py`
**Masalah:** Fallback `SECRET_KEY` hardcoded; session Flask bisa di-forge jika
.env tidak ada.

**Fix:** Tambah warning log jika key tidak diset/masih default. Key default
dibuat lebih jelas bahwa itu tidak aman.

---

### 7. `FLASK_DEBUG` default `True` — `app.py`
**Masalah:** Default `"True"` membuka Werkzeug debugger di server manapun jika
`.env` tidak tersedia.

**Fix:** Default diubah ke `"False"`. Debug mode harus diaktifkan secara eksplisit
di `.env` dengan `FLASK_DEBUG=True`.

---

### 8. Endpoint `/api/debug-connectivity` terbuka tanpa auth — `app.py`
**Masalah:** Endpoint ini mengekspos detail koneksi internal kepada siapapun.

**Fix:** Ditambah pengecekan `request.remote_addr` — hanya `127.0.0.1` / `::1`
yang diizinkan mengakses endpoint debug.

---

## 🟠 Masalah Logika (sudah diperbaiki)

### 9. `enrichHolderData` tidak update probabilitas — `detect.html`
**Masalah:** Browser-side enrichment memperbarui tabel fitur secara visual tetapi
probabilitas rug pull tidak dihitung ulang.

**Fix:** Setelah enrichment berhasil, muncul tombol "Scan Ulang dengan Data Terkini"
yang memanggil ulang server untuk mendapatkan probabilitas yang diperbarui.

---

### 10. `rugcheck_rugged=True` diabaikan di prediksi — `predictor.py`
**Masalah:** Token yang sudah dikonfirmasi rugged oleh RugCheck tetap bisa
mendapatkan probabilitas rendah dari model ML.

**Fix:**
- Jika `rugcheck_rugged=True` → override prediksi ke 99% rug pull
- Jika `rugcheck_score >= 700` dan model memberikan < 70% → boost ke 70%
- Badge "DIKONFIRMASI RUG PULL oleh RugCheck.xyz" ditampilkan di UI

---

### 11. Inkonsistensi versi di berbagai file — semua file
**Masalah:** `app.py` = v10.1, `.env` = v10, `data_fetcher.py` = v11.3

**Fix:** Semua disinkronkan ke `v11.3-FastFetch`

---

### 12. Komentar vs kode `should_skip_label` — `auto_collect.py`
**Masalah:** Komentar menyebut 55% tapi kode memakai 60%.

**Fix:** Komentar diperbarui ke 60%.

---

## 🟡 Perbaikan Kualitas Kode (sudah diperbaiki)

### 13. `_cache` dict tidak thread-safe — `data_fetcher.py`
**Masalah:** Cache global diakses dari multiple thread (ThreadPoolExecutor +
background auto-collect) tanpa lock, berpotensi race condition.

**Fix:** Tambah `_cache_lock = threading.Lock()` dan semua akses cache
(read check + write) dibungkus `with _cache_lock:`.

---

### 14. `print()` bukan `logging` di `predictor.py`
**Masalah:** Dua `print()` di `get_metrics()` mencampur output ke stdout
alih-alih masuk ke sistem logging Flask.

**Fix:** Diganti ke `logger.info()` dan `logger.error()`.

---

### 15. Base58 validation — `app.py` + `detect.html`
**Masalah:** Validasi hanya cek panjang 32–44 karakter. Karakter invalid Base58
(`0`, `O`, `I`, `l`) tidak dideteksi.

**Fix:** Tambah regex `^[1-9A-HJ-NP-Za-km-z]{32,44}$` di server. Pesan error
menjadi lebih spesifik.

---

## 🟢 Fitur Baru (ditambahkan)

### 16. Progress tracking auto-collect — `auto_collect.py` + `app.py`
- Tambah `_collect_progress` dict di `auto_collect.py` yang diperbarui setiap token
- Endpoint baru: `GET /api/auto-collect/status` mengembalikan progress realtime
- Endpoint `/api/auto-collect` sekarang mencegah double-start jika sudah berjalan

### 17. Parameter `require_complete_data` bisa dikontrol dari API
- `POST /api/auto-collect` sekarang menerima `require_complete` (bool, default `true`)
- Contoh: `{"target": 500, "require_complete": false}`

### 18. History scan — `detect.html`
- Riwayat 10 scan terakhir disimpan di `localStorage`
- Panel riwayat muncul di atas form deteksi
- Klik item riwayat untuk langsung scan ulang token tersebut
- Tombol "Hapus semua" untuk membersihkan riwayat

### 19. Cache management endpoints — `app.py` + `data_fetcher.py`
- `POST /api/cache/clear` — hapus seluruh in-memory cache
- `GET /api/cache/stats` — lihat jumlah dan daftar cache key

### 20. RugCheck confirmed badge — `detect.html`
- Banner merah "DIKONFIRMASI RUG PULL oleh RugCheck.xyz" muncul di atas
  panel hasil jika `rugcheck_confirmed=True`

---

## Ringkasan Statistik

| Kategori         | Jumlah |
|------------------|--------|
| Bug kritis fixed | 4      |
| Security fixed   | 4      |
| Logika fixed     | 4      |
| Kode quality     | 3      |
| Fitur baru       | 5      |
| **Total**        | **20** |

---

## Catatan untuk Developer

1. **Segera rotate HELIUS_API_KEY** jika project ini pernah di-share sebelum patch ini.
2. Set `SECRET_KEY` yang kuat di `.env` sebelum deploy ke production.
3. Endpoint `/api/debug-connectivity` sekarang hanya bisa diakses dari localhost.
4. Model ML belum di-retrain — perubahan prediksi dari fix #10 bersifat post-processing.
   Untuk akurasi optimal, jalankan `python model/auto_collect.py --train-after` setelah
   dataset cukup besar.


### 21. Pagination & View All Dataset — `app.py` + `dataset.html`
**Masalah:** Halaman dataset preview hanya menampilkan sampel acak 100 baris,
sehingga tidak bisa melihat semua data jika dataset memiliki 1500+ baris.

**Fix:**
- **Pagination:** Dataset ditampilkan dengan sistem halaman (default 100 baris per halaman)
- **Opsi Per-Page:** User bisa memilih 50, 100, 250, 500, atau 1000 baris per halaman
- **Tombol "Tampilkan Semua":** Klik untuk melihat seluruh dataset dalam satu halaman
- **Navigasi:** Tombol First, Previous, Next, Last untuk navigasi antar halaman
- **Performa Warning:** Peringatan muncul jika menampilkan > 1000 baris sekaligus
- **URL Parameter:** Mendukung `?page=X&per_page=Y&show_all=true` untuk bookmarking
- **Filter & Search:** Fitur filter (Semua/Legitimate/Rug Pull) dan search tetap berfungsi

**Fitur tambahan:**
- Total halaman dihitung otomatis berdasarkan jumlah data
- Status "menampilkan X dari Y baris" selalu update dinamis
- Tombol kembali ke pagination dari mode "Show All"

---

## Ringkasan Statistik (Updated)

| Kategori         | Jumlah |
|------------------|--------|
| Bug kritis fixed | 4      |
| Security fixed   | 4      |
| Logika fixed     | 4      |
| Kode quality     | 3      |
| Fitur baru       | 6      |
| **Total**        | **21** |
