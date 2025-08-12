# YayatBot

Bot pintar berbasis Node.js untuk keperluan otomatisasi.

## Setup

1. Pastikan Node.js v20+ terpasang (repo ini menggunakan ESM dan TypeScript tanpa build).
2. Install dependencies:

```
npm install
```

3. Salin file `.env.example` menjadi `.env` dan isi sesuai kebutuhan.

## Menjalankan

- Development:

```
npm run dev
```

- Cek tipe:

```
npm run typecheck
```

- Lint dan format:

```
npm run lint
npm run format
```

## Integrasi Telegram (opsional)

- Siapkan `TELEGRAM_BOT_TOKEN` di `.env`.
- Jalankan `npm run dev`. Jika token ada, bot Telegram akan otomatis diluncurkan dengan perintah dasar: `/start`, `/help`, `/ping`, `/echo`.

> Jika token tidak tersedia, integrasi Telegram akan dilewati dan aplikasi tetap berjalan dalam mode dev.
