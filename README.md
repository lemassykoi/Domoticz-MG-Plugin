# SAIC iSmart Plugin for Domoticz (MG Cars)

A comprehensive Domoticz plugin for MG (SAIC) electric and hybrid vehicles that connects directly to the MG iSmart API without requiring MQTT or external dependencies.

## 🚗 Supported Vehicles

- **MG4** (Electric)
- **MG5** (Electric) 
- **MG ZS EV**
- **MG Marvel R Electric**
- **MG HS** (Plug-in Hybrid)
- Other SAIC vehicles with iSmart connectivity

## ✨ Features

### 🔋 Battery & Charging
- **Battery Level**: Real-time state of charge (SoC) monitoring
- **Charging Status**: Active charging detection with power monitoring
- **Charging Control**: Start/stop charging remotely
- **Charge Limit Setting**: Set target SoC (40%, 50%, 60%, 70%, 80%, 90%, 100%)
- **Time to Full**: Remaining charging time estimation
- **Power Usage**: Daily power consumption tracking
- **Charging Power**: Real-time charging power (kW) monitoring

### 🚙 Vehicle Status
- **Lock Status**: Door lock/unlock detection and control
- **Engine Status**: Engine on/off monitoring
- **Hand Brake**: Parking brake status
- **Climate Control**: Remote A/C start/stop
- **12V Battery**: Auxiliary battery voltage monitoring

### 📍 Location & Navigation  
- **GPS Location**: Latitude/longitude coordinates
- **Address**: Reverse geocoding to street address
- **Home Detection**: Automatic home/away status based on GPS radius
- **Speed**: Current vehicle speed monitoring

### 🛞 Advanced Sensors
- **Tire Pressure**: All four tires with automatic PSI/bar conversion (work in progress)
- **Temperature**: Interior and exterior temperature sensors
- **Heated Seats**: Control for front left/right seat heating
- **Range**: Current driving range estimation
- **Odometer**: Total vehicle mileage

### 🔔 Smart Notifications
- **Charging Started**: Notification when charging begins
- **Charging Complete**: Alert when target charge level reached
- **Charging Stopped**: Notification when charging ends

### 🏠 Integration Features
- **Room Plans**: Automatic creation and device organization
- **Sleep Mode Detection**: Intelligent handling of vehicle sleep states
- **Token Management**: Persistent authentication with disk storage (login once every ~4 months)
- **Error Recovery**: Automatic retry with exponential backoff
- **🌙 Night Cooldown**: Reduced polling (1 hour) when at home during night hours (22:30-07:30)

## 📋 Requirements

### System Requirements
- **Domoticz** with Python plugin support enabled
- **Python 3.9+**
- **cryptography library** for token encryption (`sudo pip install -U cryptography>=3.0.0 --break-system-packages`)
- **Internet connection** for SAIC API access

### Account Requirements
- **MG iSmart account** with valid credentials
- **Vehicle registered** in the MG iSmart mobile app
- **Region selection**: Europe (eu, default) or Australia/New Zealand (au)

### iSMART Account Recommendation

**IMPORTANT**: If you are currently using the same account credentials in the official MG iSMART mobile app, you should create a dedicated secondary account for this plugin to avoid authentication conflicts. Using the same account simultaneously in both the app and the plugin will cause the mobile app to repeatedly request re-authentication.

**How to create a secondary account:**
1. Create a new MG iSMART account using a different email address
2. From your primary account, go to Settings → Secondary Account
3. Invite the newly created secondary account
4. Use the secondary account credentials in this plugin

**Note**: There's no need to associate the vehicle with the secondary account - it will inherit access through the invitation from your primary account.

## 🚀 Installation

### 1. Download Plugin
```bash
cd /home/pi/domoticz/plugins
git clone https://github.com/lemassykoi/Domoticz-MG-Plugin.git
```

### 2. Install Dependencies
`sudo pip install saic-ismart-client-ng>=2.0.0 --break-system-packages`

`sudo pip install -U requests>=2.25.0 --break-system-packages`

`sudo pip install -U cryptography>=3.0.0 --break-system-packages`


### 3. Configure Plugin
1. Go to **Setup** → **Hardware** in Domoticz
2. Add new hardware of type **SAIC iSmart (MG Cars)**
3. Configure the following parameters:

| Parameter | Description | Default |
|-----------|-------------|---------|
| **Username** | MG iSmart email address | *Required* |
| **Password** | MG iSmart password | *Required* |
| **Region** | Europe (eu) or Australia/NZ (au) | eu |
| **Update Interval** | Data refresh interval in seconds | 180 |
| **Home Radius** | GPS radius for home detection (meters) | 25 |
| **Domoticz Port** | Local Domoticz port for notifications | 8080 |
| **Night Start Hour** | The Hour only (not minutes) for starting period | 22 |
| **Night End Hour** | The Hour only (not minutes) for ending period | 7 |
| **Debug Level** | Normal, Debug, or Verbose | Normal |

