"""
<plugin key="SAIC-iSmart" name="SAIC iSmart (MG Cars)" author="lemassykoi" version="1.6.8" wikilink="https://github.com/lemassykoi/Domoticz-MG-Plugin" externallink="https://github.com/SAIC-iSmart-API">
    <description>
        <h2>SAIC iSmart Plugin for MG Cars</h2><br/>
        <p>This plugin connects directly to MG iSmart API to retrieve vehicle data without MQTT dependency.</p>
        <p>Supports MG5, MG4, ZS EV, Marvel R and other SAIC vehicles with iSmart connectivity.</p>
        <br/>
        <p><b>Requirements:</b></p>
        <ul>
            <li>Valid MG iSmart account credentials (email address only)</li>
            <li>Vehicle registered in MG iSmart app</li>
            <li>Domoticz Notification Subsystem (e.g., Telegram) configured for alerts.</li>
        </ul>
    </description>
    <params>
        <param field="Username" label="SAIC Email Address" width="200px" required="true"/>
        <param field="Password" label="SAIC Password" width="200px" required="true" password="true"/>
        <param field="Mode1"    label="Region" width="150px">
            <options>
                <option label="Europe (Default)" value="eu" default="true"/>
                <option label="Australia/New Zealand" value="au"/>
            </options>
        </param>
        <param field="Mode4" label="Home Radius (meters)" width="80px" default="25" required="false"/>
        <param field="Mode3" label="Update Interval (seconds)" width="50px" default="180"/>
        <param field="Port" label="Domoticz Port" width="50px" default="80" required="true"/>
        <param field="Mode2" label="Cool Down Start Hour" width="50px" default="22">
        <param field="Mode5" label="Cool Down End Hour" width="50px" default="7">
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
import requests
import asyncio
import urllib.parse
import math
import hashlib
import json
import os
import base64
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

# Cryptography imports with fallback
try:
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    Domoticz.Error("cryptography library not available - token encryption disabled. Install with: sudo pip install cryptography>=3.0.0 --break-system-packages")

# --- Custom Exceptions ---
class SaicApiException(Exception):
    """Base exception for SAIC API errors"""
    def __init__(self, message: str, return_code: int = None):
        self.message = message
        self.return_code = return_code
        super().__init__(self.message)

class SaicAuthException(SaicApiException):
    """Authentication failure exception"""
    pass

class SaicRetryException(SaicApiException):
    """Exception indicating the operation should be retried"""
    pass

# --- Data Models ---
@dataclass
class BasicVehicleStatus:
    """Basic vehicle status data model"""
    batteryVoltage: Optional[int] = None
    bonnetStatus: Optional[int] = None
    bootStatus: Optional[int] = None
    canBusActive: Optional[int] = None
    clstrDspdFuelLvlSgmt: Optional[int] = None
    currentJourneyId: Optional[int] = None
    currentJourneyDistance: Optional[int] = None
    dippedBeamStatus: Optional[int] = None
    driverDoor: Optional[int] = None
    driverWindow: Optional[int] = None
    engineStatus: Optional[int] = None
    extendedData1: Optional[int] = None
    extendedData2: Optional[int] = None
    exteriorTemperature: Optional[int] = None
    frontLeftSeatHeatLevel: Optional[int] = None
    frontLeftTyrePressure: Optional[int] = None
    frontRightSeatHeatLevel: Optional[int] = None
    frontRightTyrePressure: Optional[int] = None
    fuelLevelPrc: Optional[int] = None
    fuelRange: Optional[int] = None
    fuelRangeElec: Optional[int] = None
    handBrake: Optional[int] = None
    interiorTemperature: Optional[int] = None
    lastKeySeen: Optional[int] = None
    lockStatus: Optional[int] = None
    mainBeamStatus: Optional[int] = None
    mileage: Optional[int] = None
    passengerDoor: Optional[int] = None
    passengerWindow: Optional[int] = None
    powerMode: Optional[int] = None
    rearLeftDoor: Optional[int] = None
    rearLeftTyrePressure: Optional[int] = None
    rearLeftWindow: Optional[int] = None
    rearRightDoor: Optional[int] = None
    rearRightTyrePressure: Optional[int] = None
    rearRightWindow: Optional[int] = None
    remoteClimateStatus: Optional[int] = None
    rmtHtdRrWndSt: Optional[int] = None
    sideLightStatus: Optional[int] = None
    steeringHeatLevel: Optional[int] = None
    steeringWheelHeatFailureReason: Optional[int] = None
    sunroofStatus: Optional[int] = None
    timeOfLastCANBUSActivity: Optional[int] = None
    vehElecRngDsp: Optional[int] = None
    vehicleAlarmStatus: Optional[int] = None
    wheelTyreMonitorStatus: Optional[int] = None
    
    @property
    def is_parked(self) -> bool:
        """Check if vehicle is parked"""
        return self.engineStatus != 1 or self.handBrake == 1
    
    @property
    def is_engine_running(self) -> bool:
        """Check if engine is running"""
        return self.engineStatus == 1

class SAICiSmartPlugin:
    """SAIC iSmart Plugin for MG Cars"""
    
    def __init__(self):
        self.async_thread = None
        self.stop_event = threading.Event()
        self.async_loop = None
        
        self.config = None
        self.saic_client = None
        self.vin = None
        
        self.active_command_threads = []
        self.command_lock = threading.Lock()
        
        # Notification State
        self.was_charging = False
        self.notification_sent_for_session = False
        
        # Sleep Detection
        self.consecutive_invalid_data = 0
        self.last_valid_data_time = None
        
        # Night cooldown state
        self.last_known_at_home = False
        
    def onStart(self):
        """Initialize the plugin when Domoticz starts it"""
        Domoticz.Log("SAIC iSmart Plugin starting...")
        
        # Set debug level
        self.debug_level = Parameters.get("Mode6", "Normal")
        if self.debug_level == "Debug":
            Domoticz.Debugging(1)
            DumpConfigToLog()
        elif self.debug_level == "Verbose":
            Domoticz.Debugging(2)
            
        # Get parameters
        username = str(Parameters["Username"])
        password = str(Parameters["Password"])
        region   = str(Parameters["Mode1"])
        self.night_start_hour = int(Parameters.get("Mode2", 22))
        self.night_end_hour   = int(Parameters.get("Mode5", 7))
        
        if not username or not password:
            Domoticz.Error("Username and password are required")
            return
            
        try:
            from saic_ismart_client_ng.model import SaicApiConfiguration
            self.config = SaicApiConfiguration(username=username, password=password, region=region)
            Domoticz.Debug(f"Login config created for: {username}")
        except ImportError as e:
            Domoticz.Error(f"SAIC client library not found: {e}")
            return
        except Exception as e:
            Domoticz.Error(f"Failed to initialize SAIC client config: {e}")
            return
        
        self.stop_event.clear()
        self.async_thread = threading.Thread(target=self.run_async_loop)
        self.async_thread.daemon = True
        self.async_thread.start()
        Domoticz.Log("SAIC iSmart Plugin started successfully")

    def onStop(self):
        """Stop the plugin and cleanup"""
        Domoticz.Log("SAIC iSmart Plugin stopping...")
        
        self.stop_event.set()
        
        # Clean up client
        self.saic_client = None
        
        if self.async_loop and self.async_loop.is_running():
            # Cancel all pending tasks before stopping the loop
            try:
                # Get all tasks in the loop
                tasks = [task for task in asyncio.all_tasks(self.async_loop) if not task.done()]
                if tasks:
                    # Cancel all pending tasks
                    for task in tasks:
                        self.async_loop.call_soon_threadsafe(task.cancel)
                    Domoticz.Debug(f"Cancelled {len(tasks)} pending tasks")
                
            except Exception as e:
                Domoticz.Debug(f"Error during async loop cleanup: {e}")
        
        if self.async_thread and self.async_thread.is_alive():
            self.async_thread.join(timeout=5)
        
        with self.command_lock:
            command_threads_copy = self.active_command_threads.copy()
        
        for thread in command_threads_copy:
            if thread.is_alive():
                thread.join(timeout=2)
        
        Domoticz.Log("SAIC iSmart Plugin stopped")

    def onCommand(self, DeviceID, Unit, Command, Level, Color):
        """Handle commands from Domoticz - using Gemini's thread-safe approach"""
        Domoticz.Log(f"Command received - Unit: {Unit}, Command: {Command}, Level: {Level}")
        
        if not self.async_loop or not self.async_loop.is_running():
            Domoticz.Error("Async loop is not running, cannot process command.")
            return
            
        # Schedule the async command processing in the event loop
        asyncio.run_coroutine_threadsafe(
            self.process_command_async(Unit, Command, Level), 
            self.async_loop
        )

    def onHeartbeat(self):
        """Heartbeat callback - no operation needed"""
        pass

    def run_async_loop(self):
        """Runs the asyncio event loop in a dedicated thread - Gemini's core fix"""
        self.async_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.async_loop)
        try:
            self.async_loop.run_until_complete(self.main_update_loop())
        except asyncio.CancelledError:
            Domoticz.Debug("Async loop was cancelled during shutdown")
        except Exception as e:
            Domoticz.Error(f"Critical error in async loop: {e}")
        finally:
            self.async_loop.close()
            self.async_loop = None
            Domoticz.Log("Asyncio loop has been closed.")

    async def main_update_loop(self):
        """The main async task for fetching data - enhanced with original functionality"""
        base_update_interval = int(Parameters.get("Mode3", "180"))
        update_interval = base_update_interval  # Will be dynamically adjusted
        auth_failure_count = 0
        max_auth_failures = 5

        try:
            from saic_ismart_client_ng import SaicApi
            self.saic_client = SaicApi(self.config)
        except Exception as e:
            Domoticz.Error(f"Failed to create SaicApi client: {e}")
            return

        # Try to load stored token on first run
        needs_login = True
        needs_vehicle_info = True
        if not self.saic_client.token_expiration:
            stored_token, stored_expires = self.load_token()
            if stored_token and stored_expires:
                # Set the token in the SAIC client
                self.saic_client._AbstractSaicApi__api_client.user_token = stored_token
                self.saic_client._AbstractSaicApi__token_expiration = stored_expires
                needs_login = False
                Domoticz.Log("Reusing stored authentication token")

        while not self.stop_event.is_set():
            try:
                # Check if authenticated, if not, login
                if needs_login or not self.saic_client.token_expiration or self.saic_client.token_expiration < datetime.now():
                    Domoticz.Log("Logging in...")
                    await self.saic_client.login()
                    Domoticz.Log(f"Token expires at: {self.saic_client.token_expiration}")
                    
                    # Save the new token for next time
                    if hasattr(self.saic_client, '_AbstractSaicApi__api_client') and hasattr(self.saic_client._AbstractSaicApi__api_client, 'user_token'):
                        token = self.saic_client._AbstractSaicApi__api_client.user_token
                        expires_at = self.saic_client.token_expiration
                        if token and expires_at:
                            self.save_token(token, expires_at)
                    
                    needs_login = False
                    needs_vehicle_info = True  # Need to get vehicle info after login

                # Get vehicle list and VIN if needed (after login or when using stored token)
                if needs_vehicle_info or not self.vin:
                    vehicle_list_resp = await self.saic_client.vehicle_list()
                    if not vehicle_list_resp or not vehicle_list_resp.vinList:
                        raise SaicApiException("No vehicles found")
                    
                    vehicle = vehicle_list_resp.vinList[0]
                    self.vin = vehicle.vin
                    Domoticz.Debug(f"Working with vehicle: {vehicle.brandName} {vehicle.modelName}")
                    Domoticz.Debug(f"VIN hash: {self.sha256_hex_digest(self.vin)[:8]}...")
                    needs_vehicle_info = False

                # Fetch data and update Domoticz
                vehicle_data = await self.fetch_and_update_data()
                if vehicle_data:
                    # Create devices if this is first run or check for missing devices
                    if len(Devices) == 0:
                        self.create_devices(vehicle_data)
                    else:
                        self.ensure_all_devices_exist(vehicle_data)
                    self.update_devices(vehicle_data)
                    auth_failure_count = 0  # Reset on successful update
                    
                    # Calculate dynamic update interval based on time and stored home state
                    update_interval = self.calculate_update_interval()
                else:
                    # No vehicle data - use base interval
                    update_interval = base_update_interval

            except SaicAuthException as e:
                auth_failure_count += 1
                Domoticz.Error(f"Authentication error (attempt {auth_failure_count}/{max_auth_failures}): {e}")
                
                # Clear stored token on auth failure
                self.clear_token()
                needs_login = True
                needs_vehicle_info = True
                
                if auth_failure_count >= max_auth_failures:
                    Domoticz.Error("Too many authentication failures. Stopping plugin.")
                    break
                
                # Wait longer after auth failure
                await self.async_sleep(min(base_update_interval * 2, 600)) # Max 10 minutes
                continue
                
            except SaicApiException as e:
                Domoticz.Error(f"SAIC API error: {e}")
            except asyncio.CancelledError:
                Domoticz.Debug("Update loop was cancelled during shutdown")
                break
            except Exception as e:
                Domoticz.Error(f"Unexpected error in update loop: {e}")

            await self.async_sleep(update_interval)

    async def fetch_and_update_data(self):
        """Fetches all data from the API - restored full functionality"""
        if not self.vin:
            Domoticz.Error("VIN not available, cannot fetch data.")
            return None

        Domoticz.Debug(f"Auth token expires at {self.saic_client.token_expiration}")
        Domoticz.Debug("Getting vehicle list...")
        
        try:
            # Get fresh vehicle info
            vehicle_list_resp = await self.saic_client.vehicle_list()
            if not vehicle_list_resp or not vehicle_list_resp.vinList:
                raise SaicApiException("No vehicles found")
            
            vehicle = vehicle_list_resp.vinList[0]
            vehicle_status, charging_status = None, None
            
            # Get vehicle status with improved error handling
            if self.vin:
                try: 
                    vehicle_status = await self.saic_client.get_vehicle_status(self.vin)
                    if not vehicle_status:
                        raise SaicApiException("No vehicle status received")
                    Domoticz.Log(f"Raw vehicle status received")
                    Domoticz.Debug(f"Raw vehicle status data: {vehicle_status}")
                except Exception as e:
                    Domoticz.Log(f"Could not get vehicle status: {e}")
                    if "401" in str(e) or "403" in str(e):
                        raise SaicAuthException(f"Authentication failed during vehicle status: {e}")
            else:
                Domoticz.Error("No VIN available for vehicle status request")
                
            # Improved sleep detection with consecutive tracking
            should_skip_charging = False
            if vehicle_status and hasattr(vehicle_status, 'basicVehicleStatus'):
                extended_data1 = getattr(vehicle_status.basicVehicleStatus, 'extendedData1', 0)
                mileage = getattr(vehicle_status.basicVehicleStatus, 'mileage', 0)
                exterior_temp = getattr(vehicle_status.basicVehicleStatus, 'exteriorTemperature', 0)
                
                # Track invalid data patterns
                invalid_indicators = [extended_data1 == -128, mileage == -128, exterior_temp == -128]
                if sum(invalid_indicators) >= 2:  # Multiple indicators suggest deep sleep
                    self.consecutive_invalid_data += 1
                    should_skip_charging = True
                    if self.consecutive_invalid_data >= 3:
                        Domoticz.Debug(f"Car in deep sleep mode (consecutive invalid responses: {self.consecutive_invalid_data})")
                else:
                    self.consecutive_invalid_data = 0  # Reset counter on valid data

            # Get charging status if VIN is available (regardless of sleep state)
            if self.vin:
                try: 
                    charging_status = await self.saic_client.get_vehicle_charging_management_data(self.vin)
                    if not charging_status:
                        raise SaicApiException("No charging status received")
                    Domoticz.Log(f"Raw charging status received")
                    Domoticz.Debug(f"Raw charging status data: {charging_status}")
                except Exception as e:
                    Domoticz.Log(f"Could not get charging status: {e}")
                    if "401" in str(e) or "403" in str(e):
                        raise SaicAuthException(f"Authentication failed during charging status: {e}")
            else:
                Domoticz.Debug("Skipped charging status call due to missing VIN")
                
            return {"vehicle_status": vehicle_status, "charging_status": charging_status, "vehicle_info": vehicle}
                
        except Exception as e:
            Domoticz.Error(f"Failed to get vehicle data: {e}")
            return None

    async def process_command_async(self, unit, command, level):
        """Asynchronously processes commands from Domoticz - restored full functionality"""
        if not self.vin:
            Domoticz.Error("Cannot process command: VIN not available.")
            return
        
        try:
            Domoticz.Debug(f"Processing async command for unit {unit}...")
            
            if unit == 10:  # Lock/Unlock
                if command == "On":
                    await self.saic_client.lock_vehicle(self.vin)
                    Domoticz.Log("Vehicle locked.")
                else:
                    await self.saic_client.unlock_vehicle(self.vin)
                    Domoticz.Log("Vehicle unlocked.")
            elif unit == 6:  # Climate
                if command == "On":
                    await self.saic_client.start_ac(self.vin)
                    Domoticz.Log("A/C started.")
                else:
                    await self.saic_client.stop_ac(self.vin)
                    Domoticz.Log("A/C stopped.")
            elif unit == 7:  # Charging Start/Stop
                await self.saic_client.control_charging(self.vin, stop_charging=(command == "Off"))
                Domoticz.Log(f"Charging command sent: {'Stop' if command == 'Off' else 'Start'}")
            elif unit == 8:  # Set Charge Limit
                from saic_ismart_client_ng.api.vehicle_charging.schema import TargetBatteryCode
                level_map = {10: TargetBatteryCode.P_40, 20: TargetBatteryCode.P_50, 30: TargetBatteryCode.P_60, 40: TargetBatteryCode.P_70, 50: TargetBatteryCode.P_80, 60: TargetBatteryCode.P_90, 70: TargetBatteryCode.P_100}
                if level in level_map:
                    await self.saic_client.set_target_battery_soc(self.vin, level_map[level])
                    Domoticz.Log(f"Charge limit set to: {level}%")
            elif unit == 9:  # Set Charge Current Limit
                from saic_ismart_client_ng.api.vehicle_charging.schema import ChargeCurrentLimitCode, TargetBatteryCode
                level_map = {10: ChargeCurrentLimitCode.C_6A, 20: ChargeCurrentLimitCode.C_8A, 30: ChargeCurrentLimitCode.C_16A, 40: ChargeCurrentLimitCode.C_MAX}
                if level in level_map:
                    # Get current target SOC or use default 80%
                    current_target_soc = TargetBatteryCode.P_80  # Default
                    try:
                        charging_data = await self.saic_client.get_vehicle_charging_management_data(self.vin)
                        if charging_data and charging_data.chrgMgmtData:
                            limit_code = getattr(charging_data.chrgMgmtData, 'bmsOnBdChrgTrgtSOCDspCmd', 5)
                            soc_map = {0: TargetBatteryCode.P_IGNORE, 1: TargetBatteryCode.P_40, 2: TargetBatteryCode.P_50, 3: TargetBatteryCode.P_60, 4: TargetBatteryCode.P_70, 5: TargetBatteryCode.P_80, 6: TargetBatteryCode.P_90, 7: TargetBatteryCode.P_100}
                            current_target_soc = soc_map.get(limit_code, TargetBatteryCode.P_80)
                    except Exception as e:
                        Domoticz.Debug(f"Could not get current target SOC, using default 80%: {e}")
                    
                    await self.saic_client.set_target_battery_soc(self.vin, current_target_soc, level_map[level])
                    current_names = {ChargeCurrentLimitCode.C_6A: "6A", ChargeCurrentLimitCode.C_8A: "8A", ChargeCurrentLimitCode.C_16A: "16A", ChargeCurrentLimitCode.C_MAX: "MAX"}
                    Domoticz.Log(f"Charge current limit set to: {current_names[level_map[level]]}")
            elif unit in [20, 21]: # Heated Seats
                new_level_api = int(level / 10)
                if unit == 20: # Left
                    current_right_level_api = int(Devices[21].nValue / 10) if 21 in Devices else 0
                    await self.saic_client.control_heated_seats(self.vin, left_side_level=new_level_api, right_side_level=current_right_level_api)
                    Domoticz.Log(f"Left heated seat set to level: {new_level_api}")
                else: # Right
                    current_left_level_api = int(Devices[20].nValue / 10) if 20 in Devices else 0
                    await self.saic_client.control_heated_seats(self.vin, left_side_level=current_left_level_api, right_side_level=new_level_api)
                    Domoticz.Log(f"Right heated seat set to level: {new_level_api}")
            elif unit == 37:  # Scheduled Charging Mode
                from saic_ismart_client_ng.api.vehicle_charging.schema import ScheduledChargingMode
                from datetime import time
                level_map = {0: ScheduledChargingMode.DISABLED, 10: ScheduledChargingMode.UNTIL_CONFIGURED_TIME, 20: ScheduledChargingMode.UNTIL_CONFIGURED_SOC}
                if level in level_map:
                    # Set scheduled charging with default times (20:00 to 06:00)
                    start_time = time(20, 0)  # 8 PM
                    end_time = time(6, 0)     # 6 AM
                    await self.saic_client.set_schedule_charging(self.vin, start_time=start_time, end_time=end_time, mode=level_map[level])
                    mode_names = {ScheduledChargingMode.DISABLED: "Disabled", ScheduledChargingMode.UNTIL_CONFIGURED_TIME: "Until Time", ScheduledChargingMode.UNTIL_CONFIGURED_SOC: "Until SOC"}
                    Domoticz.Log(f"Scheduled charging mode set to: {mode_names[level_map[level]]}")
            elif unit == 38:  # Battery Heating Control
                await self.saic_client.control_battery_heating(self.vin, enable=(command == "On"))
                Domoticz.Log(f"Battery heating {'enabled' if command == 'On' else 'disabled'}")
            elif unit == 39:  # Charging Port Lock Control
                await self.saic_client.control_charging_port_lock(self.vin, unlock=(command == "Off"))
                Domoticz.Log(f"Charging port {'unlocked' if command == 'Off' else 'locked'}")
            else:
                Domoticz.Debug(f"No async handler for command on unit {unit}")
        except Exception as e:
            Domoticz.Error(f"Failed to process async command: {e}")

    async def async_sleep(self, seconds):
        """Async sleep that respects stop_event"""
        for _ in range(int(seconds)):
            if self.stop_event.is_set():
                break
            await asyncio.sleep(1)

    # Utility functions
    def sha256_hex_digest(self, text: str) -> str:
        """Create SHA-256 hash of text (for VIN hashing)"""
        return hashlib.sha256(text.encode('utf-8')).hexdigest()

    def get_token_storage_path(self) -> str:
        """Get the path for token storage file"""
        # Use plugin directory to store token file
        plugin_dir = os.path.dirname(__file__)
        return os.path.join(plugin_dir, 'saic_token.json')

    def derive_encryption_key(self, email: str, password: str, salt: bytes) -> bytes:
        """Derive encryption key from email + password using PBKDF2"""
        if not CRYPTO_AVAILABLE:
            return None
        
        # Combine email and password as key material
        key_material = f"{email.lower().strip()}:{password}".encode('utf-8')
        
        # Use PBKDF2 with SHA-256, 100000 iterations (recommended minimum)
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,  # 256-bit key for AES-256
            salt=salt,
            iterations=100000,
            backend=default_backend()
        )
        
        return kdf.derive(key_material)

    def encrypt_token(self, token: str, key: bytes) -> tuple[bytes, bytes]:
        """Encrypt token using AES-256-GCM"""
        if not CRYPTO_AVAILABLE or not key:
            return None, None
        
        # Generate random IV for AES-GCM
        iv = os.urandom(12)  # 96-bit IV for GCM
        
        # Create cipher
        cipher = Cipher(algorithms.AES(key), modes.GCM(iv), backend=default_backend())
        encryptor = cipher.encryptor()
        
        # Encrypt token
        token_bytes = token.encode('utf-8')
        ciphertext = encryptor.update(token_bytes) + encryptor.finalize()
        
        # Return encrypted data + authentication tag + IV
        encrypted_data = iv + encryptor.tag + ciphertext
        return encrypted_data, iv

    def decrypt_token(self, encrypted_data: bytes, key: bytes) -> Optional[str]:
        """Decrypt token using AES-256-GCM"""
        if not CRYPTO_AVAILABLE or not key or not encrypted_data:
            return None
        
        try:
            # Extract IV, tag, and ciphertext
            iv = encrypted_data[:12]  # First 12 bytes
            tag = encrypted_data[12:28]  # Next 16 bytes (GCM tag)
            ciphertext = encrypted_data[28:]  # Rest is ciphertext
            
            # Create cipher and decrypt
            cipher = Cipher(algorithms.AES(key), modes.GCM(iv, tag), backend=default_backend())
            decryptor = cipher.decryptor()
            
            # Decrypt and decode
            token_bytes = decryptor.update(ciphertext) + decryptor.finalize()
            return token_bytes.decode('utf-8')
            
        except Exception as e:
            Domoticz.Debug(f"Token decryption failed: {e}")
            return None

    def save_token(self, token: str, expires_at: datetime) -> None:
        """Save encrypted token and expiration to persistent storage"""
        try:
            if not CRYPTO_AVAILABLE:
                Domoticz.Error("Cannot save token - cryptography library not available")
                return
                
            # Get credentials for encryption
            email = Parameters["Username"] 
            password = Parameters["Password"]
            
            # Generate random salt
            salt = os.urandom(32)
            
            # Derive encryption key
            key = self.derive_encryption_key(email, password, salt)
            if not key:
                Domoticz.Error("Failed to derive encryption key")
                return
            
            # Encrypt token
            encrypted_data, iv = self.encrypt_token(token, key)
            if not encrypted_data:
                Domoticz.Error("Failed to encrypt token")
                return
                
            # Prepare data for storage
            token_data = {
                'encrypted_token': base64.b64encode(encrypted_data).decode('ascii'),
                'salt': base64.b64encode(salt).decode('ascii'),
                'expires_at': expires_at.isoformat() if expires_at else None,
                'version': '2.0'  # Version for future compatibility
            }
            
            token_file = self.get_token_storage_path()
            with open(token_file, 'w', encoding='utf-8') as f:
                json.dump(token_data, f, indent=2)
                
            Domoticz.Debug(f"Encrypted token saved to {token_file}, expires at {expires_at}")
        except Exception as e:
            Domoticz.Error(f"Failed to save token: {e}")

    def load_token(self) -> tuple[Optional[str], Optional[datetime]]:
        """Load and decrypt token from persistent storage"""
        try:
            token_file = self.get_token_storage_path()
            if not os.path.exists(token_file):
                Domoticz.Debug("No token file found")
                return None, None
                
            with open(token_file, 'r', encoding='utf-8') as f:
                token_data = json.load(f)
            
            expires_str = token_data.get('expires_at')
            if not expires_str:
                Domoticz.Debug("No expiration date in token file")
                return None, None
                
            expires_at = datetime.fromisoformat(expires_str)
            
            # Check if token is still valid (with 10 minute buffer)
            if expires_at < datetime.now() + timedelta(minutes=10):
                Domoticz.Debug("Stored token has expired")
                self.clear_token()
                return None, None

            # Check version and handle accordingly
            version = token_data.get('version', '1.0')
            
            if version == '2.0':
                # New encrypted format
                if not CRYPTO_AVAILABLE:
                    Domoticz.Error("Cannot decrypt token - cryptography library not available")
                    return None, None
                
                encrypted_token_b64 = token_data.get('encrypted_token')
                salt_b64 = token_data.get('salt')
                
                if not encrypted_token_b64 or not salt_b64:
                    Domoticz.Debug("Invalid encrypted token data")
                    return None, None
                
                try:
                    # Get credentials for decryption
                    email = Parameters["Username"]
                    password = Parameters["Password"]
                    
                    # Decode base64 data
                    encrypted_data = base64.b64decode(encrypted_token_b64.encode('ascii'))
                    salt = base64.b64decode(salt_b64.encode('ascii'))
                    
                    # Derive key and decrypt
                    key = self.derive_encryption_key(email, password, salt)
                    token = self.decrypt_token(encrypted_data, key)
                    
                    if not token:
                        Domoticz.Error("Failed to decrypt token - wrong credentials?")
                        self.clear_token()
                        return None, None
                        
                    Domoticz.Log(f"Loaded encrypted token from storage, expires at {expires_at}")
                    return token, expires_at
                    
                except Exception as e:
                    Domoticz.Error(f"Token decryption failed: {e}")
                    self.clear_token()
                    return None, None
                    
            else:
                # Legacy unencrypted format - upgrade to encrypted on next save
                token = token_data.get('token')
                if not token:
                    Domoticz.Debug("No token in legacy file")
                    return None, None
                    
                Domoticz.Log(f"Loaded legacy token from storage, expires at {expires_at} (will upgrade to encrypted on next save)")
                return token, expires_at
                
        except Exception as e:
            Domoticz.Debug(f"Failed to load token: {e}")
            return None, None

    def clear_token(self) -> None:
        """Clear stored token file"""
        try:
            token_file = self.get_token_storage_path()
            if os.path.exists(token_file):
                os.remove(token_file)
                Domoticz.Debug("Token file cleared")
        except Exception as e:
            Domoticz.Debug(f"Failed to clear token file: {e}")

    def get_domoticz_home_coordinates(self):
        """Get home coordinates from Domoticz settings"""
        try:
            params = {"type": "command", "param": "getsettings"}
            data = self.domoticz_api_call(params, is_utility_call=True)
            if data and "Location" in data:
                lat = float(data["Location"]["Latitude"])
                lon = float(data["Location"]["Longitude"])
                return lat, lon
        except Exception as e:
            Domoticz.Debug(f"Failed to get home coordinates: {e}")
        return None, None

    def is_at_home(self, lat, lon):
        """Check if car is at home based on GPS coordinates and radius"""
        try:
            home_lat, home_lon = self.get_domoticz_home_coordinates()
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

    def calculate_update_interval(self):
        """Calculate dynamic update interval with night cooldown when at home"""
        base_interval = int(Parameters.get("Mode3", "300"))
        night_cooldown_interval = 3600  # 1 hour during night cooldown

        # Get current time
        now = datetime.now()
        current_hour = now.hour
        current_minute = now.minute

        # Define night period: 22:30 to 06:30
        night_start_hour, night_start_minute = self.night_start_hour, 30
        night_end_hour, night_end_minute = self.night_end_hour, 30

        # Convert time to minutes for easier comparison
        current_time_minutes = current_hour * 60 + current_minute
        night_start_minutes = night_start_hour * 60 + night_start_minute
        night_end_minutes = night_end_hour * 60 + night_end_minute

        # Check if we're in night period (spans midnight)
        is_night_period = (
            current_time_minutes >= night_start_minutes or 
            current_time_minutes <= night_end_minutes
        )
        
        if not is_night_period:
            # Day time - use normal interval
            Domoticz.Debug(f"Day time polling: {base_interval}s")
            return base_interval
        
        # Night time - check if we're at home (use stored state)
        if is_night_period and self.last_known_at_home:
            # Check if next full cooldown would extend past end of night period
            minutes_until_day = (night_end_minutes - current_time_minutes) % (24 * 60)
            cooldown_minutes = night_cooldown_interval // 60  # Convert seconds to minutes
            
            if minutes_until_day < cooldown_minutes:
                # Next full cooldown would go into day time - transition smoothly
                # Add a few minutes buffer to start day polling properly
                transition_minutes = minutes_until_day + 3  # Poll 3 minutes after day starts
                transition_interval = max(transition_minutes * 60, base_interval)
                Domoticz.Log(f"Night cooldown would extend into day - transition in {transition_minutes}min ({transition_interval}s)")
                return transition_interval
            else:
                Domoticz.Log(f"Night cooldown active (22:30-06:30, at home): {night_cooldown_interval}s interval")
                return night_cooldown_interval
        else:
            # Night time but not at home - use normal interval
            if is_night_period:
                Domoticz.Debug(f"Night time but not at home: {base_interval}s")
            return base_interval

    def get_device_definitions(self, vehicle_name):
        return {
            1: {"Name": f"{vehicle_name} Battery Level",          "Type": 243, "Subtype": 6},
            2: {"Name": f"{vehicle_name} Range",                  "Type": 243, "Subtype": 31, "Options": {'Custom': '1;km'}},
            3: {"Name": f"{vehicle_name} Charging",               "Type": 244, "Subtype": 73},
            4: {"Name": f"{vehicle_name} GPS Location",               "TypeName": "Text"},
            5: {"Name": f"{vehicle_name} Lock Status",            "Type": 244, "Subtype": 73},
            6: {"Name": f"{vehicle_name} Climate Active",         "Type": 244, "Subtype": 73},
            7: {"Name": f"{vehicle_name} Start/Stop Charging",    "Type": 244, "Subtype": 73},
            8: {"Name": f"{vehicle_name} Set Charge Limit",       "TypeName": "Selector Switch", "Options": {"LevelActions": "||||||||", "LevelNames": "Off|40%|50%|60%|70%|80%|90%|100%", "LevelOffHidden": "true", "SelectorStyle": "0"}},
            9: {"Name": f"{vehicle_name} Charge Current Limit",   "TypeName": "Selector Switch", "Options": {"LevelActions": "|||||", "LevelNames": "Off|6A|8A|16A|MAX", "LevelOffHidden": "true", "SelectorStyle": "0"}},
            10: {"Name": f"{vehicle_name} Lock Control",          "Type": 244, "Subtype": 73},
            11: {"Name": f"{vehicle_name} Cable Connected",       "Type": 244, "Subtype": 73},
            12: {"Name": f"{vehicle_name} Odometer",              "Type": 113, "Subtype": 0,  "Switchtype": 3},
            14: {"Name": f"{vehicle_name} Max Range",             "Type": 243, "Subtype": 31, "Options": {'Custom': '1;km'}, "Used": 0},
            15: {"Name": f"{vehicle_name} Charging",              "Type": 248, "Subtype": 1,  "Options": {"EnergyMeterMode": "1"}},
            16: {"Name": f"{vehicle_name} Battery Cap.",          "Type": 243, "Subtype": 31, "Options": {'Custom': '1;kWh'}},
            17: {"Name": f"{vehicle_name} Address",               "TypeName": "Text"},
            18: {"Name": f"{vehicle_name} Speed",                 "Type": 243, "Subtype": 31, "Options": {'Custom': '1;km/h'}},
            19: {"Name": f"{vehicle_name} Power Usage",           "Type": 113, "Subtype": 0,  "Switchtype": 0, "Options": {"ValueQuantity": "Custom", "ValueUnits": "Wh"}},
            20: {"Name": f"{vehicle_name} Heated Seat Left",      "TypeName": "Selector Switch", "Options": {"LevelActions": "||||", "LevelNames": "Off|Low|Medium|High", "LevelOffHidden": "false", "SelectorStyle": "0"}},
            21: {"Name": f"{vehicle_name} Heated Seat Right",     "TypeName": "Selector Switch", "Options": {"LevelActions": "||||", "LevelNames": "Off|Low|Medium|High", "LevelOffHidden": "false", "SelectorStyle": "0"}},
            22: {"Name": f"{vehicle_name} 12V Battery",           "Type": 243, "Subtype": 8},
            25: {"Name": f"{vehicle_name} Tyre FL",               "Type": 243, "Subtype": 9},
            27: {"Name": f"{vehicle_name} Tyre FR",               "Type": 243, "Subtype": 9},
            28: {"Name": f"{vehicle_name} Tyre RL",               "Type": 243, "Subtype": 9},
            29: {"Name": f"{vehicle_name} Tyre RR",               "Type": 243, "Subtype": 9},
            30: {"Name": f"{vehicle_name} Time to Full",          "Type": 243, "Subtype": 31, "Image": 21, "Options": {'Custom': '1;min'}},
            31: {"Name": f"{vehicle_name} Engine Status",         "Type": 244, "Subtype": 73},
            32: {"Name": f"{vehicle_name} Hand Brake",            "Type": 244, "Subtype": 73},
            33: {"Name": f"{vehicle_name} Exterior Temp.",        "Type": 80, "Subtype": 5},
            34: {"Name": f"{vehicle_name} Interior Temp.",        "Type": 80, "Subtype": 5},
            35: {"Name": f"{vehicle_name} Status",                "Type": 244, "Subtype": 73},
            36: {"Name": f"{vehicle_name} Car at Home",           "Type": 244, "Subtype": 73},
            37: {"Name": f"{vehicle_name} Scheduled Charging",    "TypeName": "Selector Switch", "Options": {"LevelActions": "|||", "LevelNames": "Off|Until Time|Until SOC", "LevelOffHidden": "false", "SelectorStyle": "0"}},
            38: {"Name": f"{vehicle_name} Battery Heating",       "Type": 244, "Subtype": 73},
            39: {"Name": f"{vehicle_name} Charging Port Lock",    "Type": 244, "Subtype": 73},
        }

    def create_devices(self, vehicle_data):
        """Create Domoticz devices based on vehicle data - restored full functionality"""
        try:
            vehicle_info = vehicle_data.get("vehicle_info")
            if not vehicle_info:
                Domoticz.Error("No vehicle info available for device creation")
                return
                
            # Extract proper model name (remove "Electric" and clean up)
            raw_model = getattr(vehicle_info, 'modelName', 'Vehicle')
            model = raw_model.replace('Electric', '').replace('MG ', '').strip()
            vin_suffix = vehicle_info.vin[-4:] if hasattr(vehicle_info, 'vin') else 'XXXX'
            vehicle_name = f"{model} {vin_suffix}"
            
            Domoticz.Log(f"Creating devices for: {vehicle_name}")
            
            devices_to_create = self.get_device_definitions(vehicle_name)

            for unit, params in devices_to_create.items():
                if unit not in Devices: 
                    device_params = params.copy()
                    device_params.setdefault("Used", 1)
                    Domoticz.Device(Unit=unit, **device_params).Create()
            
            Domoticz.Log("Devices created successfully")
            
            # Create and assign devices to room plan
            self.create_room_plan(vehicle_data, devices_to_create)
            
        except Exception as e:
            Domoticz.Error(f"Failed to create devices: {e}")

    def ensure_all_devices_exist(self, vehicle_data=None):
        """Check for missing devices and recreate them if needed - restored"""
        try:
            if not vehicle_data or not vehicle_data.get("vehicle_info"):
                Domoticz.Debug("No vehicle data available for device check")
                return
                
            vehicle_info = vehicle_data.get("vehicle_info")
            # Extract proper model name (remove "Electric" and clean up)
            raw_model = getattr(vehicle_info, 'modelName', 'Vehicle')
            model = raw_model.replace('Electric', '').replace('MG ', '').strip()
            vin_suffix = vehicle_info.vin[-4:] if hasattr(vehicle_info, 'vin') else 'XXXX'
            vehicle_name = f"{model} {vin_suffix}"
                
            devices_to_create = self.get_device_definitions(vehicle_name)
            missing_devices = []
            
            for unit, params in devices_to_create.items():
                if unit not in Devices:
                    missing_devices.append(unit)
                    device_params = params.copy()
                    device_params.setdefault("Used", 1)
                    Domoticz.Device(Unit=unit, **device_params).Create()
                    Domoticz.Log(f"Recreated missing device {unit}: {params['Name']}")
            
            if missing_devices:
                Domoticz.Log(f"Recreated {len(missing_devices)} missing devices: {missing_devices}")
                # Create room plan only when devices were created
                self.create_room_plan(vehicle_data, devices_to_create)
            else:
                Domoticz.Debug("All devices present")
                
        except Exception as e:
            Domoticz.Error(f"Failed to ensure devices exist: {e}")

    def create_room_plan(self, vehicle_data, devices_to_create):
        """Create and populate room plan - restored"""
        try:
            vehicle_info = vehicle_data.get("vehicle_info")
            if not vehicle_info:
                Domoticz.Error("No vehicle info for room plan creation")
                return
                
            # Extract proper model name for room plan
            raw_model = getattr(vehicle_info, 'modelName', 'Vehicle')
            model = raw_model.replace('Electric', '').replace('MG ', '').strip()
            vin_suffix = vehicle_info.vin[-4:] if hasattr(vehicle_info, 'vin') else 'XXXX'
            plan_name = f"{model}-{vin_suffix}"
            
            plan_idx = self.get_room_plan_idx(plan_name)
            
            if plan_idx:
                added_count = 0
                for unit in devices_to_create.keys():
                    if unit in Devices:
                        device_idx = Devices[unit].ID
                        self.add_device_to_plan(device_idx, plan_idx)
                        added_count += 1
                        
                Domoticz.Log(f"Added {added_count} devices to room plan '{plan_name}'")
            else:
                Domoticz.Error(f"Failed to create or find room plan '{plan_name}'")
        except Exception as e:
            Domoticz.Error(f"Failed to create room plan: {e}")

    def update_devices(self, vehicle_data):
        """Update device values with latest vehicle data - restored full functionality"""
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
            if not self.was_charging and is_charging:
                self.notification_sent_for_session = False
            if self.was_charging and not is_charging:
                self.send_notification(f"MG Charging: Stopped. SoC is {soc_percent:.1f}%.")
            if is_charging and not self.notification_sent_for_session and soc_percent >= charge_limit_percent:
                self.send_notification(f"MG Charging: Target of {charge_limit_percent}% reached (SoC: {soc_percent:.1f}%).")
                self.notification_sent_for_session = True
            self.was_charging = is_charging

            # --- Update Devices ---
            # Check if car is sleeping to avoid updating SoC with invalid 0% values
            car_sleeping = False
            if vehicle_status and hasattr(vehicle_status, 'basicVehicleStatus'):
                bvs = vehicle_status.basicVehicleStatus
                if hasattr(bvs, 'extendedData1'):
                    car_sleeping = bvs.extendedData1 == -128

            if 1 in Devices: 
                # Update battery level if SoC is valid (> 0), regardless of sleep state
                if soc_percent > 0:
                    Devices[1].Update(nValue=int(soc_percent), sValue=str(soc_percent))
                    Domoticz.Debug(f"Battery Level: {soc_percent}%")
                else:
                    Domoticz.Debug(f"Skipping battery level update - invalid SoC value: {soc_percent:.1f}%")
            if 3 in Devices: 
                Devices[3].Update(nValue=1 if is_charging else 0, sValue="On" if is_charging else "Off")
                Domoticz.Debug(f"Charging Status: {'Charging' if is_charging else 'Not Charging'}")
            
            # Charge Limit Selector  
            if 8 in Devices and charging_status:
                # Map API codes to selector levels: 40%|50%|60%|70%|80%|90%|100%
                code_to_selector = {0: 0, 1: 10, 2: 20, 3: 30, 4: 40, 5: 50, 6: 60, 7: 70}
                limit_code = getattr(charging_status.chrgMgmtData, 'bmsOnBdChrgTrgtSOCDspCmd', None)
                if limit_code in code_to_selector:
                    # Calculate the real limit
                    real_limit = limit_map[limit_code]
                    Devices[8].Update(nValue=code_to_selector[limit_code], sValue=str(code_to_selector[limit_code]))
                    Domoticz.Debug(f"Charge limit selector updated: {limit_code} -> real limit {real_limit}%")
                else:
                    Domoticz.Debug(f"Unknown charge limit code: {limit_code} (car_sleeping: {car_sleeping})")
            elif 8 in Devices:
                Domoticz.Debug(f"Skipping charge limit selector update - no charging status (car_sleeping: {car_sleeping})")

            # Charge Current Limit Selector
            if 9 in Devices and charging_status and hasattr(charging_status, 'chrgMgmtData'):
                # Map API codes to selector levels: Off|6A|8A|16A|MAX
                current_code_to_selector = {0: 0, 1: 10, 2: 20, 3: 30, 4: 40}  # C_IGNORE, C_6A, C_8A, C_16A, C_MAX
                current_limit_code = getattr(charging_status.chrgMgmtData, 'bmsAltngChrgCrntDspCmd', None)
                if current_limit_code in current_code_to_selector:
                    current_names = {0: "Off", 1: "6A", 2: "8A", 3: "16A", 4: "MAX"}
                    selector_level = current_code_to_selector[current_limit_code]
                    Devices[9].Update(nValue=selector_level, sValue=str(selector_level))
                    Domoticz.Debug(f"Charge current limit selector updated: {current_limit_code} -> {current_names.get(current_limit_code, 'Unknown')}")
                else:
                    Domoticz.Debug(f"Unknown charge current limit code: {current_limit_code}")
            elif 9 in Devices:
                Domoticz.Debug("Skipping charge current limit selector update - no charging status")

            # Scheduled Charging Mode Selector
            if 37 in Devices and charging_status and hasattr(charging_status, 'chrgMgmtData'):
                # Map API codes to selector levels: Disabled|Until Time|Until SOC
                schedule_mode_code = getattr(charging_status.chrgMgmtData, 'bmsReserCtrlDspCmd', None)
                if schedule_mode_code is not None:
                    # API values: 1=UNTIL_CONFIGURED_TIME, 2=DISABLED, 3=UNTIL_CONFIGURED_SOC
                    schedule_code_to_selector = {1: 10, 2: 0, 3: 20}  # Map to our selector levels
                    mode_names = {1: "Until Time", 2: "Disabled", 3: "Until SOC"}
                    selector_level = schedule_code_to_selector.get(schedule_mode_code, 0)
                    Devices[37].Update(nValue=selector_level, sValue=str(selector_level))
                    Domoticz.Debug(f"Scheduled charging mode selector updated: {schedule_mode_code} -> {mode_names.get(schedule_mode_code, 'Disabled')}")
                else:
                    Domoticz.Debug("No scheduled charging mode data available")
            elif 37 in Devices:
                Domoticz.Debug("Skipping scheduled charging mode selector update - no charging status")

            # Battery Heating Status
            if 38 in Devices and charging_status and hasattr(charging_status, 'chrgMgmtData'):
                is_heating = getattr(charging_status.chrgMgmtData, 'bmsPTCHeatReqDspCmd', 0) == 1
                Devices[38].Update(nValue=1 if is_heating else 0, sValue="On" if is_heating else "Off")
                Domoticz.Debug(f"Battery heating status: {'On' if is_heating else 'Off'}")
            elif 38 in Devices:
                Domoticz.Debug("Skipping battery heating status update - no charging status")

            # Charging Port Lock Status
            if 39 in Devices and charging_status and hasattr(charging_status, 'chrgMgmtData'):
                port_locked = getattr(charging_status.chrgMgmtData, 'ccuEleccLckCtrlDspCmd', 0) == 1
                Devices[39].Update(nValue=1 if port_locked else 0, sValue="On" if port_locked else "Off")
                Domoticz.Debug(f"Charging port lock status: {'Locked' if port_locked else 'Unlocked'}")
            elif 39 in Devices:
                Domoticz.Debug("Skipping charging port lock status update - no charging status")

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
                    at_home = self.is_at_home(lat, lon)
                    try:
                        resp = requests.get(f"https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat={lat}&lon={lon}", headers={'User-Agent': 'Domoticz-SAICiSmart-Plugin/1.6'}, timeout=10)
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
                    # Update our night cooldown state
                    self.last_known_at_home = at_home
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

    def send_notification(self, message):
        """Send notification via Domoticz notification system"""
        try:
            subject = urllib.parse.quote("MG iSmart Alert")
            body = urllib.parse.quote(message)
            port = Parameters.get("Port", "8080")
            url = f"http://127.0.0.1:{port}/json.htm?type=command&param=sendnotification&subject={subject}&body={body}"
            requests.get(url, timeout=5).raise_for_status()
        except Exception as e:
            Domoticz.Error(f"Failed to send notification: {e}")

    # Room Plan Management Functions
    def get_room_plan_idx(self, plan_name):
        """Get room plan IDX or create one if it doesn't exist"""
        Domoticz.Debug(f"Finding room plan IDX for '{plan_name}'...")
        params_getplans = {"type": "command", "param": "getplans", "order": "name", "used": "true"}
        data = self.domoticz_api_call(params_getplans, is_utility_call=True)
        if data and "result" in data:
            for plan in data["result"]:
                if plan.get("Name") == plan_name:
                    plan_idx = plan.get("idx")
                    Domoticz.Debug(f"Found room plan '{plan_name}' with IDX: {plan_idx}")
                    return plan_idx
        
        Domoticz.Debug(f"Room plan '{plan_name}' not found. Creating it...")
        params_addplan = {"type": "command", "param": "addplan", "name": plan_name}
        creation_data = self.domoticz_api_call(params_addplan, is_utility_call=False)
        if creation_data and creation_data.get("status") == "OK":
            Domoticz.Debug(f"Room plan '{plan_name}' created. Re-fetching IDX...")
            # Brief wait for Domoticz to process the room plan creation - using threading safe approach
            if threading.current_thread() != threading.main_thread():
                # We're in a background thread, safe to wait
                self.stop_event.wait(1)
            # If we're in main thread (callback), skip the wait and try immediately
            data_after_create = self.domoticz_api_call(params_getplans, is_utility_call=True)
            if data_after_create and "result" in data_after_create:
                for plan in data_after_create["result"]:
                    if plan.get("Name") == plan_name:
                        return plan.get("idx")
        return None

    def add_device_to_plan(self, device_idx, plan_idx):
        """Add device to room plan"""
        if not device_idx or not plan_idx:
            return
        params = {"type": "command", "param": "addplanactivedevice", "activeidx": int(device_idx), "activetype": 0, "idx": int(plan_idx)}
        self.domoticz_api_call(params)

    def domoticz_api_call(self, params, is_utility_call=False):
        """Make API call to Domoticz"""
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

