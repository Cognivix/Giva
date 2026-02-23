// AgentConfirmationCard.swift - Inline card for agent approval/dismissal.
//
// Appears in the chat message list when the server sends an `agent_confirm`
// event.  Shows the agent name, plan summary, and approve/dismiss buttons.
// Works in both menu bar chat (compact) and full window chat.

import SwiftUI

struct AgentConfirmationCard: View {
    let confirmation: AgentConfirmation
    let onApprove: () -> Void
    let onDismiss: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            // Header
            HStack(spacing: 6) {
                Image(systemName: "cpu")
                    .foregroundColor(.blue)
                Text(confirmation.agentName)
                    .font(.headline)
                Spacer()
                Text("Needs approval")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            // Plan message
            Text(confirmation.message)
                .font(.callout)
                .foregroundColor(.primary)
                .fixedSize(horizontal: false, vertical: true)

            // Action buttons
            HStack(spacing: 10) {
                Button {
                    onApprove()
                } label: {
                    Label("Approve", systemImage: "checkmark.circle")
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)

                Button {
                    onDismiss()
                } label: {
                    Label("Dismiss", systemImage: "xmark.circle")
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
            }
        }
        .padding(10)
        .background(Color.blue.opacity(0.06))
        .cornerRadius(10)
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(Color.blue.opacity(0.15), lineWidth: 1)
        )
    }
}
