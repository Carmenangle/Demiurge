import type { ChatMessage } from "../types/chat";

export function messageActivityVersion(message: ChatMessage): string {
  const tail = (value = "") => `${value.length}:${value.slice(-80)}`;
  return [
    tail(message.text),
    tail(message.thinking),
    message.image || "",
    message.video || "",
    (message.parts || []).map((part) => `${part.type}:${part.url || tail(part.text)}`).join("|"),
    message.promptApproval
      ? `${message.promptApproval.status}:${message.promptApproval.stage || ""}:${tail(message.promptApproval.prompt)}`
      : "",
    message.routeChoice
      ? `${message.routeChoice.status}:${message.routeChoice.selectedRoute || ""}`
      : "",
  ].join("\u001f");
}

export function changedAssistantMessageIds(
  previous: ReadonlyMap<string, string>,
  messages: ChatMessage[],
): { ids: string[]; versions: Map<string, string> } {
  const assistantMessages = messages.filter((message) => message.role === "assistant");
  const versions = new Map(
    assistantMessages.map((message) => [message.id, messageActivityVersion(message)]),
  );
  return {
    ids: assistantMessages
      .filter((message) => previous.get(message.id) !== versions.get(message.id))
      .map((message) => message.id),
    versions,
  };
}

export function appendUniqueMessageIds(current: string[], additions: string[]): string[] {
  const next = [...current];
  for (const id of additions) {
    if (!next.includes(id)) next.push(id);
  }
  return next;
}
