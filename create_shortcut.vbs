Set oWS = WScript.CreateObject("WScript.Shell")
sLinkFile = oWS.ExpandEnvironmentStrings("%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\LamtonApp.lnk")
Set oLink = oWS.CreateShortcut(sLinkFile)
oLink.TargetPath = "pythonw.exe"
oLink.Arguments = "d:\LAMTON_V1\LAMTON_LINE_NEW_ESP32.py"
oLink.WorkingDirectory = "d:\LAMTON_V1"
oLink.Save
