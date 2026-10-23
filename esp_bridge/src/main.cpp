#include <Arduino.h>
#include <ESPmDNS.h>
#include <WebServer.h>
#include <WiFi.h>
#include <NimBLEDevice.h>
#include <Preferences.h>
#include <mbedtls/base64.h>
#include <algorithm>
#include "bridge_storage.h"

namespace {
constexpr char kService[] = "00001400-1212-efde-1600-785feabcd123";
constexpr char kKey[] = "00001409-1212-efde-1600-785feabcd123";
constexpr char kPosition[] = "00001403-1212-efde-1600-785feabcd123";
constexpr char kStatus[] = "00001402-1212-efde-1600-785feabcd123";
constexpr char kSensors[] = "00001651-1212-efde-1600-785feabcd123";
constexpr uint32_t kSolarCharging = 0x00040000;
constexpr uint32_t kUsbCharging = 0x00080000;
constexpr uint8_t kPollChoices[] = {0, 1, 2, 5, 10, 15, 30};
constexpr uint32_t kMinimumStaleMs = 300000;
constexpr uint16_t kHttpPort = 80;
constexpr uint16_t kCompatibilityPort = 8080;
constexpr char kFirmwareVersion[] = "multi-bridge-2026-10-17-31-bounded-startup";
constexpr uint32_t kWifiRetryIntervalMs = 30000;
constexpr char kSetupWifiPassword[] = "MySmartBlinds";
constexpr size_t kMaxPositionCommands = 32;
constexpr uint8_t kMaxCommandAttempts = 4;
constexpr uint8_t kMaxStartupReadFailures = 3;

struct MotorState {
  int position = -1;
  int batteryPercent = -1;
  int batteryVoltageRaw = 0;
  int batteryChargeRaw = 0;
  int solarPanelVoltageRaw = 0;
  int interiorTemperatureRaw = 0;
  int batteryTemperatureRaw = 0;
  int lightRaw = 0;
  bool haveStatus = false;
  bool haveSensors = false;
  bool solarCharging = false;
  bool usbCharging = false;
  uint32_t lastSampleMs = 0;
  uint32_t lastPositionMs = 0;
  uint32_t lastSensorsMs = 0;
  bool bleConnected = false;
  uint32_t bleConnectedAtMs = 0;
  uint32_t bleDisconnectCount = 0;
  uint32_t lastPollFinishedMs = 0;
  uint32_t pollCount = 0;
  bool lastPollOk = false;
  uint32_t nextPollMs = 0;
  bool forcePoll = false;
  uint8_t failures = 0;
  int queuedPosition = -1;
  uint32_t queuedAtMs = 0;
  int lastCommandPosition = -1;
  bool lastCommandWriteOk = false;
  uint32_t lastCommandLatencyMs = 0;
  uint32_t lastCommandId = 0;
  uint8_t lastCommandAttempts = 0;
  bool positionCheckPending = false;
  uint32_t lastCommandDeliveredMs = 0;
  uint8_t bleStage = 0;
  uint32_t bleStageStartedMs = 0;
  uint32_t bleStageTimesMs[8] = {};
};

struct PositionCommand {
  uint32_t id;
  uint32_t queuedAtMs;
  uint32_t nextAttemptMs;
  uint8_t blind;
  uint8_t position;
  uint8_t attempts;
};

struct MotorConnection {
  NimBLEClient* client = nullptr;
  int blind = -1;
  uint32_t lastUsedMs = 0;
  bool authenticated = false;
  bool positionSubscribed = false;
  bool disconnectPending = false;
};
// The BLE worker reuses allocated clients, but releases the radio connection
// after EVERY transaction. Polling Off must leave no motor link held open.
MotorConnection motorConnections[3];

BridgeConfig config, staged;
uint8_t pollMinutes[kMaxBlinds] = {};
MotorState states[kMaxBlinds];
PositionCommand positionCommands[kMaxPositionCommands];
size_t positionCommandCount = 0;
uint32_t nextPositionCommandId = 1;
SemaphoreHandle_t stateLock;
WebServer primaryServer(kHttpPort);
WebServer compatibilityServer(kCompatibilityPort);
WebServer* requestServer = &primaryServer;
int activeBlind = -1;
bool scanRequested = false;
int scanRssi[kMaxBlinds];
uint32_t scanAtMs = 0;
uint32_t scanCount = 0;
uint32_t setupNonce = 0;
bool uploadActive = false;
int uploadExpected = 0;
bool restartRequested = false;
uint32_t restartDelayMs = 300;
bool setupApMode = false;
bool networkPending = false;
uint32_t bootPressStartedMs = 0;
bool bootPressHandled = false;
String savedBridgeId;

String bridgeId() {
  return savedBridgeId.isEmpty() ? WiFi.macAddress() : savedBridgeId;
}

String bridgeSuffix() {
  String suffix = bridgeId();
  suffix.replace(":", "");
  suffix = suffix.substring(suffix.length() - 4);
  suffix.toUpperCase();
  return suffix;
}

String bridgeHostname() {
  String hostname = "msb-" + bridgeSuffix();
  hostname.toLowerCase();
  return hostname;
}

String setupSsid() {
  return "MSB-Setup-" + bridgeSuffix();
}

void startSetupAp() {
  if (setupApMode) return;
  WiFi.mode(WIFI_AP_STA);
  WiFi.softAPConfig(IPAddress(192, 168, 4, 1), IPAddress(192, 168, 4, 1),
                    IPAddress(255, 255, 255, 0));
  const String apName = setupSsid();
  if (!WiFi.softAP(apName.c_str(), kSetupWifiPassword)) {
    Serial.println("SETUP_AP_FAILED");
    return;
  }
  setupApMode = true;
  Serial.print("Setup Wi-Fi: ");
  Serial.println(apName);
  Serial.print("Setup page: http://");
  Serial.print(WiFi.softAPIP());
  Serial.println("/setup");
}

bool copyString(char* target, size_t capacity, const String& value) {
  if (value.length() >= capacity) return false;
  memset(target, 0, capacity);
  memcpy(target, value.c_str(), value.length());
  return true;
}
bool decodeBase64(const String& source, String& output) {
  unsigned char buffer[128] = {};
  size_t length = 0;
  if (source.length() > 170 ||
      mbedtls_base64_decode(buffer, sizeof(buffer) - 1, &length,
        reinterpret_cast<const unsigned char*>(source.c_str()), source.length()) != 0) return false;
  buffer[length] = 0;
  output = String(reinterpret_cast<const char*>(buffer));
  return output.length() == length;
}
bool split(const String& line, String* parts, int count) {
  size_t start = 0;
  for (int i = 0; i < count - 1; ++i) {
    const int divider = line.indexOf('|', start);
    if (divider < 0) return false;
    parts[i] = line.substring(start, divider);
    start = divider + 1;
  }
  parts[count - 1] = line.substring(start);
  return parts[count - 1].indexOf('|') < 0;
}
bool validMac(const String& mac) {
  if (mac.length() != 17) return false;
  for (int i = 0; i < 17; ++i) {
    if ((i + 1) % 3 == 0) {
      if (mac[i] != ':') return false;
    } else if (!isxdigit(mac[i])) return false;
  }
  return true;
}
bool validId(const String& id) {
  if (id == "office-window") return true;
  if (id.length() != 36) return false;
  for (int i = 0; i < 36; ++i) {
    if (i == 8 || i == 13 || i == 18 || i == 23) {
      if (id[i] != '-') return false;
    } else if (!isxdigit(id[i])) return false;
  }
  return true;
}
bool parseKey(const String& value, uint8_t* key) {
  if (value.length() != 12) return false;
  for (int i = 0; i < 6; ++i) {
    const String byte = value.substring(i * 2, i * 2 + 2);
    if (!isxdigit(byte[0]) || !isxdigit(byte[1])) return false;
    key[i] = static_cast<uint8_t>(strtoul(byte.c_str(), nullptr, 16));
  }
  return true;
}
bool loadConfig() {
  Preferences prefs;
  if (!prefs.begin("msbbridge", true)) return false;
  bool loaded = false;
  if (prefs.getBytesLength("config") == sizeof(config)) {
    prefs.getBytes("config", &config, sizeof(config));
    loaded = config.magic == kConfigMagic && config.count <= kMaxBlinds &&
      config.ssid[0] != 0 &&
      strnlen(config.token, sizeof(config.token)) >= 24 &&
      config.ssid[sizeof(config.ssid) - 1] == 0 &&
      config.password[sizeof(config.password) - 1] == 0 &&
      config.token[sizeof(config.token) - 1] == 0;
  }
  if (!loaded) {
    // Keep the Office Window pilot usable until it is explicitly reprovisioned.
    const String ssid = prefs.getString("ssid", "");
    const String password = prefs.getString("password", "");
    const String token = prefs.getString("token", "");
    const String mac = prefs.getString("mac", "");
    uint8_t key[6] = {};
    if (!ssid.isEmpty() && !password.isEmpty() && token.length() >= 24 &&
        validMac(mac) && prefs.getBytesLength("key") == sizeof(key)) {
      prefs.getBytes("key", key, sizeof(key));
      config = BridgeConfig();
      copyString(config.ssid, sizeof(config.ssid), ssid);
      copyString(config.password, sizeof(config.password), password);
      copyString(config.token, sizeof(config.token), token);
      config.count = 1;
      copyString(config.blinds[0].id, sizeof(config.blinds[0].id), "office-window");
      copyString(config.blinds[0].name, sizeof(config.blinds[0].name), "Office Window");
      copyString(config.blinds[0].mac, sizeof(config.blinds[0].mac), mac);
      memcpy(config.blinds[0].key, key, sizeof(key));
      config.blinds[0].assigned = true;
      loaded = true;
    }
  }
  prefs.end();
  return loaded;
}
bool saveConfig(const BridgeConfig& value) {
  Preferences prefs;
  const bool opened = prefs.begin("msbbridge", false);
  const bool saved = opened && prefs.putBytes("config", &value, sizeof(value)) == sizeof(value);
  if (opened) prefs.end();
  return saved;
}

void loadBridgeIdentity() {
  Preferences prefs;
  if (!prefs.begin("msbbridge", true)) return;
  const String identity = prefs.getString("bridgeid", "");
  prefs.end();
  if (validMac(identity)) {
    savedBridgeId = identity;
    savedBridgeId.toUpperCase();
  }
}

void waitForMigrationActivation() {
  Preferences prefs;
  if (!prefs.begin("msbbridge", true)) return;
  const bool standby = prefs.getBool("standby", false);
  prefs.end();
  if (!standby) return;
  // Do not start either radio while the old bridge still owns this identity.
  Serial.setTimeout(1000);
  Serial.println("MIGRATION_STANDBY");
  while (true) {
    if (!Serial.available()) {
      delay(20);
      continue;
    }
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line == "MIGRATION_STATUS") {
      Serial.println("MIGRATION_STANDBY");
      Serial.println("BRIDGE_ID " + bridgeId());
      for (int i = 0; i < config.count; ++i)
        if (config.blinds[i].assigned) Serial.println("ASSIGNED " + String(config.blinds[i].name));
      Serial.println("MIGRATION_STATUS_DONE");
    } else if (line == "ACTIVATE_BRIDGE") {
      if (!prefs.begin("msbbridge", false)) {
        Serial.println("ACTIVATION_FAILED");
        continue;
      }
      const bool removed = prefs.remove("standby");
      prefs.end();
      if (!removed) {
        Serial.println("ACTIVATION_FAILED");
        continue;
      }
      Serial.println("BRIDGE_ACTIVATING");
      Serial.flush();
      delay(200);
      ESP.restart();
    }
  }
}
bool validPollMinutes(uint8_t minutes) {
  for (uint8_t choice : kPollChoices)
    if (choice == minutes) return true;
  return false;
}
void loadPolling() {
  for (int i = 0; i < kMaxBlinds; ++i) pollMinutes[i] = kDefaultPollMinutes;
  Preferences prefs;
  if (!prefs.begin("msbbridge", true)) return;
  PollSettings saved;
  const bool haveSaved = prefs.getBytesLength("polling") == sizeof(saved) &&
    prefs.getBytes("polling", &saved, sizeof(saved)) == sizeof(saved) &&
    saved.magic == kPollSettingsMagic && saved.count <= kMaxBlinds;
  prefs.end();
  if (!haveSaved) return;
  for (int i = 0; i < config.count; ++i) {
    for (int j = 0; j < saved.count; ++j) {
      if (saved.blinds[j].id[sizeof(saved.blinds[j].id) - 1] == 0 &&
          String(saved.blinds[j].id).equalsIgnoreCase(config.blinds[i].id) &&
          validPollMinutes(saved.blinds[j].minutes)) {
        pollMinutes[i] = saved.blinds[j].minutes;
        break;
      }
    }
  }
}
bool savePolling(int index, uint8_t minutes) {
  PollSettings saved;
  saved.count = config.count;
  for (int i = 0; i < config.count; ++i) {
    if (!copyString(saved.blinds[i].id, sizeof(saved.blinds[i].id), config.blinds[i].id))
      return false;
    saved.blinds[i].minutes = i == index ? minutes : pollMinutes[i];
  }
  Preferences prefs;
  const bool opened = prefs.begin("msbbridge", false);
  const bool stored = opened &&
    prefs.putBytes("polling", &saved, sizeof(saved)) == sizeof(saved);
  if (opened) prefs.end();
  if (!stored) return false;
  xSemaphoreTake(stateLock, portMAX_DELAY);
  pollMinutes[index] = minutes;
  MotorState& state = states[index];
  if (state.queuedPosition < 0 && state.lastPollFinishedMs) {
    const uint32_t intervalMs = static_cast<uint32_t>(minutes) * 60000UL;
    const uint32_t backoffMs = std::min<uint32_t>(30000UL * state.failures, intervalMs);
    state.nextPollMs = state.lastPollFinishedMs +
      (state.failures ? backoffMs : intervalMs);
  }
  xSemaphoreGive(stateLock);
  return true;
}
void listenForProvisioning(bool haveConfig, uint32_t waitMs) {
  Serial.setTimeout(3000);
  Serial.println("CONFIG_READY");
  bool staging = false;
  int expected = 0;
  const uint32_t started = millis();
  while (millis() - started < waitMs || staging) {
    if (!Serial.available()) {
      delay(20);
      continue;
    }
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.startsWith("BEGIN_KEEP|") && haveConfig) {
      const String count = line.substring(strlen("BEGIN_KEEP|"));
      if (count.isEmpty() || count.length() > 2 || count.toInt() < 1 ||
          count.toInt() > kMaxBlinds) {
        Serial.println("CONFIG_ERROR");
        staging = false;
        continue;
      }
      staged = config;
      staged.count = 0;
      memset(staged.blinds, 0, sizeof(staged.blinds));
      expected = count.toInt();
      staging = true;
      Serial.println("CONFIG_BEGIN_OK");
    } else if (line.startsWith("NETWORK|")) {
      String parts[4];
      String ssid, password;
      if (!split(line, parts, 4) || parts[0] != "NETWORK" ||
          !decodeBase64(parts[1], ssid) || !decodeBase64(parts[2], password) ||
          ssid.isEmpty() || password.isEmpty() || ssid.length() > 32 ||
          password.length() > 63 || parts[3].length() < 24 || parts[3].length() > 128) {
        Serial.println("CONFIG_ERROR");
        continue;
      }
      BridgeConfig next;
      copyString(next.ssid, sizeof(next.ssid), ssid);
      copyString(next.password, sizeof(next.password), password);
      copyString(next.token, sizeof(next.token), parts[3]);
      if (!saveConfig(next)) {
        Serial.println("CONFIG_ERROR");
        continue;
      }
      config = next;
      Serial.println("PROVISIONED");
      return;
    } else if (line.startsWith("BEGIN|")) {
      String parts[5];
      String ssid, password;
      if (!split(line, parts, 5) || parts[0] != "BEGIN" ||
          !decodeBase64(parts[1], ssid) || !decodeBase64(parts[2], password) ||
          ssid.isEmpty() || password.isEmpty() || ssid.length() > 32 ||
          password.length() > 63 || parts[3].length() < 24 ||
          parts[3].length() > 128 || parts[4].length() > 2 ||
          parts[4].toInt() < 1 || parts[4].toInt() > kMaxBlinds) {
        Serial.println("CONFIG_ERROR");
        staging = false;
        continue;
      }
      staged = BridgeConfig();
      copyString(staged.ssid, sizeof(staged.ssid), ssid);
      copyString(staged.password, sizeof(staged.password), password);
      copyString(staged.token, sizeof(staged.token), parts[3]);
      expected = parts[4].toInt();
      staging = true;
      Serial.println("CONFIG_BEGIN_OK");
    } else if (staging && line.startsWith("BLIND|")) {
      String parts[6];
      String name;
      if (!split(line, parts, 6) || parts[0] != "BLIND" ||
          staged.count >= expected || !validId(parts[1]) ||
          !decodeBase64(parts[2], name) || name.isEmpty() ||
          name.length() > 48 || !validMac(parts[3]) ||
          (parts[5] != "0" && parts[5] != "1")) {
        Serial.println("CONFIG_ERROR");
        staging = false;
        continue;
      }
      for (int i = 0; i < staged.count; ++i) {
        if (parts[1].equalsIgnoreCase(staged.blinds[i].id) ||
            parts[3].equalsIgnoreCase(staged.blinds[i].mac)) {
          Serial.println("CONFIG_ERROR");
          staging = false;
          break;
        }
      }
      if (!staging) continue;
      BlindConfig& blind = staged.blinds[staged.count];
      if (!parseKey(parts[4], blind.key) ||
          !copyString(blind.id, sizeof(blind.id), parts[1]) ||
          !copyString(blind.name, sizeof(blind.name), name) ||
          !copyString(blind.mac, sizeof(blind.mac), parts[3])) {
        Serial.println("CONFIG_ERROR");
        staging = false;
        continue;
      }
      blind.assigned = parts[5] == "1";
      ++staged.count;
      Serial.println("CONFIG_BLIND_OK");
    } else if (staging && line == "COMMIT" && staged.count == expected) {
      if (!saveConfig(staged)) {
        Serial.println("CONFIG_ERROR");
        staging = false;
        continue;
      }
      config = staged;
      Serial.println("PROVISIONED");
      return;
    } else {
      Serial.println("CONFIG_ERROR");
      staging = false;
    }
  }
}
int16_t le16(const uint8_t* p) {
  return static_cast<int16_t>(static_cast<uint16_t>(p[0]) |
                              (static_cast<uint16_t>(p[1]) << 8));
}
void ingestForBlind(int index, const NimBLEUUID& uuid, const uint8_t* data, size_t length) {
  if (!data || !length || index < 0 || index >= config.count) return;
  const bool positionPacket = uuid == NimBLEUUID(kPosition) && data[0] <= 200;
  xSemaphoreTake(stateLock, portMAX_DELAY);
  MotorState& state = states[index];
  if (uuid == NimBLEUUID(kPosition) && length >= 1 && data[0] <= 200) {
    state.position = data[0];
    state.lastPositionMs = millis();
    state.lastSampleMs = millis();
    if (state.positionCheckPending && state.lastCommandWriteOk &&
        std::abs(state.position - state.lastCommandPosition) <= 1) {
      state.positionCheckPending = false;
      state.forcePoll = false;
    }
  } else if (uuid == NimBLEUUID(kStatus) && length >= 4) {
    const uint32_t flags = static_cast<uint32_t>(data[0]) |
      (static_cast<uint32_t>(data[1]) << 8) |
      (static_cast<uint32_t>(data[2]) << 16) |
      (static_cast<uint32_t>(data[3]) << 24);
    if (flags == UINT32_MAX) {
      xSemaphoreGive(stateLock);
      return;
    }
    state.solarCharging = (flags & kSolarCharging) != 0;
    state.usbCharging = (flags & kUsbCharging) != 0;
    state.haveStatus = true;
    state.lastSampleMs = millis();
  } else if (uuid == NimBLEUUID(kSensors) && length >= 14 && data[0] <= 100) {
    state.batteryPercent = data[0];
    state.batteryVoltageRaw = le16(data + 2);
    state.batteryChargeRaw = le16(data + 4);
    state.solarPanelVoltageRaw = le16(data + 6);
    state.interiorTemperatureRaw = le16(data + 8);
    state.batteryTemperatureRaw = le16(data + 10);
    state.lightRaw = le16(data + 12);
    state.haveSensors = true;
    state.lastSensorsMs = millis();
    state.lastSampleMs = millis();
  }
  xSemaphoreGive(stateLock);
  if (positionPacket) Serial.printf("BLE position received blind=%s position=%u atMs=%lu\n",
                                   config.blinds[index].name, data[0],
                                   static_cast<unsigned long>(millis()));
}
void ingest(const NimBLEUUID& uuid, const uint8_t* data, size_t length) {
  ingestForBlind(activeBlind, uuid, data, length);
}
void onIndication(NimBLERemoteCharacteristic* characteristic,
                  uint8_t* data, size_t length, bool) {
  // activeBlind identifies the worker's current request, not the motor
  // sending an asynchronous notification. Route by that motor's connection.
  const String address(characteristic->getClient()->getPeerAddress().toString().c_str());
  for (int i = 0; i < config.count; ++i) {
    if (address.equalsIgnoreCase(config.blinds[i].mac)) {
      ingestForBlind(i, characteristic->getUUID(), data, length);
      return;
    }
  }
}
class MotorClientCallbacks : public NimBLEClientCallbacks {
  void updateConnection(NimBLEClient* client, bool connected) {
    const String address(client->getPeerAddress().toString().c_str());
    for (int i = 0; i < config.count; ++i) {
      if (!address.equalsIgnoreCase(config.blinds[i].mac)) continue;
      xSemaphoreTake(stateLock, portMAX_DELAY);
      MotorState& state = states[i];
      state.bleConnected = connected;
      if (connected) state.bleConnectedAtMs = millis();
      else ++state.bleDisconnectCount;
      xSemaphoreGive(stateLock);
      return;
    }
  }
  void onConnect(NimBLEClient* client) override { updateConnection(client, true); }
  void onDisconnect(NimBLEClient* client, int) override { updateConnection(client, false); }
};
MotorClientCallbacks motorClientCallbacks;
void subscribeAndRead(NimBLERemoteService* service, const char* uuid) {
  NimBLERemoteCharacteristic* characteristic = service->getCharacteristic(uuid);
  if (!characteristic) return;
  if (characteristic->canIndicate())
    characteristic->subscribe(false, onIndication);
  else if (characteristic->canNotify())
    characteristic->subscribe(true, onIndication);
  if (characteristic->canRead()) {
    NimBLEAttValue value = characteristic->readValue();
    if (value.size()) ingest(characteristic->getUUID(), value.data(), value.size());
  }
}
void updateQueuedPositionLocked(int index) {
  MotorState& state = states[index];
  state.queuedPosition = -1;
  for (size_t i = 0; i < positionCommandCount; ++i) {
    if (positionCommands[i].blind == index) {
      state.queuedPosition = positionCommands[i].position;
      state.queuedAtMs = positionCommands[i].queuedAtMs;
    }
  }
}

