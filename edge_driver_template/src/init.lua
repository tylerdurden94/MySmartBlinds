local log = require "log"
local Driver = require "st.driver"
local capabilities = require "st.capabilities"
local mdns = require "st.mdns"
local json = require "st.json"
local ltn12 = require "ltn12"
local cosock = require "cosock"
local http = cosock.asyncify "socket.http"
local interpretation = require "interpretation"

local tilt = capabilities["__NAMESPACE__.mysmartblindsTiltPosition"]
local control = capabilities["__NAMESPACE__.mysmartblindsTiltControl"]
local telemetry = capabilities["__NAMESPACE__.mysmartblindsRawTelemetry"]
local sensors = capabilities["__NAMESPACE__.mysmartblindsSensorStatus"]
local temperatures = capabilities["__NAMESPACE__.mysmartblindsTemperatures"]
local light = capabilities["__NAMESPACE__.mysmartblindsLightMeter"]
local legacy_light = capabilities["__NAMESPACE__.mysmartblindsLightLevel"]
local endpoints = {}
local pending_creates = {}
local conflicts = {}
local PROFILE = "msb-office-blind.v6"
local DEBUG_PROFILE = "msb-office-blind.v6-debug"
local BRIDGE_PROFILE = "msb-bridge-config.v1"
local OFFICE_UUID = "00000000-0000-4000-8000-000000000001"
local HTTP_TIMEOUT_SECONDS = 60
local POSITION_READ_ATTEMPTS = 3
local POSITION_READ_RETRY_DELAY = 10

local function bridge_dni(bridge_id)
  return "msb-bridge-" .. bridge_id:gsub("[^%w]", ""):upper()
end

local function blind_dni(blind_id)
  return "msb-" .. blind_id:lower()
end

local function is_blind_id(value)
  return type(value) == "string" and
    value:match("^[0-9a-fA-F]+%-[0-9a-fA-F]+%-[0-9a-fA-F]+%-[0-9a-fA-F]+%-[0-9a-fA-F]+$") ~= nil and
    #value == 36
end

local function existing_device(driver, dni)
  for _, device in ipairs(driver:get_devices()) do
    if device.device_network_id == dni then return device end
  end
end

local function legacy_office(driver)
  for _, device in ipairs(driver:get_devices()) do
    if device.device_network_id:match("^msb%-%w+%-office%-window$") then return device end
  end
end

local function selected_profile(device)
  return device.preferences.showDiagnostics == true and DEBUG_PROFILE or PROFILE
end

local function emit_level(device, percent, force, measured_result)
  device:emit_event(capabilities.windowShadeLevel.shadeLevel(percent,
    { state_change = force == true or measured_result == true }))
  device:emit_event(capabilities.switch.switch(interpretation.switch_state(percent, device.preferences)))
  local state = interpretation.shade_state(percent, device.preferences)
  if force then
    state = state == "open" and "opening" or
      (state == "closed" and "closing" or "partially open")
  end
  device:emit_event(capabilities.windowShade.windowShade(state))
end

