"""Exercise the real Lua driver with mocked LAN/BLE readings (requires lupa)."""
from pathlib import Path
import unittest
from lupa import LuaRuntime

HERE = Path(__file__).resolve().parent

STUBS = r'''
events, fields, timers, reading, event_metadata, warnings = {}, {}, {}, {}, {}, {}
online_calls, offline_calls = 0, 0
created, discovery_results, response_by_url = {}, {found={}}, {}
scheduled, fail_get, fail_url = {}, false, nil
requests, fail_post_remaining, post_status = {}, 0, 202
socket_timeouts = {}
wire_sends, created_sockets = {}, {}
motor_mock, movement_posts, fresh_posts, command_id = false, 0, 0, 0
preferences = {bridgeToken=string.rep("x", 32)}
local function capability(id)
  local c = {ID=id, commands=setmetatable({}, {__index=function(t,k)
    local v={NAME=k}; rawset(t,k,v); return v end})}
  return setmetatable(c, {__index=function(t,k)
    return function(value, metadata)
      return {cap=id, attr=k, value=type(value)=="table" and (value.value or value) or value,
        metadata=metadata}
    end end})
end
package.preload["st.capabilities"]=function()
  return setmetatable({}, {__index=function(t,k)
    local c=capability(k); rawset(t,k,c); return c end}) end
package.preload["log"]=function() return {warn=function(message) warnings[#warnings+1]=message end} end
package.preload["st.mdns"]=function() return {discover=function() return discovery_results end} end
package.preload["st.json"]=function() return {decode=function()
  return response_by_url[last_url] or reading end} end
package.preload["ltn12"]=function() return {sink={table=function(t)
  return function(s) t[#t+1]=s; return 1 end end}} end
package.preload["cosock"]=function() return {socket={tcp=function()
  local socket={
  settimeout=function(_, seconds)
    socket_timeouts[#socket_timeouts+1]=seconds
    return 1
  end,
  send=function(_, data, first, last)
    wire_sends[#wire_sends+1]=data:sub(first or 1,last or -1)
    if send_error then return nil,send_error,send_partial or 0 end
    return last or #data
  end}
  created_sockets[#created_sockets+1]=socket
  return socket end}, asyncify=function()
  return {request=function(r) last_url=r.url; last_method=r.method;
    requests[#requests+1]={url=r.url,method=r.method}
    assert(r.create)
    local socket=r.create()
    -- Model LuaSocket overriding the create callback's timeout. Deliberately
    -- use a different value so the configured timeout must remain effective.
    socket:settimeout(5)
    local path=r.url:match("http://[^/]+(/.*)")
    local line=r.method.." "..path.." HTTP/1.1\r\n"
    assert(socket:send(line)==#line)
    local headers="Host: 127.0.0.1\r\n"
    for k,v in pairs(r.headers) do headers=headers..k..": "..v.."\r\n" end
    headers=headers.."\r\n"
    local sent,err=socket:send(headers)
    if not sent then return nil,err end
    assert(sent==#headers)
    if r.method=="POST" and r.url:match("/position%?") then
      if during_post then local callback=during_post; during_post=nil; callback() end
      if fail_post_remaining>0 then
        fail_post_remaining=fail_post_remaining-1
        return nil,"timeout"
      end
      if motor_mock and post_status==202 then
        movement_posts=movement_posts+1; command_id=command_id+1
        response_by_url[r.url]={commandId=command_id}
        reading.lastCommandId=command_id
        reading.lastCommandPosition=tonumber(r.url:match("position=(%d+)$"))
        reading.lastCommandWriteOk=true; reading.queuedPosition=-1
        r.sink("json")
      end
      return 1,post_status
    end
    if motor_mock and r.method=="POST" and r.url:match("/refresh$") then
      fresh_posts=fresh_posts+1
      if during_fresh then during_fresh(fresh_posts) end
      if fresh_status then return 1,fresh_status end
      response_by_url[r.url]={pollCount=reading.pollCount}
      if fresh_success~=false then
        reading.pollCount=reading.pollCount+1
        reading.lastPollOk=fresh_result_ok~=false
        reading.positionAgeSeconds=fresh_age or 0
      end
      r.sink("json")
    end
    if (fail_get and r.method=="GET" and not r.url:match("/v1/info$")) or
        r.url==fail_url then
      return nil,"timeout"
    end
    if r.method=="GET" then r.sink("json") end
    return 1,r.method=="POST" and 202 or 200 end} end} end
package.preload["st.driver"]=function() return function(_, config)
  driver=config
  function driver:call_with_delay(_, f) timers[#timers+1]=f end
  function driver:call_on_schedule(interval, callback) scheduled[interval]=callback end
  function driver:get_devices() return devices end
  function driver:try_create_device(metadata) created[#created+1]=metadata end
  function driver:run() end
  return driver end end
device={preferences=preferences, device_network_id="test", profile={components={batteryTemperature={id="batteryTemperature"}}}}
devices={device}
function device:get_field(k) return fields[k] end
function device:set_field(k,v) fields[k]=v end
function device:get_latest_state(component,cap,attr) return events[component..":"..cap..":"..attr] end
function device:supports_capability() return true end
function device:emit_event(e)
  events["main:"..e.cap..":"..e.attr]=e.value
  event_metadata["main:"..e.cap..":"..e.attr]=e.metadata
end
function device:emit_component_event(c,e) events[c.id..":"..e.cap..":"..e.attr]=e.value end
function device:try_update_metadata(m) chosen_profile=m.profile end
function device:online() online_calls=online_calls+1 end
function device:offline() offline_calls=offline_calls+1 end
fields.bridge_ip,fields.bridge_port,fields.blind_id="127.0.0.1",8080,"office-window"
function next_timer() local f=table.remove(timers,1); if f then f() end end
function command(cap,name,args) driver.capability_handlers[cap][name](driver,device,{args=args or {}}) end
function refresh() command("refresh","refresh") end
function setup_motor(retries)
  motor_mock=true; preferences.openCloseRetries=retries
  reading.position=30; reading.positionAgeSeconds=0; reading.pollCount=20
  reading.lastPollOk=true; reading.bleStage="idle"
end
function drain_timers()
  local count=0
  while #timers>0 do
    count=count+1; assert(count<2000, "unbounded command checks")
    next_timer()
  end
end
'''


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(STUBS)
        interpretation = self.lua.execute((HERE / "src/interpretation.lua").read_text(encoding="utf-8"))
        self.lua.globals().package.loaded.interpretation = interpretation
        self.lua.execute((HERE / "src/init.lua").read_text(encoding="utf-8").replace("__NAMESPACE__", "test"))
        self.lua.execute('reading={online=true,position=200,queuedPosition=-1,haveSensors=true,haveStatus=true,'
                         'batteryPercent=100,solarCharging=false,usbCharging=false,raw={interiorTemperature=277,batteryTemperature=337,light=379}}')

    def run_lua(self, source):
        self.lua.execute(source)

    def test_mapping_and_presets(self):
        self.run_lua('''
          local m=require "interpretation"
          for p=0,100 do assert(m.to_percent(m.to_native(p))==p) end
          assert(m.to_native(0)==200 and m.to_native(50)==100 and m.to_native(100)==0)
          assert(m.to_native(-1)==nil and m.to_native(101)==nil and m.to_native(2.5)==nil)
          command("switch","on"); assert(last_url:match("position=100$"))
          command("switch","off"); assert(last_url:match("position=200$"))
          preferences.onPosition=25; preferences.offPosition=15
          command("switch","on"); assert(last_url:match("position=150$"))
          command("switch","off"); assert(last_url:match("position=170$"))
          command("windowShade","open"); assert(last_url:match("position=150$"))
          command("windowShade","close"); assert(last_url:match("position=170$"))
          assert(m.shade_state(25,preferences)=="open")
          assert(m.shade_state(15,preferences)=="closed")
          assert(m.shade_state(35,preferences)=="partially open")
          assert(m.shade_state(100,preferences)=="closed")
        ''')

    def test_http_timeout_remains_60_seconds_after_library_override(self):
        self.run_lua('''
          command("windowShade","open")
          scheduled[60]()
          assert(#socket_timeouts>=4)
          for _, seconds in ipairs(socket_timeouts) do assert(seconds==60) end
        ''')

    def test_http_line_and_authentication_headers_use_one_write(self):
        self.run_lua('''
          command("windowShade","open")
          assert(#wire_sends==1)
          assert(wire_sends[1]:match("^POST /v1/blinds/office%-window/position%?position=100 HTTP/1%.1\\r\\n"))
          assert(wire_sends[1]:find("X-MSB-Token: "..string.rep("x",32),1,true))
          assert(wire_sends[1]:sub(-4)=="\\r\\n\\r\\n")
          scheduled[60]()
          assert(#wire_sends==2 and wire_sends[2]:match("^GET "))
        ''')

    def test_coalesced_send_preserves_failure_and_partial_byte_count(self):
        self.run_lua('''
          scheduled[60]()
          local socket=created_sockets[1]
          local line="GET /v1/info HTTP/1.1\\r\\n"
          send_error="closed";send_partial=#line+7
          assert(socket:send(line)==#line)
          local sent,err,partial=socket:send("Host: 127.0.0.1\\r\\n\\r\\n")
          assert(sent==nil and err=="closed" and partial==7)
          send_error=nil
          assert(socket:send("body")==4)
        ''')

    def test_light_meter_range_follows_preferences_above_1000(self):
        self.run_lua('''
          reading.raw.light=2500
          preferences.lightGraphMax=3000
          scheduled[60]()
          assert(events["main:test.mysmartblindsLightMeter:lightRange"].maximum==3000)
          assert(events["main:test.mysmartblindsLightMeter:lightLevel"]==2500)
          assert(events["main:test.mysmartblindsLightLevel:lightLevel"]==2500)
          preferences.lightGraphMax=5000
          scheduled[60]()
          assert(events["main:test.mysmartblindsLightMeter:lightRange"].maximum==5000)
          assert(events["main:test.mysmartblindsLightMeter:lightLevel"]==2500)
          reading.raw.light=6000
          scheduled[60]()
          assert(events["main:test.mysmartblindsLightMeter:lightRange"].maximum==6000)
          assert(events["main:test.mysmartblindsLightMeter:lightLevel"]==6000)
        ''')

    def test_closed_tolerance_reports_state_without_changing_position(self):
        self.run_lua('''
          local m=require "interpretation"
          preferences.offPosition=15; preferences.onPosition=65
          for _, percent in ipairs({12,15,18}) do
            reading.position=m.to_native(percent)
            scheduled[60]()
            assert(events["main:windowShade:windowShade"]=="closed")
            assert(events["main:switch:switch"]=="off")
            assert(events["main:windowShadeLevel:shadeLevel"]==percent)
            assert(reading.position==m.to_native(percent))
          end
          for _, percent in ipairs({11,19}) do
            reading.position=m.to_native(percent)
            scheduled[60]()
            assert(events["main:windowShade:windowShade"]=="partially open")
            assert(events["main:switch:switch"]=="on")
          end
          preferences.offPosition=0
          for _, percent in ipairs({0,3,97,100}) do
            assert(m.shade_state(percent,preferences)=="closed")
          end
          for _, percent in ipairs({4,96}) do
            assert(m.shade_state(percent,preferences)=="partially open")
          end
          assert(m.shade_state(65,preferences)=="open")
          assert(m.shade_state(67,preferences)=="partially open")
          preferences.offPosition=100
          assert(m.switch_state(97,preferences)=="off")
          assert(m.switch_state(96,preferences)=="on")
        ''')

    def test_acknowledgement_readback_and_timeout(self):
        self.run_lua('''
          command("windowShadeLevel","setShadeLevel",{shadeLevel=75})
          assert(last_url:match("position=50$"))
          assert(events["main:windowShadeLevel:shadeLevel"]==75)
          scheduled[60](); assert(events["main:windowShadeLevel:shadeLevel"]==75)
          reading.position=51; reading.lastCommandWriteOk=true; reading.lastCommandPosition=50
          next_timer(); assert(fields.pending_native_position==nil)
          assert(events["main:windowShadeLevel:shadeLevel"]==75)
          scheduled[60](); assert(events["main:windowShadeLevel:shadeLevel"]==75)
          command("windowShadeLevel","setShadeLevel",{shadeLevel=90})
          reading.position=200; reading.lastCommandWriteOk=false
          for i=1,60 do next_timer() end
          assert(fields.pending_native_position==nil)
          assert(events["main:windowShadeLevel:shadeLevel"]==0)
        ''')

    def test_transient_command_timeout_retries_same_address(self):
        self.run_lua('''
          fail_post_remaining=1
          command("windowShade","open")
          assert(fields.pending_native_position==nil)
          assert(events["main:windowShade:windowShade"]==nil)
          next_timer()
          assert(fields.pending_native_position==100)
          assert(events["main:windowShade:windowShade"]=="opening")
          local count=0
          for _,r in ipairs(requests) do
            if r.method=="POST" then
              assert(r.url=="http://127.0.0.1:8080/v1/blinds/office-window/position?position=100")
              count=count+1
            end
          end
          assert(count==2)
        ''')

    def test_preset_retries_require_fresh_miss_and_stop_at_configured_limit(self):
        self.run_lua('''
          setup_motor(3)
          command("windowShade","open")
          for i=1,14 do next_timer() end
          assert(movement_posts==1 and fresh_posts==0)
          next_timer()
          assert(movement_posts==1 and fresh_posts==1)
          next_timer()
          assert(movement_posts==2)
          drain_timers()
          assert(movement_posts==4 and fresh_posts==3)
          assert(fields.pending_native_position==nil)
          assert(fields.position_verification==nil)
          assert(event_metadata["main:windowShadeLevel:shadeLevel"].state_change==true)
          assert(events["main:windowShade:windowShade"]=="partially open")
        ''')

    def test_preset_retry_limit_supports_fifteen_extra_attempts(self):
        self.run_lua('''
          setup_motor(15)
          command("windowShade","open"); drain_timers()
          assert(movement_posts==16 and fresh_posts==15)
          reading.position=50
          for i=1,5 do scheduled[60]() end
          assert(movement_posts==16 and #timers==0)
        ''')

    def test_success_stops_retries_even_if_position_changes_later(self):
        self.run_lua('''
          setup_motor(15)
          command("windowShade","open")
          reading.position=100
          next_timer()
          assert(fields.pending_native_position==nil and #timers==0)
          reading.position=30
          scheduled[60](); drain_timers()
          assert(movement_posts==1 and fresh_posts==0)
        ''')

    def test_background_confirmation_latches_before_later_manual_change(self):
        self.run_lua('''
          setup_motor(3)
          command("windowShade","open")
          reading.position=100; scheduled[60]()
          reading.position=30; drain_timers()
          assert(movement_posts==1 and fresh_posts==0)
        ''')

    def test_success_on_retry_stops_remaining_attempts(self):
        self.run_lua('''
          setup_motor(3)
          command("windowShade","open")
          for i=1,16 do next_timer() end
          assert(movement_posts==2)
          reading.position=100; next_timer()
          assert(#timers==0 and movement_posts==2 and fresh_posts==1)
        ''')

    def test_endpoint_confirmation_accepts_exactly_three_percent_both_directions(self):
        self.run_lua('''
          setup_motor(3)
          for _, endpoint in ipairs({0,100}) do
            preferences.offPosition=endpoint
            command("windowShade","close")
            reading.position=endpoint==0 and 194 or 6
            next_timer()
            assert(#timers==0 and fields.pending_native_position==nil)
            assert(events["main:windowShade:windowShade"]=="closed")
            assert(events["main:windowShadeLevel:shadeLevel"]==(endpoint==0 and 3 or 97))
          end
          assert(movement_posts==2 and fresh_posts==0)
        ''')

    def test_endpoint_tolerance_does_not_accept_rounded_three_point_five_percent(self):
        self.run_lua('''
          setup_motor(1); preferences.offPosition=100
          command("windowShade","close"); reading.position=7
          drain_timers()
          assert(movement_posts==2 and fresh_posts==1)
        ''')

    def test_slider_commands_never_receive_mechanical_retries(self):
        self.run_lua('''
          setup_motor(15)
          command("windowShadeLevel","setShadeLevel",{shadeLevel=60})
          drain_timers()
          command("test.mysmartblindsTiltControl","setPosition",{position=80})
          drain_timers()
          assert(movement_posts==2 and fresh_posts==0)
        ''')

    def test_disabled_setting_never_retries_short_preset(self):
        self.run_lua('''
          setup_motor(0)
          command("windowShade","open"); drain_timers()
          assert(movement_posts==1 and fresh_posts==0)
        ''')

    def test_missing_or_stale_command_identity_cannot_trigger_retry(self):
        self.run_lua('''
          setup_motor(3)
          command("windowShade","open")
          reading.lastCommandId=0; reading.position=100
          next_timer()
          assert(fields.pending_native_position==100)
          reading.position=30; drain_timers()
          assert(movement_posts==1 and fresh_posts==0)
        ''')

    def test_failed_or_stale_reads_use_full_configured_movement_limit(self):
        for extra in ("fresh_success=false", "fresh_result_ok=false", "fresh_age=60"):
            with self.subTest(extra=extra):
                self.setUp()
                self.run_lua('''
                  setup_motor(3)
                  ''' + extra + '''
                  command("windowShade","open"); drain_timers()
                  assert(movement_posts==4 and fresh_posts==9)
                  assert(fields.pending_native_position==nil)
                  assert(fields.position_verification==nil and #timers==0)
                  local repeats=0
                  for _,message in ipairs(warnings) do
                    if message:find("retrying target without fresh position",1,true) then repeats=repeats+1 end
                  end
                  assert(repeats==3)
                  local final=warnings[#warnings]
                  assert(final:find("Position was not confirmed; configured retry limit reached",1,true))
                  assert(final:find("4 accepted movement requests, 3/3 extra attempts used",1,true))
                ''')

    def test_position_read_recovers_before_repeating_movement(self):
        self.run_lua('''
          setup_motor(3); fresh_result_ok=false
          during_fresh=function(count)
            if count==2 then fresh_result_ok=true; reading.position=100 end
          end
          command("windowShade","open"); drain_timers()
          assert(movement_posts==1 and fresh_posts==2)
          assert(fields.pending_native_position==nil)
          assert(events["main:windowShade:windowShade"]=="open")
        ''')

    def test_unverified_retry_can_reach_target_and_stop(self):
        self.run_lua('''
          setup_motor(15); fresh_result_ok=false
          command("windowShade","open")
          for i=1,100 do
            if movement_posts==2 then break end
            next_timer()
          end
          assert(movement_posts==2 and fresh_posts==3)
          reading.position=100; reading.positionAgeSeconds=0
          next_timer(); drain_timers()
          assert(movement_posts==2 and fields.pending_native_position==nil)
          reading.position=30; scheduled[60](); drain_timers()
          assert(movement_posts==2)
        ''')

    def test_unverified_repeat_consumes_existing_limit(self):
        self.run_lua('''
          setup_motor(1); fresh_result_ok=false
          command("windowShade","open"); drain_timers()
          assert(movement_posts==2 and fresh_posts==3)
        ''')

    def test_persistent_missing_feedback_uses_exactly_fifteen_extra_attempts(self):
        for extra in ('fresh_result_ok=false', 'fresh_success=false', 'fresh_age=60',
                      'fresh_status=503'):
            with self.subTest(extra=extra):
                self.setUp()
                self.run_lua('''
                  setup_motor(15)
                  ''' + extra + '''
                  command("windowShade","open"); drain_timers()
                  assert(movement_posts==16 and fresh_posts==45 and #timers==0)
                  assert(fields.pending_native_position==nil and fields.position_verification==nil)
                  assert(events["main:windowShade:windowShade"]=="partially open")
                  local final=warnings[#warnings]
                  assert(final:find("Position was not confirmed; configured retry limit reached",1,true))
                  assert(final:find("16 accepted movement requests, 15/15 extra attempts used",1,true))
                  for _,request in ipairs(requests) do
                    if request.url:match("/position%?") then assert(request.url:match("position=100$")) end
                  end
                  scheduled[60](); drain_timers()
                  assert(movement_posts==16)
                ''')

    def test_busy_or_queued_worker_cannot_trigger_unverified_repeat(self):
        for extra in ('reading.bleStage="connect"', 'reading.queuedPosition=100'):
            with self.subTest(extra=extra):
                self.setUp()
                self.run_lua('''
                  setup_motor(3); fresh_result_ok=false
                  command("windowShade","open")
                  for i=1,15 do next_timer() end
                  ''' + extra + '''
                  drain_timers()
                  assert(movement_posts==1 and fields.pending_native_position==nil)
                ''')

    def test_busy_fresh_read_waits_for_idle_before_retrying(self):
        self.run_lua('''
          setup_motor(3)
          command("windowShade","open")
          for i=1,15 do next_timer() end
          reading.bleStage="read"; next_timer()
          assert(movement_posts==1 and fresh_posts==1)
          reading.bleStage="idle"; reading.position=100; next_timer()
          assert(movement_posts==1 and #timers==0)
        ''')

    def test_new_command_cancels_read_recovery_delay(self):
        self.run_lua('''
          setup_motor(3); fresh_result_ok=false
          command("windowShade","open")
          for i=1,16 do next_timer() end
          command("windowShadeLevel","setShadeLevel",{shadeLevel=60})
          reading.position=80; drain_timers()
          assert(movement_posts==2 and fresh_posts==1)
          assert(reading.lastCommandPosition==80)
        ''')

    def test_recovery_cancels_when_disabled_or_superseded_or_delivery_unknown(self):
        for extra in ('preferences.openCloseRetries=0', 'reading.lastCommandId=2',
                      'reading.lastCommandWriteOk=false', 'reading.lastCommandId=0'):
            with self.subTest(extra=extra):
                self.setUp()
                self.run_lua('''
                  setup_motor(3); fresh_result_ok=false
                  command("windowShade","open")
                  for i=1,16 do next_timer() end
                  ''' + extra + '''
                  drain_timers()
                  assert(movement_posts==1 and fresh_posts==1)
                ''')

    def test_unavailable_bridge_during_recovery_cannot_trigger_movement(self):
        self.run_lua('''
          setup_motor(3); fresh_result_ok=false
          command("windowShade","open")
          for i=1,16 do next_timer() end
          fail_get=true; drain_timers()
          assert(movement_posts==1 and fields.pending_native_position==nil)
        ''')

    def test_rejected_refresh_cannot_trigger_unverified_movement(self):
        self.run_lua('''
          setup_motor(3); fresh_status=401
          command("windowShade","open"); drain_timers()
          assert(movement_posts==1 and fresh_posts==1)
        ''')

    def test_transient_refresh_failure_recovers_to_fresh_position(self):
        self.run_lua('''
          setup_motor(3); fresh_status=503
          during_fresh=function(count)
            if count==2 then fresh_status=nil; reading.position=100 end
          end
          command("windowShade","open"); drain_timers()
          assert(movement_posts==1 and fresh_posts==2)
          assert(fields.pending_native_position==nil)
        ''')

    def test_recovered_missed_position_can_use_remaining_verified_retries(self):
        self.run_lua('''
          setup_motor(3); fresh_result_ok=false
          during_fresh=function(count)
            if count==4 then fresh_result_ok=true end
          end
          command("windowShade","open"); drain_timers()
          assert(movement_posts==4 and fresh_posts==5)
        ''')

    def test_verified_and_unverified_retries_share_full_limit(self):
        self.run_lua('''
          setup_motor(15); fresh_result_ok=false
          during_fresh=function(count) fresh_result_ok=count==4 end
          command("windowShade","open"); drain_timers()
          assert(movement_posts==16 and fresh_posts==43 and #timers==0)
        ''')

    def test_no_position_at_all_still_uses_configured_retries(self):
        self.run_lua('''
          setup_motor(3); reading.position=nil; fresh_success=false
          command("windowShade","open"); drain_timers()
          assert(movement_posts==4 and fresh_posts==9 and #timers==0)
          assert(fields.pending_native_position==nil)
          assert(warnings[#warnings]:find("Position was not confirmed",1,true))
          assert(events["main:windowShade:windowShade"]~="open")
        ''')

    def test_close_and_legacy_aliases_use_full_budget_without_feedback(self):
        for cap, name, expected in [('windowShade', 'close', 200), ('switch', 'off', 200),
                                    ('switch', 'on', 100)]:
            with self.subTest(cap=cap, name=name):
                self.setUp()
                self.run_lua('''
                  setup_motor(3); fresh_success=false
                  command("''' + cap + '","' + name + '''"); drain_timers()
                  assert(movement_posts==4 and fresh_posts==9 and #timers==0)
                  assert(reading.lastCommandPosition==''' + str(expected) + ''')
                ''')

    def test_success_after_multiple_unverified_retries_stops_remaining_budget(self):
        self.run_lua('''
          setup_motor(15); fresh_result_ok=false
          command("windowShade","open")
          for i=1,200 do
            if movement_posts==4 then break end
            next_timer()
          end
          assert(movement_posts==4)
          reading.position=100; reading.positionAgeSeconds=0
          scheduled[60](); drain_timers()
          assert(movement_posts==4 and fields.pending_native_position==nil)
          reading.position=30; scheduled[60](); drain_timers()
          assert(movement_posts==4)
        ''')

    def test_new_command_after_multiple_unverified_retries_cancels_old_budget(self):
        self.run_lua('''
          setup_motor(15); fresh_result_ok=false
          command("windowShade","open")
          for i=1,200 do
            if movement_posts==4 then break end
            next_timer()
          end
          assert(movement_posts==4)
          command("windowShadeLevel","setShadeLevel",{shadeLevel=60})
          reading.position=80; reading.positionAgeSeconds=0
          drain_timers()
          assert(movement_posts==5 and reading.lastCommandPosition==80)
        ''')

    def test_new_command_during_read_request_cancels_old_recovery(self):
        self.run_lua('''
          setup_motor(3); fresh_result_ok=false
          during_fresh=function()
            during_fresh=nil
            command("windowShade","close"); reading.position=200
          end
          command("windowShade","open"); drain_timers()
          assert(movement_posts==2 and fresh_posts==1)
          assert(reading.lastCommandPosition==200 and fields.pending_native_position==nil)
        ''')

    def test_new_command_cancels_waiting_mechanical_retry(self):
        self.run_lua('''
          setup_motor(3)
          command("windowShade","open")
          for i=1,15 do next_timer() end
          command("windowShade","close"); reading.position=200
          drain_timers()
          assert(movement_posts==2 and command_id==2)
          assert(reading.lastCommandPosition==200)
        ''')

    def test_external_newer_command_stops_old_retry(self):
        self.run_lua('''
          setup_motor(3)
          command("windowShade","open")
          reading.lastCommandId=2
          drain_timers()
          assert(movement_posts==1 and fresh_posts==0)
        ''')

    def test_turning_retries_off_cancels_remaining_attempts(self):
        self.run_lua('''
          setup_motor(3)
          command("windowShade","open")
          for i=1,15 do next_timer() end
          preferences.openCloseRetries=0
          drain_timers()
          assert(movement_posts==1)
        ''')

    def test_command_delivery_retries_are_bounded(self):
        self.run_lua('''
          fail_post_remaining=10
          command("windowShade","open")
          for i=1,5 do next_timer() end
          assert(#timers==0)
          assert(fields.pending_native_position==nil)
          assert(events["main:windowShade:windowShade"]==nil)
          local count=0
          for _,r in ipairs(requests) do if r.method=="POST" then count=count+1 end end
          assert(count==3)
        ''')

    def test_new_close_cancels_retry_of_failed_open(self):
        self.run_lua('''
          fail_post_remaining=1
          command("windowShade","open")
          command("windowShade","close")
          next_timer()
          assert(fields.pending_native_position==200)
          local posts={}
          for _,r in ipairs(requests) do
            if r.method=="POST" then posts[#posts+1]=r.url end
          end
          assert(#posts==2 and posts[2]:match("position=200$"))
        ''')

    def test_superseded_inflight_command_cannot_acknowledge_old_target(self):
        self.run_lua('''
          during_post=function() command("windowShade","close") end
          command("windowShade","open")
          assert(fields.pending_native_position==200)
          assert(events["main:windowShade:windowShade"]=="closing")
          assert(#timers==1)
        ''')

    def test_authentication_error_does_not_retry(self):
        self.run_lua('''
          post_status=401
          command("windowShade","open")
          assert(#timers==0 and fields.pending_native_position==nil)
          assert(events["main:windowShade:windowShade"]==nil)
        ''')

    def test_busy_bridge_retries_and_recovers(self):
        self.run_lua('''
          post_status=503
          command("windowShade","open")
          assert(fields.pending_native_position==nil)
          post_status=202
          next_timer()
          assert(fields.pending_native_position==100)
        ''')

    def test_failed_manual_poll_keeps_recent_reading_online(self):
        self.run_lua('''
          response_by_url["http://127.0.0.1:8080/v1/blinds/office-window/refresh"]={pollCount=1}
          reading.pollCount=2
          reading.lastPollOk=false
          refresh()
          next_timer()
          assert(online_calls==1)
          assert(offline_calls==0)
        ''')

    def test_power_cycle_recovers_online_status(self):
        self.run_lua('''
          scheduled[60]()
          assert(online_calls==1 and offline_calls==0)
          fail_get=true
          scheduled[60]()
          assert(offline_calls==1)
          fail_get=false
          scheduled[60]()
          assert(online_calls==2)
        ''')

    def test_missing_token_is_offline_then_recovers_when_configured(self):
        self.run_lua('''
          preferences.bridgeToken=""
          scheduled[60]()
          assert(offline_calls==1 and online_calls==0)
          preferences.bridgeToken=string.rep("x",32)
          scheduled[60]()
          assert(online_calls==1)
        ''')

    def test_bridge_reconnects_after_ip_change(self):
        self.run_lua('''
          local id="11111111-1111-1111-1111-111111111111"
          local bridge="AA0000000001"
          device.device_network_id="msb-"..id
          fields.bridge_ip="192.168.1.10"
          fields.bridge_port=8080
          fields.blind_id=id
          fields.bridge_id=bridge
          local setup={device_network_id="msb-bridge-"..bridge,
            preferences={bridgeToken=string.rep("x",32)}}
          function setup:set_field() end
          devices={device,setup}
          fail_url="http://192.168.1.10:8080/v1/blinds/"..id
          discovery_results={found={{host_info={address="192.168.1.11",port=8080}}}}
          response_by_url["http://192.168.1.11:8080/v1/info"]={
            apiVersion=1,bridgeId="AA:00:00:00:00:01",blinds={{id=id,name="A"}}}
          scheduled[60]()
          assert(fields.bridge_ip=="192.168.1.11")
          assert(last_url=="http://192.168.1.11:8080/v1/blinds/"..id)
          assert(online_calls==1 and offline_calls==0)
        ''')

    def test_profile_switch_and_sensor_calibration(self):
        self.run_lua('''
          driver.lifecycle_handlers.init(driver,device)
          assert(chosen_profile=="msb-office-blind.v6")
          next_timer()
          assert(events["main:test.mysmartblindsSensorStatus:chargingSource"]=="none")
          assert(events["main:temperatureMeasurement:temperature"]==nil)
          assert(events["main:illuminanceMeasurement:illuminance"]==nil)
          assert(events["main:test.mysmartblindsTemperatures:ambientDisplay"]=="Needs calibration")
          assert(events["main:test.mysmartblindsTemperatures:batteryDisplay"]=="Needs calibration")
          assert(events["main:test.mysmartblindsLightMeter:lightLevel"]==379)
          assert(events["main:test.mysmartblindsLightMeter:lightRange"].maximum==1000)
          preferences.showDiagnostics=true
          driver.lifecycle_handlers.infoChanged(driver,device)
          assert(chosen_profile=="msb-office-blind.v6-debug")
          preferences.temperatureScale="deciC"; preferences.interiorOffset=-1
          preferences.luxPerCount=2; preferences.lightDarkOffset=100
          next_timer()
          assert(events["main:temperatureMeasurement:temperature"]==26.7)
          assert(events["main:test.mysmartblindsTemperatures:batteryTemperature"]==33.7)
          assert(events["main:test.mysmartblindsTemperatures:ambientDisplay"]=="80.1 °F")
          preferences.temperatureUnit="C"; preferences.lightGraphMax=100; scheduled[60]()
          assert(events["main:test.mysmartblindsTemperatures:ambientDisplay"]=="26.7 °C")
          assert(events["main:test.mysmartblindsLightMeter:lightRange"].maximum==379)
          preferences.temperatureScale="deciCTrial"; preferences.interiorOffset=-0.422222
          preferences.temperatureUnit="F"; preferences.lightGraphBaseline=379
          preferences.lightGraphMax=1000; scheduled[60]()
          assert(events["main:test.mysmartblindsTemperatures:ambientDisplay"]=="81.1 °F")
          assert(events["main:test.mysmartblindsTemperatures:batteryDisplay"]=="92.7 °F")
          assert(events["main:test.mysmartblindsLightMeter:lightRange"].minimum==379)
          local m=require "interpretation"
          assert(m.light_range(300,1000,379).minimum==300)
          assert(m.light_range(379,100,379).maximum==380)
          assert(events["main:illuminanceMeasurement:illuminance"]==558)
          preferences.showDiagnostics=false
          driver.lifecycle_handlers.infoChanged(driver,device)
          assert(chosen_profile=="msb-office-blind.v6")
        ''')

    def test_startup_reconciles_slider_without_moving_blind(self):
        self.run_lua('''
          setup_motor(3); reading.position=45
          driver.lifecycle_handlers.init(driver,device)
          next_timer()
          assert(events["main:windowShadeLevel:shadeLevel"]==78)
          assert(event_metadata["main:windowShadeLevel:shadeLevel"].state_change==true)
          assert(events["main:windowShade:windowShade"]=="partially open")
          assert(movement_posts==0 and fresh_posts==0)
        ''')

    def test_two_bridge_discovery_routes_by_uuid_and_detects_overlap(self):
        self.run_lua(r'''
          local a="11111111-1111-1111-1111-111111111111"
          local b="22222222-2222-2222-2222-222222222222"
          discovery_results={found={
            {host_info={address="192.168.1.10",port=8080}},
            {host_info={address="192.168.1.11",port=8080}}
          }}
          response_by_url["http://192.168.1.10:8080/v1/info"]={
            apiVersion=1,bridgeId="AA:00:00:00:00:01",blinds={{id=a,name="A"}}}
          response_by_url["http://192.168.1.11:8080/v1/info"]={
            apiVersion=1,bridgeId="AA:00:00:00:00:02",blinds={{id=b,name="B"}}}
          driver.discovery(driver)
          assert(#created==4)
          local found={}
          for _, item in ipairs(created) do found[item.device_network_id]=item end
          assert(found["msb-"..a].label=="A")
          assert(found["msb-"..b].label=="B")
          assert(found["msb-bridge-AA0000000001"])
          assert(found["msb-bridge-AA0000000002"])
          local setup={device_network_id="msb-bridge-AA0000000002",
            preferences={bridgeToken=string.rep("y",32)}}
          function setup:set_field() end
          devices={device,setup}
          device.device_network_id="msb-"..b
          preferences.bridgeToken=""
          command("windowShadeLevel","setShadeLevel",{shadeLevel=75})
          assert(last_url=="http://192.168.1.11:8080/v1/blinds/"..b.."/position?position=50")
          response_by_url["http://192.168.1.11:8080/v1/info"].blinds={{id=b,name="B"},{id=a,name="A"}}
          created={}
          driver.discovery(driver)
          device.device_network_id="msb-"..a
          command("windowShadeLevel","setShadeLevel",{shadeLevel=50})
          assert(last_url~="http://192.168.1.10:8080/v1/blinds/"..a.."/position?position=100")
        ''')


if __name__ == "__main__":
    unittest.main()