### 4. Enable Hardware
Click **Add** to enable the plugin. The plugin will:
- Authenticate with MG iSmart API
- Discover your vehicle(s)
- Create 36+ devices automatically
- Create a room plan for organization

## 🎛️ Device Overview

The plugin creates **36+ devices** organized by category:

### Core Status (6 devices)
- Battery Level, Range, Charging Status, Location, Lock Status, Engine Status

### Controls (4 devices)  
- Climate Control, Charging Start/Stop, Charge Limit Selector, Lock Control

### Sensors (12 devices)
- 4x Tire Pressure, 2x Temperature, Speed, 12V Battery, Hand Brake, etc.

### Advanced (14+ devices)
- Heated Seats, Power Monitoring, Address, Home Detection, etc.

## 🔧 Configuration Tips

### Debug Levels
- **Normal**: Essential logs only
- **Debug**: Detailed operation logs  
- **Verbose**: Full debugging including API responses

### Home Detection
- Set **Home Radius** to appropriate distance (default 25m)
- Plugin uses GPS coordinates to determine home/away status
- Requires Domoticz location to be configured

### Update Interval
- **180 seconds** (default) - Good balance of data freshness and API usage
- **300+ seconds** - Conservative usage for occasional monitoring
- **60-120 seconds** - Frequent updates (may impact battery if car is awake)

#### 🌙 Night Cooldown Mode (v1.6.8+)
The plugin automatically reduces polling frequency during nighttime hours (22:30-06:30) when the car is detected at home:
- **Night + At Home**: Polls every 1 hour to preserve 12V battery
- **Night + Away**: Uses normal interval for security monitoring
- **Daytime**: Always uses configured interval regardless of location

## 🛠️ Troubleshooting

### Common Issues

#### Authentication Problems
```
Error: Authentication failed
```
- Verify MG iSmart credentials are correct
- Check region setting (Europe vs Australia/NZ)
- Ensure vehicle is registered in MG iSmart app

#### Missing Devices
```
Error: No vehicles found
```
- Confirm vehicle is properly registered
- Try different region setting
- Check debug logs for API errors

#### Connection Issues  
```
Error: SAIC API error: 500
```
- Check internet connection
- Verify MG iSmart service is operational
- Enable Debug logging for detailed error info

### Debug Process
1. Set **Debug Level** to "Debug" or "Verbose"
2. Restart Domoticz: `sudo systemctl restart domoticz`
3. Check logs: `sudo journalctl -u domoticz -f`
4. Look for plugin startup and authentication messages

## 🔐 Security & Privacy

### Data Protection
- **VIN numbers** are hashed in logs for privacy
- **Passwords** are securely stored by Domoticz
- **API tokens** are encrypted with AES-256-GCM using credentials-derived keys and stored in `saic_token.json`
- **Token encryption** prevents token theft - files are useless without matching email/password

### Network Access
- **MG iSmart API**: Official SAIC/MG servers only
- **OpenStreetMap**: For reverse geocoding (address lookup)
- **Local Domoticz API**: For notifications and room plans

## 📊 Technical Details

### Architecture
- **Threading**: Dedicated asyncio thread for API operations
- **Token Management**: Persistent authentication with automatic renewal
- **Sleep Detection**: Intelligent handling of vehicle sleep modes
- **Error Handling**: Retry mechanisms with exponential backoff

### API Efficiency
- **Login Frequency**: Once every ~4 months (when token expires)
- **Token Storage**: Persistent disk storage prevents re-login after plugin restarts
- **Data Caching**: Efficient API usage with proper token reuse
- **Sleep Awareness**: Reduced API calls when vehicle is sleeping

## 🤝 Contributing

### Development Setup
1. Fork the repository
2. Create a feature branch
3. Test with your vehicle
4. Submit a pull request

### Issue Reporting
Please include:
- Domoticz version
- Python version
- Vehicle model and year
- Debug logs (with VIN redacted)
- Steps to reproduce

## 📝 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🙏 Acknowledgments

- **SAIC iSmart API Client**: Based on the excellent `saic-ismart-client-ng` library
- **Domoticz Community**: For the robust plugin framework
- **MG Owners**: For testing and feedback

---

**Version**: 1.6.8  
**Author**: lemassykoi  
**Repository**: https://github.com/lemassykoi/Domoticz-MG-Plugin
