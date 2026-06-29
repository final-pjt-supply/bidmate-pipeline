import win32com.client as win32

try:
    hwp = win32.gencache.EnsureDispatch("HWPFrame.HwpObject")
    print("성공: COM 객체 잡힘")
    print(hwp)
except Exception as e:
    print("실패:", e)