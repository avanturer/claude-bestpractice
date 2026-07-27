@echo off
rem Windows shim for %~n0. Claude Code runs hooks through Git Bash when it is present and
rem PowerShell when it is not; PowerShell resolves an extensionless path through PATHEXT,
rem so this file is what makes `bin/session-start` runnable there without changing a single
rem hook command. Git Bash keeps using the shebang on the extensionless file next to it.
rem
rem `py -3` first: the python.org installer ships py.exe and python.exe but NOT python3.exe,
rem so the shebang the POSIX side relies on has no name to resolve to on Windows.
setlocal
where /q py && (
  py -3 "%~dp0%~n0" %*
) || (
  python "%~dp0%~n0" %*
)
exit /b %ERRORLEVEL%
