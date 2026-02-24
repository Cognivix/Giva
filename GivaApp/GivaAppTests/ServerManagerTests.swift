// ServerManagerTests.swift - Tests for ServerManager state transitions.

import Testing
@testable import GivaApp

@Suite("ServerManager")
@MainActor
struct ServerManagerTests {
    @Test("Initial state is offline")
    func initialState() {
        let manager = ServerManager()
        #expect(manager.connectionState == .offline)
        #expect(manager.isRunning == false)
        #expect(manager.lastError == nil)
    }

    @Test("recordHeartbeat transitions to connected")
    func heartbeatConnects() {
        let manager = ServerManager()
        manager.recordHeartbeat()
        #expect(manager.connectionState == .connected)
        #expect(manager.isRunning == true)
        #expect(manager.lastError == nil)
    }

    @Test("recordDisconnect transitions to connecting")
    func disconnectTransition() {
        let manager = ServerManager()
        manager.recordHeartbeat()
        #expect(manager.connectionState == .connected)

        manager.recordDisconnect()
        #expect(manager.connectionState == .connecting)
    }

    @Test("markOffline transitions to offline")
    func markOfflineTransition() {
        let manager = ServerManager()
        manager.recordHeartbeat()
        manager.markOffline()
        #expect(manager.connectionState == .offline)
        #expect(manager.isRunning == false)
    }

    @Test("markConnecting transitions to connecting")
    func markConnectingTransition() {
        let manager = ServerManager()
        manager.markConnecting()
        #expect(manager.connectionState == .connecting)
    }

    @Test("Full lifecycle: offline → heartbeat → connected → disconnect → connecting → markOffline → offline")
    func fullLifecycle() {
        let manager = ServerManager()
        #expect(manager.connectionState == .offline)

        manager.recordHeartbeat()
        #expect(manager.connectionState == .connected)

        manager.recordDisconnect()
        #expect(manager.connectionState == .connecting)

        manager.markOffline()
        #expect(manager.connectionState == .offline)
    }

    @Test("isRunning setter changes connectionState")
    func isRunningSetterWorks() {
        let manager = ServerManager()
        manager.isRunning = true
        #expect(manager.connectionState == .connected)

        manager.isRunning = false
        #expect(manager.connectionState == .offline)
    }

    @Test("recordHeartbeat clears lastError")
    func heartbeatClearsError() {
        let manager = ServerManager()
        manager.lastError = "Some error"
        manager.recordHeartbeat()
        #expect(manager.lastError == nil)
    }

    @Test("ConnectionState dot colors")
    func dotColors() {
        #expect(ConnectionState.connected.dotColor == "green")
        #expect(ConnectionState.connecting.dotColor == "yellow")
        #expect(ConnectionState.offline.dotColor == "red")
    }

    @Test("ConnectionState raw values")
    func rawValues() {
        #expect(ConnectionState.connected.rawValue == "Connected")
        #expect(ConnectionState.connecting.rawValue == "Connecting...")
        #expect(ConnectionState.offline.rawValue == "Offline")
    }
}
