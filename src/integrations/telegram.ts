import { Telegraf } from "telegraf";

export async function startTelegramBot(token: string): Promise<void> {
  const bot = new Telegraf(token);

  bot.start(async (ctx) => {
    await ctx.reply("YayatBot siap! Kirim /help untuk bantuan.");
  });

  bot.help(async (ctx) => {
    await ctx.reply(
      [
        "Perintah:",
        "/start - Mulai bot",
        "/help - Bantuan",
        "/ping - Cek latensi",
        "/echo <teks> - Balas dengan teks yang sama",
      ].join("\n"),
    );
  });

  bot.command("ping", async (ctx) => {
    const before = Date.now();
    const message = await ctx.reply("Pong!");
    const latencyMs = Date.now() - before;
    await ctx.telegram.editMessageText(
      message.chat.id,
      message.message_id,
      undefined,
      `Pong! ~${latencyMs}ms`,
    );
  });

  bot.command("echo", async (ctx) => {
    const text = ctx.message?.text ?? "";
    const payload = text.replace(/^\/echo\s*/, "");
    if (payload.length === 0) {
      return ctx.reply("Contoh: /echo hello world");
    }
    return ctx.reply(payload);
  });

  bot.on("text", async (ctx) => {
    // Simple acknowledgment
    if (ctx.message.text.startsWith("/")) return; // command handled elsewhere
    await ctx.reply("Terima kasih! Gunakan /help untuk daftar perintah.");
  });

  await bot.launch();
  console.log("[telegram] Bot launched.");

  // Graceful stop
  process.once("SIGINT", () => bot.stop("SIGINT"));
  process.once("SIGTERM", () => bot.stop("SIGTERM"));
}