void removePositionCommandLocked(size_t slot) {
  const int index = positionCommands[slot].blind;
  for (size_t i = slot + 1; i < positionCommandCount; ++i)
    positionCommands[i - 1] = positionCommands[i];
  --positionCommandCount;
  updateQueuedPositionLocked(index);
}

const char* bleStageName(uint8_t stage) {
  static const char* names[] = {"idle", "connect", "scan", "discover", "authenticate",
                                "write", "read", "disconnect"};
  return names[stage < 8 ? stage : 0];
}
void markBleStage(int index, uint8_t stage) {
  const uint32_t now = millis();
  xSemaphoreTake(stateLock, portMAX_DELAY);
  MotorState& state = states[index];
  if (state.bleStage) state.bleStageTimesMs[state.bleStage] += now - state.bleStageStartedMs;
  state.bleStage = stage;
  state.bleStageStartedMs = now;
  xSemaphoreGive(stateLock);
  Serial.printf("BLE phase blind=%s stage=%s atMs=%lu\n", config.blinds[index].name,
                bleStageName(stage), static_cast<unsigned long>(now));
}
bool releaseMotorConnection(MotorConnection& slot) {
  // Invalidate session state even if the controller takes time to disconnect.
  // Never reuse a session once release has begun.
  slot.authenticated = false;
  slot.positionSubscribed = false;
  if (!slot.client) return true;
  slot.disconnectPending = true;
  if (slot.client->isConnected()) {
    slot.client->disconnect();
    const uint32_t started = millis();
    while (slot.client->isConnected() && millis() - started < 2500UL)
      vTaskDelay(pdMS_TO_TICKS(10));
  }
  if (slot.client->isConnected()) {
    Serial.printf("BLE disconnect pending blind=%s; session will not be reused\n",
                  config.blinds[slot.blind].name);
    return false;
  }
  slot.disconnectPending = false;
  return true;
}
void releaseIdleMotorConnections() {
  // Only the serial worker calls this, between transactions. Retry a failed
  // disconnect even when polling is Off and there are no new commands.
  for (auto& slot : motorConnections) {
    if (slot.client && (slot.disconnectPending || slot.client->isConnected()))
      releaseMotorConnection(slot);
  }
}
MotorConnection* connectionForMotor(int index) {
  MotorConnection* chosen = nullptr;
  for (auto& slot : motorConnections) {
    if (slot.blind == index && slot.client) {
      if (slot.disconnectPending && !releaseMotorConnection(slot)) return nullptr;
      slot.lastUsedMs = millis();
      if (!slot.client->isConnected()) {
        slot.authenticated = false;
        slot.positionSubscribed = false;
      }
      return &slot;
    }
    if (!chosen || !slot.client || (chosen->client &&
        static_cast<int32_t>(slot.lastUsedMs - chosen->lastUsedMs) < 0)) chosen = &slot;
  }
  if (chosen->client) {
    Serial.printf("BLE connection released blind=%s reason=pool-capacity\n",
                  config.blinds[chosen->blind].name);
    // NimBLE deleteClient() defers deletion of a connected client until
    // the disconnect event. Reuse the object instead of allocating a new
    // one before the controller has released that connection slot.
    if (!releaseMotorConnection(*chosen)) return nullptr;
    chosen->client->deleteServices();
  }
  NimBLEClient* reusable = chosen->client;
  *chosen = MotorConnection{};
  chosen->blind = index;
  chosen->lastUsedMs = millis();
  chosen->client = reusable ? reusable : NimBLEDevice::createClient();
  if (chosen->client) {
    chosen->client->setSelfDelete(false, false);
    chosen->client->setClientCallbacks(&motorClientCallbacks, false);
  }
  return chosen;
}
bool pollMotor(int index, const PositionCommand* command, bool& writeOkay) {
  writeOkay = false;
  xSemaphoreTake(stateLock, portMAX_DELAY);
  const bool positionOnly = !command && states[index].positionCheckPending;
  std::fill(std::begin(states[index].bleStageTimesMs), std::end(states[index].bleStageTimesMs), 0);
  xSemaphoreGive(stateLock);
  MotorConnection* slot = connectionForMotor(index);
  NimBLEClient* client = slot ? slot->client : nullptr;
  if (!client) {
    Serial.printf("BLE %s: client unavailable\n", config.blinds[index].name);
    return false;
  }
  MotorConnection& connection = *slot;
  client->setConnectTimeout(10000);
  const BlindConfig& blind = config.blinds[index];
  bool okay = false;
  const char* failure = "position read timed out";
  activeBlind = index;
  xSemaphoreTake(stateLock, portMAX_DELAY);
  const uint32_t priorPositionMs = states[index].lastPositionMs;
  xSemaphoreGive(stateLock);
  do {
    // These motors have static random addresses. A direct attempt can catch
    // their connection window; a fresh scan is a fallback after a failed try.
    const NimBLEAddress address(std::string(blind.mac), BLE_ADDR_RANDOM);
    markBleStage(index, 1);
    if (!client->isConnected() && !client->connect(address, false, false, false)) {
      const int firstError = client->getLastError();
      NimBLEScan* scanner = NimBLEDevice::getScan();
      scanner->setActiveScan(true);
      markBleStage(index, 2);
      NimBLEScanResults results = scanner->getResults(8000);
      const NimBLEAdvertisedDevice* target = nullptr;
      for (int i = 0; i < results.getCount(); ++i) {
        const NimBLEAdvertisedDevice* candidate = results.getDevice(i);
        if (String(candidate->getAddress().toString().c_str()).equalsIgnoreCase(blind.mac)) {
          target = candidate;
          break;
        }
      }
      if (!target) {
        scanner->clearResults();
        failure = "motor not advertising after direct attempt";
        Serial.printf("BLE %s: direct connection error %d\n", blind.name, firstError);
        break;
      }
      const int rssi = target->getRSSI();
      const int addressType = target->getAddress().getType();
      markBleStage(index, 1);
      const bool connected = client->connect(target, false, false, false);
      const int connectError = client->getLastError();
      scanner->clearResults();
      if (!connected) {
        Serial.printf("BLE %s: connection errors %d/%d, type %d, RSSI %d dBm\n",
                      blind.name, firstError, connectError, addressType, rssi);
        failure = "connect failed";
        break;
      }
    }
    markBleStage(index, 3);
    NimBLERemoteService* service = client->getService(kService);
    if (!service) {
      failure = "motor service unavailable";
      break;
    }
    NimBLERemoteCharacteristic* key = service->getCharacteristic(kKey);
    NimBLERemoteCharacteristic* position = service->getCharacteristic(kPosition);
    if (!key || !position) {
      failure = "motor characteristic unavailable";
      break;
    }
    markBleStage(index, 4);
    if (!connection.authenticated && !key->writeValue(blind.key, sizeof(blind.key), true)) {
      failure = "motor authentication write failed";
      break;
    }
    connection.authenticated = true;
    if (!connection.positionSubscribed) {
      markBleStage(index, 6);
      if (position->canIndicate())
        connection.positionSubscribed = position->subscribe(false, onIndication);
      else if (position->canNotify())
        connection.positionSubscribed = position->subscribe(true, onIndication);
      if (!connection.positionSubscribed)
        Serial.printf("BLE %s: position subscription unavailable; readback fallback enabled\n", blind.name);
    }
    if (command) {
      const uint8_t value = command->position;
      markBleStage(index, 5);
      writeOkay = position->writeValue(&value, 1, true);
      if (!writeOkay) {
        failure = "position write failed";
        break;
      }
      // Movement delivery ends at the acknowledged write. Status work runs
      // after queued movement commands, and reads only position for this check.
      break;
    }
    markBleStage(index, 6);
    NimBLEAttValue currentPosition = position->readValue();
    if (currentPosition.size()) ingest(position->getUUID(), currentPosition.data(), currentPosition.size());
    if (!positionOnly) {
      subscribeAndRead(service, kStatus);
      subscribeAndRead(service, kSensors);
    }
    const uint32_t samplingStarted = millis();
    while (millis() - samplingStarted < 6000UL) {
      xSemaphoreTake(stateLock, portMAX_DELAY);
      const bool havePosition = states[index].lastPositionMs != priorPositionMs;
      const bool commandWaiting = positionCommandCount > 0;
      xSemaphoreGive(stateLock);
      if (commandWaiting || (havePosition && millis() - samplingStarted >= 1500)) break;
      vTaskDelay(pdMS_TO_TICKS(100));
    }
    xSemaphoreTake(stateLock, portMAX_DELAY);
    const bool havePosition = states[index].lastPositionMs != priorPositionMs;
    xSemaphoreGive(stateLock);
    if (!havePosition) {
      NimBLEAttValue readback = position->readValue();
      if (readback.size()) ingest(position->getUUID(), readback.data(), readback.size());
    }
    xSemaphoreTake(stateLock, portMAX_DELAY);
    okay = states[index].lastPositionMs != priorPositionMs;
    xSemaphoreGive(stateLock);
  } while (false);
  // Acknowledged position writes run on the motor. The existing queued
  // confirmation reads reconnect as needed; no idle link is kept for them.
  // Release failed reads too, so a stalled authenticated session is discarded.
  markBleStage(index, 7);
  releaseMotorConnection(connection);
  markBleStage(index, 0);
  activeBlind = -1;
  if (!okay && !writeOkay) Serial.printf("BLE %s: %s\n", blind.name, failure);
  return okay;
}
void scanKnownMotors() {
  NimBLEScan* scanner = NimBLEDevice::getScan();
  scanner->setActiveScan(true);
  NimBLEScanResults results = scanner->getResults(8000);
  xSemaphoreTake(stateLock, portMAX_DELAY);
  for (int i = 0; i < config.count; ++i) scanRssi[i] = -127;
  for (int i = 0; i < results.getCount(); ++i) {
    const NimBLEAdvertisedDevice* found = results.getDevice(i);
    const String address(found->getAddress().toString().c_str());
    for (int j = 0; j < config.count; ++j) {
      if (address.equalsIgnoreCase(config.blinds[j].mac))
        scanRssi[j] = found->getRSSI();
    }
  }
  scanAtMs = millis();
  ++scanCount;
  xSemaphoreGive(stateLock);
  for (int i = 0; i < config.count; ++i) {
    if (!config.blinds[i].assigned) continue;
    if (scanRssi[i] == -127)
      Serial.printf("BLE scan %s: not detected\n", config.blinds[i].name);
    else
      Serial.printf("BLE scan %s: %d dBm\n", config.blinds[i].name, scanRssi[i]);
  }
  scanner->clearResults();
}
void handleSerialDiagnostics() {
  static String command;
  while (Serial.available()) {
    const char c = static_cast<char>(Serial.read());
    if (c == '\r' || c == '\n') {
      if (command == "SCAN") {
        xSemaphoreTake(stateLock, portMAX_DELAY);
        scanRequested = true;
        xSemaphoreGive(stateLock);
        Serial.println("BLE placement scan queued");
      }
      command = "";
    } else if (command.length() < 16) {
      command += c;
    } else {
      command = "";
    }
  }
}
void bleWorker(void*) {
  for (;;) {
    releaseIdleMotorConnections();
    int selected = -1;
    PositionCommand command = {};
    bool haveCommand = false;
    bool scan = false;
    const uint32_t now = millis();
    xSemaphoreTake(stateLock, portMAX_DELAY);
    // The array is FIFO. A retry may yield to another blind, but a later
    // command for the same blind never overtakes its predecessor.
    for (size_t i = 0; i < positionCommandCount; ++i) {
      bool earlierForBlind = false;
      for (size_t j = 0; j < i; ++j) {
        if (positionCommands[j].blind == positionCommands[i].blind) {
          earlierForBlind = true;
          break;
        }
      }
      if (!earlierForBlind &&
          static_cast<int32_t>(now - positionCommands[i].nextAttemptMs) >= 0) {
        command = positionCommands[i];
        selected = command.blind;
        haveCommand = true;
        break;
      }
    }
    if (selected < 0) {
    for (int i = 0; i < config.count; ++i) {
      if (config.blinds[i].assigned &&
          states[i].forcePoll &&
          static_cast<int32_t>(now - states[i].nextPollMs) >= 0) {
        selected = i;
        states[i].forcePoll = false;
        break;
      }
    }
    }
    if (selected < 0 && scanRequested) {
      scanRequested = false;
      scan = true;
    }
    if (selected < 0 && !scan) {
      int32_t oldestDue = -1;
      for (int i = 0; i < config.count; ++i) {
        // Polling Off permits startup recovery, not unbounded background
        // traffic. Any valid motor sample ends startup work even if position
        // is unavailable. With no data, stop after three failed read attempts.
        // Explicit refreshes, movement and their bounded confirmation reads
        // are selected above independently of this automatic-read budget.
        if (states[i].queuedPosition >= 0 ||
            (pollMinutes[i] == 0 && (states[i].lastSampleMs ||
              states[i].failures >= kMaxStartupReadFailures))) continue;
        const int32_t dueBy = static_cast<int32_t>(now - states[i].nextPollMs);
        if (config.blinds[i].assigned && dueBy >= 0 && dueBy > oldestDue) {
          selected = i;
          oldestDue = dueBy;
        }
      }
    }
    xSemaphoreGive(stateLock);
    if (selected >= 0) {
      if (haveCommand) {
        Serial.printf("BLE start id=%lu blind=%s target=%u attempt=%u waitMs=%lu\n",
                      static_cast<unsigned long>(command.id), config.blinds[selected].name,
                      command.position, command.attempts + 1,
                      static_cast<unsigned long>(now - command.queuedAtMs));
      }
      bool writeOkay = false;
      const bool okay = pollMotor(selected, haveCommand ? &command : nullptr, writeOkay);
      bool confirmationTimedOut = false;
      xSemaphoreTake(stateLock, portMAX_DELAY);
      MotorState& state = states[selected];
      if (haveCommand) {
        state.lastCommandId = command.id;
        state.lastCommandPosition = command.position;
        state.lastCommandWriteOk = writeOkay;
        state.lastCommandAttempts = command.attempts + 1;
        state.lastCommandLatencyMs = millis() - command.queuedAtMs;
        for (size_t i = 0; i < positionCommandCount; ++i) {
          if (positionCommands[i].id != command.id) continue;
          if (writeOkay || positionCommands[i].attempts + 1 >= kMaxCommandAttempts) {
            removePositionCommandLocked(i);
          } else {
            PositionCommand& retry = positionCommands[i];
            ++retry.attempts;
            retry.nextAttemptMs = millis() +
              std::min<uint32_t>(5000UL << (retry.attempts - 1), 30000UL);
          }
          break;
        }
      }
      if (!haveCommand) {
        state.lastPollOk = okay;
        state.failures = okay ? 0 : std::min<int>(state.failures + 1, 6);
      }
      const uint32_t intervalMs = static_cast<uint32_t>(pollMinutes[selected]) * 60000UL;
      const uint32_t retryCapMs = intervalMs ? intervalMs : 120000UL;
      const uint32_t delayMs = okay ? intervalMs :
        std::min<uint32_t>(30000UL * state.failures, retryCapMs);
      state.lastPollFinishedMs = millis();
      ++state.pollCount;
      if (writeOkay) {
        state.forcePoll = true;
        state.positionCheckPending = true;
        state.lastCommandDeliveredMs = millis();
      } else if (!haveCommand && state.positionCheckPending) {
        const bool confirmed = okay && state.lastPositionMs &&
          std::abs(state.position - state.lastCommandPosition) <= 1;
        const bool confirmationExpired = millis() - state.lastCommandDeliveredMs >= 30000UL;
        confirmationTimedOut = !confirmed && confirmationExpired;
        state.positionCheckPending = !confirmed && !confirmationExpired;
        state.forcePoll = state.positionCheckPending;
      }
      state.nextPollMs = (writeOkay || state.positionCheckPending) ? state.lastPollFinishedMs + 5000UL :
        (state.forcePoll ? state.lastPollFinishedMs : state.lastPollFinishedMs + delayMs);
      xSemaphoreGive(stateLock);
      if (confirmationTimedOut)
        Serial.printf("BLE position confirmation timeout blind=%s; movement write was acknowledged\n",
                      config.blinds[selected].name);
      if (haveCommand) {
        Serial.printf("BLE %s id=%lu blind=%s target=%u attempt=%u read=%s latencyMs=%lu\n",
                      writeOkay ? "complete" :
                        (command.attempts + 1 >= kMaxCommandAttempts ? "failed" : "retry"),
                      static_cast<unsigned long>(command.id), config.blinds[selected].name,
                      command.position, command.attempts + 1,
                      okay ? "ok" : "unverified",
                      static_cast<unsigned long>(millis() - command.queuedAtMs));
      }
      if (!haveCommand) Serial.printf("BLE %s: %s\n", config.blinds[selected].name,
                                     okay ? "read complete" : "read failed");
    } else if (scan) {
      scanKnownMotors();
      Serial.println("BLE placement scan complete");
    } else {
      vTaskDelay(pdMS_TO_TICKS(100));
    }
  }
}
String jsonString(const String& value) {
  String result = "\"";
  for (size_t i = 0; i < value.length(); ++i) {
    const char c = value[i];
    if (c == '"' || c == '\\') result += '\\';
    if (static_cast<uint8_t>(c) < 0x20) continue;
    result += c;
  }
  result += '"';
  return result;
}
bool authorized() {
  if (config.token[0] == 0 || requestServer->header("X-MSB-Token") != config.token) {
    requestServer->send(401, "application/json", "{\"error\":\"unauthorized\"}");
    return false;
  }
  return true;
}
int blindIndex(const String& id) {
  for (int i = 0; i < config.count; ++i) {
    if (config.blinds[i].assigned && id.equalsIgnoreCase(config.blinds[i].id)) return i;
  }
  return -1;
}
void sendInfo() {
  String body = "{\"apiVersion\":1,\"bridgeId\":" + jsonString(bridgeId()) +
    ",\"firmwareVersion\":" + jsonString(kFirmwareVersion) +
    ",\"setupAccessPoint\":" + String(setupApMode ? "true" : "false") +
    ",\"uptimeSeconds\":" + String(millis() / 1000) + ",\"blinds\":[";
  bool first = true;
  for (int i = 0; i < config.count; ++i) {
    if (!config.blinds[i].assigned) continue;
    if (!first) body += ',';
    first = false;
    body += "{\"id\":" + jsonString(config.blinds[i].id) +
      ",\"name\":" + jsonString(config.blinds[i].name) + "}";
  }
  body += "]}";
  requestServer->send(200, "application/json", body);
}
void sendState(int index) {
  if (!authorized()) return;
  MotorState snapshot;
  uint8_t minutes;
  xSemaphoreTake(stateLock, portMAX_DELAY);
  snapshot = states[index];
  minutes = pollMinutes[index];
  xSemaphoreGive(stateLock);
  const uint32_t age = snapshot.lastSampleMs ? (millis() - snapshot.lastSampleMs) / 1000 : 0;
  const uint32_t positionAge = snapshot.lastPositionMs ?
    (millis() - snapshot.lastPositionMs) / 1000 : 0;
  const uint32_t intervalMs = static_cast<uint32_t>(minutes) * 60000UL;
  const uint32_t staleMs = std::max<uint32_t>(kMinimumStaleMs, intervalMs * 2 + 60000UL);
  const bool online = snapshot.lastSampleMs &&
    (minutes == 0 || millis() - snapshot.lastSampleMs < staleMs);
  String body = "{\"id\":" + jsonString(config.blinds[index].id) + ",\"online\":";
  body += online ? "true" : "false";
  body += ",\"ageSeconds\":" + String(age);
  body += ",\"positionAgeSeconds\":";
  body += snapshot.lastPositionMs ? String(positionAge) : "null";
  body += ",\"pollMinutes\":" + String(minutes);
  body += ",\"pollCount\":" + String(snapshot.pollCount);
  body += ",\"lastPollOk\":" + String(snapshot.lastPollOk ? "true" : "false");
  body += ",\"position\":" + String(snapshot.position);
  body += ",\"batteryPercent\":" + String(snapshot.batteryPercent);
  body += ",\"batteryAgeSeconds\":";
  body += snapshot.haveSensors ? String((millis() - snapshot.lastSensorsMs) / 1000) : "null";
  body += ",\"bleConnected\":" + String(snapshot.bleConnected ? "true" : "false");
  body += ",\"bleConnectionAgeSeconds\":";
  body += snapshot.bleConnected ? String((millis() - snapshot.bleConnectedAtMs) / 1000) : "null";
  body += ",\"bleDisconnectCount\":" + String(snapshot.bleDisconnectCount);
  body += ",\"solarCharging\":" + String(snapshot.solarCharging ? "true" : "false");
  body += ",\"usbCharging\":" + String(snapshot.usbCharging ? "true" : "false");
  body += ",\"haveStatus\":" + String(snapshot.haveStatus ? "true" : "false");
  body += ",\"haveSensors\":" + String(snapshot.haveSensors ? "true" : "false");
  body += ",\"queuedPosition\":" + String(snapshot.queuedPosition);
  body += ",\"lastCommandPosition\":" + String(snapshot.lastCommandPosition);
  body += ",\"lastCommandWriteOk\":" + String(snapshot.lastCommandWriteOk ? "true" : "false");
  body += ",\"lastCommandLatencyMs\":" + String(snapshot.lastCommandLatencyMs);
  body += ",\"lastCommandId\":" + String(snapshot.lastCommandId);
  body += ",\"lastCommandAttempts\":" + String(snapshot.lastCommandAttempts);
  body += ",\"bleStage\":" + jsonString(bleStageName(snapshot.bleStage));
  body += ",\"bleStageAgeMs\":" + String(snapshot.bleStage ? millis() - snapshot.bleStageStartedMs : 0);
  body += ",\"bleTimingMs\":{\"connect\":" + String(snapshot.bleStageTimesMs[1]);
  body += ",\"scan\":" + String(snapshot.bleStageTimesMs[2]);
  body += ",\"discover\":" + String(snapshot.bleStageTimesMs[3]);
  body += ",\"authenticate\":" + String(snapshot.bleStageTimesMs[4]);
  body += ",\"write\":" + String(snapshot.bleStageTimesMs[5]);
  body += ",\"read\":" + String(snapshot.bleStageTimesMs[6]);
  body += ",\"disconnect\":" + String(snapshot.bleStageTimesMs[7]) + "}";
  body += ",\"raw\":{\"batteryVoltage\":" + String(snapshot.batteryVoltageRaw);
  body += ",\"batteryCharge\":" + String(snapshot.batteryChargeRaw);
  body += ",\"solarPanelVoltage\":" + String(snapshot.solarPanelVoltageRaw);
  body += ",\"interiorTemperature\":" + String(snapshot.interiorTemperatureRaw);
  body += ",\"batteryTemperature\":" + String(snapshot.batteryTemperatureRaw);
  body += ",\"light\":" + String(snapshot.lightRaw) + "}}";
  requestServer->send(200, "application/json", body);
}
void queuePosition(int index) {
  if (!authorized()) return;
  const String value = requestServer->arg("position");
  if (value.isEmpty() || value.length() > 3) {
    requestServer->send(400, "application/json", "{\"error\":\"position must be 0..200\"}");
    return;
  }
  for (size_t i = 0; i < value.length(); ++i) {
    if (value[i] < '0' || value[i] > '9') {
      requestServer->send(400, "application/json", "{\"error\":\"position must be 0..200\"}");
      return;
    }
  }
  const int position = value.toInt();
  if (position > 200) {
    requestServer->send(400, "application/json", "{\"error\":\"position must be 0..200\"}");
    return;
  }
  xSemaphoreTake(stateLock, portMAX_DELAY);
  if (positionCommandCount >= kMaxPositionCommands) {
    xSemaphoreGive(stateLock);
    Serial.printf("CMD rejected blind=%s target=%d reason=queue-full\n",
                  config.blinds[index].name, position);
    requestServer->send(503, "application/json",
      "{\"queued\":false,\"error\":\"command queue full\"}");
    return;
  }
  const uint32_t id = nextPositionCommandId++;
  const uint32_t now = millis();
  positionCommands[positionCommandCount++] = {
    id, now, now, static_cast<uint8_t>(index), static_cast<uint8_t>(position), 0
  };
  updateQueuedPositionLocked(index);
  const size_t depth = positionCommandCount;
  xSemaphoreGive(stateLock);
  Serial.printf("CMD received id=%lu blind=%s target=%d\n",
                static_cast<unsigned long>(id), config.blinds[index].name, position);
  Serial.printf("CMD queued id=%lu depth=%u\n",
                static_cast<unsigned long>(id), static_cast<unsigned>(depth));
  requestServer->send(202, "application/json",
    "{\"queued\":true,\"commandId\":" + String(id) +
    ",\"queueDepth\":" + String(depth) + "}");
}
void setPollMinutes(int index) {
  if (!authorized()) return;
  const String requested = requestServer->arg("minutes");
  int selectedMinutes = -1;
  for (uint8_t choice : kPollChoices) {
    if (requested == String(choice)) {
      selectedMinutes = choice;
      break;
    }
  }
  if (selectedMinutes < 0) {
    requestServer->send(400, "application/json", "{\"error\":\"invalid polling interval\"}");
    return;
  }
  if (!savePolling(index, static_cast<uint8_t>(selectedMinutes))) {
    requestServer->send(500, "application/json", "{\"error\":\"could not save polling interval\"}");
    return;
  }
  requestServer->send(200, "application/json",
    "{\"pollMinutes\":" + String(selectedMinutes) + "}");
}
void queueFreshRead(int index) {
  if (!authorized()) return;
  xSemaphoreTake(stateLock, portMAX_DELAY);
  states[index].forcePoll = true;
  states[index].positionCheckPending = false;
  states[index].nextPollMs = millis();
  const uint32_t count = states[index].pollCount;
  xSemaphoreGive(stateLock);
  requestServer->send(202, "application/json",
    "{\"queued\":true,\"pollCount\":" + String(count) + "}");
}
void sendScan() {
  if (!authorized()) return;
  uint32_t when;
  uint32_t count;
  int rssi[kMaxBlinds];
  xSemaphoreTake(stateLock, portMAX_DELAY);
  when = scanAtMs;
  count = scanCount;
  memcpy(rssi, scanRssi, sizeof(rssi));
  xSemaphoreGive(stateLock);
  String body = "{\"scanCount\":" + String(count) + ",\"ageSeconds\":";
  body += when ? String((millis() - when) / 1000) : "null";
  body += ",\"blinds\":[";
  for (int i = 0; i < config.count; ++i) {
    if (i) body += ',';
    body += "{\"id\":" + jsonString(config.blinds[i].id) +
      ",\"name\":" + jsonString(config.blinds[i].name) +
      ",\"assigned\":" + String(config.blinds[i].assigned ? "true" : "false") +
      ",\"rssi\":";
    body += when && rssi[i] != -127 ? String(rssi[i]) : "null";
    body += '}';
  }
  body += "]}";
  requestServer->send(200, "application/json", body);
}
bool setupAuthorized() {
  // The setup Wi-Fi requires its own password and appears during initial
  // provisioning, Wi-Fi recovery, or after a physical BOOT press.
  if (setupApMode && requestServer->client().localIP() == WiFi.softAPIP()) return true;
  if (requestServer->authenticate("msb", kSetupWifiPassword) ||
      (config.token[0] && requestServer->authenticate("msb", config.token))) return true;
  requestServer->requestAuthentication(BASIC_AUTH, "MySmartBlinds setup");
  return false;
}
bool validNonce() {
  if (requestServer->arg("nonce") == String(setupNonce, HEX)) return true;
  requestServer->send(403, "text/plain", "Setup session expired");
  return false;
}
String htmlEscape(const String& input) {
  String output;
  for (size_t i = 0; i < input.length(); ++i) {
    switch (input[i]) {
      case '&': output += "&amp;"; break;
      case '<': output += "&lt;"; break;
      case '>': output += "&gt;"; break;
      case '"': output += "&quot;"; break;
      case '\'': output += "&#39;"; break;
      default: output += input[i]; break;
    }
  }
  return output;
}
void sendSetupPage() {
  if (!setupAuthorized()) return;
  String page;
  page.reserve(9000);
  page += R"MSB(<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MySmartBlinds bridge setup</title>
<style>body{font:16px system-ui,sans-serif;max-width:850px;margin:2em auto;padding:0 1em;color:#1b2530}
table{border-collapse:collapse;width:100%}th,td{padding:.65em;border-bottom:1px solid #ccd;text-align:left}
button{padding:.7em 1.1em;margin:.5em .3em .5em 0;cursor:pointer}input[type=file]{max-width:100%}
.note{color:#52606d}.card{border:1px solid #ccd;border-radius:8px;padding:1em;margin:1em 0}
</style><h1>MySmartBlinds bridge</h1>)MSB";
  page += "<p class=\"note\">Bridge " + htmlEscape(bridgeId()) +
    " · " + htmlEscape(kFirmwareVersion) + "</p>";
  if (WiFi.status() == WL_CONNECTED) {
    const String hostname = bridgeHostname();
    page += "<p>Home setup: <a href=\"http://" + hostname + ".local/setup\">" +
      hostname + ".local/setup</a> &nbsp;·&nbsp; Current IP: <a href=\"http://" +
      WiFi.localIP().toString() + "/setup\">" + WiFi.localIP().toString() +
      "/setup</a></p>";
  }
  page += "<div class=\"card\"><h2>Wi-Fi network</h2>";
  page += setupApMode ? "<p>Connect this ESP32 to your home 2.4 GHz Wi-Fi.</p>" :
    "<p>Change the Wi-Fi network used by this ESP32.</p>";
  if (config.token[0]) {
    const String apName = setupSsid();
    page += "<p class=\"note\">For direct phone setup, hold BOOT for five seconds while this ESP32 is running. "
      "Join <strong>" + htmlEscape(apName) + "</strong> with Wi-Fi password <code>" +
      String(kSetupWifiPassword) +
      "</code>, then open http://192.168.4.1/setup. No additional login is needed on the setup network. "
      "Restart the ESP32 to turn off its setup network.</p>";
  } else {
    page += "<p class=\"note\">This first-time setup network uses Wi-Fi password "
      + String(kSetupWifiPassword) + ". No web login is needed.</p>";
  }
  page += "<form method=\"post\" action=\"/setup/network\"><input type=\"hidden\" name=\"nonce\" value=\"" +
    String(setupNonce, HEX) + "\"><label>Nearby networks<br><select onchange=\"document.getElementById('ssid').value=this.value\">"
    "<option value=\"\">Choose a network</option>";
  const int networks = WiFi.scanNetworks(false, true);
  for (int i = 0; i < networks && i < 40; ++i) {
    const String ssid = WiFi.SSID(i);
    if (!ssid.isEmpty()) page += "<option value=\"" + htmlEscape(ssid) + "\">" +
      htmlEscape(ssid) + " (" + String(WiFi.RSSI(i)) + " dBm)</option>";
  }
  WiFi.scanDelete();
  page += "</select></label><p><label>Network name (or type a hidden network)<br><input id=\"ssid\" name=\"ssid\" maxlength=\"32\" required value=\"" +
    htmlEscape(config.ssid) + "\"></label></p><p><label>Wi-Fi password<br><input id=\"wifi-password\" name=\"password\" type=\"password\" maxlength=\"63\" autocomplete=\"new-password\"></label> "
    "<label><input type=\"checkbox\" onchange=\"document.getElementById('wifi-password').type=this.checked?'text':'password'\"> Show password</label></p>";
  if (config.ssid[0]) page += "<p class=\"note\">Leave the password blank to keep it when the network name is unchanged. "
    "For an open network, check this box: <label><input type=\"checkbox\" name=\"open\" value=\"1\"> No password</label></p>";
  else page += "<p class=\"note\">Leave the password blank only for an open network.</p>";
  if (config.token[0] == 0) page += "<p><label>Bridge LAN token (optional)<br><input name=\"token\" autocomplete=\"off\" maxlength=\"128\"></label>"
    "<br><span class=\"note\">Leave blank to generate one. Copy the token shown after saving; SmartThings needs it for bridge commands.</span></p>";
  page += "<button>Save Wi-Fi</button></form></div>";
  if (config.token[0] == 0) {
    page += "</html>";
    requestServer->sendHeader("Cache-Control", "no-store");
    requestServer->send(200, "text/html; charset=utf-8", page);
    return;
  }
  page += "<div class=\"card\"><h2>Known blinds</h2><p>Each blind should be enabled on one ESP32 only. "
    "Use the scan to compare reach; confirm with a successful motor read.</p>";
  page += "<p>Last scan: ";
  page += scanAtMs ? String((millis() - scanAtMs) / 1000) + " seconds ago" : "not run";
  page += "</p><form method=\"post\" action=\"/setup/scan\"><input type=\"hidden\" name=\"nonce\" value=\"" +
    String(setupNonce, HEX) + "\"><button>Scan nearby blinds</button></form>";
  page += "<form method=\"post\" action=\"/setup/assign\"><input type=\"hidden\" name=\"nonce\" value=\"" +
    String(setupNonce, HEX) + "\"><table><thead><tr><th>Blind</th><th>Signal</th><th>Enabled here</th></tr></thead><tbody>";
  for (int i = 0; i < config.count; ++i) {
    page += "<tr><td>" + htmlEscape(config.blinds[i].name) + "</td><td>";
    page += scanAtMs && scanRssi[i] != -127 ? String(scanRssi[i]) + " dBm" : "not seen";
    page += "</td><td><input type=\"checkbox\" name=\"b" + String(i) +
      "\" value=\"1\"";
    if (config.blinds[i].assigned) page += " checked";
    page += "></td></tr>";
  }
  page += "</tbody></table><button>Save enabled blinds</button></form></div>";
  page += R"MSB(<div class="card"><h2>Import private app backup</h2>
<p class="note">Choose the JSON backup on this computer. The page reads it locally and
sends only each blind's UUID, name, address, and key to this ESP32. The complete
backup file is never stored on the ESP32. The ESP32 keeps motor keys private.</p>
<input id="backup" type="file" accept=".json,application/json">
<button type="button" onclick="importBackup()">Import blinds</button>
<p id="result" role="status"></p></div>
<script>
const nonce = ")MSB";
  page += String(setupNonce, HEX);
  page += R"MSB(";
const hex = n => n.toString(16).padStart(2,"0").toUpperCase();
async function post(path, values) {
  const response = await fetch(path, {method:"POST", credentials:"same-origin",
    body:new URLSearchParams({nonce:nonce,...values})});
  if (!response.ok) throw Error(path + ": HTTP " + response.status);
}
async function importBackup() {
  const result = document.getElementById("result");
  const file = document.getElementById("backup").files[0];
  if (!file) { result.textContent="Choose a JSON backup first."; return; }
  try {
    const data = JSON.parse(await file.text());
    const records = (data.user?.rooms || []).flatMap(room => room.blinds || [])
      .map(blind => {
        const mac = Array.from(atob(blind.encodedMacAddress), c => c.charCodeAt(0));
        const key = Array.from(atob(blind.encodedPasskey), c => c.charCodeAt(0));
        if (mac.length !== 6 || key.length !== 6) throw Error("Unexpected motor record");
        return {id:String(blind.id).toLowerCase(),name:String(blind.name),
          mac:mac.reverse().map(hex).join(":"),key:key.map(hex).join("")};
      });
    if (records.length < 1 || records.length > 10) throw Error("Expected 1–10 blinds");
    result.textContent = "Importing " + records.length + " blinds...";
    await post("/setup/import/start", {count:String(records.length)});
    for (const record of records) await post("/setup/import/blind", record);
    await post("/setup/import/commit", {});
    result.textContent = "Saved. The bridge is restarting; reload this page in a few seconds.";
    setTimeout(() => location.reload(), 7000);
  } catch (error) {
    result.textContent = "Import failed: " + error.message;
  }
}
</script></html>)MSB";
  requestServer->sendHeader("Cache-Control", "no-store");
  requestServer->send(200, "text/html; charset=utf-8", page);
}
void saveNetwork() {
  if (!setupAuthorized() || !validNonce()) return;
  const String ssid = requestServer->arg("ssid");
  String password = requestServer->arg("password");
  if (requestServer->hasArg("open")) password = "";
  else if (password.isEmpty() && ssid == config.ssid) password = config.password;
  String token = config.token;
  const bool firstSetup = token.isEmpty();
  if (token.isEmpty()) {
    token = requestServer->arg("token");
    if (token.isEmpty()) {
      for (int i = 0; i < 8; ++i) {
        char part[9];
        snprintf(part, sizeof(part), "%08x", esp_random());
        token += part;
      }
    }
  }
  bool validToken = token.length() >= 24 && token.length() <= 128;
  for (size_t i = 0; i < token.length(); ++i)
    if (!isalnum(static_cast<unsigned char>(token[i])) && token[i] != '-' && token[i] != '_') validToken = false;
  if (ssid.isEmpty() || ssid.length() > 32 || password.length() > 63 ||
      (password.length() > 0 && password.length() < 8) || !validToken) {
    requestServer->send(400, "text/plain", "Invalid network name, password, or bridge token");
    return;
  }
  BridgeConfig next = config;
  if (!copyString(next.ssid, sizeof(next.ssid), ssid) ||
      !copyString(next.password, sizeof(next.password), password) ||
      !copyString(next.token, sizeof(next.token), token) || !saveConfig(next)) {
    requestServer->send(500, "text/plain", "Could not save Wi-Fi settings");
    return;
  }
  String page = "<html><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    "<h1>Wi-Fi saved</h1><p>The ESP32 is ready to join " + htmlEscape(ssid) + ".</p>";
  if (firstSetup) page += "<p><strong>Copy this bridge LAN token before continuing:</strong></p><pre>" +
    htmlEscape(token) + "</pre><p>SmartThings uses this token for bridge commands; the setup page uses password MySmartBlinds.</p>";
  const String hostname = bridgeHostname();
  page += "<p>After your phone rejoins home Wi-Fi, tap "
    "<a href=\"http://" + hostname + ".local/setup\">Open bridge setup</a>. "
    "Sign in as msb with password MySmartBlinds.</p>";
  page += "<form method=\"post\" action=\"/setup/network/finish\"><input type=\"hidden\" name=\"nonce\" value=\"" +
    String(setupNonce, HEX) + "\"><button>Connect to Wi-Fi now</button></form></html>";
  requestServer->sendHeader("Cache-Control", "no-store");
  requestServer->send(200, "text/html; charset=utf-8", page);
  networkPending = true;
}
void finishNetwork() {
  if (!setupAuthorized() || !validNonce()) return;
  if (!networkPending) {
    requestServer->send(409, "text/plain", "Save Wi-Fi settings first");
    return;
  }
  const String hostname = bridgeHostname();
  const String homeName = hostname + ".local";
  const String page = "<html><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    "<h1>Connecting to home Wi-Fi</h1>"
    "<p id=\"status\">Waiting for your phone to rejoin home Wi-Fi and for the bridge to appear...</p>"
    "<p>When the bridge is ready, this page will open its setup automatically. "
    "You can also tap <a href=\"http://" + homeName + "/setup\">Open bridge setup</a>. "
    "Use msb / MySmartBlinds if asked to sign in.</p>"
    "<p>If the local name does not open, find this ESP32 in your router's device list as " +
    hostname + " and open its IP address in a browser.</p>"
    "<script>const host='" + homeName + "';"
    "let checking=false;async function checkBridge(){if(checking)return;checking=true;"
    "const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),2500);"
    "try{await fetch('http://'+host+'/v1/info?now='+Date.now(),"
    "{mode:'no-cors',cache:'no-store',signal:controller.signal});"
    "document.getElementById('status').textContent='Bridge found. Opening setup...';"
    "location.replace('http://'+host+'/setup');}catch(e){}"
    "finally{clearTimeout(timer);checking=false}}"
    "setTimeout(checkBridge,3000);setInterval(checkBridge,4000);</script></html>";
  requestServer->sendHeader("Cache-Control", "no-store");
  requestServer->send(200, "text/html; charset=utf-8", page);
  restartDelayMs = 1500;
  restartRequested = true;
}
void queueSetupScan() {
  if (!setupAuthorized() || !validNonce()) return;
  xSemaphoreTake(stateLock, portMAX_DELAY);
  scanRequested = true;
  xSemaphoreGive(stateLock);
  requestServer->send(200, "text/html; charset=utf-8",
    "<html><meta http-equiv=\"refresh\" content=\"11;url=/setup\">"
    "<p>BLE scan queued. This page will return to setup shortly.</p></html>");
}
void saveAssignments() {
  if (!setupAuthorized() || !validNonce()) return;
  if (config.token[0] == 0) {
    requestServer->send(400, "text/plain", "Configure Wi-Fi before assigning blinds");
    return;
  }
  BridgeConfig next = config;
  for (int i = 0; i < next.count; ++i)
    next.blinds[i].assigned = requestServer->hasArg("b" + String(i));
  if (!saveConfig(next)) {
    requestServer->send(500, "text/plain", "Could not save assignments");
    return;
  }
  requestServer->send(200, "text/html; charset=utf-8",
    "<html><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    "<meta http-equiv=\"refresh\" content=\"12;url=/setup\">"
    "<p>Assignments saved. The bridge is restarting.</p>"
    "<p><a href=\"/setup\">Return to setup</a> if this page does not reload.</p></html>");
  restartDelayMs = 1500;
  restartRequested = true;
}
void startImport() {
  if (!setupAuthorized() || !validNonce()) return;
  if (config.token[0] == 0) {
    requestServer->send(400, "text/plain", "Configure Wi-Fi before importing blinds");
    return;
  }
  const String count = requestServer->arg("count");
  if (count.isEmpty() || count.length() > 2 || count.toInt() < 1 ||
      count.toInt() > kMaxBlinds) {
    requestServer->send(400, "text/plain", "Invalid blind count");
    return;
  }
  staged = BridgeConfig();
  copyString(staged.ssid, sizeof(staged.ssid), config.ssid);
  copyString(staged.password, sizeof(staged.password), config.password);
  copyString(staged.token, sizeof(staged.token), config.token);
  uploadExpected = count.toInt();
  uploadActive = true;
  requestServer->send(200, "application/json", "{\"ok\":true}");
}
void importBlind() {
  if (!setupAuthorized() || !validNonce()) return;
  const String id = requestServer->arg("id");
  const String name = requestServer->arg("name");
  const String mac = requestServer->arg("mac");
  const String key = requestServer->arg("key");
  if (!uploadActive || staged.count >= uploadExpected || !validId(id) ||
      name.isEmpty() || name.length() > 48 || !validMac(mac) || key.length() != 12) {
    requestServer->send(400, "text/plain", "Invalid blind record");
    return;
  }
  for (int i = 0; i < staged.count; ++i) {
    if (id.equalsIgnoreCase(staged.blinds[i].id) ||
        mac.equalsIgnoreCase(staged.blinds[i].mac)) {
      requestServer->send(400, "text/plain", "Duplicate blind record");
      return;
    }
  }
  BlindConfig& blind = staged.blinds[staged.count];
  if (!parseKey(key, blind.key) ||
      !copyString(blind.id, sizeof(blind.id), id) ||
      !copyString(blind.name, sizeof(blind.name), name) ||
      !copyString(blind.mac, sizeof(blind.mac), mac)) {
    requestServer->send(400, "text/plain", "Invalid blind record");
    return;
  }
  for (int i = 0; i < config.count; ++i) {
    if (id.equalsIgnoreCase(config.blinds[i].id) ||
        (String(config.blinds[i].id) == "office-window" && name == "Office Window"))
      blind.assigned = config.blinds[i].assigned;
  }
  ++staged.count;
  requestServer->send(200, "application/json", "{\"ok\":true}");
}
void commitImport() {
  if (!setupAuthorized() || !validNonce()) return;
  if (!uploadActive || staged.count != uploadExpected || !saveConfig(staged)) {
    requestServer->send(400, "text/plain", "Import incomplete or save failed");
    return;
  }
  uploadActive = false;
  requestServer->send(200, "application/json", "{\"ok\":true}");
  restartRequested = true;
}
void handleUnknown() {
  const String path = requestServer->uri();
  if (!path.startsWith("/v1/blinds/")) {
    requestServer->send(404, "application/json", "{\"error\":\"not found\"}");
    return;
  }
  const String rest = path.substring(strlen("/v1/blinds/"));
  const bool command = rest.endsWith("/position");
  const bool polling = rest.endsWith("/poll");
  const bool freshRead = rest.endsWith("/refresh");
  const String id = command ? rest.substring(0, rest.length() - strlen("/position")) :
    polling ? rest.substring(0, rest.length() - strlen("/poll")) :
    freshRead ? rest.substring(0, rest.length() - strlen("/refresh")) : rest;
  const int index = blindIndex(id);
  if (index < 0) {
    requestServer->send(404, "application/json", "{\"error\":\"blind not assigned here\"}");
  } else if (command && requestServer->method() == HTTP_POST) {
    queuePosition(index);
  } else if (polling && requestServer->method() == HTTP_POST) {
    setPollMinutes(index);
  } else if (freshRead && requestServer->method() == HTTP_POST) {
    queueFreshRead(index);
  } else if (!command && !polling && !freshRead && requestServer->method() == HTTP_GET) {
    sendState(index);
  } else {
    requestServer->send(405, "application/json", "{\"error\":\"method not allowed\"}");
  }
}

void registerHttpRoutes(WebServer& httpServer) {
  requestServer = &httpServer;
  const char* headers[] = {"X-MSB-Token"};
  requestServer->collectHeaders(headers, 1);
  requestServer->on("/v1/info", HTTP_GET, sendInfo);
  requestServer->on("/v1/scan", HTTP_GET, sendScan);
  requestServer->on("/v1/scan", HTTP_POST, []() {
    if (!authorized()) return;
    xSemaphoreTake(stateLock, portMAX_DELAY);
    scanRequested = true;
    xSemaphoreGive(stateLock);
    requestServer->send(202, "application/json", "{\"queued\":true}");
  });
  requestServer->on("/", HTTP_GET, []() {
    requestServer->sendHeader("Location", "/setup", true);
    requestServer->send(302, "text/plain", "Open /setup");
  });
  requestServer->on("/setup", HTTP_GET, sendSetupPage);
  requestServer->on("/setup/assign", HTTP_GET, []() {
    requestServer->sendHeader("Location", "/setup", true);
    requestServer->send(303, "text/plain", "Open /setup");
  });
  requestServer->on("/setup/network", HTTP_POST, saveNetwork);
  requestServer->on("/setup/network/finish", HTTP_POST, finishNetwork);
  requestServer->on("/setup/scan", HTTP_POST, queueSetupScan);
  requestServer->on("/setup/assign", HTTP_POST, saveAssignments);
  requestServer->on("/setup/import/start", HTTP_POST, startImport);
  requestServer->on("/setup/import/blind", HTTP_POST, importBlind);
  requestServer->on("/setup/import/commit", HTTP_POST, commitImport);
  requestServer->onNotFound(handleUnknown);
  requestServer->begin();
}
}  // namespace

