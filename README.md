# EcoFlow Desktop Widget

[Українська версія](README.uk.md)

A small always-on-top widget and tray icon for Windows that shows the live status of an EcoFlow DELTA 2 Max power station and lets you switch its outputs.

![The widget: normal, charging, low battery, connection lost](docs/images/widget-states.png)

![Tray icon: charging, normal, low, no data](docs/images/tray-icons.png)

## Features

- Battery % ring, charging or discharging state, time to full or empty
- Input and output watts, battery temperature
- AC, DC 12V and USB output switches with live watts for each port (turning AC off asks for confirmation first)
- Tray icon with the battery %, colored by level, a summary on hover and the output switches in its menu
- Shows clearly when data is stale or the connection is lost
- The Secret Key is stored in Windows Credential Manager, not in a file
- Remembers its position on screen and can start with Windows

## Supported devices

Built and tested with the **DELTA 2 Max**. Other EcoFlow models report their data in different fields, so some values may stay blank and the switches may not work.

## How it works

The widget uses the official EcoFlow IoT Developer API, which is cloud-based. Your PC talks to EcoFlow's servers over the internet and does not connect to the station on your local network. So both the station (over Wi-Fi) and the PC need internet for updates. During a power outage the widget keeps showing the last known values with "updated X min ago" until your router and internet are back.

## Requirements

- Windows 10 or 11
- Python 3.9 or newer from [python.org](https://www.python.org/downloads/)
- The station added in the EcoFlow mobile app and connected to Wi-Fi
- An EcoFlow developer account with an Access Key and Secret Key. See [Getting your API keys](docs/getting-api-keys.md)

## Installation

1. **Check Python.** Press Win+R, type `cmd`, press Enter and run `py --version`. Version 3.9 or newer is fine. If the command is not found or the Microsoft Store opens, install Python from python.org and tick **Add python.exe to PATH** on the first installer screen.
2. **Download the widget.** On this page click the green **Code** button, then **Download ZIP**, or get the ZIP from **Releases**. Unzip it to a permanent folder such as `C:\Tools\ecoflow-widget`. Don't run it from Downloads, because the startup shortcut will point to this folder.
3. Double-click `install.bat`.
4. Double-click `start.bat`. The settings window opens. Paste your Access Key and Secret Key, press **Connect**, pick your station and press **Save**. If the station is not in the list, type its serial number into **Device** and press **Save**.
5. Optional: double-click `add_to_startup.bat` so the widget starts with Windows.

**Try it without keys:** open the widget folder, click the File Explorer address bar, type `cmd`, press Enter and run `start.bat --demo`.

Windows may show a security warning when you first run `install.bat`, because the file came from the internet. Click **Run**, or **More info** and then **Run anyway**. The scripts are not signed, which is normal for small open-source tools. After that the widget clears this mark from its files by itself, and the startup shortcut launches Python directly, so the warning won't come back at startup.

## Using it

- **Drag** the panel anywhere. It remembers the position.
- **Click an AC, DC 12V or USB tile** to switch that output. A blue switch means on, and the number next to it is how many watts that port is using right now. USB adds up all USB-A and USB-C ports. Turning AC off asks for confirmation, because it cuts power to everything plugged into the station's AC sockets.
- **The ring** is blue above 20%, turns orange-red at 20% and below, and goes grey when the data is stale.
- **Right-click** the panel or click `...` for Refresh now, Settings, Open data folder and Quit. The `x` hides the panel to the tray.
- **The tray icon** shows the battery %. It is blue normally, red at 20% and below, and grey when data is stale. A white line under the number means charging. Left-click shows or hides the panel, right-click opens a menu with the output switches. Hover for a summary.
- To keep the tray icon always visible: Settings > Personalization > Taskbar > Other system tray icons, then turn on the widget.

## Status line

| Text | Meaning |
|---|---|
| `Online · updated 12s ago` | Everything works |
| `No connection` | The PC can't reach EcoFlow. The last known values are shown in grey |
| `Device offline in EcoFlow cloud` | The station is off or not connected to Wi-Fi |
| `Keys rejected` | Check the keys in Settings |

## Updating

Download the new ZIP and replace the files in your existing folder. Keep the same folder so the startup shortcut keeps working. Run `start.bat` once by hand: Windows may ask one last time, then the widget clears the warning for the new files. If `requirements.txt` changed, run `install.bat` again. Your settings and keys are kept.

Updating from v1.0.0 or v1.1.0? Run `add_to_startup.bat` once more. It replaces the old startup shortcut with one that never triggers the security warning.

## Uninstalling

1. Quit the widget from the tray menu.
2. Press Win+R, type `shell:startup` and delete the "EcoFlow Widget" shortcut, if you added it.
3. Delete the widget folder.
4. Delete `%APPDATA%\EcoFlowWidget` (paste it into the File Explorer address bar).
5. Open Credential Manager > Windows Credentials and remove the entry that contains "EcoFlowWidget".

## Privacy and security

- The widget talks only to EcoFlow's API servers. There is no telemetry.
- The Secret Key never leaves your PC. It is only used to sign requests.
- Settings live in `%APPDATA%\EcoFlowWidget`: `config.json` (Access Key, serial number, settings), `last_data.json` (the last reading) and `widget.log`. Don't share these files publicly.

## Troubleshooting

See [Troubleshooting](docs/troubleshooting.md).

## Disclaimer

This is an independent project and is not affiliated with or endorsed by EcoFlow. "EcoFlow" and "DELTA" are trademarks of their owners. Switching outputs remotely is at your own risk. EcoFlow can change its API at any time, which may break the widget.

## Acknowledgements

The DELTA 2 Max data field names and command formats are based on the work of the [hassio-ecoflow-cloud](https://github.com/tolwi/hassio-ecoflow-cloud) Home Assistant integration.

## License

[MIT](LICENSE)
