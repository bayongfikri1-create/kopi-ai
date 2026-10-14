# Kopi AI - Sistem Pemesanan Coffee Shop (Python)

Tanpa library tambahan. Cukup Python 3.8 atau lebih baru.

## Cara menjalankan di VS Code
1. File > Open Folder > pilih folder `kopi-ai-python`.
2. Buka terminal: Terminal > New Terminal.
3. Cek Python: `python --version` (di macOS/Linux: `python3 --version`).
4. Jalankan:

       python server.py

   (macOS/Linux: `python3 server.py`)
5. Buka di browser:
   - Pelanggan : http://localhost:3000/?meja=1
   - Barista   : http://localhost:3000/barista
   - Laporan   : http://localhost:3000/laporan
   - Kelola menu: http://localhost:3000/menu
   - QR meja   : http://localhost:3000/meja

Untuk mencoba dari HP, sambungkan ke Wi-Fi yang sama lalu buka alamat IP yang tampil di terminal.
Jika port 3000 dipakai: `PORT=3001 python3 server.py` (PowerShell: `$env:PORT=3001; python server.py`).

## Kelola menu (tambah / ubah / hapus)
Buka http://localhost:3000/menu (atau klik **Kelola menu** di menu atas).
- **Tambah menu**: isi formulir lalu klik *Tambah menu*.
- **Ubah**: klik *Ubah* pada baris menu, edit, lalu *Simpan perubahan*.
- **Tandai habis / Sediakan**: klik tombolnya, menu langsung nonaktif di halaman pelanggan.
- **Hapus**: klik *Hapus* lalu konfirmasi.
Perubahan langsung tampil di halaman pelanggan tanpa refresh dan tersimpan di `data/menu.json`.

## Rekomendasi AI (opsional)
Bawaan: pencocokan kata kunci lokal. Untuk memakai Claude:

    # Windows PowerShell
    $env:ANTHROPIC_API_KEY="sk-ant-xxxx"; python server.py

    # macOS / Linux
    ANTHROPIC_API_KEY=sk-ant-xxxx python3 server.py

## Struktur
    server.py          server + API + logika rekomendasi
    data/menu.json     daftar menu (edit untuk mengganti menu)
    data/orders.json   pesanan (dibuat otomatis)
    public/            halaman web (pelanggan, barista, kelola menu, laporan, QR meja)

## Login halaman staf
Halaman /barista, /menu, /laporan, /meja dikunci jika variabel `STAFF_PASSWORD` diisi
(username bawaan `staf`, bisa diganti lewat `STAFF_USER`). Halaman pelanggan tetap terbuka.
Lokal (PowerShell): `$env:STAFF_PASSWORD="rahasia123"; python server.py`
Jika `STAFF_PASSWORD` kosong, tidak ada login (cocok untuk dipakai di laptop sendiri).

## Hosting gratis (Render)
1. Upload folder ini ke repositori GitHub (publik atau privat).
2. Di render.com: New > Web Service > pilih repositori. Render membaca `render.yaml`
   (Runtime Python, Build `pip install -r requirements.txt`, Start `python server.py`, Plan Free).
3. Tunggu deploy selesai, lalu buka alamat `https://nama-app.onrender.com`.

Catatan paket gratis: aplikasi tidur setelah 15 menit tanpa pengunjung (bangun sekitar 1 menit),
dan file `data/*.json` kembali ke kondisi awal setiap restart/redeploy.
Di Render, isi variabel `STAFF_PASSWORD` agar halaman staf terkunci.
Zona waktu laporan memakai WIB; ubah lewat variabel `TZ_OFFSET` (WITA = 8, WIT = 9).
