import win32com.client as win32
import time

hwp = win32.gencache.EnsureDispatch("HWPFrame.HwpObject")
hwp.RegisterModule("FilePathCheckDLL", "FilePathCheckerModule")

src = r"C:\Users\Administrator\Downloads\설계공모지침서(R26DC00213032).hwpx"
dst = r"C:\Users\Administrator\Downloads\설계공모지침서(R26DC00213032).pdf"

start = time.perf_counter()

open_result = hwp.Open(src, "HWPX", "forceopen:true")
open_time = time.perf_counter()
print(f"Open 결과: {open_result} ({open_time - start:.2f}초)")

save_result = hwp.SaveAs(dst, "PDF")
save_time = time.perf_counter()
print(f"SaveAs 결과: {save_result} ({save_time - open_time:.2f}초)")

hwp.Quit()

print(f"전체 소요: {save_time - start:.2f}초")
print("완료:", dst)