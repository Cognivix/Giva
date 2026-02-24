// AgentActionHandlerTests.swift - Tests for shared agent action parsing.

import Testing
@testable import GivaApp

@Suite("AgentActionHandler")
struct AgentActionHandlerTests {
    // MARK: - parseActions

    @Test("Parses task_created actions")
    func parseTaskCreated() {
        let json = """
        [{"type": "task_created", "title": "Buy groceries", "note": "From email discussion"}]
        """
        let actions = AgentActionHandler.parseActions(json)
        #expect(actions.count == 1)
        #expect(actions[0].type == "task_created")
        #expect(actions[0].title == "Buy groceries")
        #expect(actions[0].note == "From email discussion")
    }

    @Test("Parses objective_created actions")
    func parseObjectiveCreated() {
        let json = """
        [{"type": "objective_created", "title": "Learn Swift", "note": null}]
        """
        let actions = AgentActionHandler.parseActions(json)
        #expect(actions.count == 1)
        #expect(actions[0].type == "objective_created")
        #expect(actions[0].title == "Learn Swift")
        #expect(actions[0].note == nil)
    }

    @Test("Parses multiple actions")
    func parseMultipleActions() {
        let json = """
        [
            {"type": "task_created", "title": "Task 1"},
            {"type": "preference", "key": "theme", "note": "dark"}
        ]
        """
        let actions = AgentActionHandler.parseActions(json)
        #expect(actions.count == 2)
        #expect(actions[0].type == "task_created")
        #expect(actions[1].type == "preference")
        #expect(actions[1].key == "theme")
    }

    @Test("Returns empty array for invalid JSON")
    func invalidJSON() {
        #expect(AgentActionHandler.parseActions("not json").isEmpty)
        #expect(AgentActionHandler.parseActions("").isEmpty)
        #expect(AgentActionHandler.parseActions("{}").isEmpty) // not an array
    }

    @Test("Skips entries without type")
    func missingType() {
        let json = """
        [{"title": "No type field"}, {"type": "valid", "title": "Has type"}]
        """
        let actions = AgentActionHandler.parseActions(json)
        #expect(actions.count == 1)
        #expect(actions[0].type == "valid")
    }

    // MARK: - parseConfirmation

    @Test("Parses valid agent confirmation")
    func parseConfirmation() {
        let json = """
        {
            "job_id": "abc-123",
            "agent_id": "task_extractor",
            "agent_name": "Task Extractor",
            "message": "Found 3 tasks. Create them?",
            "params": {"count": 3}
        }
        """
        let confirmation = AgentActionHandler.parseConfirmation(json)
        #expect(confirmation != nil)
        #expect(confirmation?.id == "abc-123")
        #expect(confirmation?.agentName == "Task Extractor")
        #expect(confirmation?.message == "Found 3 tasks. Create them?")
    }

    @Test("Returns nil for invalid confirmation JSON")
    func invalidConfirmation() {
        #expect(AgentActionHandler.parseConfirmation("not json") == nil)
        #expect(AgentActionHandler.parseConfirmation("{}") == nil) // missing required fields
    }

    // MARK: - parseQueuedAgentName

    @Test("Parses queued agent name")
    func parseQueuedName() {
        let json = """
        {"agent_name": "Context Builder"}
        """
        #expect(AgentActionHandler.parseQueuedAgentName(json) == "Context Builder")
    }

    @Test("Returns nil for missing agent_name")
    func missingAgentName() {
        #expect(AgentActionHandler.parseQueuedAgentName("{}") == nil)
        #expect(AgentActionHandler.parseQueuedAgentName("invalid") == nil)
    }
}
