"""
<plugin key="SAICiSmart" name="SAIC iSmart (MG Cars)" author="lemassykoi" version="1.5.1" wikilink="https://github.com/lemassykoi/Domoticz-MG-Plugin" externallink="https://github.com/SAIC-iSmart-API">
    <description>
        <h2>SAIC iSmart Plugin for MG Cars</h2><br/>
        <p>This plugin connects directly to MG iSmart API to retrieve vehicle data without MQTT dependency.</p>
        <p>Supports MG5, MG4, ZS EV, Marvel R and other SAIC vehicles with iSmart connectivity.</p>
        <br/>
        <p><b>Requirements:</b></p>
        <ul>
            <li>Valid MG iSmart account credentials</li>
            <li>Vehicle registered in MG iSmart app</li>
            <li>Domoticz Notification Subsystem (e.g., Telegram) configured for alerts.</li>
        </ul>
    </description>
    <params>
        <param field="Username" label="SAIC Email Address" width="200px" required="true"/>
        <param field="Password" label="SAIC Password" width="200px" required="true" password="true"/>
        <param field="Mode1" label="Region" width="150px">
            <options>
                <option label="Europe (Default)" value="eu" default="true"/>
                <option label="Australia/New Zealand" value="au"/>
            </options>
        </param>

        <param field="Mode4" label="Home Radius (meters)" width="80px" default="25" required="false"/>
        <param field="Mode3" label="Update Interval (seconds)" width="50px" default="180"/>
        <param field="Port" label="Domoticz Port" width="50px" default="8080" required="true"/>
        <param field="Mode6" label="Debug Level" width="100px">
            <options>
                <option label="Normal" value="Normal" default="true"/>
                <option label="Debug" value="Debug"/>
                <option label="Verbose" value="Verbose"/>
            </options>
        </param>
    </params>
</plugin>
"""

import Domoticz
import threading
import time
import requests
import asyncio
import urllib.parse
import math

# --- Global variables ---
update_thread = None
stop_event = threading.Event()
config = None

# --- Thread Management ---
active_command_threads = []
command_lock = threading.Lock()

# --- Notification State ---
was_charging = False
notification_sent_for_session = False

# --- Sleep Detection ---
consecutive_invalid_data = 0
last_valid_data_time = None

def get_domoticz_home_coordinates():
    """Get home coordinates from Domoticz settings"""
    try:
        params = {"type": "command", "param": "getsettings"}
        data = domoticz_api_call(params, is_utility_call=True)
        if data and "Location" in data:
            lat = float(data["Location"]["Latitude"])
            lon = float(data["Location"]["Longitude"])
            return lat, lon
    except Exception as e:
        Domoticz.Debug(f"Failed to get home coordinates: {e}")
    return None, None

def is_at_home(lat, lon):
    """Check if car is at home based on GPS coordinates and radius"""
    try:
        home_lat, home_lon = get_domoticz_home_coordinates()
        if home_lat is None or home_lon is None:
            return False
        
        home_radius = float(Parameters.get("Mode4", "100"))
        
        # Calculate distance using Haversine formula
        R = 6371000  # Earth's radius in meters
        lat1, lon1 = math.radians(lat), math.radians(lon)
        lat2, lon2 = math.radians(home_lat), math.radians(home_lon)
        
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        distance = R * c
        
        Domoticz.Debug(f"Distance from home: {distance:.1f}m (threshold: {home_radius}m)")
        return distance <= home_radius
    except Exception as e:
        Domoticz.Debug(f"Home detection failed: {e}")
        return False

def onStart():
    """Initialize the plugin when Domoticz starts it"""
    global config, update_thread, stop_event, was_charging, notification_sent_for_session
    
    Domoticz.Log("SAIC iSmart Plugin starting...")
    
    was_charging = False
    notification_sent_for_session = False

    username = Parameters["Username"]
    password = Parameters["Password"]
    region   = Parameters["Mode1"]
    update_interval = int(Parameters.get("Mode3", "180"))
    
    if not username or not password:
        Domoticz.Error("Username and password are required")
        return
        
    debug_level = Parameters.get("Mode6", "Normal")
    if debug_level == "Debug":
        Domoticz.Debugging(1)
    elif debug_level == "Verbose":
        Domoticz.Debugging(2)
        
    try:
        from saic_ismart_client_ng.model import SaicApiConfiguration
        config = SaicApiConfiguration(username=username, password=password, region=region)
        Domoticz.Debug(f"Login config created for: {username}")
    except ImportError as e:
        Domoticz.Error(f"SAIC client library not found: {e}")
        return
    except Exception as e:
        Domoticz.Error(f"Failed to initialize SAIC client: {e}")
        return
    
    update_thread = threading.Thread(target=update_loop, args=(update_interval,))
    update_thread.daemon = True
    update_thread.start()
    Domoticz.Log("SAIC iSmart Plugin started successfully")

