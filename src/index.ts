import "dotenv/config";

async function main(): Promise<void> {
  const mode = process.argv[2] ?? "hello";

  switch (mode) {
    case "hello": {
      console.log("YayatBot ready. Use 'dev' to start development mode.");
      break;
    }
    case "dev": {
      console.log("Starting YayatBot in dev mode...");
      const telegramToken = process.env.TELEGRAM_BOT_TOKEN;
      if (telegramToken) {
        const { startTelegramBot } = await import("./integrations/telegram");
        await startTelegramBot(telegramToken);
      } else {
        console.log(
          "[telegram] TELEGRAM_BOT_TOKEN tidak ditemukan. Lewati integrasi Telegram.",
        );
      }
      break;
    }
    default: {
      console.error(`Unknown command: ${mode}`);
      process.exitCode = 1;
    }
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});