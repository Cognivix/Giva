// ChatView.swift - Message list with streaming text and input field.

import SwiftUI

struct ChatView: View {
    @EnvironmentObject var viewModel: GivaViewModel

    var body: some View {
        VStack(spacing: 0) {
            // Message list
            ScrollViewReader { proxy in
                ScrollView {
                    if viewModel.messages.isEmpty {
                        VStack(spacing: 12) {
                            Image(systemName: "bubble.left.and.text.bubble.right")
                                .font(.system(size: 32))
                                .foregroundColor(.secondary.opacity(0.5))
                            Text("Ask Giva anything about your emails,\ncalendar, or tasks.")
                                .font(.callout)
                                .foregroundColor(.secondary)
                                .multilineTextAlignment(.center)
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .padding(.top, 60)
                    } else {
                        LazyVStack(alignment: .leading, spacing: 8) {
                            ForEach(viewModel.messages) { message in
                                MessageBubble(message: message)
                                    .id(message.id)
                            }
                        }
                        .padding(12)
                    }
                }
                .onChange(of: viewModel.messages.count) { _, _ in
                    if let last = viewModel.messages.last {
                        withAnimation(.easeOut(duration: 0.2)) {
                            proxy.scrollTo(last.id, anchor: .bottom)
                        }
                    }
                }
                // Also scroll when streaming content updates
                .onChange(of: viewModel.messages.last?.content.count ?? 0) { _, _ in
                    if let last = viewModel.messages.last {
                        proxy.scrollTo(last.id, anchor: .bottom)
                    }
                }
            }

            Divider()

            // Input field
            HStack(spacing: 8) {
                TextField("Ask Giva...", text: $viewModel.currentInput)
                    .textFieldStyle(.plain)
                    .font(.system(size: 13))
                    .onSubmit {
                        viewModel.sendMessage()
                    }
                    .disabled(viewModel.isStreaming)

                if viewModel.isStreaming {
                    Button(action: { viewModel.cancelStreaming() }) {
                        Image(systemName: "stop.circle.fill")
                            .font(.system(size: 18))
                            .foregroundColor(.red)
                    }
                    .buttonStyle(.plain)
                    .help("Stop generating")
                } else {
                    Button(action: { viewModel.sendMessage() }) {
                        Image(systemName: "arrow.up.circle.fill")
                            .font(.system(size: 18))
                            .foregroundColor(.accentColor)
                    }
                    .buttonStyle(.plain)
                    .disabled(viewModel.currentInput.trimmingCharacters(in: .whitespaces).isEmpty)
                    .help("Send message")
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
        }
    }
}

struct MessageBubble: View {
    let message: ChatMessage

    var body: some View {
        HStack(alignment: .top) {
            if message.role == "user" {
                Spacer(minLength: 60)
            }

            VStack(alignment: message.role == "user" ? .trailing : .leading, spacing: 4) {
                Text(message.content)
                    .font(.system(size: 13))
                    .textSelection(.enabled)
                    .lineLimit(nil)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(
                        message.role == "user"
                            ? Color.accentColor.opacity(0.15)
                            : Color(nsColor: .controlBackgroundColor)
                    )
                    .cornerRadius(10)

                if message.isStreaming {
                    HStack(spacing: 4) {
                        ProgressView()
                            .controlSize(.mini)
                        Text("Generating...")
                            .font(.caption2)
                            .foregroundColor(.secondary)
                    }
                }
            }

            if message.role != "user" {
                Spacer(minLength: 40)
            }
        }
    }
}