def onStop():
    """Stop the plugin and cleanup"""
    global stop_event, update_thread, active_command_threads, command_lock
    Domoticz.Log("SAIC iSmart Plugin stopping...")
    
    # Signal all threads to stop
    stop_event.set()
    
    # Wait for update thread
    if update_thread and update_thread.is_alive():
        update_thread.join(timeout=5)
    
    # Wait for command threads to complete
    with command_lock:
        command_threads_copy = active_command_threads.copy()
    
    for thread in command_threads_copy:
        if thread.is_alive():
            thread.join(timeout=2)
    
    Domoticz.Log("SAIC iSmart Plugin stopped")

def onCommand(Unit, Command, Level, Hue):
    """Handle commands from Domoticz"""
    global active_command_threads, command_lock
    Domoticz.Log(f"Command received - Unit: {Unit}, Command: {Command}, Level: {Level}")
    
    # Clean up finished threads
    with command_lock:
        active_command_threads = [t for t in active_command_threads if t.is_alive()]
    
    # Create and track new command thread
    command_thread = threading.Thread(target=process_command_wrapper, args=(Unit, Command, Level))
    command_thread.daemon = True
    
    with command_lock:
        active_command_threads.append(command_thread)
    
    command_thread.start()

def onHeartbeat():
    pass

def update_loop(update_interval):
    """Main update loop running in separate thread"""
    while not stop_event.is_set():
        try:
            if config is None:
                Domoticz.Error("SAIC configuration not available")
                break
            vehicle_data = get_vehicle_data()
            if vehicle_data:
                if len(Devices) < 20:
                    create_devices(vehicle_data)
                update_devices(vehicle_data)
        except Exception as e:
            Domoticz.Error(f"Error in update loop: {e}")
        stop_event.wait(update_interval)

def get_vehicle_data():
    """Get vehicle data from SAIC API with fresh connection"""
    global config
    if not config:
        return None
    try:
        from saic_ismart_client_ng import SaicApi
        saic_client = SaicApi(config)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            Domoticz.Debug("Creating fresh connection and logging in...")
            loop.run_until_complete(saic_client.login())
            Domoticz.Debug("Fresh login successful")
            
            Domoticz.Debug("Getting vehicle list...")
            vehicle_list_resp = loop.run_until_complete(saic_client.vehicle_list())
            if not vehicle_list_resp or not vehicle_list_resp.vinList:
                Domoticz.Error("No vehicles found")
                return None
            vehicle = vehicle_list_resp.vinList[0]
            Domoticz.Debug(f"Working with vehicle: {vehicle.brandName} {vehicle.modelName}")
            vehicle_status, charging_status = None, None
            try: 
                vehicle_status = loop.run_until_complete(saic_client.get_vehicle_status(vehicle.vin))
                Domoticz.Debug(f"Raw vehicle status: {vehicle_status}")
            except Exception as e:
                Domoticz.Log(f"Could not get vehicle status: {e}")
            
            # Get charging status unless car is truly sleeping (not just extendedData1=-128 while charging)
            should_skip_charging = False
            if vehicle_status and hasattr(vehicle_status, 'basicVehicleStatus'):
                extended_data1 = getattr(vehicle_status.basicVehicleStatus, 'extendedData1', 0)
                # Only skip if multiple indicators suggest deep sleep mode
                multiple_invalid = (
                    extended_data1 == -128 and 
                    vehicle_status.basicVehicleStatus.mileage == -128 and
                    vehicle_status.basicVehicleStatus.exteriorTemperature == -128
                )
                if multiple_invalid:
                    should_skip_charging = True
                    Domoticz.Debug("Car appears in deep sleep mode, skipping charging status call")
                elif extended_data1 == -128:
                    Domoticz.Debug("Car has extendedData1=-128 but may be charging, still getting charging status")
            
            if not should_skip_charging:
                try: 
                    charging_status = loop.run_until_complete(saic_client.get_vehicle_charging_management_data(vehicle.vin))
                    Domoticz.Debug(f"Raw charging status: {charging_status}")
                except Exception as e:
                    Domoticz.Log(f"Could not get charging status: {e}")
            else:
                Domoticz.Debug("Skipped charging status call due to car sleep mode")
            return {"vehicle_status": vehicle_status, "charging_status": charging_status, "vehicle_info": vehicle}
        finally:
            loop.close()
    except Exception as e:
        Domoticz.Error(f"Failed to get vehicle data: {e}")
        return None

