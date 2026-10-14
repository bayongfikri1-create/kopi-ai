#!/usr/bin/env python3
"""
Kopi AI - Sistem Pemesanan Coffee Shop (Python, tanpa library tambahan)

Jalankan:  python server.py      (Windows)
           python3 server.py     (macOS / Linux)
Butuh Python 3.8 atau lebih baru.
"""
import base64
import hmac
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

PORT = int(os.environ.get("PORT", 3000))
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PUBLIC_DIR = BASE_DIR / "public"
MENU_FILE = DATA_DIR / "menu.json"
ORDERS_FILE = DATA_DIR / "orders.json"

UKURAN = {"S": 0, "M": 3000, "L": 6000}  # tambahan harga per ukuran
GULA = [0, 50, 100]
SUHU = ["Es", "Panas"]
STATUS = ["Baru", "Diproses", "Siap", "Selesai"]

HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli",
         "Agustus", "September", "Oktober", "November", "Desember"]

# Zona waktu laporan. Server cloud biasanya UTC, jadi dipatok ke WIB (UTC+7).
# Ubah lewat variabel lingkungan TZ_OFFSET (WITA = 8, WIT = 9).
ZONA = timezone(timedelta(hours=int(os.environ.get("TZ_OFFSET", 7))))

# Login halaman staf (barista, kelola menu, laporan, QR meja).
# Aktif jika variabel STAFF_PASSWORD diisi; kosong = tanpa login (pemakaian lokal).
STAFF_USER = os.environ.get("STAFF_USER", "staf")
STAFF_PASSWORD = os.environ.get("STAFF_PASSWORD", "")
HALAMAN_STAF = {"/barista", "/menu", "/laporan", "/meja",
                "/barista.html", "/menu.html", "/laporan.html", "/meja.html"}


def api_khusus_staf(method, path):
    """Endpoint API yang hanya boleh dipakai staf."""
    if method == "GET" and path in ("/api/orders", "/api/laporan"):
        return True
    if method == "PATCH" and re.fullmatch(r"/api/orders/\d+", path):
        return True
    if path.startswith("/api/menu") and method != "GET":
        return True
    return False


def sama(a, b):
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


lock = threading.Lock()


# ---------- Penyimpanan (file JSON) ----------
def read_json(path, fallback):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return fallback


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


DATA_DIR.mkdir(exist_ok=True)
if not ORDERS_FILE.exists():
    write_json(ORDERS_FILE, [])


def get_menu():
    return read_json(MENU_FILE, [])


def get_orders():
    return read_json(ORDERS_FILE, [])


# ---------- Real-time (Server-Sent Events) ----------
clients = []
clients_lock = threading.Lock()


def broadcast(event, data):
    msg = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")
    with clients_lock:
        for wfile in list(clients):
            try:
                wfile.write(msg)
                wfile.flush()
            except Exception:
                clients.remove(wfile)


# ---------- Logika pesanan ----------
def buat_pesanan(data):
    menu = get_menu()
    meja = str(data.get("meja") or "").strip()[:10]
    if not meja:
        raise ValueError("Nomor meja wajib diisi")
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("Keranjang kosong")
    if len(raw_items) > 50:
        raise ValueError("Terlalu banyak item")

    items = []
    for it in raw_items:
        if not isinstance(it, dict):
            raise ValueError("Item tidak valid")
        try:
            id_menu = int(it.get("id"))
        except (TypeError, ValueError):
            raise ValueError("Menu tidak ditemukan")
        m = next((x for x in menu if x["id"] == id_menu), None)
        if m is None:
            raise ValueError("Menu tidak ditemukan")
        if not m.get("tersedia"):
            raise ValueError(f"{m['nama']} sedang habis")
        try:
            qty = int(float(it.get("qty")))
        except (TypeError, ValueError):
            raise ValueError("Jumlah tidak valid")
        if not 1 <= qty <= 20:
            raise ValueError("Jumlah tidak valid")

        item = {"id": m["id"], "nama": m["nama"], "qty": qty, "harga": m["harga"]}
        if m.get("minuman"):
            ukuran = it.get("ukuran") if it.get("ukuran") in UKURAN else "M"
            try:
                gula = int(it.get("gula"))
            except (TypeError, ValueError):
                gula = 50
            if gula not in GULA:
                gula = 50
            suhu = it.get("suhu") if it.get("suhu") in SUHU else "Es"
            item.update({"ukuran": ukuran, "gula": gula, "suhu": suhu,
                         "harga": m["harga"] + UKURAN[ukuran]})
        items.append(item)

    total = sum(i["harga"] * i["qty"] for i in items)
    with lock:
        orders = get_orders()
        new_id = max((o["id"] for o in orders), default=0) + 1
        waktu = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        order = {
            "id": new_id,
            "kode": f"#{new_id:03d}",
            "meja": meja,
            "items": items,
            "catatan": str(data.get("catatan") or "")[:200],
            "total": total,
            "status": "Baru",
            "waktu": waktu,
        }
        orders.append(order)
        write_json(ORDERS_FILE, orders)
    broadcast("update", {})
    return order


