// SettingsView.swift - macOS Settings window with tabbed layout.
//
// Displayed inside the Settings scene (⌘,). Fetches config from the server
// on appear, lets the user edit values, and persists changes via PUT /api/config.
// Tabs: Models, Sync, General, Goals, Profile.
//
// The Models tab fetches compatible models from HuggingFace (same endpoint as
// bootstrap) so users can pick from a dropdown instead of typing model IDs.

import SwiftUI

struct SettingsView: View {
    @Environment(GivaViewModel.self) private var viewModel

    @State private var selectedTab: SettingsTab = .models

    // ── Config editing state ──

    @State private var llmModel = ""
    @State private var llmFilterModel = ""
    @State private var llmMaxTokens = 2048
    @State private var llmTemperature = 0.7
    @State private var llmContextBudget = 8000

    @State private var voiceEnabled = false
    @State private var ttsVoice = "af_heart"

    @State private var powerEnabled = true
    @State private var batteryPause = 20
    @State private var batteryDeferHeavy = 50
    @State private var modelIdleTimeout = 20

    @State private var mailSyncInterval = 15
    @State private var calSyncInterval = 15
    @State private var mailBatchSize = 50
    @State private var calPastDays = 7
    @State private var calFutureDays = 30

    @State private var agentsEnabled = true
    @State private var agentRouting = true
    @State private var agentTimeout = 60

    @State private var dailyReviewHour = 18
    @State private var planHorizonDays = 7

    @State private var vlmEnabled = false
    @State private var vlmModel = ""

    @State private var isSaving = false
    @State private var hasChanges = false
    @State private var saveMessage: String?

    // ── Model browsing (HuggingFace) ──

    @State private var availableModels: AvailableModelsResponse?
    @State private var isLoadingModels = false
    @State private var modelError: String?