def create_devices(vehicle_data):
    """Create Domoticz devices based on vehicle data"""
    try:
        vehicle_info = vehicle_data.get("vehicle_info")
        model = getattr(vehicle_info, 'modelName', 'Vehicle').replace('Electric', '').strip()
        vin_suffix = vehicle_info.vin[-4:] if hasattr(vehicle_info, 'vin') else 'XXXX'
        vehicle_name = f"{model} {vin_suffix}"
        
        Domoticz.Log(f"Creating devices for: {vehicle_name}")
        
        devices_to_create = {
            1: {"Name": f"{vehicle_name} Battery Level", "Type": 243, "Subtype": 6},
            2: {"Name": f"{vehicle_name} Range", "Type": 243, "Subtype": 31, "Options": {'Custom': '1;km'}},
            3: {"Name": f"{vehicle_name} Charging", "Type": 244, "Subtype": 73},
            4: {"Name": f"{vehicle_name} Location", "TypeName": "Text"},
            5: {"Name": f"{vehicle_name} Lock Status", "Type": 244, "Subtype": 73},
            6: {"Name": f"{vehicle_name} Clim Active", "Type": 244, "Subtype": 73},
            7: {"Name": f"{vehicle_name} Start/Stop Charging", "Type": 244, "Subtype": 73},
            8: {"Name": f"{vehicle_name} Charge Limit", "TypeName": "Selector Switch", "Options": {"LevelActions": "||||||||", "LevelNames": "0%|40%|50%|60%|70%|80%|90%|100%", "LevelOffHidden": "true", "SelectorStyle": "0"}},
            9: {"Name": f"{vehicle_name} Charge Current Limit", "TypeName": "Selector Switch", "Options": {"LevelActions": "|||||", "LevelNames": "0A|6A|8A|16A|MAX", "LevelOffHidden": "true", "SelectorStyle": "0"}},
            10: {"Name": f"{vehicle_name} Lock Control", "Type": 244, "Subtype": 73},
            11: {"Name": f"{vehicle_name} Cable Connected", "Type": 244, "Subtype": 73},
            12: {"Name": f"{vehicle_name} Odometer", "Type": 113, "Subtype": 0, "Switchtype": 3, "Options": {"ValueQuantity": "Custom", "ValueUnits": "km"}},
            14: {"Name": f"{vehicle_name} Max Range", "Type": 243, "Subtype": 31, "Options": {'Custom': '1;km'}, "Used": 0},
            15: {"Name": f"{vehicle_name} Charging Power", "Type": 243, "Subtype": 29},
            16: {"Name": f"{vehicle_name} Battery Capacity", "Type": 243, "Subtype": 31, "Options": {'Custom': '1;kWh'}},
            17: {"Name": f"{vehicle_name} Address", "TypeName": "Text"},
            18: {"Name": f"{vehicle_name} Speed", "Type": 243, "Subtype": 31, "Options": {'Custom': '1;km/h'}},
            19: {"Name": f"{vehicle_name} Power Usage Today", "Type": 113, "Subtype": 0, "Switchtype": 0},
            20: {"Name": f"{vehicle_name} Heated Seat Left", "TypeName": "Selector Switch", "Options": {"LevelActions": "|||", "LevelNames": "Off|Low|Medium|High", "LevelOffHidden": "false", "SelectorStyle": "0"}},
            21: {"Name": f"{vehicle_name} Heated Seat Right", "TypeName": "Selector Switch", "Options": {"LevelActions": "|||", "LevelNames": "Off|Low|Medium|High", "LevelOffHidden": "false", "SelectorStyle": "0"}},
            22: {"Name": f"{vehicle_name} 12V Battery", "Type": 243, "Subtype": 8},
            25: {"Name": f"{vehicle_name} Tyre FL", "Type": 243, "Subtype": 9},
            27: {"Name": f"{vehicle_name} Tyre FR", "Type": 243, "Subtype": 9},
            28: {"Name": f"{vehicle_name} Tyre RL", "Type": 243, "Subtype": 9},
            29: {"Name": f"{vehicle_name} Tyre RR", "Type": 243, "Subtype": 9},
            30: {"Name": f"{vehicle_name} Time to Full", "Type": 243, "Subtype": 31, "Image": 21, "Options": {'Custom': '1;min'}},
            31: {"Name": f"{vehicle_name} Engine Status", "Type": 244, "Subtype": 73},
            32: {"Name": f"{vehicle_name} Hand Brake", "Type": 244, "Subtype": 73},
            33: {"Name": f"{vehicle_name} Exterior Temp.", "Type": 80, "Subtype": 5},
            34: {"Name": f"{vehicle_name} Interior Temp.", "Type": 80, "Subtype": 5},
            35: {"Name": f"{vehicle_name} Status", "Type": 244, "Subtype": 73},
            36: {"Name": f"{vehicle_name} Car at Home", "Type": 244, "Subtype": 73}
        }

        for unit, params in devices_to_create.items():
            if unit not in Devices: 
                device_params = params.copy()
                device_params.setdefault("Used", 1) ## you have to manually add used = 0 for unwanted devices
                Domoticz.Device(Unit=unit, **device_params).Create()
        
        Domoticz.Log("Devices created successfully")

        # Create and assign devices to room plan
        try:
            vin_suffix = vehicle_info.vin[-4:] if (vehicle_info and hasattr(vehicle_info, 'vin')) else 'XXXX'
            plan_name = f"MG-{vin_suffix}"
            plan_idx = get_room_plan_idx(plan_name)
            
            if plan_idx:
                # Add all devices to the room plan
                for unit in devices_to_create.keys():
                    if unit in Devices:
                        device_idx = Devices[unit].ID
                        add_device_to_plan(device_idx, plan_idx)
                        
                Domoticz.Log(f"All devices added to room plan '{plan_name}'")
        except Exception as e:
            Domoticz.Error(f"Failed to create room plan: {e}")
        
    except Exception as e:
        Domoticz.Error(f"Failed to create devices: {e}")