def waktu_lokal(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ZONA)


def buat_laporan():
    sekarang = datetime.now(ZONA)
    orders = [o for o in get_orders() if waktu_lokal(o["waktu"]).date() == sekarang.date()]
    per_jam = [0] * 24
    terlaris = {}
    total = 0
    for o in orders:
        total += o["total"]
        per_jam[waktu_lokal(o["waktu"]).hour] += o["total"]
        for i in o["items"]:
            terlaris[i["nama"]] = terlaris.get(i["nama"], 0) + i["qty"]
    top5 = [{"nama": n, "qty": q}
            for n, q in sorted(terlaris.items(), key=lambda x: -x[1])[:5]]
    return {
        "tanggal": f"{HARI[sekarang.weekday()]}, {sekarang.day} {BULAN[sekarang.month - 1]} {sekarang.year}",
        "jumlahPesanan": len(orders),
        "totalPenjualan": total,
        "rataRata": round(total / len(orders)) if orders else 0,
        "top5": top5,
        "perJam": per_jam,
        "riwayat": list(reversed(orders))[:20],
    }


# ---------- Kelola menu (tambah / ubah / hapus) ----------
TAGS_OK = ["pahit", "manis", "segar", "creamy", "kuat", "asam", "gurih"]


class NotFound(Exception):
    pass


def validasi_menu(data):
    nama = str(data.get("nama") or "").strip()[:60]
    if not nama:
        raise ValueError("Nama menu wajib diisi")
    kategori = str(data.get("kategori") or "").strip()[:30]
    if not kategori:
        raise ValueError("Kategori wajib diisi")
    try:
        harga = int(float(data.get("harga")))
    except (TypeError, ValueError):
        raise ValueError("Harga harus berupa angka")
    if not 0 < harga <= 10_000_000:
        raise ValueError("Harga tidak valid (1 - 10.000.000)")
    minuman = bool(data.get("minuman"))
    tags = data.get("tags") if isinstance(data.get("tags"), list) else []
    tags = [t for t in dict.fromkeys(tags) if t in TAGS_OK]
    if not minuman:
        tags.append("makanan")
    return {
        "nama": nama,
        "kategori": kategori,
        "harga": harga,
        "deskripsi": str(data.get("deskripsi") or "").strip()[:150],
        "tags": tags,
        "minuman": minuman,
        "tersedia": bool(data.get("tersedia", True)),
    }


def nama_kembar(menu, nama, abaikan_id=None):
    return any(m["nama"].lower() == nama.lower() and m["id"] != abaikan_id for m in menu)


def tambah_menu(data):
    baru = validasi_menu(data)
    with lock:
        menu = get_menu()
        if nama_kembar(menu, baru["nama"]):
            raise ValueError("Menu dengan nama itu sudah ada")
        baru = {"id": max((m["id"] for m in menu), default=0) + 1, **baru}
        menu.append(baru)
        write_json(MENU_FILE, menu)
    broadcast("menu", {})
    return baru


