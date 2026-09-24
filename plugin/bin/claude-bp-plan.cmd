@echo off
rem Windows shim for %~n0. Claude Code runs hooks through Git Bash when it is present and
rem PowerShell when it is not; PowerShell resolves an extensionless path through PATHEXT,
rem so this file is what makes `bin/session-start` runnable there without changing a single
rem hook command. Git Bash keeps using the shebang on the extensionless file next to it.
rem
rem `py -3` first: the python.org installer ships py.exe and python.exe but NOT python3.exe,
rem so the shebang the POSIX side relies on has no name to resolve to on Windows.
rem
rem Branched on the exit code of `where`, never chained with && and ||. cmd.exe runs the
rem command after || whenever what comes before it fails, and before it stood the whole
rem `where /q py && (py -3 ...)`: a gate that refused by exiting 2 was run a second time
rem through `python`, with its stdin already read - and where `python` is the Store
rem alias, the refusal became an unrelated error the harness does not treat as one.
setlocal
where /q py
if %ERRORLEVEL% EQU 0 (
  py -3 "%~dp0%~n0" %*
) else (
  python "%~dp0%~n0" %*
)
exit /b %ERRORLEVEL%