local function request(ip, port, path, method, token)
  local response = {}
  local headers = { ["Connection"] = "close" }
  if token and #token >= 24 then headers["X-MSB-Token"] = token end
  if method == "POST" then headers["Content-Length"] = "0" end
  local ok, code = http.request({
    url = string.format("http://%s:%d%s", ip, port, path),
    method = method,
    headers = headers,
    sink = ltn12.sink.table(response),
    create = function()
      local socket = cosock.socket.tcp()
      -- LuaSocket HTTP sets its module timeout again after create() returns.
      -- Keep this connection's chosen timeout when that happens.
      local settimeout = socket.settimeout
      socket.settimeout = function(self, _, mode)
        return settimeout(self, HTTP_TIMEOUT_SECONDS, mode)
      end
      socket:settimeout(HTTP_TIMEOUT_SECONDS)
      -- LuaSocket sends the request line and headers separately. The bridge can
      -- prematurely finish parsing a fragmented request, then reject the
      -- missing token. Submit the complete header block in one socket write.
      local send = socket.send
      local request_line
      socket.send = function(self, data, first, last)
        if request_line then
          local prefix = request_line
          request_line = nil
          local start = first or 1
          local payload = data:sub(start, last or -1)
          local sent, err, partial = send(self, prefix .. payload)
          if sent then return start + sent - #prefix - 1 end
          return nil, err, start - 1 + math.max(0, (partial or 0) - #prefix)
        end
        if not first and not last and data:match("^%u+ [^\r\n]+ HTTP/1%.1\r\n$") then
          request_line = data
          return #data
        end
        return send(self, data, first, last)
      end
      return socket
    end
  })
  if not ok then return nil, code end
  local status = tonumber(code)
  if not status or status < 200 or status >= 300 then return nil, code end
  local body = table.concat(response)
  if body == "" then return {}, status end
  local decoded_ok, decoded = pcall(json.decode, body)
  if not decoded_ok then return nil, "invalid JSON" end
  return decoded, status
end

local function is_ipv4(address)
  return type(address) == "string" and address:match("^%d+%.%d+%.%d+%.%d+$") ~= nil
end

local function find_bridge(driver, create_devices)
  local result = mdns.discover("_msbbridge._tcp", "local")
  local candidates = {}
  for _, found in ipairs((result and result.found) or {}) do
    local host = found.host_info
    if host and is_ipv4(host.address) and type(host.port) == "number" then
      local info = request(host.address, host.port, "/v1/info", "GET")
      if info and info.apiVersion == 1 and type(info.bridgeId) == "string" then
        local bridge_id = info.bridgeId:gsub("[^%w]", ""):upper()
        if bridge_id ~= "" then
          local setup_dni = bridge_dni(bridge_id)
          local setup = existing_device(driver, setup_dni)
          if setup then
            setup:set_field("bridge_ip", host.address, { persist = true })
            setup:set_field("bridge_port", host.port, { persist = true })
          elseif create_devices and not pending_creates[setup_dni] then
            driver:try_create_device({ type = "LAN", device_network_id = setup_dni,
              label = "MySmartBlinds Bridge " .. bridge_id:sub(-4), profile = BRIDGE_PROFILE,
              manufacturer = "MySmartBlinds", model = "ESP32 BLE bridge" })
            pending_creates[setup_dni] = true
          end
        end
        for _, blind in ipairs(info.blinds or {}) do
          if is_blind_id(blind.id) or blind.id == "office-window" then
            local id = blind.id:lower()
            local item = { ip = host.address, port = host.port,
              bridge_id = bridge_id, blind_id = id, name = blind.name }
            if candidates[id] and candidates[id].bridge_id ~= bridge_id then
              candidates[id] = { conflict = true, name = candidates[id].name }
            elseif not candidates[id] then
              candidates[id] = item
            end
          end
        end
      end
    end
  end
  for id, item in pairs(candidates) do
    local dni = id == "office-window" and nil or blind_dni(id)
    local legacy = (id == "office-window" or id == OFFICE_UUID) and legacy_office(driver)
    if legacy then dni = legacy.device_network_id end
    if item.conflict then
      if dni then
        endpoints[dni] = nil
        conflicts[dni] = true
      end
      log.warn("Blind " .. id .. " is assigned to both bridges; resolve the overlap")
    elseif dni then
      conflicts[dni] = nil
      endpoints[dni] = item
      local device = existing_device(driver, dni)
      if device then
        device:set_field("bridge_ip", item.ip, { persist = true })
        device:set_field("bridge_port", item.port, { persist = true })
        device:set_field("bridge_id", item.bridge_id, { persist = true })
        device:set_field("blind_id", item.blind_id, { persist = true })
      elseif create_devices and not pending_creates[dni] then
        driver:try_create_device({ type = "LAN", device_network_id = dni,
          label = item.name or "MySmartBlinds Blind", profile = PROFILE,
          manufacturer = "MySmartBlinds", model = "BLE tilt blind" })
        pending_creates[dni] = true
      end
    end
  end
end

local function endpoint_for(driver, device)
  if conflicts[device.device_network_id] then return nil end
  local endpoint = endpoints[device.device_network_id]
  if endpoint then return endpoint end
  local ip = device:get_field("bridge_ip")
  local port = device:get_field("bridge_port")
  local id = device:get_field("blind_id")
  if device.device_network_id:match("^msb%-%w+%-office%-window$") then
    -- The pilot device retains its network ID, but inventory imports replace
    -- its API path with the stable Office Window UUID.
    id = OFFICE_UUID
  end
  if not id then id = device.device_network_id:match("^msb%-(.+)$") end
  local bridge_id = device:get_field("bridge_id")
  if ip and port and id then return { ip = ip, port = port, blind_id = id, bridge_id = bridge_id } end
  find_bridge(driver, false)
  return endpoints[device.device_network_id]
end

local function token_for(driver, device, endpoint)
  if endpoint and endpoint.bridge_id then
    local setup = existing_device(driver, bridge_dni(endpoint.bridge_id))
    local token = setup and setup.preferences.bridgeToken
    if type(token) == "string" and #token >= 24 then return token end
  end
  local fallback = device.preferences.bridgeToken
  if type(fallback) == "string" and #fallback >= 24 then return fallback end
  -- Keep the existing Office Window token available to other blinds on the
  -- same ESP after migrating from the one-blind pilot.
  if endpoint and endpoint.bridge_id then
    local office = legacy_office(driver)
    if office and office:get_field("bridge_id") == endpoint.bridge_id then
      local shared = office.preferences.bridgeToken
      if type(shared) == "string" and #shared >= 24 then return shared end
    end
  end
end

local function refresh(driver, device)
  if device.device_network_id:match("^msb%-bridge%-") then return end
  local endpoint = endpoint_for(driver, device)
  if not endpoint then
    if device.offline then device:offline() end
    return
  end
  local token = token_for(driver, device, endpoint)
  if not token then
    if device.offline then device:offline() end
    return
  end
  local reading, err = request(endpoint.ip, endpoint.port,
    "/v1/blinds/" .. endpoint.blind_id, "GET", token)
  if not reading then
    find_bridge(driver, false)
    local updated = endpoints[device.device_network_id]
    if updated and (updated.ip ~= endpoint.ip or updated.port ~= endpoint.port) then
      endpoint = updated
      token = token_for(driver, device, endpoint)
      if token then reading, err = request(endpoint.ip, endpoint.port,
        "/v1/blinds/" .. endpoint.blind_id, "GET", token) end
    end
  end
  if not reading then
    log.warn("Bridge reading failed for " .. (device.label or device.device_network_id) ..
      " at " .. endpoint.ip .. ":" .. tostring(endpoint.port) .. ": " .. tostring(err))
    if device.offline then device:offline() end
    return
  end
  local desired_poll = tonumber(device.preferences.positionPollMinutes)
  if type(reading.pollMinutes) == "number" and desired_poll and
      reading.pollMinutes ~= desired_poll then
    local saved, save_err = request(endpoint.ip, endpoint.port,
      string.format("/v1/blinds/%s/poll?minutes=%d", endpoint.blind_id, desired_poll),
      "POST", token)
    if not saved then log.warn("Could not set position polling: " .. tostring(save_err)) end
  end
  if reading.online ~= true then
    if device.offline then device:offline() end
    return
  end
  if device.online then device:online() end
  if type(reading.position) == "number" and reading.position >= 0 and reading.position <= 200 then
    device:emit_event(tilt.position(reading.position))
    -- Seed the requested-position slider from a real reading on first use.
    -- Subsequent readings remain separate from the accepted command target.
    if device:supports_capability(control) and
        device:get_latest_state("main", control.ID, "targetPosition") == nil then
      device:emit_event(control.targetPosition(reading.position))
    end
    local pending = device:get_field("pending_native_position")
    local verification = device:get_field("position_verification")
    local confirmed = pending and
      interpretation.command_confirmed(reading, pending, verification and verification.command_id,
        verification and verification.tolerance)
    if confirmed then
      if verification then verification.operation.complete = true end
      device:set_field("pending_native_position", nil)
      device:set_field("position_verification", nil)
      -- Keep the real position visible when accepting the wider endpoint tolerance.
      local measured = math.abs(reading.position - pending) <= 1 and pending or reading.position
      emit_level(device, interpretation.to_percent(measured), false, true)
    elseif not pending then
      local measured = reading.position
      if reading.queuedPosition == -1 and reading.lastCommandWriteOk == true and
          type(reading.lastCommandPosition) == "number" and
          math.abs(measured - reading.lastCommandPosition) <= 1 then
        measured = reading.lastCommandPosition
      end
      emit_level(device, interpretation.to_percent(measured))
    end
  end
  if reading.haveSensors == true and type(reading.batteryPercent) == "number" and
      reading.batteryPercent >= 0 and reading.batteryPercent <= 100 then
    device:emit_event(capabilities.battery.battery(reading.batteryPercent))
    local raw = reading.raw or {}
    local names = { "batteryVoltage", "batteryCharge", "solarPanelVoltage",
      "interiorTemperature", "batteryTemperature", "light" }
    for _, name in ipairs(names) do
      if type(raw[name]) == "number" then
        device:emit_event(telemetry[name](raw[name]))
      end
    end
    local interior = interpretation.temperature(raw.interiorTemperature,
      device.preferences.temperatureScale, device.preferences.interiorOffset)
    local battery = interpretation.temperature(raw.batteryTemperature,
      device.preferences.temperatureScale, device.preferences.batteryOffset)
    device:emit_event(sensors.temperatureReady(interior and battery and "ready" or "unverified"))
    device:emit_event(temperatures.ambientDisplay(
      interpretation.temperature_display(interior, device.preferences.temperatureUnit), { state_change = true }))
    device:emit_event(temperatures.batteryDisplay(
      interpretation.temperature_display(battery, device.preferences.temperatureUnit), { state_change = true }))
    if interior then
      device:emit_event(capabilities.temperatureMeasurement.temperature({ value = interior, unit = "C" }))
      device:emit_event(temperatures.ambientTemperature({ value = interior, unit = "C" }))
    end
    if battery then
      device:emit_event(temperatures.batteryTemperature({ value = battery, unit = "C" }))
    end
    if type(raw.light) == "number" and raw.light >= 0 and raw.light <= 32767 then
      device:emit_event(light.lightRange({ value = interpretation.light_range(raw.light, device.preferences.lightGraphMax,
        device.preferences.lightGraphBaseline), unit = "sensor units" },
        { state_change = true }))
      device:emit_event(light.lightLevel({ value = raw.light, unit = "sensor units" }, { state_change = true }))
      -- Preserve light readings used by existing routines while replacing the meter UI.
      if device:supports_capability(legacy_light) then
        device:emit_event(legacy_light.lightLevel({ value = raw.light, unit = "sensor units" }))
      end
    end
    local lux = interpretation.illuminance(raw.light, device.preferences.luxPerCount,
      device.preferences.lightDarkOffset)
    device:emit_event(sensors.lightReady(lux and "ready" or "unverified"))
    if lux then
      device:emit_event(capabilities.illuminanceMeasurement.illuminance({ value = lux, unit = "lux" }))
    end
  end
  if reading.haveStatus == true then
    device:emit_event(telemetry.solarCharging(reading.solarCharging == true))
    device:emit_event(telemetry.usbCharging(reading.usbCharging == true))
    device:emit_event(sensors.chargingSource(interpretation.charging(
      reading.solarCharging == true, reading.usbCharging == true), { state_change = true }))
  end
  return reading
end

local function fresh_read(driver, device)
  if device.device_network_id:match("^msb%-bridge%-") then
    find_bridge(driver, false)
    return
  end
  local endpoint = endpoint_for(driver, device)
  if not endpoint then return end
  local token = token_for(driver, device, endpoint)
  if not token then return end
  local result, err = request(endpoint.ip, endpoint.port,
    "/v1/blinds/" .. endpoint.blind_id .. "/refresh", "POST", token)
  if not result then
    log.warn("Fresh position read failed for " .. (device.label or device.device_network_id) ..
      " at " .. endpoint.ip .. ":" .. tostring(endpoint.port) .. ": " .. tostring(err))
    refresh(driver, device)
    return
  end
  local generation = (device:get_field("manual_refresh_generation") or 0) + 1
  device:set_field("manual_refresh_generation", generation)
  local function check(attempt)
    if device:get_field("manual_refresh_generation") ~= generation then return end
    local reading = refresh(driver, device)
    if reading and type(result.pollCount) == "number" and
        type(reading.pollCount) == "number" and reading.pollCount > result.pollCount then
      return
    end
    if attempt < 12 then
      driver:call_with_delay(5, function() check(attempt + 1) end)
    end
  end
  driver:call_with_delay(5, function() check(1) end)
end

local function set_position(driver, device, command, preset_command)
  local value = tonumber(command.args.position)
  if not value or value < 0 or value > 200 or
      value ~= math.floor(value) then return end
  -- Start the generation before HTTP delivery so a newer command also
  -- cancels retries for a target that has not yet been accepted.
  local generation = (device:get_field("position_request_generation") or 0) + 1
  device:set_field("position_request_generation", generation)
  device:set_field("pending_native_position", nil)
  device:set_field("position_verification", nil)
  local configured_retries = tonumber(device.preferences.openCloseRetries) or 0
  local retries_left = preset_command and math.max(0, math.min(15, math.floor(configured_retries))) or 0
  local retry_limit = retries_left
  local accepted_movements = 0
  local tolerance = interpretation.command_tolerance(value, preset_command)
  local confirmation_checks = retries_left > 0 and 15 or 60
  local operation = {complete = false}
  local function active()
    return not operation.complete and device:get_field("position_request_generation") == generation
  end
  local function finish(reading, warning)
    if not active() then return end
    operation.complete = true
    device:set_field("pending_native_position", nil)
    device:set_field("position_verification", nil)
    if reading and interpretation.to_percent(reading.position) then
      emit_level(device, interpretation.to_percent(reading.position), false, true)
    end
    if warning then
      log.warn((device.label or device.device_network_id) .. ": " .. warning ..
        string.format("; %d accepted movement requests, %d/%d extra attempts used",
          accepted_movements, retry_limit - retries_left, retry_limit))
    end
  end
  local send
  send = function(attempt)
    if not active() then return end
    local endpoint = endpoint_for(driver, device)
    if not endpoint then return end
    local token = token_for(driver, device, endpoint)
    if not token then return end
    local path = string.format("/v1/blinds/%s/position?position=%d", endpoint.blind_id, value)
    local result, err = request(endpoint.ip, endpoint.port, path, "POST", token)
    if not active() then return end
    if not result then
      find_bridge(driver, false)
      if not active() then return end
      local updated = endpoints[device.device_network_id]
      if updated and (updated.ip ~= endpoint.ip or updated.port ~= endpoint.port) then
        endpoint = updated
        token = token_for(driver, device, endpoint)
        if token then
          path = string.format("/v1/blinds/%s/position?position=%d", endpoint.blind_id, value)
          result, err = request(endpoint.ip, endpoint.port, path, "POST", token)
        end
      end
    end
    if not active() then return end
    if not result then
      local status = tonumber(err)
      local transient = not status or status == 408 or status == 429 or status >= 500
      if transient and attempt < 3 then
        -- The ESP can remain at the same IP through a temporary LAN timeout.
        -- Repeating a position target is safe even if the first HTTP reply was
        -- lost after acceptance. Never let a delayed retry replace a newer target.
        local delay = attempt == 1 and 1 or 3
        log.warn("Bridge command delivery retry for " .. device.device_network_id .. ": " .. tostring(err))
        driver:call_with_delay(delay, function() send(attempt + 1) end)
      else
        finish(nil, "Bridge command delivery failed: " .. tostring(err) .. "; stopping retries")
      end
    else
      accepted_movements = accepted_movements + 1
      -- Acknowledge the accepted target promptly so the app does not time out
      -- while BLE connects. The measured position is only emitted by refresh.
      if device:supports_capability(control) then
        device:emit_event(control.targetPosition(value, { state_change = true }))
      end
      device:set_field("pending_native_position", value)
      emit_level(device, interpretation.to_percent(value), true)
      local command_id = type(result.commandId) == "number" and result.commandId or nil
      device:set_field("position_verification", {command_id = command_id, tolerance = tolerance,
        operation = operation})
      local function confirmed(reading)
        return interpretation.command_confirmed(reading, value, command_id, tolerance)
      end
      local function superseded(reading)
        return reading and ((reading.queuedPosition and reading.queuedPosition >= 0 and
          reading.queuedPosition ~= value) or (command_id and
          type(reading.lastCommandId) == "number" and reading.lastCommandId > command_id))
      end
      local function verify_for_retry(reading)
        if not active() then return end
        if retries_left <= 0 then
          finish(reading, "Position was not confirmed; configured retry limit reached")
          return
        end
        if (tonumber(device.preferences.openCloseRetries) or 0) <= 0 or
            not command_id or not interpretation.command_delivered(reading, value, command_id) then
          finish(reading, "Position was not confirmed; no further movement attempts")
          return
        end
        local request_fresh
        local function read_unavailable(current, read_attempt, reason)
          if not active() then return end
          if superseded(current) then finish(current); return end
          if confirmed(current) then finish(current); return end
          if retries_left <= 0 or (tonumber(device.preferences.openCloseRetries) or 0) <= 0 then
            finish(current, "Position was not confirmed; no further movement attempts")
            return
          end
          if read_attempt < POSITION_READ_ATTEMPTS then
            log.warn((device.label or device.device_network_id) .. ": " .. reason ..
              "; retrying position read " .. tostring(read_attempt + 1) .. "/" .. POSITION_READ_ATTEMPTS)
            driver:call_with_delay(POSITION_READ_RETRY_DELAY, function()
              if not active() then return end
              local latest = refresh(driver, device)
              if not active() then return end
              if superseded(latest) then finish(latest); return end
              if confirmed(latest) then finish(latest); return end
              request_fresh(latest, read_attempt + 1)
            end)
          else
            -- Missing position feedback does not consume the remaining movement
            -- budget. Repeat the absolute target after an acknowledged write;
            -- command identity and worker availability must still be known.
            if current and current.bleStage == "idle" and
                interpretation.command_delivered(current, value, command_id) then
              retries_left = retries_left - 1
              log.warn((device.label or device.device_network_id) .. ": " .. reason ..
                string.format("; retrying target without fresh position (extra attempt %d/%d)",
                  retry_limit - retries_left, retry_limit))
              send(1)
            else
              finish(current, reason .. "; stopping retries without fresh position")
            end
          end
        end
        request_fresh = function(current, read_attempt)
          if not active() then return end
          if retries_left <= 0 or (tonumber(device.preferences.openCloseRetries) or 0) <= 0 or
              not interpretation.command_delivered(current, value, command_id) then
            finish(current, "Cannot verify acknowledged command; stopping retries")
            return
          end
          local endpoint = endpoint_for(driver, device)
          local token = endpoint and token_for(driver, device, endpoint)
          if not endpoint or not token then finish(current, "Cannot verify motor position"); return end
          local fresh, fresh_err = request(endpoint.ip, endpoint.port,
            "/v1/blinds/" .. endpoint.blind_id .. "/refresh", "POST", token)
          if not active() then return end
          if not fresh or type(fresh.pollCount) ~= "number" then
            local status = tonumber(fresh_err)
            if status and status >= 400 and status < 500 and status ~= 408 and status ~= 429 then
              finish(current, "Fresh motor read rejected: " .. tostring(fresh_err)); return
            end
            local latest = refresh(driver, device)
            if not active() then return end
            read_unavailable(latest, read_attempt, "Fresh motor read unavailable: " .. tostring(fresh_err))
            return
          end
          local function check_fresh(check)
            if not active() then return end
            local latest = refresh(driver, device)
            if not active() then return end
            if superseded(latest) then finish(latest); return end
            if confirmed(latest) then finish(latest); return end
            -- A completed counter can belong to another operation while this
            -- requested read is still running. Wait for an idle worker first.
            if latest and latest.bleStage == "idle" and latest.queuedPosition == -1 and
                type(latest.pollCount) == "number" and latest.pollCount > fresh.pollCount then
              if latest.lastPollOk == true and type(latest.positionAgeSeconds) == "number" and
                  latest.positionAgeSeconds <= 15 and latest.positionAgeSeconds >= 0 and
                  interpretation.command_delivered(latest, value, command_id) and
                  interpretation.to_percent(latest.position) then
                if (tonumber(device.preferences.openCloseRetries) or 0) <= 0 then finish(latest); return end
                retries_left = retries_left - 1
                log.warn((device.label or device.device_network_id) .. ": Open/Close stopped short; retrying target")
                send(1)
              else
                read_unavailable(latest, read_attempt, "Motor read did not provide fresh position")
              end
              return
            end
            if check < 12 then
              driver:call_with_delay(5, function() check_fresh(check + 1) end)
            else
              read_unavailable(latest, read_attempt, "Fresh motor read timed out")
            end
          end
          driver:call_with_delay(5, function() check_fresh(1) end)
        end
        request_fresh(reading, 1)
      end
      local function poll_result(attempt)
        if not active() then return end
        local reading = refresh(driver, device)
        if not active() then return end
        if superseded(reading) then finish(reading); return end
        if confirmed(reading) then
          device:set_field("position_verification", nil)
          return
        end
        if attempt < confirmation_checks then
          driver:call_with_delay(2, function() poll_result(attempt + 1) end)
        else
          verify_for_retry(reading)
        end
      end
      driver:call_with_delay(2, function() poll_result(1) end)
    end
  end
  send(1)
end

local function set_percent(driver, device, command)
  local native = interpretation.to_native(tonumber(command.args.shadeLevel))
  if native then set_position(driver, device, { args = { position = native } }) end
end

local function set_preset(driver, device, name, fallback)
  set_position(driver, device, { args = {
    position = interpretation.to_native(interpretation.preset(device.preferences, name, fallback))
  } }, true)
end

local function on_added(_, device)
  pending_creates[device.device_network_id] = nil
  local endpoint = endpoints[device.device_network_id]
  if endpoint then
    device:set_field("bridge_ip", endpoint.ip, { persist = true })
    device:set_field("bridge_port", endpoint.port, { persist = true })
    device:set_field("bridge_id", endpoint.bridge_id, { persist = true })
    device:set_field("blind_id", endpoint.blind_id, { persist = true })
  end
end

local function on_init(driver, device)
  if device.device_network_id:match("^msb%-bridge%-") then return end
  local desired = selected_profile(device)
  if device:get_field("ui_profile") ~= desired then
    device:try_update_metadata({ profile = desired })
    device:set_field("ui_profile", desired, { persist = true })
  end
  local endpoint = endpoints[device.device_network_id]
  if endpoint then
    device:set_field("bridge_ip", endpoint.ip, { persist = true })
    device:set_field("bridge_port", endpoint.port, { persist = true })
    device:set_field("bridge_id", endpoint.bridge_id, { persist = true })
    device:set_field("blind_id", endpoint.blind_id, { persist = true })
  end
  driver:call_with_delay(3, function()
    device:emit_event(capabilities.windowShade.supportedWindowShadeCommands({ "open", "close" }))
    local reading = refresh(driver, device)
    -- Reconcile the displayed slider after a restart, including a prior failed target.
    if reading and not device:get_field("pending_native_position") and
        interpretation.to_percent(reading.position) then
      emit_level(device, interpretation.to_percent(reading.position), false, true)
    end
  end)
end

local driver = Driver("MySmartBlinds ESP Bridge", {
  discovery = function(self, _discovery_args)
    find_bridge(self, true)
  end,
  lifecycle_handlers = {
    added = on_added,
    init = on_init,
    infoChanged = function(self, device)
      if device.device_network_id:match("^msb%-bridge%-") then return end
      local desired = selected_profile(device)
      if device:get_field("ui_profile") ~= desired then
        device:try_update_metadata({ profile = desired })
        device:set_field("ui_profile", desired, { persist = true })
      end
      self:call_with_delay(2, function() refresh(self, device) end)
    end
  },
  capability_handlers = {
    [capabilities.windowShade.ID] = {
      [capabilities.windowShade.commands.open.NAME] = function(self, device)
        set_preset(self, device, "onPosition", 50)
      end,
      [capabilities.windowShade.commands.close.NAME] = function(self, device)
        set_preset(self, device, "offPosition", 0)
      end
    },
    [capabilities.windowShadeLevel.ID] = {
      [capabilities.windowShadeLevel.commands.setShadeLevel.NAME] = set_percent
    },
    [capabilities.switch.ID] = {
      [capabilities.switch.commands.on.NAME] = function(self, device)
        set_preset(self, device, "onPosition", 50)
      end,
      [capabilities.switch.commands.off.NAME] = function(self, device)
        set_preset(self, device, "offPosition", 0)
      end
    },
    [control.ID] = {
      [control.commands.setPosition.NAME] = set_position
    },
    [tilt.ID] = {
      [tilt.commands.setPosition.NAME] = set_position
    },
    [capabilities.refresh.ID] = {
      [capabilities.refresh.commands.refresh.NAME] = fresh_read
    }
  }
})

driver:call_on_schedule(60, function()
  for _, device in ipairs(driver:get_devices()) do
    if not device.device_network_id:match("^msb%-bridge%-") then refresh(driver, device) end
  end
end)
driver:call_on_schedule(300, function() find_bridge(driver, false) end)
driver:run()
