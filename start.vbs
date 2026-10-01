Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root
python = root & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(python) Then
    MsgBox "Run setup.ps1 first.", 48, "UCPC"
Else
    shell.Run Chr(34) & python & Chr(34) & " -m ucpc", 0, False
End If

