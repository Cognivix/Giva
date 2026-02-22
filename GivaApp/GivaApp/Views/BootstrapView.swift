// BootstrapView.swift - First-run setup screen with spinning animation and progress.

import SwiftUI

struct BootstrapView: View {
    @ObservedObject var bootstrap: BootstrapManager

    @State private var dotCount = 0
    private let dotTimer = Timer.publish(every: 0.5, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(spacing: 0) {
            Spacer()

            // Animated icon — use TimelineView to avoid implicit animation layout issues
            TimelineView(.animation(minimumInterval: 1.0 / 30.0)) { context in
                let angle = context.date.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: 2) / 2 * 360
                ZStack {
                    Circle()
                        .stroke(
                            AngularGradient(
                                gradient: Gradient(colors: [.accentColor.opacity(0.1), .accentColor]),
                                center: .center
                            ),
                            lineWidth: 3
                        )
                        .frame(width: 70, height: 70)
                        .rotationEffect(.degrees(angle))

                    Image(systemName: phaseIcon)
                        .font(.system(size: 28))
                        .foregroundColor(.accentColor)
                }
            }
            .frame(width: 76, height: 76)
            .padding(.bottom, 24)

            // Phase title
            Text(phaseTitle)
                .font(.system(size: 16, weight: .semibold))
                .foregroundColor(.primary)
                .padding(.bottom, 4)

            // Phase subtitle
            Text(bootstrap.phase.rawValue)
                .font(.system(size: 12))
                .foregroundColor(.secondary)
                .padding(.bottom, 20)

            // Error display
            if let error = bootstrap.errorMessage {
                VStack(spacing: 8) {
                    Text(error)
                        .font(.system(size: 11))
                        .foregroundColor(.red)
                        .multilineTextAlignment(.center)
                        .lineLimit(4)
                        .padding(.horizontal, 24)

                    Button("Retry") {
                        bootstrap.errorMessage = nil
                        bootstrap.logLines = []
                        Task { await bootstrap.runBootstrap() }
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                }
                .padding(.bottom, 16)
            }

            // Log output (scrolling)
            if !bootstrap.logLines.isEmpty {
                ScrollViewReader { proxy in
                    ScrollView {
                        VStack(alignment: .leading, spacing: 2) {
                            ForEach(Array(bootstrap.logLines.enumerated()), id: \.offset) { i, line in
                                Text(line)
                                    .font(.system(size: 10, design: .monospaced))
                                    .foregroundColor(.secondary)
                                    .id(i)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(8)
                    }
                    .frame(maxHeight: 100)
                    .background(Color(nsColor: .controlBackgroundColor))
                    .cornerRadius(6)
                    .padding(.horizontal, 24)
                    .onChange(of: bootstrap.logLines.count) { _, _ in
                        if let last = bootstrap.logLines.indices.last {
                            proxy.scrollTo(last, anchor: .bottom)
                        }
                    }
                }
            }

            Spacer()

            // Footer
            HStack {
                Text("This only happens once")
                    .font(.system(size: 10))
                    .foregroundColor(.secondary.opacity(0.6))

                Spacer()

                Button("Quit") {
                    NSApplication.shared.terminate(nil)
                }
                .buttonStyle(.plain)
                .font(.system(size: 10))
                .foregroundColor(.secondary)
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 12)
        }
        .frame(width: 420, height: 520)
        .onReceive(dotTimer) { _ in
            dotCount = (dotCount + 1) % 4
        }
    }

    private var phaseTitle: String {
        switch bootstrap.phase {
        case .done:
            return "All set!"
        case .failed:
            return "Oops"
        default:
            return "Cooking" + String(repeating: ".", count: dotCount)
        }
    }

    private var phaseIcon: String {
        switch bootstrap.phase {
        case .findingPython: return "magnifyingglass"
        case .creatingVenv: return "shippingbox"
        case .installingDeps: return "arrow.down.circle"
        case .downloadingDefaultModel: return "brain"
        case .installingDaemon: return "gearshape.2"
        case .startingServer: return "bolt.fill"
        case .done: return "checkmark.circle.fill"
        case .failed: return "exclamationmark.triangle.fill"
        }
    }
}
