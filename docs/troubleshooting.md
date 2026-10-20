# Troubleshooting

[Українська версія](troubleshooting.uk.md)

Most problems show up in the status line at the bottom of the widget or in the settings window. For details, open **Open data folder** from the widget menu and look at `widget.log`.

## "Keys work, but EcoFlow lists no devices"

The keys are valid, but the account they belong to has no stations linked.

1. Open the EcoFlow mobile app and check the station is on its home screen. If not, add it with "+".
2. Check the app is logged into the same account as the developer portal. See [Getting your API keys](getting-api-keys.md).
3. As a shortcut, type the serial number into **Device** and press **Save**. The serial is on the station's label and in the app's device settings. The widget then checks it directly with EcoFlow and shows the exact reason if it's refused.

## "Keys rejected by EcoFlow"

- Copy both keys again. Watch for spaces at the start or end.
- Check the key's **state** is "Enable" on the developer portal.
- Check your PC's clock is correct. Requests carry a timestamp, and a clock that is far off gets rejected.

## "No connection"

The PC can't reach EcoFlow's servers. Check the PC's internet. Firewalls or VPNs can also block it. The widget retries on its own and keeps showing the last known values in grey.

## "Device offline in EcoFlow cloud"

The station is off, asleep or not on Wi-Fi. Check that the EcoFlow app shows it online. Some stations drop Wi-Fi after a long standby, and turning the station's screen on usually wakes it.

## Some values show "--"

Your model may use different data fields. The widget is built for the DELTA 2 Max. `last_data.json` in the data folder shows exactly what EcoFlow sent, which helps when adding support for another model.

## A switch doesn't change

- Wait 5 to 10 seconds. The station confirms the change on the next update.
- If an error box appears, its text is EcoFlow's reason. Some firmware versions or models don't accept remote commands.

## Python problems

- **"py is not recognized"** or the Microsoft Store opens: install Python from python.org and tick **Add python.exe to PATH**.
- **"No module named tkinter"**: run the Python installer again, choose **Modify** and make sure **tcl/tk and IDLE** is ticked.
- **Nothing happens when running `start.bat`**: open a Command Prompt in the folder and run `py ecoflow_widget.pyw`. This shows any error on screen.

## The tray icon is missing

Windows 11 hides new tray icons. Click the `^` arrow near the clock, or turn the widget on in Settings > Personalization > Taskbar > Other system tray icons.

## The widget is off-screen

This can happen after changing monitors. Quit the widget, open `config.json` in the data folder, set `"x"` and `"y"` to `null`, save and start the widget again. It returns to the bottom-right corner.
