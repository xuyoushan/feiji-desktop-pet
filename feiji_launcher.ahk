#SingleInstance Force

; 肥鸡桌面宠物 - 开机自启动脚本（AHK v1）
; 静默启动 main_new.py

PythonExe := "C:\Users\Friendly_Xu\AppData\Local\Programs\Python\Python311\python.exe"
ScriptPath := A_ScriptDir . "\main_new.py"

Run, %PythonExe% "%ScriptPath%", , Hide
