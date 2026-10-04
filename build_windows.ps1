$ErrorActionPreference = "Stop"
Write-Host "Building Paper2Lab for Windows..."
python -m pip install --upgrade pyinstaller
python -m PyInstaller --noconfirm --clean --distpath dist --workpath build Paper2Lab.spec
Write-Host "Running frozen executable smoke tests..."
& .\dist\Paper2Lab.exe --self-test
if ($LASTEXITCODE -ne 0) { throw "Frozen UI compatibility self-test failed with exit code $LASTEXITCODE" }
& .\dist\Paper2Lab.exe --service-self-test
if ($LASTEXITCODE -ne 0) { throw "Frozen service self-test failed with exit code $LASTEXITCODE" }
Write-Host "Done: dist\\Paper2Lab.exe"