global _plugin
_plugin = SAICiSmartPlugin()

def onStart():
    global _plugin
    _plugin.onStart()

def onStop():
    global _plugin
    _plugin.onStop()

def onCommand(DeviceID, Unit, Command, Level, Color):
    global _plugin
    _plugin.onCommand(DeviceID, Unit, Command, Level, Color)

def onHeartbeat():
    global _plugin
    _plugin.onHeartbeat()

def DumpConfigToLog():
    for x in Parameters:
        if x != 'Password':
            if Parameters[x] != "":
                Domoticz.Debug( "'" + x + "':'" + str(Parameters[x]) + "'")
    Domoticz.Debug("Settings count: " + str(len(Settings)))
    for x in Settings:
        Domoticz.Debug( "'" + x + "':'" + str(Settings[x]) + "'")
    Domoticz.Debug("Image count: " + str(len(Images)))
    for x in Images:
        Domoticz.Debug( "'" + x + "':'" + str(Images[x]) + "'")
    Domoticz.Debug("Device count: " + str(len(Devices)))
    for x in Devices:
        Domoticz.Debug("Device:           " + str(x) + " - " + str(Devices[x]))
        Domoticz.Debug("Device ID:       '" + str(Devices[x].ID) + "'")
        Domoticz.Debug("Device Name:     '" + Devices[x].Name + "'")
        Domoticz.Debug("Device nValue:    " + str(Devices[x].nValue))
        Domoticz.Debug("Device sValue:   '" + Devices[x].sValue + "'")
        Domoticz.Debug("Device LastLevel: " + str(Devices[x].LastLevel))
        Domoticz.Debug("Device Image:     " + str(Devices[x].Image))
    return
