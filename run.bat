@echo off
set PYTHON=C:\Users\woomin\AppData\Local\Programs\Python\Python39\python.exe

if "%1"=="" goto help
if "%1"=="help" goto help
if "%1"=="server" goto server
if "%1"=="pipeline" goto pipeline
if "%1"=="backtest" goto backtest
if "%1"=="walkforward" goto walkforward
if "%1"=="montecarlo" goto montecarlo
if "%1"=="dart" goto dart
if "%1"=="report" goto report
if "%1"=="notify" goto notify
if "%1"=="strategy" goto strategy
if "%1"=="status" goto status
echo Unknown command: %1
goto :eof

:server
%PYTHON% -X utf8 server.py %2 %3 %4 %5
goto :eof

:pipeline
%PYTHON% -X utf8 weekly_pipeline.py %2 %3 %4 %5
goto :eof

:backtest
%PYTHON% -X utf8 backtest.py %2 %3 %4 %5
goto :eof

:walkforward
%PYTHON% -X utf8 walk_forward.py %2 %3 %4 %5 %6
goto :eof

:montecarlo
%PYTHON% -X utf8 monte_carlo.py %2 %3 %4 %5
goto :eof

:dart
%PYTHON% -X utf8 dart_fetcher.py %2 %3 %4 %5
goto :eof

:report
%PYTHON% -X utf8 report_builder.py %2 %3 %4
goto :eof

:notify
%PYTHON% -X utf8 notifier.py %2 %3 %4
goto :eof

:strategy
%PYTHON% -X utf8 strategy_manager.py %2 %3 %4
goto :eof

:status
%PYTHON% -X utf8 status.py %2 %3 %4
goto :eof

:help
echo.
echo  Usage: run [command] [options]
echo.
echo  run server              Start 24/7 server daemon
echo  run server --test       Print schedule and exit
echo.
echo  run pipeline            Run weekly pipeline (full)
echo  run pipeline --no-ai    Skip AI analysis
echo  run pipeline --no-real  Skip real data validation
echo  run pipeline --report   Print performance only
echo.
echo  run backtest            Simulation backtest
echo  run backtest --real     Real data backtest (yfinance)
echo.
echo  run walkforward         Walk-forward test (real data, 2yr window)
echo  run walkforward --dart  Use DART quarterly fundamentals
echo  run walkforward --compare  Compare current vs previous version
echo.
echo  run montecarlo          Monte Carlo test (sim, 50 runs)
echo  run montecarlo --runs 100
echo  run montecarlo --compare
echo.
echo  run dart                Fetch DART quarterly fundamentals (30 stocks, 5yr)
echo  run dart --test         Test with Samsung only
echo  run dart --refresh      Re-fetch ignoring cache
echo.
echo  run report              Generate HTML report preview
echo  run notify              Test notification channels
echo  run strategy            Show current strategy parameters
echo.
goto :eof
