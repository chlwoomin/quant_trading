@echo off
set HOST=ec2-user@ec2-3-36-109-13.ap-northeast-2.compute.amazonaws.com
set KEY=%USERPROFILE%\.ssh\Quant_Server_Key.pem
set PORT=8080

echo.
echo Opening quant dashboard tunnel...
echo Browser URL: http://localhost:%PORT%
echo Close this window to close the tunnel.
echo.

start "" "http://localhost:%PORT%"
ssh -i "%KEY%" -N -L %PORT%:localhost:%PORT% %HOST%