    var body: some View {
        Group {
            if viewModel.isLoadingConfig && viewModel.config == nil {
                ProgressView("Loading settings...")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if viewModel.config != nil {
                settingsContent
            } else {
                ContentUnavailableView(
                    "Settings Unavailable",
                    systemImage: "gear.badge.xmark",
                    description: Text("Could not load settings from the server.")
                )
            }
        }
        .task { await viewModel.loadConfig() }
        .onChange(of: viewModel.config) { _, cfg in
            if let cfg { populateFields(from: cfg) }
        }
        .onChange(of: viewModel.selectedSettingsTab) { _, tab in
            if let tab {
                selectedTab = tab
                viewModel.selectedSettingsTab = nil
            }
        }
    }

    private var settingsContent: some View {
        TabView(selection: $selectedTab) {
            modelsTab
                .tabItem { Label("Models", systemImage: "cpu") }
                .tag(SettingsTab.models)

            syncTab
                .tabItem { Label("Sync", systemImage: "arrow.triangle.2.circlepath") }
                .tag(SettingsTab.sync)

            generalTab
                .tabItem { Label("General", systemImage: "gear") }
                .tag(SettingsTab.general)

            goalsTab
                .tabItem { Label("Goals", systemImage: "mountain.2") }
                .tag(SettingsTab.goals)

            profileTab
                .tabItem { Label("Profile", systemImage: "person.circle") }
                .tag(SettingsTab.profile)

            shortcutsTab
                .tabItem { Label("Shortcuts", systemImage: "keyboard") }
                .tag(SettingsTab.shortcuts)
        }
        .frame(width: 560, height: 460)
    }

    // MARK: - Models Tab

    private var modelsTab: some View {
        Form {
            Section {
                modelPickerField("Assistant Model", selection: assistantBinding, role: .assistant)
                modelPickerField("Filter Model", selection: filterBinding, role: .filter)

                if let error = modelError {
                    Label(error, systemImage: "exclamationmark.triangle")
                        .font(.caption)
                        .foregroundStyle(.orange)
                }
            } header: {
                HStack {
                    Label("Models", systemImage: "cpu")
                    Spacer()
                    if let hw = availableModels?.hardware {
                        Text("\(hw.chip) \u{2022} \(hw.ramGb) GB RAM")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            } footer: {
                Text("Model changes take effect after a server restart.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Inference") {
                LabeledContent("Max Tokens") {
                    TextField("", value: $llmMaxTokens, format: .number)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 100)
                        .onChange(of: llmMaxTokens) { _, _ in hasChanges = true }
                }

                LabeledContent("Temperature") {
                    HStack(spacing: 8) {
                        Slider(value: $llmTemperature, in: 0...2, step: 0.1)
                            .frame(width: 160)
                        Text(String(format: "%.1f", llmTemperature))
                            .font(.body.monospacedDigit())
                            .frame(width: 32, alignment: .trailing)
                    }
                    .onChange(of: llmTemperature) { _, _ in hasChanges = true }
                }

                LabeledContent("Context Budget (tokens)") {
                    TextField("", value: $llmContextBudget, format: .number)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 100)
                        .onChange(of: llmContextBudget) { _, _ in hasChanges = true }
                }
            }

            Section {
                Toggle("Enable Browser Automation", isOn: $vlmEnabled)
                    .onChange(of: vlmEnabled) { _, _ in hasChanges = true }

                if vlmEnabled {
                    vlmModelPickerField
                }
            } header: {
                Label("Vision (VLM)", systemImage: "eye")
            } footer: {
                Text("VLM enables visual web task execution via the Chrome extension.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .safeAreaInset(edge: .bottom) { configBottomBar }
        .task { await loadAvailableModels() }
    }

    // MARK: - Model Picker

    private enum ModelRole {
        case assistant
        case filter
    }

    private var assistantBinding: Binding<String> {
        Binding(get: { llmModel }, set: { llmModel = $0 })
    }

    private var filterBinding: Binding<String> {
        Binding(get: { llmFilterModel }, set: { llmFilterModel = $0 })
    }

    @ViewBuilder
    private var vlmModelPickerField: some View {
        LabeledContent("VLM Model") {
            if let models = availableModels, let vlmList = models.vlmModels, !vlmList.isEmpty {
                let sorted = vlmList.sorted {
                    let o0 = downloadSortOrder($0), o1 = downloadSortOrder($1)
                    if o0 != o1 { return o0 < o1 }
                    return $0.sizeGb > $1.sizeGb
                }
                Picker("", selection: $vlmModel) {
                    ForEach(sorted) { model in
                        Text(modelPickerLabel(model)).tag(model.modelId)
                    }
                    if !sorted.contains(where: { $0.modelId == vlmModel })
                        && !vlmModel.isEmpty {
                        Text(vlmModel
                            .replacingOccurrences(of: "mlx-community/", with: ""))
                            .tag(vlmModel)
                    }
                }
                .frame(maxWidth: 360)
                .onChange(of: vlmModel) { _, _ in hasChanges = true }
            } else {
                HStack {
                    TextField("", text: $vlmModel)
                        .textFieldStyle(.roundedBorder)
                        .frame(maxWidth: 300)
                        .onChange(of: vlmModel) { _, _ in hasChanges = true }

                    if isLoadingModels {
                        ProgressView().controlSize(.small)
                    } else {
                        Button("Browse") {
                            Task { await loadAvailableModels() }
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func modelPickerField(_ label: String, selection: Binding<String>, role: ModelRole) -> some View {
        LabeledContent(label) {
            if let models = availableModels {
                let candidates = role == .assistant ? assistantModels(models) : filterModels(models)
                Picker("", selection: selection) {
                    ForEach(candidates) { model in
                        Text(modelPickerLabel(model)).tag(model.modelId)
                    }
                    // Include current value if not in the fetched list
                    if !candidates.contains(where: { $0.modelId == selection.wrappedValue })
                        && !selection.wrappedValue.isEmpty {
                        Text(selection.wrappedValue
                            .replacingOccurrences(of: "mlx-community/", with: ""))
                            .tag(selection.wrappedValue)
                    }
                }
                .frame(maxWidth: 360)
                .onChange(of: selection.wrappedValue) { _, _ in hasChanges = true }
            } else {
                HStack {
                    TextField("", text: selection)
                        .textFieldStyle(.roundedBorder)
                        .frame(maxWidth: 300)
                        .onChange(of: selection.wrappedValue) { _, _ in hasChanges = true }

                    if isLoadingModels {
                        ProgressView().controlSize(.small)
                    } else {
                        Button("Browse") {
                            Task { await loadAvailableModels() }
                        }
                    }
                }
            }
        }
    }

    private func assistantModels(_ models: AvailableModelsResponse) -> [ModelInfo] {
        models.compatibleModels
            .filter { !$0.modelId.lowercased().contains("embedding") }
            .sorted {
                let o0 = downloadSortOrder($0), o1 = downloadSortOrder($1)
                if o0 != o1 { return o0 < o1 }
                return $0.sizeGb > $1.sizeGb
            }
    }

    private func filterModels(_ models: AvailableModelsResponse) -> [ModelInfo] {
        models.compatibleModels
            .filter { $0.sizeGb <= 10 && !$0.modelId.lowercased().contains("embedding") }
            .sorted {
                let o0 = downloadSortOrder($0), o1 = downloadSortOrder($1)
                if o0 != o1 { return o0 < o1 }
                return $0.sizeGb < $1.sizeGb
            }
    }

    private func downloadSortOrder(_ model: ModelInfo) -> Int {
        switch model.downloadStatus {
        case "complete": return 0
        case "partial": return 1
        default: return 2
        }
    }

    private func modelPickerLabel(_ model: ModelInfo) -> String {
        let suffix: String
        switch model.downloadStatus {
        case "complete": suffix = " [ready]"
        case "partial": suffix = " [incomplete]"
        default: suffix = ""
        }
        return "\(model.displayName) (\(model.sizeString))\(suffix)"
    }

    private func loadAvailableModels() async {
        guard let api = viewModel.apiService else { return }
        isLoadingModels = true
        modelError = nil
        do {
            availableModels = try await api.getAvailableModels()
        } catch {
            modelError = "Could not fetch models: \(error.localizedDescription)"
        }
        isLoadingModels = false
    }

    // MARK: - Sync Tab

    private var syncTab: some View {
        Form {
            Section {
                LabeledContent("Sync Interval (min)") {
                    TextField("", value: $mailSyncInterval, format: .number)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 60)
                        .onChange(of: mailSyncInterval) { _, _ in hasChanges = true }
                }

                LabeledContent("Batch Size") {
                    TextField("", value: $mailBatchSize, format: .number)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 60)
                        .onChange(of: mailBatchSize) { _, _ in hasChanges = true }
                }
            } header: {
                Label("Mail", systemImage: "envelope")
            }

            Section {
                LabeledContent("Sync Interval (min)") {
                    TextField("", value: $calSyncInterval, format: .number)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 60)
                        .onChange(of: calSyncInterval) { _, _ in hasChanges = true }
                }

                LabeledContent("Past Days") {
                    TextField("", value: $calPastDays, format: .number)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 60)
                        .onChange(of: calPastDays) { _, _ in hasChanges = true }
                }

                LabeledContent("Future Days") {
                    TextField("", value: $calFutureDays, format: .number)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 60)
                        .onChange(of: calFutureDays) { _, _ in hasChanges = true }
                }
            } header: {
                Label("Calendar", systemImage: "calendar")
            }
        }
        .formStyle(.grouped)
        .safeAreaInset(edge: .bottom) { configBottomBar }
    }

    // MARK: - General Tab

    private var generalTab: some View {
        Form {
            Section {
                Toggle("Enable Voice", isOn: $voiceEnabled)
                    .onChange(of: voiceEnabled) { _, _ in hasChanges = true }

                if voiceEnabled {
                    LabeledContent("TTS Voice") {
                        TextField("", text: $ttsVoice)
                            .textFieldStyle(.roundedBorder)
                            .frame(maxWidth: 200)
                            .onChange(of: ttsVoice) { _, _ in hasChanges = true }
                    }
                }
            } header: {
                Label("Voice", systemImage: "waveform")
            }

            Section {
                Toggle("Power-Aware Scheduling", isOn: $powerEnabled)
                    .onChange(of: powerEnabled) { _, _ in hasChanges = true }

                if powerEnabled {
                    LabeledContent("Pause Below Battery %") {
                        TextField("", value: $batteryPause, format: .number)
                            .textFieldStyle(.roundedBorder)
                            .frame(width: 60)
                            .onChange(of: batteryPause) { _, _ in hasChanges = true }
                    }

                    LabeledContent("Defer Heavy Below Battery %") {
                        TextField("", value: $batteryDeferHeavy, format: .number)
                            .textFieldStyle(.roundedBorder)
                            .frame(width: 60)
                            .onChange(of: batteryDeferHeavy) { _, _ in hasChanges = true }
                    }

                    LabeledContent("Model Idle Timeout (min)") {
                        TextField("", value: $modelIdleTimeout, format: .number)
                            .textFieldStyle(.roundedBorder)
                            .frame(width: 60)
                            .onChange(of: modelIdleTimeout) { _, _ in hasChanges = true }
                    }
                }
            } header: {
                Label("Power", systemImage: "bolt")
            } footer: {
                Text("Controls when background work pauses to save battery.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section {
                Toggle("Enable Agents", isOn: $agentsEnabled)
                    .onChange(of: agentsEnabled) { _, _ in hasChanges = true }

                if agentsEnabled {
                    Toggle("LLM Routing", isOn: $agentRouting)
                        .onChange(of: agentRouting) { _, _ in hasChanges = true }

                    LabeledContent("Agent Timeout (sec)") {
                        TextField("", value: $agentTimeout, format: .number)
                            .textFieldStyle(.roundedBorder)
                            .frame(width: 60)
                            .onChange(of: agentTimeout) { _, _ in hasChanges = true }
                    }
                }
            } header: {
                Label("Agents", systemImage: "cpu.fill")
            }
        }
        .formStyle(.grouped)
        .safeAreaInset(edge: .bottom) { configBottomBar }
    }

    // MARK: - Goals Tab

    private var goalsTab: some View {
        Form {
            Section {
                LabeledContent("Daily Review Hour") {
                    Picker("", selection: $dailyReviewHour) {
                        ForEach(0..<24, id: \.self) { hour in
                            Text(hourLabel(hour)).tag(hour)
                        }
                    }
                    .frame(width: 120)
                    .onChange(of: dailyReviewHour) { _, _ in hasChanges = true }
                }

                LabeledContent("Plan Horizon (days)") {
                    TextField("", value: $planHorizonDays, format: .number)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 60)
                        .onChange(of: planHorizonDays) { _, _ in hasChanges = true }
                }
            } header: {
                Label("Goals", systemImage: "mountain.2")
            }
        }
        .formStyle(.grouped)
        .safeAreaInset(edge: .bottom) { configBottomBar }
    }

    // MARK: - Profile Tab

    private var profileTab: some View {
        Group {
            if let profile = viewModel.profile {
                Form {
                    Section {
                        LabeledContent("Name", value: profile.displayName)
                        LabeledContent("Email", value: profile.emailAddress)
                    } header: {
                        Label("Identity", systemImage: "person.circle")
                    }

                    if !profile.topTopics.isEmpty {
                        Section("Top Topics") {
                            FlowLayout(spacing: 6) {
                                ForEach(profile.topTopics, id: \.self) { topic in
                                    Text(topic)
                                        .font(.caption)
                                        .padding(.horizontal, 8)
                                        .padding(.vertical, 4)
                                        .background(.fill.tertiary, in: Capsule())
                                }
                            }
                        }
                    }

                    Section {
                        LabeledContent("Avg Response Time") {
                            Text(String(format: "%.0f min", profile.avgResponseTimeMin))
                        }
                        LabeledContent("Email Volume") {
                            Text(String(format: "%.1f/day", profile.emailVolumeDaily))
                        }
                        if let updatedAt = profile.updatedAt {
                            LabeledContent("Last Updated", value: formatDate(updatedAt))
                        }
                    } header: {
                        Label("Activity", systemImage: "chart.bar")
                    }

                    if !profile.summary.isEmpty {
                        Section("Summary") {
                            Text(profile.summary)
                                .font(.callout)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                .formStyle(.grouped)
                .safeAreaInset(edge: .bottom) {
                    HStack {
                        Spacer()
                        Button("Refresh") {
                            Task { await viewModel.loadProfile() }
                        }
                    }
                    .padding(.horizontal, 20)
                    .padding(.vertical, 10)
                    .background(.bar)
                }
            } else {
                VStack(spacing: 12) {
                    Image(systemName: "person.circle")
                        .font(.system(size: 40))
                        .foregroundStyle(.secondary)
                    Text("No profile data yet.")
                        .foregroundStyle(.secondary)
                    Text("Profile is built after your first email sync.")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                    Button("Refresh") {
                        Task { await viewModel.loadProfile() }
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .task { await viewModel.loadProfile() }
    }

    // MARK: - Shortcuts Tab

    private var shortcutsTab: some View {
        Form {
            Section {
                shortcutRow("New Chat", shortcut: "⌘N")
                shortcutRow("Settings", shortcut: "⌘,")
                shortcutRow("Quick Drop", shortcut: "⌥Space")
            } header: {
                Label("Global", systemImage: "globe")
            } footer: {
                Text("Quick Drop works from any app when Accessibility permission is granted.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section {
                shortcutRow("Send Message", shortcut: "⏎")
                shortcutRow("Stop Generating", shortcut: "Esc")
            } header: {
                Label("Chat", systemImage: "bubble.left.and.text.bubble.right")
            }

            Section {
                shortcutRow("Send in Background", shortcut: "⏎")
                shortcutRow("Open in Full Chat", shortcut: "⌘⏎")
                shortcutRow("Dismiss", shortcut: "Esc")
            } header: {
                Label("Quick Drop", systemImage: "sparkle")
            }
        }
        .formStyle(.grouped)
    }

    private func shortcutRow(_ action: String, shortcut: String) -> some View {
        LabeledContent(action) {
            Text(shortcut)
                .font(.system(size: 12, design: .rounded).weight(.medium))
                .padding(.horizontal, 8)
                .padding(.vertical, 3)
                .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 5))
                .overlay(RoundedRectangle(cornerRadius: 5).stroke(Color.secondary.opacity(0.3), lineWidth: 0.5))
        }
    }

    // MARK: - Bottom Bar (Save / Revert)

    private var configBottomBar: some View {
        HStack {
            if let msg = saveMessage {
                Text(msg)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .transition(.opacity)
            }

            Spacer()

            Button("Revert") {
                if let cfg = viewModel.config {
                    populateFields(from: cfg)
                    hasChanges = false
                    saveMessage = nil
                }
            }
            .disabled(!hasChanges || isSaving)

            Button("Save") {
                Task { await save() }
            }
            .buttonStyle(.borderedProminent)
            .disabled(!hasChanges || isSaving)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 10)
        .background(.bar)
    }

    // MARK: - FlowLayout (for topic tags)

    private struct FlowLayout: Layout {
        var spacing: CGFloat = 6

        func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
            let result = arrange(proposal: proposal, subviews: subviews)
            return result.size
        }

        func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
            let result = arrange(proposal: proposal, subviews: subviews)
            for (index, position) in result.positions.enumerated() {
                subviews[index].place(
                    at: CGPoint(x: bounds.minX + position.x, y: bounds.minY + position.y),
                    proposal: .unspecified
                )
            }
        }

        private func arrange(proposal: ProposedViewSize, subviews: Subviews) -> (size: CGSize, positions: [CGPoint]) {
            let maxWidth = proposal.width ?? .infinity
            var positions: [CGPoint] = []
            var x: CGFloat = 0
            var y: CGFloat = 0
            var rowHeight: CGFloat = 0

            for subview in subviews {
                let size = subview.sizeThatFits(.unspecified)
                if x + size.width > maxWidth && x > 0 {
                    x = 0
                    y += rowHeight + spacing
                    rowHeight = 0
                }
                positions.append(CGPoint(x: x, y: y))
                rowHeight = max(rowHeight, size.height)
                x += size.width + spacing
            }

            return (CGSize(width: maxWidth, height: y + rowHeight), positions)
        }
    }

    // MARK: - Helpers

    private func populateFields(from cfg: ConfigResponse) {
        llmModel = cfg.llm.model
        llmFilterModel = cfg.llm.filterModel
        llmMaxTokens = cfg.llm.maxTokens
        llmTemperature = cfg.llm.temperature
        llmContextBudget = cfg.llm.contextBudgetTokens

        voiceEnabled = cfg.voice.enabled
        ttsVoice = cfg.voice.ttsVoice

        powerEnabled = cfg.power.enabled
        batteryPause = cfg.power.batteryPauseThreshold
        batteryDeferHeavy = cfg.power.batteryDeferHeavyThreshold
        modelIdleTimeout = cfg.power.modelIdleTimeoutMinutes

        mailSyncInterval = cfg.mail.syncIntervalMinutes
        mailBatchSize = cfg.mail.batchSize
        calSyncInterval = cfg.calendar.syncIntervalMinutes
        calPastDays = cfg.calendar.syncWindowPastDays
        calFutureDays = cfg.calendar.syncWindowFutureDays

        agentsEnabled = cfg.agents.enabled
        agentRouting = cfg.agents.routingEnabled
        agentTimeout = cfg.agents.maxExecutionSeconds

        dailyReviewHour = cfg.goals.dailyReviewHour
        planHorizonDays = cfg.goals.planHorizonDays

        vlmEnabled = cfg.vlm.enabled
        vlmModel = cfg.vlm.model

        hasChanges = false
    }

    private func save() async {
        isSaving = true
        saveMessage = nil

        var updates: [String: Any] = [:]

        if let cfg = viewModel.config {
            if llmModel != cfg.llm.model || llmFilterModel != cfg.llm.filterModel
                || llmMaxTokens != cfg.llm.maxTokens || llmTemperature != cfg.llm.temperature
                || llmContextBudget != cfg.llm.contextBudgetTokens {
                updates["llm"] = [
                    "model": llmModel,
                    "filter_model": llmFilterModel,
                    "max_tokens": llmMaxTokens,
                    "temperature": llmTemperature,
                    "context_budget_tokens": llmContextBudget,
                ] as [String: Any]
            }

            if voiceEnabled != cfg.voice.enabled || ttsVoice != cfg.voice.ttsVoice {
                updates["voice"] = [
                    "enabled": voiceEnabled,
                    "tts_voice": ttsVoice,
                ] as [String: Any]
            }

            if powerEnabled != cfg.power.enabled
                || batteryPause != cfg.power.batteryPauseThreshold
                || batteryDeferHeavy != cfg.power.batteryDeferHeavyThreshold
                || modelIdleTimeout != cfg.power.modelIdleTimeoutMinutes {
                updates["power"] = [
                    "enabled": powerEnabled,
                    "battery_pause_threshold": batteryPause,
                    "battery_defer_heavy_threshold": batteryDeferHeavy,
                    "model_idle_timeout_minutes": modelIdleTimeout,
                ] as [String: Any]
            }

            if mailSyncInterval != cfg.mail.syncIntervalMinutes
                || mailBatchSize != cfg.mail.batchSize {
                updates["mail"] = [
                    "sync_interval_minutes": mailSyncInterval,
                    "batch_size": mailBatchSize,
                ] as [String: Any]
            }

            if calSyncInterval != cfg.calendar.syncIntervalMinutes
                || calPastDays != cfg.calendar.syncWindowPastDays
                || calFutureDays != cfg.calendar.syncWindowFutureDays {
                updates["calendar"] = [
                    "sync_interval_minutes": calSyncInterval,
                    "sync_window_past_days": calPastDays,
                    "sync_window_future_days": calFutureDays,
                ] as [String: Any]
            }

            if agentsEnabled != cfg.agents.enabled || agentRouting != cfg.agents.routingEnabled
                || agentTimeout != cfg.agents.maxExecutionSeconds {
                updates["agents"] = [
                    "enabled": agentsEnabled,
                    "routing_enabled": agentRouting,
                    "max_execution_seconds": agentTimeout,
                ] as [String: Any]
            }

            if dailyReviewHour != cfg.goals.dailyReviewHour
                || planHorizonDays != cfg.goals.planHorizonDays {
                updates["goals"] = [
                    "daily_review_hour": dailyReviewHour,
                    "plan_horizon_days": planHorizonDays,
                ] as [String: Any]
            }

            if vlmEnabled != cfg.vlm.enabled || vlmModel != cfg.vlm.model {
                updates["vlm"] = [
                    "enabled": vlmEnabled,
                    "model": vlmModel,
                ] as [String: Any]
            }
        }

        if updates.isEmpty {
            saveMessage = "No changes to save."
            isSaving = false
            return
        }

        await viewModel.updateConfig(updates: updates)
        hasChanges = false
        saveMessage = "Settings saved. Some changes require a server restart."
        isSaving = false
    }

    private func hourLabel(_ hour: Int) -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "h a"
        var comps = DateComponents()
        comps.hour = hour
        let date = Calendar.current.date(from: comps) ?? Date()
        return formatter.string(from: date)
    }

    private func formatDate(_ isoString: String) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: isoString) {
            let display = DateFormatter()
            display.dateFormat = "MMM d, yyyy 'at' h:mm a"
            return display.string(from: date)
        }
        return String(isoString.prefix(16))
    }
}
