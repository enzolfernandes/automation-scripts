Option Explicit

Dim shell, fileSystem, baseFolder, scriptPath, command

Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

baseFolder = fileSystem.GetParentFolderName(WScript.ScriptFullName)
scriptPath = baseFolder & "\Tools\Local-Channel-Diagnostics.ps1"
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " & Chr(34) & scriptPath & Chr(34) & " -Acao Interface"

shell.Run command, 0, False
