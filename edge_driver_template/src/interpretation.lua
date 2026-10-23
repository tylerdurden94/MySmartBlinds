-- User's full tilt range: native 200 = 0%, 100 = 50%, 0 = 100%.
local M = {}
local CLOSED_TOLERANCE = 3 -- percentage points; include the boundary

function M.to_native(percent)
  if type(percent) ~= "number" or percent < 0 or percent > 100 or
      percent ~= math.floor(percent) then return nil end
  return 200 - percent * 2
end

function M.to_percent(native)
  if type(native) ~= "number" or native < 0 or native > 200 then return nil end
  return math.floor((200 - native) / 2 + 0.5)
end

function M.command_tolerance(native, preset_command)
  -- Three percentage points are six native counts; do not round the comparison.
  if preset_command and (native == 0 or native == 200) then return 6 end
  return 1
end

function M.position_reached(position, target, tolerance)
  return type(position) == "number" and position >= 0 and position <= 200 and
    math.abs(position - target) <= (tolerance or 1)
end

function M.command_delivered(reading, target, command_id)
  return reading and reading.queuedPosition == -1 and
    reading.lastCommandWriteOk == true and reading.lastCommandPosition == target and
    (command_id == nil or reading.lastCommandId == command_id)
end

function M.command_confirmed(reading, target, command_id, tolerance)
  return M.command_delivered(reading, target, command_id) and
    M.position_reached(reading.position, target, tolerance) and
    (command_id == nil or (type(reading.positionAgeSeconds) == "number" and
      reading.positionAgeSeconds >= 0 and reading.positionAgeSeconds <= 30))
end

function M.preset(preferences, name, fallback)
  local value = tonumber(preferences[name])
  if M.to_native(value) then return value end
  return fallback
end

function M.switch_state(percent, preferences)
  return math.abs(percent - M.preset(preferences, "offPosition", 0)) <= CLOSED_TOLERANCE and "off" or "on"
end

function M.shade_state(percent, preferences)
  if math.abs(percent - M.preset(preferences, "offPosition", 0)) <= CLOSED_TOLERANCE then return "closed" end
  if math.abs(percent - M.preset(preferences, "onPosition", 50)) <= 1 then return "open" end
  if percent <= CLOSED_TOLERANCE or percent >= 100 - CLOSED_TOLERANCE then return "closed" end
  return "partially open"
end

function M.charging(solar, usb)
  if solar and usb then return "both" end
  if solar then return "solar" end
  if usb then return "usb" end
  return "none"
end

function M.temperature(raw, scale, offset)
  -- The conversion preference distinguishes trial and confirmed readings.
  if (scale ~= "deciC" and scale ~= "deciCTrial") or type(raw) ~= "number" then return nil end
  local value = raw / 10 + (tonumber(offset) or 0)
  if value < -100 or value > 150 then return nil end
  return math.floor(value * 10 + 0.5) / 10
end

function M.temperature_display(value, unit)
  if value == nil then return "Needs calibration" end
  if unit == "C" then return string.format("%.1f °C", value) end
  return string.format("%.1f °F", value * 9 / 5 + 32)
end

function M.light_range(raw, maximum, baseline)
  maximum = tonumber(maximum) or 1000
  baseline = math.max(0, math.min(32766, tonumber(baseline) or 0))
  return { minimum = math.min(baseline, raw),
    maximum = math.min(32767, math.max(baseline + 1, maximum, raw)), step = 1 }
end

function M.illuminance(raw, scale, offset)
  -- Explicit, owner-supplied linear calibration; zero disables lux reporting.
  scale = tonumber(scale) or 0
  if scale <= 0 or type(raw) ~= "number" then return nil end
  return math.floor(math.max(0, math.min(100000,
    (raw - (tonumber(offset) or 0)) * scale)) + 0.5)
end

return M