def update_devices(vehicle_data):
    """Update device values with latest vehicle data"""
    global was_charging, notification_sent_for_session, consecutive_invalid_data, last_valid_data_time
    try:
        vehicle_status = vehicle_data.get("vehicle_status")
        charging_status = vehicle_data.get("charging_status")
        if not vehicle_status and not charging_status:
            return

        # --- Extract key metrics ---
        is_charging, soc_percent, charge_limit_percent = False, 0, 100
        if charging_status:
            if hasattr(charging_status, 'rvsChargeStatus'):
                if hasattr(charging_status, 'chrgMgmtData'):
                    bms_status = getattr(charging_status.chrgMgmtData, 'bmsChrgSts', 0)
                else:
                    bms_status = 0
                is_charging = (bms_status == 1) and (getattr(charging_status.rvsChargeStatus, 'chargingGunState', 0) == 1)
            if hasattr(charging_status, 'chrgMgmtData'):
                raw_soc = getattr(charging_status.chrgMgmtData, 'bmsPackSOCDsp', 0)
                # Validate SoC (ignore invalid sentinel values like 1023)
                if 0 <= raw_soc <= 1000:
                    soc_percent = raw_soc / 10.0
                limit_code = getattr(charging_status.chrgMgmtData, 'bmsOnBdChrgTrgtSOCDspCmd', 7)
                limit_map = {0: 0, 1: 40, 2: 50, 3: 60, 4: 70, 5: 80, 6: 90, 7: 100}
                charge_limit_percent = limit_map.get(limit_code, 0)

        # --- Notification Logic ---
        if not was_charging and is_charging:
            notification_sent_for_session = False
        if was_charging and not is_charging:
            send_notification(f"MG Charging: Stopped. SoC is {soc_percent:.1f}%.")
        if is_charging and not notification_sent_for_session and soc_percent >= charge_limit_percent:
            send_notification(f"MG Charging: Target of {charge_limit_percent}% reached (SoC: {soc_percent:.1f}%).")
            notification_sent_for_session = True
        was_charging = is_charging

        # --- Update Devices ---
        # Check if car is sleeping to avoid updating SoC with invalid 0% values
        car_sleeping = False
        if bvs and hasattr(bvs, 'extendedData1'):
            car_sleeping = bvs.extendedData1 == -128
        
        if 1 in Devices: 
            # Skip battery level updates when car is sleeping or SoC is 0/invalid
            if not car_sleeping and soc_percent > 0:
                Devices[1].Update(nValue=int(soc_percent), sValue=str(int(soc_percent)))
                Domoticz.Debug(f"Battery Level: {soc_percent:.1f}%")
            else:
                if car_sleeping:
                    Domoticz.Debug(f"Skipping battery level update - car is sleeping (SoC: {soc_percent:.1f}%)")
                else:
                    Domoticz.Debug(f"Skipping battery level update - invalid SoC value: {soc_percent:.1f}%")
        if 3 in Devices: 
            Devices[3].Update(nValue=1 if is_charging else 0, sValue="On" if is_charging else "Off")
            Domoticz.Debug(f"Charging Status: {'Charging' if is_charging else 'Not Charging'}")
        
        # Charge Limit Selector
        if 8 in Devices and charging_status and not car_sleeping:
            code_to_selector = {1: 0, 2: 10, 3: 20, 4: 30, 5: 40, 6: 50, 7: 60}
            limit_code = getattr(charging_status.chrgMgmtData, 'bmsOnBdChrgTrgtSOCDspCmd', None)
            if limit_code in code_to_selector:
                Devices[8].Update(nValue=code_to_selector[limit_code], sValue=str(code_to_selector[limit_code]))
        elif 8 in Devices and car_sleeping:
            Domoticz.Debug("Skipping charge limit selector update - car is sleeping")

        # Charging Power
        if 15 in Devices and charging_status and hasattr(charging_status, 'rvsChargeStatus'):
            working_current = charging_status.rvsChargeStatus.workingCurrent
            Domoticz.Debug(f"Charging Power Check - is_charging: {is_charging}, workingCurrent: {working_current}")
            if is_charging and working_current > 0:
                current_amps = working_current / 1000.0
                power_w = 3 * 0.86 * current_amps * 220.0
                Devices[15].Update(nValue=0, sValue=f"{power_w};0", Options={'EnergyMeterMode': '1'})
                Domoticz.Debug(f"Charging Power: {power_w/1000:.2f} kW (Current: {current_amps} A)")
            else:
                Devices[15].Update(nValue=0, sValue="0;0", Options={'EnergyMeterMode': '1'})
                Domoticz.Debug(f"Charging Power set to 0 - is_charging: {is_charging}, workingCurrent: {working_current}")

        # Real-time Battery Pack Capacity 
        if 16 in Devices and charging_status and hasattr(charging_status.rvsChargeStatus, 'realtimePower'):
            power_kwh = charging_status.rvsChargeStatus.realtimePower / 10.0
            if power_kwh > 0:  # Only update when there's actual power
                Devices[16].Update(nValue=0, sValue=str(power_kwh))
                Domoticz.Debug(f"Real-time Power: {power_kwh} kWh")

        # Vehicle Status based updates
        if vehicle_status:
            if 18 in Devices and hasattr(vehicle_status, 'gpsPosition'):
                speed_raw = getattr(vehicle_status.gpsPosition.wayPoint, 'speed', 0)
                speed_kmh = speed_raw / 10.0 if speed_raw > 0 else 0
                Devices[18].Update(nValue=0, sValue=str(speed_kmh))
            if 22 in Devices and hasattr(vehicle_status.basicVehicleStatus, 'batteryVoltage'):
                voltage = vehicle_status.basicVehicleStatus.batteryVoltage / 10.0
                if voltage > 5:
                    Devices[22].Update(nValue=0, sValue=str(voltage))
            # Heated Seats
            if hasattr(vehicle_status.basicVehicleStatus, 'frontLeftSeatHeatLevel'):
                if 20 in Devices:
                    Devices[20].Update(nValue=vehicle_status.basicVehicleStatus.frontLeftSeatHeatLevel * 10, sValue=str(vehicle_status.basicVehicleStatus.frontLeftSeatHeatLevel * 10))
                if 21 in Devices:
                    Devices[21].Update(nValue=vehicle_status.basicVehicleStatus.frontRightSeatHeatLevel * 10, sValue=str(vehicle_status.basicVehicleStatus.frontRightSeatHeatLevel * 10))
            # Tyre Pressures (ignore sentinel values like -128)
            if hasattr(vehicle_status.basicVehicleStatus, 'frontLeftTyrePressure'):
                bvs = vehicle_status.basicVehicleStatus
                if bvs.frontLeftTyrePressure > 0 and bvs.frontLeftTyrePressure != -128:
                    # Try direct conversion first (API might be returning bar*20)
                    pressure_bar = bvs.frontLeftTyrePressure / 20.0
                    # If result seems unrealistic (>6 bar), fall back to PSI conversion
                    if pressure_bar > 6.0:
                        pressure_bar = bvs.frontLeftTyrePressure * 0.0689476
                    
                    if 25 in Devices:
                        Devices[25].Update(nValue=0, sValue=f"{pressure_bar:.2f}")
                    if 27 in Devices:
                        fl_pressure = bvs.frontRightTyrePressure / 20.0
                        if fl_pressure > 6.0: fl_pressure = bvs.frontRightTyrePressure * 0.0689476
                        Devices[27].Update(nValue=0, sValue=f"{fl_pressure:.2f}")
                    if 28 in Devices:
                        rl_pressure = bvs.rearLeftTyrePressure / 20.0
                        if rl_pressure > 6.0: rl_pressure = bvs.rearLeftTyrePressure * 0.0689476
                        Devices[28].Update(nValue=0, sValue=f"{rl_pressure:.2f}")
                    if 29 in Devices:
                        rr_pressure = bvs.rearRightTyrePressure / 20.0
                        if rr_pressure > 6.0: rr_pressure = bvs.rearRightTyrePressure * 0.0689476
                        Devices[29].Update(nValue=0, sValue=f"{rr_pressure:.2f}")
        
        # Charging Status based updates
        if charging_status:
            # Power Usage Today
            if 19 in Devices and hasattr(charging_status.rvsChargeStatus, 'powerUsageOfDay'):
                power_wh = charging_status.rvsChargeStatus.powerUsageOfDay
                #Devices[19].Update(nValue=0, sValue=f"{power_wh};0")
                Devices[19].Update(nValue=0, sValue=str(power_wh))
                Domoticz.Debug(f"Power Usage Today: {power_wh} Wh")
            # Time to Full
            if 30 in Devices and hasattr(charging_status, 'chrgMgmtData'):
                rem_time = getattr(charging_status.chrgMgmtData, 'chrgngRmnngTime', 1023)
                is_valid = getattr(charging_status.chrgMgmtData, 'chrgngRmnngTimeV', 1) == 0
                if is_charging and is_valid:
                    Devices[30].Update(nValue=0, sValue=str(rem_time))
                else:
                    Devices[30].Update(nValue=0, sValue="0")
        
        # Other sensors (condensed) - validate range values
        if 2 in Devices and charging_status:
            range_val = getattr(charging_status.chrgMgmtData, 'clstrElecRngToEPT', 0)
            if 0 < range_val < 1000:
                Devices[2].Update(nValue=0, sValue=str(range_val))
        if 4 in Devices and vehicle_status and vehicle_status.gpsPosition and vehicle_status.gpsPosition.wayPoint:
            pos = vehicle_status.gpsPosition.wayPoint.position
            lat, lon = pos.latitude/1e6, pos.longitude/1e6
            Devices[4].Update(nValue=0, sValue=f"{lat:.6f},{lon:.6f}")
            if 17 in Devices:
                at_home = is_at_home(lat, lon)
                try:
                    resp = requests.get(f"https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat={lat}&lon={lon}", headers={'User-Agent': 'Domoticz-SAICiSmart-Plugin/1.5'}, timeout=10)
                    resp.raise_for_status()
                    address = resp.json().get('display_name', 'Address not found')
                    
                    # Check if we're at home and update address accordingly
                    if at_home:
                        address = "Home"
                    
                    Devices[17].Update(nValue=0, sValue=address)
                except Exception as e:
                    Domoticz.Error(f"Reverse geocoding failed: {e}")
            
            # Update "Car at Home" sensor
            if 36 in Devices:
                Devices[36].Update(nValue=1 if at_home else 0, sValue="On" if at_home else "Off")
        if vehicle_status:
            if 5 in Devices:
                lock_status = vehicle_status.basicVehicleStatus.lockStatus == 1
                Devices[5].Update(nValue=1 if lock_status else 0, sValue="On" if lock_status else "Off")
            if 6 in Devices:
                climate_on = vehicle_status.basicVehicleStatus.remoteClimateStatus > 0
                Devices[6].Update(nValue=1 if climate_on else 0, sValue="On" if climate_on else "Off")
        if 11 in Devices and charging_status:
            cable_connected = charging_status.rvsChargeStatus.chargingGunState == 1
            Devices[11].Update(nValue=1 if cable_connected else 0, sValue="On" if cable_connected else "Off")
        if 12 in Devices and charging_status and charging_status.rvsChargeStatus.mileage > 0:
            Devices[12].Update(nValue=0, sValue=str(int(charging_status.rvsChargeStatus.mileage / 10.0)))
        if 14 in Devices and charging_status:
            est_range = getattr(charging_status.chrgMgmtData, 'bmsEstdElecRng', 0)
            if 0 < est_range < 1000:
                Devices[14].Update(nValue=0, sValue=str(est_range))

        # New sensors from vehicle basic status
        if vehicle_status and hasattr(vehicle_status, 'basicVehicleStatus'):
            bvs = vehicle_status.basicVehicleStatus
            
            # Engine Status (Unit 31)
            if 31 in Devices and hasattr(bvs, 'engineStatus'):
                engine_on = bvs.engineStatus == 1
                Devices[31].Update(nValue=1 if engine_on else 0, sValue="On" if engine_on else "Off")
                Domoticz.Debug(f"Engine Status: {'On' if engine_on else 'Off'}")
            
            # Hand Brake (Unit 32)
            if 32 in Devices and hasattr(bvs, 'handBrake'):
                brake_on = bvs.handBrake == 1
                Devices[32].Update(nValue=1 if brake_on else 0, sValue="On" if brake_on else "Off")
                Domoticz.Debug(f"Hand Brake: {'On' if brake_on else 'Off'}")
            
            # Exterior Temperature (Unit 33) - ignore invalid values like -128
            if 33 in Devices and hasattr(bvs, 'exteriorTemperature'):
                ext_temp = bvs.exteriorTemperature
                if ext_temp > -100 and ext_temp != -128:
                    Devices[33].Update(nValue=0, sValue=str(ext_temp))
                    Domoticz.Debug(f"Exterior Temperature: {ext_temp}°C")
            
            # Interior Temperature (Unit 34) - ignore invalid values like -128
            if 34 in Devices and hasattr(bvs, 'interiorTemperature'):
                int_temp = bvs.interiorTemperature
                if int_temp > -100 and int_temp != -128:
                    Devices[34].Update(nValue=0, sValue=str(int_temp))
                    Domoticz.Debug(f"Interior Temperature: {int_temp}°C")
            
            # Car Status (Unit 35) - Online/Sleeping based on extendedData1
            if 35 in Devices and hasattr(bvs, 'extendedData1'):
                car_sleeping = bvs.extendedData1 == -128
                status_text = "Sleeping" if car_sleeping else "Online"
                Devices[35].Update(nValue=0 if car_sleeping else 1, sValue=status_text)
                Domoticz.Debug(f"Car Status: {status_text}")
        
    except Exception as e:
        Domoticz.Error(f"Failed to update devices: {e}")

