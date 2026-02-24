// ServerPhaseTests.swift - Tests for the ServerPhase enum.

import Testing
@testable import GivaApp

@Suite("ServerPhase")
struct ServerPhaseTests {
    @Test("Known phases parse from raw strings")
    func knownPhases() {
        #expect(ServerPhase(serverString: "ready") == .ready)
        #expect(ServerPhase(serverString: "syncing") == .syncing)
        #expect(ServerPhase(serverString: "onboarding") == .onboarding)
        #expect(ServerPhase(serverString: "operational") == .operational)
        #expect(ServerPhase(serverString: "validating") == .validating)
    }

    @Test("Snake-case phases parse correctly")
    func snakeCasePhases() {
        #expect(ServerPhase(serverString: "downloading_default_model") == .downloadingDefaultModel)
        #expect(ServerPhase(serverString: "awaiting_model_selection") == .awaitingModelSelection)
        #expect(ServerPhase(serverString: "downloading_user_models") == .downloadingUserModels)
    }

    @Test("Unknown strings default to .unknown")
    func unknownPhase() {
        #expect(ServerPhase(serverString: "bogus") == .unknown)
        #expect(ServerPhase(serverString: "") == .unknown)
        #expect(ServerPhase(serverString: "OPERATIONAL") == .unknown) // case-sensitive
    }

    @Test("Raw values round-trip")
    func rawValueRoundTrip() {
        for phase in [ServerPhase.ready, .syncing, .onboarding, .operational,
                      .downloadingDefaultModel, .awaitingModelSelection,
                      .downloadingUserModels, .validating] {
            #expect(ServerPhase(rawValue: phase.rawValue) == phase)
        }
    }
}