def ubah_menu(menu_id, data):
    baru = validasi_menu(data)
    with lock:
        menu = get_menu()
        item = next((m for m in menu if m["id"] == menu_id), None)
        if item is None:
            raise NotFound("Menu tidak ditemukan")
        if nama_kembar(menu, baru["nama"], menu_id):
            raise ValueError("Menu dengan nama itu sudah ada")
        item.update(baru)
        write_json(MENU_FILE, menu)
    broadcast("menu", {})
    return item


def hapus_menu(menu_id):
    with lock:
        menu = get_menu()
        sisa = [m for m in menu if m["id"] != menu_id]
        if len(sisa) == len(menu):
            raise NotFound("Menu tidak ditemukan")
        write_json(MENU_FILE, sisa)
    broadcast("menu", {})


# ---------- Rekomendasi menu ----------
# 1) Jika ANTHROPIC_API_KEY diatur -> memakai Claude.
# 2) Jika tidak -> pencocokan kata kunci lokal (tetap berfungsi tanpa internet).
def rekomendasi_lokal(pesan, menu):
    t = pesan.lower()
    tersedia = [m for m in menu if m.get("tersedia")]
    skor = {m["id"]: 0 for m in tersedia}

    def ubah(tag, bobot):
        for m in tersedia:
            if tag in m.get("tags", []):
                skor[m["id"]] += bobot

    if re.search(r"\b(tidak|ga|gak|nggak|ngga|bukan|kurang|anti)\s*(terlalu\s*)?(suka\s*)?pahit", t):
        ubah("pahit", -3)
        ubah("manis", 2)
        ubah("creamy", 2)
    elif re.search(r"pahit|strong|pekat", t):
        ubah("pahit", 2)
    if re.search(r"segar|dingin|fresh|panas terik|haus", t):
        ubah("segar", 2)
    if re.search(r"manis|sweet|gula", t):
        ubah("manis", 2)
    if re.search(r"susu|creamy|lembut|halus", t):
        ubah("creamy", 2)
    if re.search(r"kuat|ngantuk|begadang|semangat|melek|kafein", t):
        ubah("kuat", 2)
    if re.search(r"asam|lemon", t):
        ubah("asam", 2)
    if re.search(r"lapar|makan|camilan|cemilan|snack|roti", t):
        ubah("makanan", 4)
    if re.search(r"tanpa kopi|non.?kopi|bukan kopi|tidak (bisa )?kopi|maag", t):
        for m in tersedia:
            if m["kategori"] == "Kopi":
                skor[m["id"]] -= 5

    if not skor or max(skor.values()) <= 0:
        pilih = [m for m in tersedia if m["id"] in (3, 5, 2)][:3]
        return {
            "jawaban": "Saya belum menangkap seleranya. Ini beberapa menu favorit pelanggan kami. "
                       "Coba ceritakan: suka yang manis, pahit, segar, atau creamy?",
            "rekomendasi": [{"id": m["id"], "alasan": m["deskripsi"]} for m in pilih],
            "sumber": "lokal",
        }
    pilih = sorted([m for m in tersedia if skor[m["id"]] > 0],
                   key=lambda m: -skor[m["id"]])[:3]
    return {
        "jawaban": "Berdasarkan permintaanmu, ini yang cocok:",
        "rekomendasi": [{"id": m["id"], "alasan": m["deskripsi"]} for m in pilih],
        "sumber": "lokal",
    }


