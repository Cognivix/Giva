// AgentActionHandler.swift - Shared agent action parsing for both ViewModels.

import Foundation

enum AgentActionHandler {
    struct ParsedAction {
        let type: String
        let title: String?
        let note: String?
        let key: String?
    }

    static func parseActions(_ json: String) -> [ParsedAction] {
        guard let data = json.data(using: .utf8),
              let actions = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]]
        else { return [] }

        return actions.compactMap { action in
            guard let type = action["type"] as? String else { return nil }
            return ParsedAction(
                type: type,
                title: action["title"] as? String,
                note: action["note"] as? String,
                key: action["key"] as? String
            )
        }
    }

    static func parseConfirmation(_ json: String) -> AgentConfirmation? {
        AgentConfirmation(from: json)
    }

    static func parseQueuedAgentName(_ json: String) -> String? {
        guard let data = json.data(using: .utf8),
              let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let agentName = dict["agent_name"] as? String
        else { return nil }
        return agentName
    }
}
