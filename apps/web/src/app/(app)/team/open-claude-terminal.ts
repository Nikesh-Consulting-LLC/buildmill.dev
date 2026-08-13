// US-55.6: shared by the roster row action and the agent's own page — same
// pop-out window, same target URL, so there's exactly one place that knows
// how to launch a Claude Terminal session.
export function openClaudeTerminal(serverId: string, agentSlotId: string) {
  window.open(
    `/terminal/${serverId}?agentSlot=${agentSlotId}`,
    `ssh_${serverId}_${agentSlotId}`,
    "popup=yes,width=1024,height=720,location=no,menubar=no,toolbar=no,status=no,scrollbars=yes,resizable=yes",
  );
}