void setup() {
  Serial.begin(115200);
  pinMode(0, INPUT_PULLUP);
  stateLock = xSemaphoreCreateMutex();
  const bool haveConfig = loadConfig();
  loadBridgeIdentity();
  waitForMigrationActivation();
  listenForProvisioning(haveConfig, haveConfig ? 3500 : 10000);
  loadPolling();
  WiFi.mode(WIFI_STA);
  if (config.ssid[0]) {
    WiFi.begin(config.ssid, config.password);
    const uint32_t started = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - started < 20000) delay(200);
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("Bridge IP: ");
    Serial.println(WiFi.localIP());
  } else {
    if (config.ssid[0]) Serial.println("WIFI_CONNECTION_FAILED");
    startSetupAp();
  }
  NimBLEDevice::init("MSB ESP Bridge");
  if (!NimBLEDevice::setPower(9)) Serial.println("BLE_TX_POWER_SET_FAILED");
  const String hostname = bridgeHostname();
  if (!setupApMode && MDNS.begin(hostname.c_str()))
    MDNS.addService("msbbridge", "tcp", kHttpPort);
  setupNonce = esp_random();
  registerHttpRoutes(primaryServer);
  registerHttpRoutes(compatibilityServer);
  xTaskCreatePinnedToCore(bleWorker, "msb-ble", 12288, nullptr, 1, nullptr, 0);
}
void loop() {
  handleSerialDiagnostics();
  static uint32_t lastWifiRetryMs = millis();
  static bool wasWifiConnected = WiFi.status() == WL_CONNECTED;
  requestServer = &primaryServer;
  primaryServer.handleClient();
  requestServer = &compatibilityServer;
  compatibilityServer.handleClient();
  if (digitalRead(0) == LOW) {
    if (!bootPressStartedMs) bootPressStartedMs = millis();
    if (!bootPressHandled && millis() - bootPressStartedMs >= 5000) {
      startSetupAp();
      bootPressHandled = true;
    }
  } else {
    bootPressStartedMs = 0;
    bootPressHandled = false;
  }
  if (restartRequested) {
    delay(restartDelayMs);
    ESP.restart();
  }
  const bool wifiConnected = WiFi.status() == WL_CONNECTED;
  if (wifiConnected != wasWifiConnected) {
    if (wifiConnected) {
      Serial.print("WIFI_RECONNECTED: ");
      Serial.println(WiFi.localIP());
    } else {
      Serial.println("WIFI_DISCONNECTED");
    }
    wasWifiConnected = wifiConnected;
  }
  // The WiFi library already retries normal disconnects. Its reconnect() also
  // forces a disconnect, so calling it every loop iteration aborts association.
  // Keep a slow fallback for failure reasons the library does not retry.
  if (!setupApMode && !wifiConnected && millis() - lastWifiRetryMs >= kWifiRetryIntervalMs) {
    lastWifiRetryMs = millis();
    Serial.println("WIFI_RETRY");
    WiFi.reconnect();
  }
  delay(2);
}
