// Jika halaman dibuka langsung dari file (file://), tombol tidak bisa bekerja
// karena tidak ada server. Tampilkan peringatan yang jelas.
(function () {
  if (location.protocol !== 'file:') return;
  document.addEventListener('DOMContentLoaded', function () {
    var d = document.createElement('div');
    d.setAttribute('role', 'alert');
    d.style.cssText = 'background:#b8402f;color:#fff;padding:14px 18px;font:600 15px system-ui,sans-serif;position:sticky;top:0;z-index:100';
    d.textContent = 'Halaman dibuka langsung dari file, jadi tombol tidak akan berfungsi. ' +
      'Jalankan "python server.py" di terminal, lalu buka http://localhost:3000';
    document.body.insertBefore(d, document.body.firstChild);
  });
})();
