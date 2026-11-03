import slint, asyncio, sys, subprocess, sounddevice
from pathlib import Path



class Player:
    def __init__(self, ffmpeg_path : Path):
        self.ffmpeg = ffmpeg_path / 'ffmpeg.exe'
        self.is_playing = None



    async def play(self, path : str | Path = None):
        if path is not None:
            if type(path) is not str:
                path = str(path)

            self.is_playing = 1
            
            await asyncio.to_thread(self.__play_loop, path)

            self.is_playing = None


    def __play_loop(self, path):
        sample_rate = 44100
        channels = 2
        chunk = 4096 

        command = [
            str(self.ffmpeg),
            "-i", path,
            "-f", "s16le",
            "-acodec", "pcm_s16le",
            "-ar", str(sample_rate),
            "-ac", str(channels),
            "-"
        ]
        self.__current_process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        try:
            with sounddevice.RawOutputStream(samplerate=sample_rate, channels=channels, dtype='int16') as stream:
                while self.is_playing:
                    data = self.__current_process.stdout.read(chunk)
                    
                    if not data: # Трек закончился
                        break
                        
                    if self.is_playing:
                        stream.write(data)
        finally:
            stream.close()
            self.__cleanup()


    def __cleanup(self):
        if self.__current_process:
            try:
                self.__current_process.terminate()
                self.__current_process.wait(timeout=0.2)
            except (subprocess.TimeoutExpired, ProcessLookupError):
                try:
                    self.__current_process.kill()
                except ProcessLookupError:
                    pass
            self.__current_process = None


    def stop(self):
        self.is_playing = None



class App:
    def __init__(self):
        if sys.platform == "win32":
            from windows.windowshandler import WindowsHander
            self.handler = WindowsHander()
        elif sys.platform == "linux":
            pass
        else:
            sys.exit(0)
        self.ui = slint.load_file("assets/ui.slint")
        self.Window = self.ui.MainWindow()
        self.handler.Window = self.Window
        


    async def SetUp(self):
        await self.handler.centred()
        await self.handler.init_env(self.quit)
        self.player = Player(self.handler.ffmpeg_path)
        await self.test_func()
        return self.Window


    async def test_func(self):
        asyncio.create_task(self.player.play(Path("test.mp3")))
        await asyncio.sleep(15)
        print("stop")
        self.player.stop()


    def quit(self):
        self.player.stop()



if __name__ == "__main__":
    app = App()

    try:
        slint.run_event_loop(app.SetUp())
    except KeyboardInterrupt:
        pass
    finally:
        app.quit()
        slint.quit_event_loop()