def rekomendasi_ai(pesan, menu):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    tersedia = [m for m in menu if m.get("tersedia")]
    system = (
        "Kamu barista yang ramah di sebuah coffee shop. Rekomendasikan 2-3 menu HANYA dari daftar menu "
        "yang diberikan (jangan mengarang menu). Jawab dalam Bahasa Indonesia, singkat. Balas HANYA dengan "
        'JSON tanpa teks lain, format: {"jawaban":"kalimat pembuka singkat",'
        '"rekomendasi":[{"id":<id menu>,"alasan":"alasan satu kalimat"}]}'
    )
    ringkas = [{k: m[k] for k in ("id", "nama", "kategori", "deskripsi", "tags")} for m in tersedia]
    body = {
        "model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        "max_tokens": 600,
        "system": system,
        "messages": [{"role": "user", "content":
                      f"Daftar menu tersedia:\n{json.dumps(ringkas, ensure_ascii=False)}\n\n"
                      f"Permintaan pelanggan: {pesan}"}],
    }
    try:
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=json.dumps(body).encode("utf-8"),
            headers={"content-type": "application/json", "x-api-key": key,
                     "anthropic-version": "2023-06-01"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as res:
            data = json.loads(res.read().decode("utf-8"))
        teks = "".join(c.get("text", "") for c in data.get("content", []))
        teks = re.sub(r"```json|```", "", teks).strip()
        parsed = json.loads(teks)
        ids = {m["id"] for m in tersedia}
        valid = []
        for r in parsed.get("rekomendasi", []):
            try:
                rid = int(r.get("id"))
            except (TypeError, ValueError):
                continue
            if rid in ids:
                valid.append({"id": rid, "alasan": str(r.get("alasan", ""))[:200]})
        valid = valid[:3]
        if not valid:
            return None
        return {"jawaban": str(parsed.get("jawaban", ""))[:300], "rekomendasi": valid, "sumber": "claude"}
    except Exception:
        return None


# ---------- HTTP ----------
MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}
HALAMAN = {"/": "index.html", "/barista": "barista.html", "/menu": "menu.html",
           "/laporan": "laporan.html", "/meja": "meja.html"}


