$ErrorActionPreference = "Stop"
Write-Host "Building Paper2Lab for Windows..."
python -m pip install --upgrade pyinstaller
if ($LASTEXITCODE -ne 0) { throw "PyInstaller installation failed with exit code $LASTEXITCODE" }
python -m PyInstaller --noconfirm --clean --distpath dist --workpath build Paper2Lab.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed with exit code $LASTEXITCODE" }
Write-Host "Running frozen executable smoke tests..."
$UiSmoke = Start-Process -FilePath (Join-Path $PSScriptRoot "dist\Paper2Lab.exe") -ArgumentList "--self-test" -Wait -PassThru
if ($UiSmoke.ExitCode -ne 0) { throw "Frozen UI compatibility self-test failed with exit code $($UiSmoke.ExitCode)" }
$ServiceSmoke = Start-Process -FilePath (Join-Path $PSScriptRoot "dist\Paper2Lab.exe") -ArgumentList "--service-self-test" -Wait -PassThru
if ($ServiceSmoke.ExitCode -ne 0) { throw "Frozen service self-test failed with exit code $($ServiceSmoke.ExitCode)" }
Write-Host "Done: dist\\Paper2Lab.exe"
