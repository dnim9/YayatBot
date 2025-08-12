export interface BotContext {
  userId?: string;
}

export async function startBot(): Promise<void> {
  // Extend this function with real integrations later
  // For now, just simulate a tick
  console.log("[bot] Bootstrapped. Listening for events...");
}