class Handler(BaseHTTPRequestHandler):
    server_version = "KopiAI/1.0"

    def log_message(self, fmt, *args):  # senyapkan log bawaan
        pass

    # --- helper ---
    def send_json(self, code, obj):
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            raise ValueError("Body terlalu besar")
        if length == 0:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            raise ValueError("JSON tidak valid")
        if not isinstance(data, dict):
            raise ValueError("JSON tidak valid")
        return data

    def butuh_login(self, path, method):
        """True (dan kirim 401) jika akses staf tanpa kredensial yang benar."""
        if not STAFF_PASSWORD:
            return False
        staf = path in HALAMAN_STAF or (path.startswith("/api/") and api_khusus_staf(method, path))
        if not staf:
            return False
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                user, _, pw = base64.b64decode(header[6:]).decode("utf-8").partition(":")
                if sama(user, STAFF_USER) and sama(pw, STAFF_PASSWORD):
                    return False
            except Exception:
                pass
        pesan = "Login diperlukan".encode("utf-8")
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Kopi Sudut Staf", charset="UTF-8"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(pesan)))
        self.end_headers()
        self.wfile.write(pesan)
        return True

    # --- routing ---
    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PATCH(self):
        self.route("PATCH")

    def do_PUT(self):
        self.route("PUT")

    def do_DELETE(self):
        self.route("DELETE")

    def route(self, method):
        path = urlparse(self.path).path
        if self.butuh_login(path, method):
            return
        if path.startswith("/api/"):
            try:
                self.handle_api(method, path)
            except NotFound as e:
                self.send_json(404, {"error": str(e)})
            except ValueError as e:
                self.send_json(400, {"error": str(e)})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:  # noqa
                self.send_json(500, {"error": f"Kesalahan server: {e}"})
        elif method == "GET":
            self.serve_static(path)
        else:
            self.send_json(405, {"error": "Metode tidak diizinkan"})

    def handle_api(self, method, p):
        if method == "GET" and p == "/api/menu":
            return self.send_json(200, get_menu())

        if method == "GET" and p == "/api/events":
            return self.stream_events()

        if method == "GET" and p == "/api/orders":
            return self.send_json(200, get_orders())

        if method == "POST" and p == "/api/orders":
            return self.send_json(201, buat_pesanan(self.read_body()))

        m = re.fullmatch(r"/api/orders/(\d+)", p)
        if m:
            oid = int(m.group(1))
            if method == "GET":
                order = next((o for o in get_orders() if o["id"] == oid), None)
                if not order:
                    return self.send_json(404, {"error": "Pesanan tidak ditemukan"})
                return self.send_json(200, order)
            if method == "PATCH":
                status = self.read_body().get("status")
                if status not in STATUS:
                    return self.send_json(400, {"error": "Status tidak valid"})
                with lock:
                    orders = get_orders()
                    order = next((o for o in orders if o["id"] == oid), None)
                    if not order:
                        return self.send_json(404, {"error": "Pesanan tidak ditemukan"})
                    order["status"] = status
                    write_json(ORDERS_FILE, orders)
                broadcast("update", {})
                return self.send_json(200, order)

        if method == "POST" and p == "/api/menu":
            return self.send_json(201, tambah_menu(self.read_body()))

        m = re.fullmatch(r"/api/menu/(\d+)", p)
        if m and method == "PUT":
            return self.send_json(200, ubah_menu(int(m.group(1)), self.read_body()))
        if m and method == "DELETE":
            hapus_menu(int(m.group(1)))
            return self.send_json(200, {"ok": True})

        m = re.fullmatch(r"/api/menu/(\d+)/tersedia", p)
        if m and method == "PATCH":
            with lock:
                menu = get_menu()
                item = next((x for x in menu if x["id"] == int(m.group(1))), None)
                if not item:
                    return self.send_json(404, {"error": "Menu tidak ditemukan"})
                item["tersedia"] = not item.get("tersedia", True)
                write_json(MENU_FILE, menu)
            broadcast("menu", {})
            return self.send_json(200, item)

        if method == "GET" and p == "/api/laporan":
            return self.send_json(200, buat_laporan())

        if method == "POST" and p == "/api/rekomendasi":
            teks = str(self.read_body().get("pesan") or "").strip()[:300]
            if not teks:
                return self.send_json(400, {"error": "Pesan kosong"})
            menu = get_menu()
            hasil = rekomendasi_ai(teks, menu) or rekomendasi_lokal(teks, menu)
            return self.send_json(200, hasil)

        return self.send_json(404, {"error": "Endpoint tidak ditemukan"})

    def stream_events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.wfile.write(b"retry: 3000\n\n")
        self.wfile.flush()
        with clients_lock:
            clients.append(self.wfile)
        try:
            while True:  # tahan koneksi; kirim ping agar tidak putus
                time.sleep(25)
                with clients_lock:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except Exception:
            pass
        finally:
            with clients_lock:
                if self.wfile in clients:
                    clients.remove(self.wfile)

    def serve_static(self, path):
        rel = HALAMAN.get(path) or unquote(path).lstrip("/")
        file = (PUBLIC_DIR / rel).resolve()
        if PUBLIC_DIR not in file.parents or not file.is_file():
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Halaman tidak ditemukan".encode("utf-8"))
            return
        content = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(file.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def ip_lokal():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))  # tidak mengirim data, hanya mencari IP jaringan
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def main():
    server = Server(("0.0.0.0", PORT), Handler)
    print("\n=== Kopi AI - Sistem Pemesanan Coffee Shop (Python) ===")
    print(f"Pelanggan : http://localhost:{PORT}/?meja=1")
    print(f"Barista   : http://localhost:{PORT}/barista")
    print(f"Kelola menu: http://localhost:{PORT}/menu")
    print(f"Laporan   : http://localhost:{PORT}/laporan")
    print(f"QR meja   : http://localhost:{PORT}/meja")
    ip = ip_lokal()
    if ip:
        print(f"\nAkses dari HP (satu Wi-Fi): http://{ip}:{PORT}/?meja=1")
    if STAFF_PASSWORD:
        print(f"\nLogin staf aktif (username: {STAFF_USER})")
    else:
        print("\nLogin staf: nonaktif (set STAFF_PASSWORD sebelum dipublikasikan)")
    if os.environ.get("ANTHROPIC_API_KEY"):
        print("\nRekomendasi AI: Claude aktif")
    else:
        print("\nRekomendasi AI: mode lokal (set ANTHROPIC_API_KEY untuk memakai Claude)")
    print("Tekan Ctrl+C untuk berhenti.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer dihentikan.")
        server.server_close()


if __name__ == "__main__":
    main()
