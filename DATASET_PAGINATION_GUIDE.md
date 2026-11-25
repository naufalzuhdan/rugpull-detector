# Panduan Pagination Dataset

## Overview
Fitur pagination memungkinkan Anda melihat dan mengelola dataset yang besar (1500+ baris) dengan mudah dan efisien.

---

## Cara Menggunakan

### 1. **Mode Pagination (Default)**
Secara default, dataset ditampilkan dengan sistem halaman:
- **100 baris per halaman** (default)
- Navigasi dengan tombol: **First**, **Previous**, **Next**, **Last**
- Bisa ubah jumlah baris per halaman: 50, 100, 250, 500, 1000

**Cara mengubah jumlah baris per halaman:**
1. Klik dropdown "Per halaman"
2. Pilih jumlah yang diinginkan (50/100/250/500/1000)
3. Halaman akan reload otomatis dengan pengaturan baru

---

### 2. **Mode "Tampilkan Semua"**
Untuk melihat **seluruh dataset dalam satu halaman**:

1. Klik tombol **"📋 Tampilkan Semua (1500)"** di pojok kanan atas
2. Seluruh dataset akan dimuat sekaligus
3. Filter dan search tetap berfungsi
4. Untuk kembali ke pagination: klik **"↩️ Kembali ke Pagination"**

⚠️ **Peringatan:** Jika dataset > 1000 baris, performa browser mungkin melambat

---

### 3. **Filter & Search**
Fitur ini bekerja di **semua mode** (pagination & show all):

**Filter Label:**
- **Semua** — tampilkan semua data
- **✅ Legitimate** — hanya token legitimate
- **🚨 Rug Pull** — hanya token rug pull

**Search Box:**
- Ketik untuk mencari berdasarkan:
  - Nama token
  - Simbol token
  - Contract address
- Search bekerja real-time (instant)

---

## URL Bookmarking

Anda bisa bookmark halaman dengan parameter tertentu:

```
# Halaman 5 dengan 250 baris per halaman
http://localhost:5000/dataset?page=5&per_page=250

# Tampilkan semua data
http://localhost:5000/dataset?show_all=true

# Halaman pertama dengan 1000 baris
http://localhost:5000/dataset?page=1&per_page=1000
```

---

## Tips Penggunaan

### Untuk Dataset Kecil (< 500 baris)
✅ Gunakan **"Tampilkan Semua"** untuk kemudahan

### Untuk Dataset Sedang (500-1500 baris)
✅ Gunakan **pagination 250-500** baris per halaman
✅ Atau gunakan "Tampilkan Semua" jika browser cukup kuat

### Untuk Dataset Besar (> 1500 baris)
✅ Gunakan **pagination 100-250** baris per halaman
⚠️ Hindari "Tampilkan Semua" (performa browser akan lambat)
✅ Gunakan filter & search untuk menemukan data spesifik

---

## Fitur Lainnya

### Copy Address
- Klik ikon **⎘** di samping contract address untuk copy

### View di Explorer
- Klik **contract address** → buka di Solscan
- Klik ikon **📊** → buka di DexScreener

### Color Coding
- 🔴 **Merah** = Nilai berbahaya / risiko tinggi
- 🟠 **Orange** = Nilai perlu diwaspadai  
- 🟢 **Hijau** = Nilai aman / normal
- ⚪ **Abu-abu** = Nilai netral

---

## Troubleshooting

**Q: Browser lambat saat "Tampilkan Semua"**  
A: Dataset terlalu besar untuk browser. Gunakan pagination atau kurangi data dengan filter.

**Q: Pagination tidak muncul**  
A: Dataset Anda < 100 baris atau Anda sedang dalam mode "Show All". Kembali ke pagination dengan klik tombol yang tersedia.

**Q: Search tidak menemukan data**  
A: Pastikan Anda tidak sedang dalam mode filter yang salah (misal: filter "Legitimate" tapi search token rug pull).

---

## Technical Details

**Backend:**
- Pagination di-handle oleh Flask di `app.py`
- Menggunakan Pandas `iloc` untuk slicing data efisien
- Maksimal per_page: 10000 (untuk keamanan)

**Frontend:**
- Client-side filtering & search (instant response)
- Tidak perlu reload halaman untuk filter/search
- Menggunakan CSS untuk hide/show rows

**Performance:**
- < 100 rows: instant
- 100-500 rows: sangat cepat
- 500-1000 rows: cepat
- 1000-2000 rows: normal (sedikit delay)
- > 2000 rows: bisa lambat tergantung browser

---

*Versi: v11.3-FastFetch*  
*Tanggal: 2026-10-03*
