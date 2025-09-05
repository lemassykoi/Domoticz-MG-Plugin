# SAIC iSmart Domoticz Plugin

A Domoticz plugin for MG iSmart vehicles (MG5, MG4, ZS EV, Marvel R, etc.) that connects directly to the SAIC API without requiring MQTT.

## Features

- **Battery & Charging Monitoring**
  - Battery level (SoC) display
  - Current and maximum range tracking
  - Real-time charging power monitoring
  - Daily energy consumption tracking
  - Time to full charge estimation
  
- **Vehicle Control**
  - Start/stop charging control
  - Charge limit setting (40-100% in 10% increments)
  - Vehicle lock/unlock control
  - Climate control (HVAC start/stop)
  - Heated seat control (left/right, 4 levels)
  
- **Status Monitoring**
  - Vehicle location with reverse geocoding
  - Individual tyre pressure monitoring (4 wheels)
  - 12V auxiliary battery voltage
  - Vehicle speed display
  - Charging cable connection status

## Installation

1. **Install the SAIC Python client library:**
   ```bash
   sudo pip3 install saic-ismart-client-ng
   ```

2. **Copy the plugin to Domoticz:**
   ```bash
   cd /home/pi/domoticz/plugins
   git clone https://github.com/lemassykoi/Domoticz-MG-Plugin.git
   ```

3. **Restart Domoticz**
   ```bash
   sudo systemctl restart domoticz
   ```

4. **Add Hardware:**
   - Go to Setup -> Hardware
   - Select "SAIC iSmart (MG Cars)" from the Type dropdown
   - Fill in your MG iSmart credentials
   - Select your region (Europe or Australia/New Zealand)
   - Set update interval (recommended: 300 seconds)

<img width="760" height="783" alt="image" src="https://github.com/user-attachments/assets/46ad14d0-3de4-44c7-a195-c96755ec550b" />

## Configuration

- **Username**: Your MG iSmart account email address
- **Password**: Your MG iSmart account password  
- **Region**: Select "Europe" for EU users, "Australia/New Zealand" for AU/NZ users, or "Auto" for automatic detection
- **Update Interval**: How often to poll the API (default: 180 seconds, minimum recommended: 300 seconds for production)
- **Home Radius**: Detection radius for "at home" status (default: 25 meters)
- **Domoticz Port**: Port number for Domoticz API (default: 8080)
- **Debug Level**: Normal/Debug/Verbose logging levels

**IMPORTANT**: Home detection is based on GPS coordinates in Domoticz settings. For the plugin to be able to access the coordinates, please fill in the appropriate settings. **Setup, Settings, SYSTEM tab**.

**IMPORTANT**: For the Plugin to be able to create a room plan and assign devices to it, you need to provide the port to access your domoticz instance. Also be sure you set "127.0.0.1" as exception in Trusted Networks. **Setup, Settings, SECURITY tab**.

<img width="418" height="82" alt="image" src="https://github.com/user-attachments/assets/2b2fac4b-f65e-4390-ba96-8dced224afbb" />


### iSMART Account Recommendation

**IMPORTANT**: If you are currently using the same account credentials in the official MG iSMART mobile app, you should create a dedicated secondary account for this plugin to avoid authentication conflicts. Using the same account simultaneously in both the app and the plugin will cause the mobile app to repeatedly request re-authentication.

**How to create a secondary account:**
1. Create a new MG iSMART account using a different email address
2. From your primary account, go to Settings → Secondary Account
3. Invite the newly created secondary account
4. Use the secondary account credentials in this plugin

**Note**: There's no need to associate the vehicle with the secondary account - it will inherit access through the invitation from your primary account.

## Devices Created

The plugin automatically creates these devices and organizes them in a `MG-XXXX` room plan:

### Battery & Charging
1. **Battery Level** - Current State of Charge (%)
2. **Range** - Current driving range (km)  
3. **Charging Status** - Charging indicator (On/Off)
4. **Charging Power** - 3-phase AC charging power (W)
5. **Real-time Power** - Current power consumption (W)
6. **Power Usage Today** - Daily energy consumption (Wh)
7. **Time to Full** - Charging time remaining (minutes)
8. **Max Range** - Maximum range when fully charged (km)
9. **Cable Connected** - Charging cable status (On/Off)

### Vehicle Control
10. **Lock Control** - Lock/unlock vehicle
11. **Climate Active** - HVAC control (On/Off)
12. **Start/Stop Charging** - Charging control (On/Off)
13. **Set Charge Limit** - Target SoC (40-100% in 10% steps)
14. **Charge Current Limit** - Current limit (6A, 8A, 16A, MAX)
15. **Heated Seat Left/Right** - Individual seat heating (Off/Low/Medium/High)

### Status & Monitoring
16. **Location** - GPS coordinates
17. **Address** - Reverse geocoded address (shows "Home" when at home location)
18. **Speed** - Current vehicle speed (km/h)
19. **Lock Status** - Vehicle lock status (read-only)
20. **Odometer** - Vehicle mileage counter
21. **12V Battery** - Auxiliary battery voltage
22. **Tyre Pressure FL/FR/RL/RR** - Individual wheel pressures (Bar)
23. **Car at Home** - Indicates if vehicle is within home radius

## Commands

Vehicle commands can be sent through device switches:
- **Lock/Unlock** vehicle via Lock Control device
- **Start/Stop climate** via Climate Active device  
- **Start/Stop charging** via Start/Stop Charging device
- **Set charge limit** via Set Charge Limit selector (40-100%)
- **Heated seat control** via individual seat devices (4 heat levels)

## Important Notes

### Sleep Mode & Polling
- **Default**: 180 seconds (3 minutes) - may prevent car sleep
- **Recommended**: 300+ seconds (5+ minutes) for production use
- **Sleep Detection**: Plugin monitors for invalid data patterns and adapts during charging
- **Charging Compatibility**: Continues monitoring charging status even when extendedData1=-128
- **Battery Impact**: Frequent polling prevents 12V battery conservation

### Power Monitoring
- **Charging Power**: Calculated using 3-phase AC formula (11kW capability)
- **Real-time Power**: Shows current consumption/generation
- **Energy Counters**: Track daily usage patterns

### Data Validation
- Ignores unrealistic sensor values (-128, 1023, 2047 sentinel values)
- Only updates devices when receiving valid data
- Continues operation even if partial data fails

## Technical Details

- Direct SAIC API communication (no MQTT required)
- Fresh API connections for reliability
- Proper Domoticz threading compliance
- Automatic room plan organization
- Home detection using Domoticz's configured location
- Smart tire pressure conversion (handles different API units)
- Based on: https://github.com/SAIC-iSmart-API

## Troubleshooting

### Common Issues
- **Authentication**: Verify credentials work in official MG app
- **Region Selection**: Ensure correct region (EU/AU/Auto)
- **API Errors**: Check logs for 500 errors or rate limiting
- **Sleep Mode**: Consider longer polling intervals if car shows invalid data
- **Charging Power**: If empty, restart Domoticz after device creation (EnergyMeterMode fix applied)

### Debug Logging
Enable Debug or Verbose mode to see:
- Raw API responses
- Data validation details
- Sleep mode detection
- Power calculations
