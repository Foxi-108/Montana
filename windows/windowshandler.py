import asyncio, win32gui, win32con, win32process, os, sys, shutil, aiohttp, aiofiles, zipfile, ctypes, _thread, threading
from signal import raise_signal
from pathlib import Path
from win32api import GetSystemMetrics



WINEVENTPROC = ctypes.WINFUNCTYPE(None, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_long, ctypes.c_long, ctypes.c_ulong, ctypes.c_ulong)



class WindowsHander:
    async def centred(self):
        self.Window.show()
        await asyncio.sleep(0)
        self.hwnd = win32gui.FindWindow(None, "Montana")
        if self.hwnd:
            x = (GetSystemMetrics(0) - 800) // 2
            y = (GetSystemMetrics(1) - 500) // 2
            win32gui.SetWindowPos(self.hwnd, 0, x, y, 800, 500, win32con.SWP_NOZORDER)
            return False
        return True


    async def init_env(self, quit_func):
        self.working_path = Path(__file__).parent
        self.ffmpeg_path = shutil.which('ffmpeg')
        self.__quit_func = quit_func

        if Path(self.working_path / 'ffmpeg').exists():
            if Path(self.working_path / 'ffmpeg').is_dir() and next(Path(self.working_path / 'ffmpeg').glob("*"), None):
                self.ffmpeg_path = self.working_path / 'ffmpeg'

        if self.ffmpeg_path == None:
            resault = await self.download_ffmpeg()
            if not resault:
                print("err")
                
        self.loop = asyncio.get_running_loop()
        thread_id, pid = win32process.GetWindowThreadProcessId(self.hwnd)
        self.__WINEVENTPROC = WINEVENTPROC(self.__event_hook)
        self.hook_id = ctypes.windll.user32.SetWinEventHook(
            win32con.EVENT_OBJECT_DESTROY,  # eventMin
            win32con.EVENT_OBJECT_DESTROY,  # eventMax
            0,                               # hmodWinEventProc (0 для локальных хуков)
            self.__WINEVENTPROC,              # lpfnWinEventProc
            pid,                      # idProcess
            thread_id,                       # idThread
            0                                # dwflags (0 по умолчанию)
        )


    async def download_ffmpeg(self):
        print('FFmpeg not found. Attempting to install ...')
        async with aiohttp.ClientSession(headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Accept-Encoding": "gzip, deflate, br"}, connector = aiohttp.TCPConnector(ssl=False)) as session:
            async with session.get("https://www.gyan.dev/ffmpeg/builds/ffmpeg-git-github") as response:
                if response.status != 200:
                    print(f'[1] Response status: {response.status}')
                    return False

                base_url, tag = str(response.url).split("/tag/")
                print('[*] Latest build found. Downloading...') 


            print(base_url + f"/download/{tag}/ffmpeg-{tag}-full_build.zip")
            async with session.get(base_url + f"/download/{tag}/ffmpeg-{tag}-full_build.zip") as response:
                if response.status != 200:
                    print(f'[2] Response status: {response.status}')
                    return False

                total_size = int(response.headers.get('Content-Length', 0))
                downloaded = 0
                self.ffmpeg_path = self.working_path / "ffmpeg"
                self.ffmpeg_path.mkdir(exist_ok = True)

                async with aiofiles.open(self.working_path / 'ffmpeg' / 'temp.zip', mode='wb') as f:
                    async for chunk in response.content.iter_chunked(65536): #64 kib
                        await f.write(chunk)
                        
                        downloaded += len(chunk)
                        if total_size > 0:
                            percent = (downloaded / total_size) * 100
                            print(f"\rDownload: {percent:.1f}% ({downloaded / (1024*1024):.2f} MB)", end="")
                        else:
                            print(f"\r", end="")

        await asyncio.to_thread(self.__extract_archive(self.working_path / 'ffmpeg'))
        await asyncio.sleep(1)
        os.remove(self.working_path / 'ffmpeg' / 'temp.zip')
        return True


    def __extract_archive(path):
        with zipfile.ZipFile( path / 'temp.zip', 'r') as zip:
            i : zipfile.ZipInfo
            for i in zip.infolist():
                if not i.is_dir() and "/bin/" in i.filename:
                    _, name = i.filename.split("/bin/")
                    file = zip.read(i)

                    with open(path / name, "wb") as f:
                        f.write(file)


    def __event_hook(self, hWinEventHook, event, hwnd, idObject, idChild, idEventThread, dwmsEventTime):
        if hwnd == self.hwnd:
            if event == win32con.EVENT_OBJECT_DESTROY:
                if self.hook_id:
                    ctypes.windll.user32.UnhookWinEvent(self.hook_id)
                    self.loop.call_soon_threadsafe(self.__quit_func)
