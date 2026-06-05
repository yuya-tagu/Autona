Push-Location ..

New-Item -Path "Autona" -ItemType Directory

Copy-Item -Recurse -Force "assets" "Autona\assets"

# Already embedded inside release exe
Remove-Item -Path "Autona\assets\versions.json" -Force

# Copy layout excluding game_images and symlinks
Get-ChildItem -Path "layout" -Recurse | Where-Object {
    $_.FullName -notmatch "layout\\game_images" -and 
    $_.FullName -notmatch "layout\\game_screenshots" -and 
    -not $_.Attributes.HasFlag([System.IO.FileAttributes]::ReparsePoint)
} | ForEach-Object {
    $destination = $_.FullName.Replace("layout", "Autona\layout")
    if ($_.PSIsContainer) {
        New-Item -ItemType Directory -Path $destination -Force
    } else {
        Copy-Item -Path $_.FullName -Destination $destination -Force
    }
}

Copy-Item -Recurse -Force "user_data" "Autona\user_data"
Copy-Item -Recurse -Force "web\build" "Autona\web\build"
Copy-Item -Force "LICENSE" "Autona\LICENSE"
Copy-Item -Force "Autona.exe" "Autona\Autona.exe"

Compress-Archive -Path "Autona" -DestinationPath "release-windows.zip" -Update

Pop-Location