def process_command_wrapper(unit, command, level):
    """Wrapper for command processing that handles thread cleanup"""
    global active_command_threads, command_lock
    current_thread = threading.current_thread()
    try:
        process_command(unit, command, level)
    finally:
        # Remove this thread from tracking
        with command_lock:
            if current_thread in active_command_threads:
                active_command_threads.remove(current_thread)

def process_command(unit, command, level):
    """Process commands sent to devices with fresh connection"""
    global config
    try:
        if not config:
            Domoticz.Error("SAIC configuration not available")
            return
        from saic_ismart_client_ng import SaicApi
        saic_client = SaicApi(config)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(saic_client.login())
            vin = loop.run_until_complete(saic_client.vehicle_list()).vinList[0].vin
            if not vin:
                Domoticz.Error("No vehicles found")
                return

            if unit == 10:  # Lock/Unlock
                if command == "On":
                    loop.run_until_complete(saic_client.lock_vehicle(vin))
                else:
                    loop.run_until_complete(saic_client.unlock_vehicle(vin))
            elif unit == 6:  # Climate
                if command == "On":
                    loop.run_until_complete(saic_client.start_ac(vin))
                else:
                    loop.run_until_complete(saic_client.stop_ac(vin))
            elif unit == 7:  # Charging Start/Stop
                loop.run_until_complete(saic_client.control_charging(vin, stop_charging=(command == "Off")))
            elif unit == 8:  # Set Charge Limit
                from saic_ismart_client_ng.api.vehicle_charging.schema import TargetBatteryCode
                level_map = {10: TargetBatteryCode.P_40, 20: TargetBatteryCode.P_50, 30: TargetBatteryCode.P_60, 40: TargetBatteryCode.P_70, 50: TargetBatteryCode.P_80, 60: TargetBatteryCode.P_90, 70: TargetBatteryCode.P_100}
                if level in level_map:
                    loop.run_until_complete(saic_client.set_target_battery_soc(vin, level_map[level]))
            elif unit in [20, 21]: # Heated Seats
                new_level_api = int(level / 10)
                if unit == 20: # Left
                    current_right_level_api = int(Devices[21].nValue / 10)
                    loop.run_until_complete(saic_client.control_heated_seats(vin, left_side_level=new_level_api, right_side_level=current_right_level_api))
                else: # Right
                    current_left_level_api = int(Devices[20].nValue / 10)
                    loop.run_until_complete(saic_client.control_heated_seats(vin, left_side_level=current_left_level_api, right_side_level=new_level_api))
        finally:
            loop.close()
    except Exception as e:
        Domoticz.Error(f"Failed to process command: {e}")

