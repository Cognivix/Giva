// SettingsView.swift - User-facing settings panel.
//
// Displayed inside the main window content area. Fetches current config from
// the server on appear, lets the user edit values, and persists changes via
// PUT /api/config. Grouped into sections: Models, Voice, Power, Sync, Agents,
// Goals. Changes require a server restart to take full effect for some
// settings (e.g. model changes).

import SwiftUI

struct SettingsView: View {
    @Environment(GivaViewModel.self) private var viewModel

    // Local editing copies — written back on save.
    @State private var llmModel: String = ""
    @State private var llmFilterModel: String = ""
    @State private var llmMaxTokens: Int = 2048
    @State private var llmTemperature: Double = 0.7
    @State private var llmContextBudget: Int = 8000

    @State private var voiceEnabled: Bool = false
    @State private var ttsVoice: String = "af_heart"

    @State private var powerEnabled: Bool = true
    @State private var batteryPause: Int = 20
    @State private var batteryDeferHeavy: Int = 50
    @State private var modelIdleTimeout: Int = 20

    @State private var mailSyncInterval: Int = 15
    @State private var calSyncInterval: Int = 15
    @State private var mailBatchSize: Int = 50
    @State private var calPastDays: Int = 7
    @State private var calFutureDays: Int = 30

    @State private var agentsEnabled: Bool = true
    @State private var agentRouting: Bool = true
    @State private var agentTimeout: Int = 60

    @State private var dailyReviewHour: Int = 18
    @State private var planHorizonDays: Int = 7

    @State private var isSaving: Bool = false
    @State private var hasChanges: Bool = false
    @State private var saveMessage: String?

    var body: some View {
        Group {
            if viewModel.isLoadingConfig && viewModel.config == nil {
                ProgressView("Loading settings...")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if viewModel.config != nil {
                settingsForm
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
    }

    // MARK: - Form

    private var settingsForm: some View {
        Form {
            modelsSection
            voiceSection
            powerSection
            syncSection
            agentsSection
            goalsSection
        }
        .formStyle(.grouped)
        .scrollContentBackground(.visible)
        .safeAreaInset(edge: .bottom) {
            bottomBar
        }
    }

    // MARK: - Models Section

    private var modelsSection: some View {
        Section {
            LabeledContent("Assistant Model") {
                TextField("", text: $llmModel)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 360)
                    .onChange(of: llmModel) { _, _ in hasChanges = true }
            }

            LabeledContent("Filter Model") {
                TextField("", text: $llmFilterModel)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 360)
                    .onChange(of: llmFilterModel) { _, _ in hasChanges = true }
            }

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
        } header: {
            Label("Models", systemImage: "cpu")
        } footer: {
            Text("Model changes take effect after a server restart.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    // MARK: - Voice Section

    private var voiceSection: some View {
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
    }

    // MARK: - Power Section

    private var powerSection: some View {
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
    }

    // MARK: - Sync Section

    private var syncSection: some View {
        Section {
            LabeledContent("Mail Sync Interval (min)") {
                TextField("", value: $mailSyncInterval, format: .number)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 60)
                    .onChange(of: mailSyncInterval) { _, _ in hasChanges = true }
            }

            LabeledContent("Mail Batch Size") {
                TextField("", value: $mailBatchSize, format: .number)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 60)
                    .onChange(of: mailBatchSize) { _, _ in hasChanges = true }
            }

            LabeledContent("Calendar Sync Interval (min)") {
                TextField("", value: $calSyncInterval, format: .number)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 60)
                    .onChange(of: calSyncInterval) { _, _ in hasChanges = true }
            }

            LabeledContent("Calendar Past Days") {
                TextField("", value: $calPastDays, format: .number)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 60)
                    .onChange(of: calPastDays) { _, _ in hasChanges = true }
            }

            LabeledContent("Calendar Future Days") {
                TextField("", value: $calFutureDays, format: .number)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 60)
                    .onChange(of: calFutureDays) { _, _ in hasChanges = true }
            }
        } header: {
            Label("Sync", systemImage: "arrow.triangle.2.circlepath")
        }
    }

    // MARK: - Agents Section

    private var agentsSection: some View {
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

    // MARK: - Goals Section

    private var goalsSection: some View {
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

    // MARK: - Bottom Bar

    private var bottomBar: some View {
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

        hasChanges = false
    }

    private func save() async {
        isSaving = true
        saveMessage = nil

        var updates: [String: Any] = [:]

        if let cfg = viewModel.config {
            // Only include sections that actually changed
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
}
