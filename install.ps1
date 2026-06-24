$dest = "$env:LOCALAPPDATA\Microsoft\WindowsApps"

Copy-Item "$PSScriptRoot\switch-model.bat" "$dest\switch-model.bat" -Force
Copy-Item "$PSScriptRoot\switch_model.py" "$dest\switch_model.py" -Force
Copy-Item "$PSScriptRoot\backends.json" "$dest\backends.json" -Force

Write-Host "Installed. Run: switch-model setup"