def send_notification(message):
    """Send notification via Domoticz notification system"""
    try:
        subject = urllib.parse.quote("MG iSmart Alert")
        body = urllib.parse.quote(message)
        port = Parameters.get("Port", "8080")
        url = f"http://127.0.0.1:{port}/json.htm?type=command&param=sendnotification&subject={subject}&body={body}"
        requests.get(url, timeout=5).raise_for_status()
    except Exception as e:
        Domoticz.Error(f"Failed to send notification: {e}")

# Room Plan Management Functions (Unchanged)
def get_room_plan_idx(plan_name):
    Domoticz.Debug(f"Finding room plan IDX for '{plan_name}'...")
    params_getplans = {"type": "command", "param": "getplans", "order": "name", "used": "true"}
    data = domoticz_api_call(params_getplans, is_utility_call=True)
    if data and "result" in data:
        for plan in data["result"]:
            if plan.get("Name") == plan_name:
                plan_idx = plan.get("idx")
                Domoticz.Debug(f"Found room plan '{plan_name}' with IDX: {plan_idx}")
                return plan_idx
    
    Domoticz.Debug(f"Room plan '{plan_name}' not found. Creating it...")
    params_addplan = {"type": "command", "param": "addplan", "name": plan_name}
    creation_data = domoticz_api_call(params_addplan, is_utility_call=False)
    if creation_data and creation_data.get("status") == "OK":
        Domoticz.Debug(f"Room plan '{plan_name}' created. Re-fetching IDX...")
        time.sleep(1)
        data_after_create = domoticz_api_call(params_getplans, is_utility_call=True)
        if data_after_create and "result" in data_after_create:
            for plan in data_after_create["result"]:
                if plan.get("Name") == plan_name:
                    return plan.get("idx")
    return None

