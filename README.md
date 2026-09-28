# PLN ADR – Notifikasi Email & Customer App (PoC)

Alur: **CSV feeder → deteksi trigger (S ≥ 80% S_rated) → alokasi target per pelanggan → email berisi link → pelanggan memilih Accept 100% / Accept 80% / Decline di app → iterasi 2 bila kurang → M&V → insentif/penalti → dampak ke tagihan bulanan.**

## Struktur

```
app.py               Konsol operator (Streamlit): monitor, kirim email, pantau respons, settlement
pages/Customer.py    Halaman pelanggan, dibuka dari link email: <APP_BASE_URL>/Customer?token=...
send_requests.py     Alternatif tanpa UI: python send_requests.py data/feeder_snapshot.csv
config.py            Aturan ADR, parameter biaya, pengaturan email
adr/core.py          Rumus: trigger, alokasi w_j, insentif Eq.(2), penalti Eq.(3)
adr/workflow.py      Alur 2 iterasi, respons, settlement, ringkasan tagihan Eq.(7)
adr/mailer.py        Template email + kirim via SMTP / simpan ke folder outbox
adr/db.py            SQLite (adr.db) yang dipakai bersama konsol & halaman pelanggan
data/                Contoh CSV (skenario hipotetis GI Garut)
```

## Format CSV

**feeder_snapshot.csv** – satu baris per feeder per interval
`timestamp, substation, feeder, s_kva, s_rated_kva, duration_h` (duration_h opsional, default 1 jam)

**customers.csv** – pelanggan ADR terdaftar
`customer_id, name, feeder, email, flex_kw, monthly_bill_idr`
- `flex_kw` = D_j, kapasitas penurunan dari baseline 7 hari → dasar bobot alokasi
- `email` = **alamat test** selama PoC

**M&V CSV** (diunggah setelah event) – `customer_id, delivered_kw`

## Menjalankan

```bash
pip install -r requirements.txt
streamlit run app.py
```

Default `EMAIL_MODE = "file"`: email **tidak dikirim**, tapi disimpan di folder `outbox/` (.html bisa dibuka di browser, .eml di Outlook/Thunderbird). Cocok untuk uji coba awal.

Untuk benar-benar mengirim email:
1. Buat akun Gmail khusus test, aktifkan 2-Step Verification, buat **App Password**.
2. Salin `.streamlit/secrets.toml.example` → `.streamlit/secrets.toml`, isi `EMAIL_MODE="smtp"`, SMTP, dan `APP_BASE_URL`.
3. Isi kolom `email` di customers.csv dengan alamat test tim (trik Gmail: `namatim+b01@gmail.com`, `namatim+b02@gmail.com` semuanya masuk ke satu inbox).

## Alur uji (sesuai Table 6 di laporan)

1. Tab **Monitor & send** → *Send ADR requests* → email iterasi 1 terkirim.
2. Buka link di email → pilih Accept 100% / 80% / Decline.
3. Tab **Responses** → *Close Iteration 1*. Pending otomatis dianggap setuju. Kalau komitmen < target feeder, iterasi 2 dikirim untuk kekurangannya (dengan info penalti).
4. *Close Iteration 2* → event CONFIRMED. Di iterasi 2, tidak menjawab = **tidak berkomitmen** (tidak ada penalti).
5. Tab **Settlement** → unggah M&V CSV → insentif/penalti dihitung, email ringkasan terkirim, tagihan bulanan terlihat di halaman pelanggan.

## Aturan perhitungan

| | Rumus |
|---|---|
| Trigger | S ≥ 0,8 × S_rated |
| Target feeder | P_target [kW] = ΔS [kVA] (konservatif) |
| Target pelanggan | P_j = w_j × P_target, w_j = D_j / ΣD |
| Insentif (iterasi 1) | 1/3 × C_avoided × P_terverifikasi × T (maks. sebesar komitmen) |
| Penalti (iterasi 2) | 80% × (komitmen − terverifikasi) × C_avoidable × T |
| Tagihan | B_net = B − ΣInsentif + ΣPenalti |

## Catatan deploy (Streamlit Community Cloud)

- Konsol & halaman pelanggan **harus satu app** (folder `pages/`) supaya memakai adr.db yang sama.
- File di Streamlit Cloud **tidak permanen**: adr.db bisa hilang saat app restart/redeploy. Cukup untuk demo; untuk uji lebih lama pakai database eksternal (mis. Supabase/Google Sheets).
- Link di email berisi token acak per pelanggan; siapa pun yang punya link bisa merespons, jadi hanya kirim ke alamat test.
