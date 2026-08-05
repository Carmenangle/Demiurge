export interface ActiveAgentStream {
  botId: string;
  abort: () => void;
}

export function releaseAgentStream(
  stream: ActiveAgentStream | null,
  reason: "navigation" | "stop",
): null {
  if (reason === "stop") stream?.abort();
  return null;
}