def add_device_to_plan(device_idx, plan_idx):
    if not device_idx or not plan_idx:
        return
    params = {"type": "command", "param": "addplanactivedevice", "activeidx": int(device_idx), "activetype": 0, "idx": int(plan_idx)}
    domoticz_api_call(params)

def domoticz_api_call(params, is_utility_call=False):
    domoticz_port = Parameters.get("Port")
    url = f"http://127.0.0.1:{domoticz_port}/json.htm"
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if data.get("status") == "OK":
            if not is_utility_call: 
                action_title = params.get("param", "Unknown Action")
                name_param_value = params.get('sensorname', params.get('name', "Unknown Device"))
                
                if action_title == "addplanactivedevice":
                     Domoticz.Debug(f"API call '{action_title}' successful for device IDX {params.get('activeidx')} to plan IDX {params.get('idx')}.")
                elif action_title == "addplan":
                    Domoticz.Debug(f"API call '{action_title}' for plan '{params.get('name')}' successful. API Title: {data.get('title')}")
                elif action_title == "setused":
                    Domoticz.Debug(f"API call '{action_title}' for device IDX {params.get('idx')} ('{name_param_value}') successful. API Title: {data.get('title')}")
            return data
        else: 
            Domoticz.Error(f"Domoticz API error: {data.get('message', 'Unknown error')}")
            return None
    except Exception as e: 
        Domoticz.Error(f"Request failed for params {params}: {e}")
        return None

def DumpConfigToLog():
    """Debug helper function to dump configuration"""
    for x in Parameters:
        if Parameters[x] != "":
            Domoticz.Debug(f"'{x}': '{str(Parameters[x])}'")
    Domoticz.Debug(f"Device count: {len(Devices)}")
    for x in Devices:
        Domoticz.Debug(f"Device: {x} - {Devices[x]}")
        Domoticz.Debug(f"Device ID: '{Devices[x].ID}'")
        Domoticz.Debug(f"Device Name: '{Devices[x].Name}'")
        Domoticz.Debug(f"Device nValue: {Devices[x].nValue}")
        Domoticz.Debug(f"Device sValue: '{Devices[x].sValue}'")
        Domoticz.Debug(f"Device LastLevel: {Devices[x].LastLevel}")
