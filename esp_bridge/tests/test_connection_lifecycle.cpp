#include "ble_stubs.h"
#include "firmware_under_test.inc"

void reset() {
  clients.clear(); sim = Simulation{};
  config = BridgeConfig{}; config.count = 1;
  std::strcpy(config.blinds[0].name, "Test motor");
  std::strcpy(config.blinds[0].mac, "motor0");
  config.blinds[0].assigned = true;
  for (auto& state : states) state = MotorState{};
  for (auto& slot : motorConnections) slot = MotorConnection{};
  for (auto& minutes : pollMinutes) minutes = 0;
  activeBlind = -1; positionCommandCount = 0; scanRequested = false;
}
void assertReleased() {
  for (const auto& slot : motorConnections) {
    assert(!slot.client || !slot.client->isConnected());
    assert(!slot.authenticated && !slot.positionSubscribed && !slot.disconnectPending);
  }
  for (int i = 0; i < config.count; ++i) assert(!states[i].bleConnected);
}
void runWorker(uint32_t duration) {
  sim.stopAfter = sim.elapsed + duration;
  try { bleWorker(nullptr); } catch (const StopWorker&) {}
  sim.stopAfter = UINT64_MAX;
}
int main() {
  int cases = 0;
  bool written;

  reset();
  assert(pollMotor(0, nullptr, written));
  assert(!written && sim.writes == 0 && states[0].batteryPercent == 19);
  assert(states[0].lastSensorsMs && states[0].bleDisconnectCount == 1);
  assertReleased(); ++cases;

  // Zero is real sensor data; unavailable sensor data remains unknown.
  reset(); sim.battery = 0;
  assert(pollMotor(0, nullptr, written));
  assert(states[0].batteryPercent == 0 && states[0].haveSensors);
  assertReleased(); ++cases;
  reset(); sim.sensorsOk = false;
  assert(pollMotor(0, nullptr, written));
  assert(states[0].batteryPercent == -1 && !states[0].haveSensors);
  assert(states[0].lastSensorsMs == 0);
  assertReleased(); ++cases;

  reset();
  PositionCommand command = {1, millis(), millis(), 0, 70, 0};
  assert(!pollMotor(0, &command, written)); // A write is not position confirmation.
  assert(written && sim.writes == 1 && sim.connects == 1);
  assertReleased();
  assert(pollMotor(0, nullptr, written));
  assert(sim.connects == 2 && sim.authWrites == 2 && sim.writes == 1 && states[0].position == 70);
  assertReleased(); ++cases;

  // A position-only confirmation cannot make an old battery sample look fresh.
  reset(); assert(pollMotor(0, nullptr, written));
  const uint32_t sensorTime = states[0].lastSensorsMs;
  states[0].positionCheckPending = true; sim.battery = 90;
  assert(pollMotor(0, nullptr, written));
  assert(states[0].batteryPercent == 19 && states[0].lastSensorsMs == sensorTime);
  assertReleased(); ++cases;

  // Every early failure path releases its session.
  for (int failure = 0; failure < 5; ++failure) {
    reset();
    if (failure == 0) sim.connectOk = false;
    if (failure == 1) sim.serviceOk = false;
    if (failure == 2) sim.authOk = false;
    if (failure == 3) sim.writeOk = false;
    if (failure == 4) sim.readOk = false;
    const PositionCommand* request = failure == 3 ? &command : nullptr;
    assert(!pollMotor(0, request, written));
    assert(!written);
    assertReleased(); ++cases;
  }

  // An authenticated connection with missing position data must be torn down.
  reset(); assert(pollMotor(0, nullptr, written));
  sim.readOk = false;
  assert(!pollMotor(0, nullptr, written));
  assert(sim.disconnects == 2);
  assertReleased(); ++cases;

  // A failed disconnect cannot be reused, and the idle worker keeps retrying.
  reset(); sim.disconnectOk = false;
  assert(pollMotor(0, nullptr, written));
  MotorConnection* slot = nullptr;
  for (auto& candidate : motorConnections) if (candidate.client) slot = &candidate;
  assert(slot && slot->disconnectPending && !slot->authenticated && !slot->positionSubscribed);
  assert(connectionForMotor(0) == nullptr);
  sim.disconnectOk = true;
  releaseIdleMotorConnections();
  assertReleased(); ++cases;

  // Disconnect timeout works across the 32-bit clock wrap.
  reset(); sim.now = 0xfffffff0U; sim.disconnectOk = false;
  assert(pollMotor(0, nullptr, written));
  assert(sim.elapsed < 12000);
  sim.disconnectOk = true; releaseIdleMotorConnections();
  assertReleased(); ++cases;

  // Polling Off obtains one startup read, then performs no further motor work.
  reset(); runWorker(180000);
  assert(states[0].pollCount == 1 && sim.connects == 1 && sim.writes == 0);
  assertReleased(); ++cases;

  // Cached matching position cannot end confirmation after a failed fresh read.
  reset(); states[0].position = 70; states[0].lastPositionMs = 1;
  states[0].lastCommandPosition = 70; states[0].lastCommandDeliveredMs = millis();
  states[0].forcePoll = true; states[0].positionCheckPending = true;
  sim.readOk = false;
  runWorker(60000);
  assert(states[0].pollCount > 1); // No premature success from the old matching cache.
  assert(!states[0].positionCheckPending && sim.writes == 0);
  assertReleased(); ++cases;

  // Existing FIFO movement and fresh readback still work with cold connections.
  reset(); states[0].lastPositionMs = 1;
  positionCommands[0] = {1, millis(), millis(), 0, 0, 0};
  positionCommands[1] = {2, millis(), millis(), 0, 70, 0};
  positionCommandCount = 2; updateQueuedPositionLocked(0);
  runWorker(60000);
  assert(sim.targets == std::vector<int>({0, 70}));
  assert(positionCommandCount == 0 && states[0].lastCommandId == 2);
  assert(states[0].lastCommandWriteOk && !states[0].positionCheckPending);
  assert(sim.connects == 3); // Two writes, one confirmation read, then idle.
  assertReleased(); ++cases;

  // Five-motor group crosses the three-object pool without losing any target.
  reset(); config.count = 5; sim.position = 100;
  for (int i = 0; i < config.count; ++i) {
    std::snprintf(config.blinds[i].name, sizeof(config.blinds[i].name), "Motor %d", i);
    std::snprintf(config.blinds[i].mac, sizeof(config.blinds[i].mac), "motor%d", i);
    config.blinds[i].assigned = true; states[i].lastPositionMs = 1;
    positionCommands[i] = {static_cast<uint32_t>(i + 1), millis(), millis(),
                          static_cast<uint8_t>(i), 100, 0};
  }
  positionCommandCount = 5;
  for (int i = 0; i < config.count; ++i) updateQueuedPositionLocked(i);
  runWorker(60000);
  assert(positionCommandCount == 0 && sim.targets == std::vector<int>({100, 100, 100, 100, 100}));
  for (int i = 0; i < config.count; ++i) {
    assert(states[i].lastCommandId == static_cast<uint32_t>(i + 1));
    assert(states[i].position == 100 && states[i].lastCommandWriteOk);
    assert(!states[i].positionCheckPending && states[i].pollCount == 2);
  }
  assert(sim.connects == 10 && sim.authWrites == 10);
  assertReleased(); ++cases;

  // Fresh battery/status without position ends automatic startup work when Off.
  reset(); sim.readOk = false;
  runWorker(600000);
  assert(states[0].position == -1 && states[0].batteryPercent == 19);
  assert(states[0].pollCount == 1 && sim.connects == 1 && !states[0].lastPollOk);
  assertReleased(); ++cases;

  // A completely silent motor gets a finite recovery budget, then stays idle.
  reset(); sim.readOk = sim.sensorsOk = sim.statusOk = false;
  runWorker(600000);
  assert(states[0].lastSampleMs == 0 && states[0].position == -1);
  assert(states[0].pollCount == 3 && sim.connects == 3 && sim.writes == 0);
  assertReleased(); ++cases;

  // Explicit Refresh still works after the automatic startup budget is exhausted.
  sim.readOk = sim.sensorsOk = sim.statusOk = true;
  states[0].forcePoll = true; states[0].nextPollMs = millis();
  runWorker(60000);
  assert(states[0].pollCount == 4 && sim.connects == 4 && states[0].position == 70);
  assertReleased(); ++cases;

  // Movement and its confirmation also remain independent of that budget.
  reset(); sim.readOk = sim.sensorsOk = sim.statusOk = false;
  runWorker(600000);
  sim.readOk = sim.sensorsOk = sim.statusOk = true;
  positionCommands[0] = {1, millis(), millis(), 0, 70, 0};
  positionCommandCount = 1; updateQueuedPositionLocked(0);
  runWorker(60000);
  assert(sim.writes == 1 && states[0].lastCommandWriteOk && states[0].position == 70);
  assert(states[0].pollCount == 5 && sim.connects == 5 && !states[0].positionCheckPending);
  assertReleased(); ++cases;

  std::printf("PASS: %d firmware lifecycle scenarios\n", cases);
}
