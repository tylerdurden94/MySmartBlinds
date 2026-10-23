#pragma once

#include <stdint.h>

// Shared with the USB migration utility. Keep the saved blob layout stable.
constexpr uint8_t kMaxBlinds = 10;
constexpr uint32_t kConfigMagic = 0x4D534232;
constexpr uint32_t kPollSettingsMagic = 0x4D534250;
constexpr uint8_t kDefaultPollMinutes = 2;

struct BlindConfig {
  char id[37] = {};
  char name[49] = {};
  char mac[18] = {};
  uint8_t key[6] = {};
  bool assigned = false;
};
struct BridgeConfig {
  uint32_t magic = kConfigMagic;
  uint8_t count = 0;
  char ssid[33] = {};
  char password[64] = {};
  char token[129] = {};
  BlindConfig blinds[kMaxBlinds];
};
struct BlindPollSetting {
  char id[37] = {};
  uint8_t minutes = kDefaultPollMinutes;
};
struct PollSettings {
  uint32_t magic = kPollSettingsMagic;
  uint8_t count = 0;
  BlindPollSetting blinds[kMaxBlinds];
};
static_assert(sizeof(BridgeConfig) == 1344, "Saved bridge configuration layout changed");
static_assert(sizeof(PollSettings) == 388, "Saved polling layout changed");
