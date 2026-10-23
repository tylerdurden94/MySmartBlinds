#pragma once
// Host-only radio/clock doubles. Tests compile and execute the production
// connection, polling, ingestion, and scheduling functions without hardware.
#include <algorithm>
#include <cassert>
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <string>
#include <vector>

struct StopWorker {};
struct Simulation {
  uint32_t now = 100;
  uint64_t elapsed = 0, stopAfter = UINT64_MAX;
  bool connectOk = true, serviceOk = true, authOk = true;
  bool writeOk = true, readOk = true, disconnectOk = true, sensorsOk = true, statusOk = true;
  int position = 70, battery = 19;
  int connects = 0, disconnects = 0, writes = 0, positionReads = 0, authWrites = 0;
  std::vector<int> targets;
} sim;
uint32_t millis() { return sim.now; }
void advance(uint32_t ms) {
  sim.now += ms; sim.elapsed += ms;
  if (sim.elapsed >= sim.stopAfter) throw StopWorker{};
}
void vTaskDelay(uint32_t ms) { advance(ms); }
#define pdMS_TO_TICKS(ms) (ms)
constexpr int portMAX_DELAY = 0;
int stateLock = 0;
void xSemaphoreTake(int, int) {}
void xSemaphoreGive(int) {}
struct SerialStub {
  template<typename... Args> void printf(const char*, Args...) {}
  void println(const char*) {}
} Serial;

class String : public std::string {
 public:
  using std::string::string;
  bool equalsIgnoreCase(const char* other) const {
    std::string a = *this, b = other;
    std::transform(a.begin(), a.end(), a.begin(), [](unsigned char c) { return std::tolower(c); });
    std::transform(b.begin(), b.end(), b.begin(), [](unsigned char c) { return std::tolower(c); });
    return a == b;
  }
};
struct NimBLEUUID {
  std::string text;
  explicit NimBLEUUID(const char* value) : text(value) {}
  bool operator==(const NimBLEUUID& rhs) const { return text == rhs.text; }
};
constexpr int BLE_ADDR_RANDOM = 1;
struct NimBLEAddress {
  std::string value;
  NimBLEAddress(std::string text = "", int = BLE_ADDR_RANDOM) : value(text) {}
  std::string toString() const { return value; }
  int getType() const { return BLE_ADDR_RANDOM; }
};
struct NimBLEAttValue : std::vector<uint8_t> {
  using std::vector<uint8_t>::vector;
};
class NimBLEClient;
class NimBLERemoteService;
class NimBLERemoteCharacteristic;
class NimBLEClientCallbacks {
 public:
  virtual void onConnect(NimBLEClient*) {}
  virtual void onDisconnect(NimBLEClient*, int) {}
  virtual ~NimBLEClientCallbacks() = default;
};
class NimBLEAdvertisedDevice {
 public:
  NimBLEAddress getAddress() const { return NimBLEAddress("missing"); }
  int getRSSI() const { return -50; }
};
class NimBLEClient {
 public:
  bool connected = false;
  NimBLEAddress address;
  NimBLEClientCallbacks* callbacks = nullptr;
  bool isConnected() const { return connected; }
  void setConnectTimeout(int) {}
  void setSelfDelete(bool, bool) {}
  void setClientCallbacks(NimBLEClientCallbacks* cb, bool) { callbacks = cb; }
  bool connect(const NimBLEAddress& peer, bool, bool, bool) {
    ++sim.connects; advance(1); address = peer;
    connected = sim.connectOk;
    if (connected && callbacks) callbacks->onConnect(this);
    return connected;
  }
  bool connect(const NimBLEAdvertisedDevice* device, bool a, bool b, bool c) {
    return connect(device->getAddress(), a, b, c);
  }
  bool disconnect() {
    ++sim.disconnects;
    if (!sim.disconnectOk) return false;
    const bool wasConnected = connected;
    connected = false;
    if (wasConnected && callbacks) callbacks->onDisconnect(this, 0);
    return true;
  }
  NimBLEAddress getPeerAddress() const { return address; }
  int getLastError() const { return 1; }
  void deleteServices() {}
  NimBLERemoteService* getService(const char*);
};
class NimBLERemoteCharacteristic {
 public:
  NimBLEClient* client = nullptr;
  const char* uuid = "";
  bool canIndicate() const { return true; }
  bool canNotify() const { return false; }
  bool canRead() const { return true; }
  bool subscribe(bool, void (*)(NimBLERemoteCharacteristic*, uint8_t*, size_t, bool)) { return true; }
  NimBLEClient* getClient() { return client; }
  NimBLEUUID getUUID() const { return NimBLEUUID(uuid); }
  bool writeValue(const uint8_t* value, size_t, bool) {
    advance(1);
    if (std::string(uuid).find("1409") != std::string::npos) {
      ++sim.authWrites;
      return sim.authOk;
    }
    assert(std::string(uuid).find("1403") != std::string::npos);
    ++sim.writes; sim.targets.push_back(*value);
    return sim.writeOk;
  }
  NimBLEAttValue readValue() {
    advance(1);
    const std::string id(uuid);
    if (id.find("1403") != std::string::npos) {
      ++sim.positionReads;
      return sim.readOk ? NimBLEAttValue{static_cast<uint8_t>(sim.position)} : NimBLEAttValue{};
    }
    if (id.find("1651") != std::string::npos) {
      if (!sim.sensorsOk) return {};
      NimBLEAttValue packet(14, 0);
      packet[0] = static_cast<uint8_t>(sim.battery);
      packet[2] = 42;
      return packet;
    }
    return sim.statusOk ? NimBLEAttValue{0, 0, 4, 0} : NimBLEAttValue{};
  }
};
class NimBLERemoteService {
 public:
  NimBLEClient* client = nullptr;
  NimBLERemoteCharacteristic characteristics[4];
  NimBLERemoteCharacteristic* getCharacteristic(const char* uuid) {
    size_t i = std::string(uuid).find("1409") != std::string::npos ? 0 :
      std::string(uuid).find("1403") != std::string::npos ? 1 :
      std::string(uuid).find("1402") != std::string::npos ? 2 : 3;
    characteristics[i].client = client;
    characteristics[i].uuid = uuid;
    return &characteristics[i];
  }
} service;
NimBLERemoteService* NimBLEClient::getService(const char*) {
  service.client = this;
  return sim.serviceOk ? &service : nullptr;
}
struct NimBLEScanResults {
  int getCount() const { return 0; }
  const NimBLEAdvertisedDevice* getDevice(int) const { return nullptr; }
};
struct NimBLEScan {
  void setActiveScan(bool) {}
  NimBLEScanResults getResults(uint32_t ms) { advance(ms); return {}; }
  void clearResults() {}
} scanner;
std::vector<std::unique_ptr<NimBLEClient>> clients;
struct NimBLEDevice {
  static NimBLEClient* createClient() {
    clients.emplace_back(new NimBLEClient());
    return clients.back().get();
  }
  static NimBLEScan* getScan() { return &scanner; }
};
